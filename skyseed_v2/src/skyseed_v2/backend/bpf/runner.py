"""Own precomputed-only live execution for one started BPF deployment."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Condition, RLock, Thread
from time import monotonic, monotonic_ns, sleep, time
from typing import Any, Mapping, Sequence

from skyseed_v2.shared import NodeId
from ._runner.event_history import _NonBlockingEventHistory
from ._runner.runtime_realization import (
    _IncompleteActivationError,
    _TcBpfRuntimeRealization,
)
from ._runner.traffic_telemetry import _TrafficTelemetryRecorder
from .compiled_realization import _PhysicalWorldProjection
from ._runner_artifact import (
    _BoundaryOperation,
    _NominalBoundary,
    _open_runner_artifact,
    _resolve_nominal_boundaries,
)


_PROJECT_LABEL = "com.docker.compose.project"
_SERVICE_LABEL = "com.docker.compose.service"
_ASN_LABEL = "org.seedsecuritylabs.seedemu.meta.asn"
_ENTITY_TYPE_LABEL = "org.seedsecuritylabs.seedemu.meta.skyseed.entity_type"
_SATELLITE_ID_LABEL = "org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id"
_GROUND_STATION_ID_LABEL = "org.seedsecuritylabs.seedemu.meta.skyseed.ground_station_id"
_RUNNER_HELPER_LABEL = "org.skyseed.runner.foundation"
_RUNNER_PRIVATE_ROOT = Path(__file__).with_name("_runner")
_PRIVILEGED_COMMAND_PATH = _RUNNER_PRIVATE_ROOT / "privileged_runtime_command.py"
_TRAFFIC_TELEMETRY_COLLECTOR_PATH = (
    _RUNNER_PRIVATE_ROOT / "traffic_telemetry_collector.py"
)
_RECENT_EVENT_LIMIT = 512
_RUNTIME_WORKER_ENVIRONMENT = "SKYSEED_BPF_RUNTIME_WORKERS"
_EVENT_SEQUENCE_LIMIT = 64


def _runtime_worker_count() -> int:
    raw = os.environ.get(_RUNTIME_WORKER_ENVIRONMENT)
    if raw is None:
        return min(8, max(1, os.cpu_count() or 1))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(
            f"{_RUNTIME_WORKER_ENVIRONMENT} must be an integer"
        ) from error
    if not 1 <= value <= 64:
        raise ValueError(
            f"{_RUNTIME_WORKER_ENVIRONMENT} must be between 1 and 64"
        )
    return value


def _compact_event_value(value: object) -> object:
    """Detach bounded evidence without copying large runtime collections."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if name == "per_node" and isinstance(item, Sequence):
                result["per_node_count"] = len(item)
                continue
            result[name] = _compact_event_value(item)
        return result
    if isinstance(value, (set, frozenset)):
        value = tuple(sorted(value, key=repr))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _EVENT_SEQUENCE_LIMIT:
            return {"item_count": len(value), "detail": "omitted-from-inline-evidence"}
        return tuple(_compact_event_value(item) for item in value)
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "detail": "omitted-from-inline-evidence",
    }



@dataclass(frozen=True, slots=True)
class _RuntimeBinding:
    node_id: NodeId
    service_name: str
    container_id: str
    container_name: str
    image_id: str
    pid: int
    process_start_time_ticks: int
    netns_inode: int
    started_at: str


def _execute(
    command: Sequence[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        tuple(command), input=input_text, text=True, capture_output=True, check=False
    )


def _require_success(
    result: subprocess.CompletedProcess[str], *, context: str
) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        raise RuntimeError(
            f"{context} failed ({result.returncode}); "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
    return result


class _PrivilegedRuntimeCommandError(RuntimeError):
    """Preserve a private child failure classification across docker exec."""

    def __init__(
        self,
        message: str,
        *,
        remote_error_type: object,
        remote_evidence: object = None,
    ) -> None:
        super().__init__(message)
        self.remote_error_type = remote_error_type
        self.remote_evidence = remote_evidence


class _PrivilegedHelper:
    """Own one privileged container and one persistent command stream."""

    def __init__(
        self,
        *,
        docker_executable: str,
        name: str,
        foundation_token: str,
        image_id: str,
    ) -> None:
        self._docker = docker_executable
        self._name = name
        self._foundation_token = foundation_token
        self._image_id = image_id
        self._started = False
        self._process: subprocess.Popen[str] | None = None
        self._command_lock = RLock()
        self._channel_condition = Condition(RLock())
        self._channel_busy = False
        self._main_waiters = 0
        self._main_deadline = None

    def _set_main_deadline(self, deadline):
        with self._channel_condition:
            self._main_deadline = deadline
            self._channel_condition.notify_all()

    @contextmanager
    def _channel(self, action):
        main = action == "activate_boundary"
        background = action in {"prewrite_generation", "reclaim_generations"}
        with self._channel_condition:
            self._main_waiters += int(main)
            try:
                while self._channel_busy or (background and (
                    self._main_waiters or (self._main_deadline is not None
                    and monotonic() >= self._main_deadline - 0.02)
                )):
                    self._channel_condition.wait(0.01)
                self._channel_busy = True
            finally:
                self._main_waiters -= int(main)
        try:
            yield
        finally:
            with self._channel_condition:
                self._channel_busy = False
                self._channel_condition.notify_all()

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Runner privileged helper is already started")
        if _execute((self._docker, "inspect", self._name)).returncode == 0:
            raise RuntimeError(f"Runner-owned helper name already exists: {self._name}")
        result = _execute(
            (
                self._docker,
                "run",
                "--detach",
                "--name",
                self._name,
                "--label",
                f"{_RUNNER_HELPER_LABEL}={self._foundation_token}",
                "--label",
                f"{_PROJECT_LABEL}=",
                "--label",
                f"{_SERVICE_LABEL}=",
                "--privileged",
                "--pid",
                "host",
                "--network",
                "host",
                "--mount",
                "type=bind,source=/,target=/host,readonly",
                "--mount",
                "type=bind,source=/sys/fs/bpf,target=/host/sys/fs/bpf",
                "--entrypoint",
                "/bin/sh",
                self._image_id,
                "-c",
                "while :; do sleep 3600; done",
            )
        )
        if result.returncode != 0:
            partial = _execute((self._docker, "inspect", self._name))
            if partial.returncode == 0:
                values = json.loads(partial.stdout)
                labels = values[0].get("Config", {}).get("Labels") or {}
                if labels.get(_RUNNER_HELPER_LABEL) == self._foundation_token:
                    _execute((self._docker, "rm", "--force", self._name))
        _require_success(result, context="Runner privileged helper startup")
        self._started = True
        try:
            self._process = subprocess.Popen(
                (self._docker, "exec", "--interactive", self._name,
                 "chroot", "/host", sys.executable, "-B", str(_PRIVILEGED_COMMAND_PATH), "serve"),
                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=1,
            )
        except BaseException:
            self.stop()
            raise

    def assert_current(self) -> None:
        if not self._started:
            raise RuntimeError("Runner privileged helper is not started")
        result = _require_success(
            _execute((self._docker, "inspect", self._name)),
            context="Runner privileged helper inspection",
        )
        values = json.loads(result.stdout)
        if len(values) != 1:
            raise RuntimeError("Runner privileged helper inspection is ambiguous")
        labels = values[0].get("Config", {}).get("Labels") or {}
        if (
            labels.get(_RUNNER_HELPER_LABEL) != self._foundation_token
            or not values[0].get("State", {}).get("Running")
        ):
            raise RuntimeError("Runner privileged helper evidence is stale")

    def execute(self, action: str, request: Mapping[str, Any] | str) -> dict[str, Any]:
        stamps = {"command_start_monotonic_ns": monotonic_ns()}
        response = None
        request_bytes = response_bytes = None

        def observation():
            stamps["command_end_monotonic_ns"] = monotonic_ns()
            values = {**stamps, "request_bytes": request_bytes, "response_bytes": response_bytes,
                      "helper": response.get("helper_timing") if isinstance(response, Mapping) else None}
            for label, first, last in (
                ("command_seconds", "command_start", "command_end"),
                ("json_encode_seconds", "encode_start", "encode_end"),
                ("channel_write_seconds", "write_start", "write_end"),
                ("channel_wait_seconds", "write_end", "read_end"),
                ("response_decode_seconds", "decode_start", "decode_end"),
            ):
                start = stamps.get(first + "_monotonic_ns")
                end = stamps.get(last + "_monotonic_ns")
                values[label] = None if start is None or end is None else (end - start) / 1e9
            return values

        try:
            with self._channel(action), self._command_lock:
                process = self._process
                if process is None or process.poll() is not None:
                    raise RuntimeError("Runner privileged execution channel is unavailable")
                assert process.stdin is not None and process.stdout is not None
                stamps["encode_start_monotonic_ns"] = monotonic_ns()
                if isinstance(request, str):
                    if action != "activate_boundary":
                        raise ValueError("only immutable activation requests are preencoded")
                    payload = request
                    stamps["encode_end_monotonic_ns"] = stamps["encode_start_monotonic_ns"]
                else:
                    payload = json.dumps(
                        {"action": action, "request": request},
                        default=lambda value: dict(value) if isinstance(value, Mapping) else value,
                        separators=(",", ":"),
                    ) + "\n"
                    stamps["encode_end_monotonic_ns"] = monotonic_ns()
                request_bytes = len(payload)  # JSON's default ASCII encoding.
                stamps["write_start_monotonic_ns"] = monotonic_ns()
                process.stdin.write(payload)
                process.stdin.flush()
                stamps["write_end_monotonic_ns"] = monotonic_ns()
                line = process.stdout.readline()
                stamps["read_end_monotonic_ns"] = monotonic_ns()
                if not line:
                    raise RuntimeError("Runner privileged execution channel closed before result")
                response_bytes = len(line)
                stamps["decode_start_monotonic_ns"] = monotonic_ns()
                response = json.loads(line)
                stamps["decode_end_monotonic_ns"] = monotonic_ns()
                if response.get("status") != "ok":
                    raise _PrivilegedRuntimeCommandError(
                        str(response.get("error")), remote_error_type=response.get("error_type"),
                        remote_evidence=response.get("evidence"),
                    )
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("Runner kernel executor omitted its mutation result")
        except BaseException as error:
            error.command_timing = observation()
            raise
        result["command_timing"] = observation()
        return result

    def start_telemetry_collector(
        self, request: Mapping[str, Any]
    ) -> subprocess.Popen[str]:
        """Start one supervised long-lived command in the owned helper."""

        self.assert_current()
        process = subprocess.Popen(
            (
                self._docker,
                "exec",
                "--interactive",
                self._name,
                "chroot",
                "/host",
                sys.executable,
                "-B",
                str(_TRAFFIC_TELEMETRY_COLLECTOR_PATH),
            ),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(json.dumps(request, sort_keys=True) + "\n")
            process.stdin.flush()
        except BaseException:
            process.terminate()
            process.wait(timeout=2)
            raise
        return process

    def stop(self) -> None:
        if not self._started:
            return
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            self._process = None
        result = _require_success(
            _execute((self._docker, "inspect", self._name)),
            context="Runner privileged helper cleanup inspection",
        )
        values = json.loads(result.stdout)
        if len(values) != 1:
            raise RuntimeError("Runner helper cleanup ownership is ambiguous")
        labels = values[0].get("Config", {}).get("Labels") or {}
        if labels.get(_RUNNER_HELPER_LABEL) != self._foundation_token:
            raise RuntimeError("Runner helper cleanup ownership is stale")
        _require_success(
            _execute((self._docker, "rm", "--force", self._name)),
            context="Runner privileged helper cleanup",
        )
        self._started = False

class BpfRunner:
    """Own initial Preparation, finite-batch Maintenance, Main and cleanup.

    Layer: Backend Runner.
    Public because generated execution needs the explicit lifecycle owner.
    It is not a Planner, Compiler, or detached Tool.
    """

    def __init__(
        self,
        *,
        compose_project_name: str,
        runtime_artifact: Path,
        batch_lead_fraction: float = 0.75,
        batch_capacity_factor: int = 2,
    ) -> None:
        if not isinstance(compose_project_name, str):
            raise TypeError("compose_project_name must be a string")
        project_name = compose_project_name.strip()
        if not project_name or "\n" in project_name or "\r" in project_name:
            raise ValueError("compose_project_name must be a non-empty single line")
        if not isinstance(runtime_artifact, Path):
            raise TypeError("runtime_artifact must be a pathlib.Path")
        if type(batch_lead_fraction) not in (int, float) or not 0 < batch_lead_fraction <= 1:
            raise ValueError("batch_lead_fraction must be in (0, 1]")
        if type(batch_capacity_factor) is not int or not 1 <= batch_capacity_factor <= 16:
            raise ValueError("batch_capacity_factor must be an integer from 1 through 16")
        self._compose_project_name = project_name
        self._artifact_path = runtime_artifact.resolve()
        self._artifact = None
        self._artifact_snapshot = {}
        self._artifact_boundary_count = 0
        self._artifact_end = None
        self._artifact_expected_node_count = 0
        self._runtime_worker_count = _runtime_worker_count()
        self._docker = "docker"
        self._state = "new"
        self._events: deque[dict[str, Any]] = deque(maxlen=_RECENT_EVENT_LIMIT)
        self._event_history: _NonBlockingEventHistory | None = None
        self._event_count = 0
        self._event_lock = RLock()
        self._bindings: dict[NodeId, _RuntimeBinding] = {}
        self._boundaries: Sequence[_NominalBoundary] = ()
        self._prepared_plans = ()
        self._active = None
        self._active_count = 0
        self._active_frontier = None
        self._foundation_token: str | None = None
        self._helper: _PrivilegedHelper | None = None
        self._runtime_realization: _TcBpfRuntimeRealization | None = None
        self._traffic_telemetry_directory: Path | None = None
        self._traffic_telemetry: _TrafficTelemetryRecorder | None = None
        self._condition = Condition(RLock())
        self._execution_mode = None
        self._main_in_flight = False
        self._nominal_origin: datetime | None = None
        self._wall_origin: float | None = None
        self._batch_lead_fraction = float(batch_lead_fraction)
        self._batch_capacity_factor = batch_capacity_factor
        self._prepared_base = 0
        self._batch_index = 0
        self._batch_final = True
        self._batch_interval = None
        self._batch_end = None
        self._pending_batches = deque()
        self._delivery_tail = None
        self._ready_batch = None
        self._retired_plan_batches = deque()
        self._pending_retirement = None
        self._maintenance_thread = None
        self._maintenance_stop = False
        self._maintenance_error = None

    @staticmethod
    def _delivery_contract(path):
        if path.is_symlink():
            raise ValueError("batch manifest must not be a symlink")
        raw = path.read_bytes()
        value = json.loads(raw)
        delivery = value.get("delivery")
        if not isinstance(delivery, dict):
            raise ValueError("batch append requires explicit sealed delivery metadata")
        return {"path": path.resolve(), "manifest_sha256": sha256(raw).hexdigest(),
                "delivery": delivery, "timeline": value["timeline"],
                "world": sha256(json.dumps([value["physical_world"], value["zone_prefixes"]], sort_keys=True).encode()).hexdigest(),
                "last_plan": value["lineage"][-1]["source_plan_id"],
                "last_state": value["lineage"][-1]["output_state_fingerprint"],
                "generation_count": value["runtime_capacity_demand"]["generation_count"]}

    def append_batch(self, runtime_artifact: Path) -> None:
        """Register one immutable next sealed batch; Maintenance loads its data.

        Only one pending delivery is admitted. A later append is possible after
        its predecessor has become current. A terminal batch closes delivery.
        """
        if not isinstance(runtime_artifact, Path):
            raise TypeError("runtime_artifact must be a pathlib.Path")
        candidate = self._delivery_contract(runtime_artifact)
        with self._condition:
            if self._state not in {"new", "prepared"} or self._pending_batches or self._ready_batch is not None:
                raise RuntimeError("Runner accepts only one next delivery at a time")
            previous = self._delivery_tail or self._delivery_contract(self._artifact_path)
            before, after = previous["delivery"], candidate["delivery"]
            step = before["observation_interval_seconds"]
            previous_generation = ((before["prior_generation"] or 0)
                                   + previous["generation_count"] - 1)
            if (before["final"] or after["index"] != before["index"] + 1
                or after["previous_manifest_sha256"] != previous["manifest_sha256"]
                or candidate["world"] != previous["world"]
                or after["observation_interval_seconds"] != step
                or after["prior_end"] != previous["timeline"]["end"]
                or after["prior_plan_id"] != previous["last_plan"]
                or after["prior_state_fingerprint"] != previous["last_state"]
                or after["prior_generation"] != previous_generation
                or datetime.fromisoformat(candidate["timeline"]["start"])
                != datetime.fromisoformat(previous["timeline"]["end"]) + timedelta(seconds=step)):
                raise ValueError("batch is non-contiguous, incompatible, replaced, or has broken continuation identity")
            self._pending_batches.append(candidate)
            self._delivery_tail = candidate
            self._condition.notify_all()
        self._record("batch_appended", batch_index=after["index"],
                     manifest_sha256=candidate["manifest_sha256"],
                     timeline=candidate["timeline"], final=after["final"],
                     binary_loading="scheduled-maintenance")

    def _enable_traffic_telemetry(self, directory: Path) -> None:
        if self._state != "new":
            raise RuntimeError("Runner Traffic telemetry must be enabled before preparation")
        path = Path(directory)
        if not path.name:
            raise ValueError("traffic telemetry directory must not be empty")
        self._traffic_telemetry_directory = path

    def _enable_event_history(self, path: Path) -> None:
        if self._state != "new" or self._events or self._event_history is not None:
            raise RuntimeError(
                "Runner durable event history must be enabled before preparation"
            )
        self._event_history = _NonBlockingEventHistory(path)

    def prepare(self) -> None:
        """Prepare the first finite delivery and activate B0 exactly once."""

        if self._state != "new":
            raise RuntimeError("BpfRunner preparation may run only once")
        started = monotonic()
        self._state = "preparing"
        self._record(
            "preparation_start",
            execution_contract="sealed-precomputed-only",
            runtime_worker_count=self._runtime_worker_count,
            artifact=self._artifact_snapshot,
        )
        try:
            artifact_started = monotonic_ns()
            self._artifact = _open_runner_artifact(self._artifact_path)
            self._artifact_snapshot = self._artifact.snapshot()
            delivery = self._artifact_snapshot.get("delivery")
            if delivery is not None:
                if delivery["index"] != 0:
                    raise ValueError("Runner session must begin with delivery zero")
                self._batch_final = delivery["final"]
                self._batch_interval = timedelta(seconds=delivery["observation_interval_seconds"])
                self._batch_end = datetime.fromisoformat(self._artifact.timeline_end)
                if self._pending_batches and self._pending_batches[0]["delivery"]["previous_manifest_sha256"] != self._artifact_snapshot["manifest_sha256"]:
                    raise ValueError("initial batch changed after its successor was registered")
                if self._delivery_tail is None:
                    self._delivery_tail = self._delivery_contract(self._artifact_path)
            self._artifact_boundary_count = self._artifact.boundary_count
            self._artifact_end = self._artifact.timeline_end
            self._artifact_expected_node_count = self._artifact.physical_world_node_count
            self._record("preparation_stage", stage="artifact-loading-validation",
                         duration_seconds=(monotonic_ns() - artifact_started) / 1e9)
            normalization_started = monotonic()
            boundaries = tuple(self._artifact.boundaries())
            if not boundaries:
                raise ValueError("BpfRunner artifact contains no nominal boundary")
            self._boundaries = boundaries
            self._nominal_origin = self._boundaries[0].nominal_time
            self._record(
                "preparation_stage",
                stage="finite-boundary-materialization",
                duration_seconds=monotonic() - normalization_started,
                boundary_count=len(self._boundaries),
                boundary_decode="complete-in-preparation",
                rich_compiled_realizations_reconstructed=False,
            )
            discovery_started = monotonic()
            bindings = self._discover_runtime_bindings(
                self._artifact.physical_world_projection
            )
            foundation_token = self._foundation_identity(bindings)
            self._record(
                "preparation_stage",
                stage="runtime-binding-discovery",
                duration_seconds=monotonic() - discovery_started,
                node_count=len(bindings),
                incarnation_capture_mode="single-privileged-batch",
                incarnation_capture_docker_run_count=1,
                incarnation_capture_pid_count=len(bindings),
                parent_host_netns_stat_used=False,
            )
            first_binding = min(bindings.values(), key=lambda value: value.node_id.value)
            helper = _PrivilegedHelper(
                docker_executable=self._docker,
                name=f"skyseed-bpf-{foundation_token[-12:]}",
                foundation_token=foundation_token,
                image_id=first_binding.image_id,
            )
            self._helper = helper
            helper_started = monotonic()
            helper.start()
            self._record(
                "preparation_stage", stage="privileged-helper-start",
                duration_seconds=monotonic() - helper_started,
            )
            runtime = _TcBpfRuntimeRealization(
                executor=helper,
                pin_root=f"/sys/fs/bpf/skyseed-v2/run-{foundation_token[-12:]}",
                bindings=bindings,
                telemetry_enabled=self._traffic_telemetry_directory is not None,
                worker_count=self._runtime_worker_count,
                continuation_enabled=delivery is not None and not delivery["final"],
                batch_capacity_factor=self._batch_capacity_factor,
            )
            self._bindings = bindings
            self._foundation_token = foundation_token
            self._helper = helper
            self._runtime_realization = runtime
            initialize_started = monotonic()
            foundation = runtime.initialize(
                self._artifact.physical_world_projection,
                zone_prefixes=self._artifact.zone_prefixes,
                runtime_capacity_demand=self._artifact.runtime_capacity_demand,
            )
            self._record(
                "capacity_audit_end",
                scope="sealed-artifact-exact-demand",
                capacity=foundation.get("capacity_audit"),
            )
            self._record(
                "preparation_stage",
                stage="runtime-initialization",
                duration_seconds=monotonic() - initialize_started,
                execution_metrics=foundation.get("execution_metrics"),
                phases=foundation.get("preparation_phases"),
            )
            self._record("runtime_foundation_ready", **foundation)
            if self._traffic_telemetry_directory is not None:
                telemetry = _TrafficTelemetryRecorder(
                    self._traffic_telemetry_directory
                )
                self._traffic_telemetry = telemetry
                try:
                    telemetry.start(
                        helper=helper,
                        foundation=runtime.telemetry_foundation(
                            foundation_token=foundation_token
                        ),
                    )
                except BaseException as error:
                    telemetry.record_failure(error)
                self._record("traffic_telemetry_started", **telemetry.snapshot())
            plan_started = monotonic_ns()
            self._prepared_plans = runtime.prepare_all(
                boundaries, logical_ports=self._artifact.logical_ports,
            )
            self._record("preparation_stage", stage="finite-plan-construction-and-prewrite",
                         duration_seconds=(monotonic_ns() - plan_started) / 1e9,
                         phases=getattr(runtime, "_preparation_timings", {}))
            self._boundaries = ()
            self._state = "prepared"
            self._activate_prepared(ideal_activation=None, paced_not_before=None, preparation=True)
            self._record(
                "preparation_end", duration_seconds=monotonic() - started,
                active_frontier=self._iso(self._active_frontier),
                prepared_boundary_count=len(self._prepared_plans),
                semantic_generations_prewritten=True,
            )
            self._record_resource_health_checkpoint("after-preparation")
        except BaseException as error:
            self._state = "failed"
            self._record_failure("preparation", error)
            raise

    def activate_next(self) -> None:
        """Manually execute the next prepared formal boundary after boundary 0."""
        with self._condition:
            if self._execution_mode == "automatic":
                raise RuntimeError("automatic and manual Runner modes are exclusive")
            if self._state != "prepared":
                raise RuntimeError("manual activation requires a prepared Runner")
            if not self._batch_final:
                raise RuntimeError("continued delivery requires the automatic fixed-origin run")
            self._execution_mode = "manual"
        self._activate_prepared(ideal_activation=None, paced_not_before=None)

    def run(self) -> None:
        self._run_bounded(None)

    def _run_bounded(self, activation_limit: int | None) -> None:
        if activation_limit is not None and (type(activation_limit) is not int or activation_limit <= 0):
            raise ValueError("activation_limit must be a positive int")
        with self._condition:
            if self._execution_mode is not None or self._state != "prepared" or self._active_count != 1:
                raise RuntimeError("automatic run requires a freshly prepared Runner")
            self._execution_mode = "automatic"
        nominal_origin = self._nominal_origin
        wall_origin = monotonic()
        self._wall_origin = wall_origin
        if self._traffic_telemetry is not None:
            self._traffic_telemetry.write_simulation_clock_anchor(
                nominal_origin=nominal_origin.isoformat(), monotonic_origin_seconds=wall_origin,
            )
        self._record("automatic_run_start", nominal_origin=nominal_origin.isoformat(),
                     wall_origin=wall_origin, activation_limit=activation_limit,
                     artifact_boundary_count=self._artifact_boundary_count)
        start_count = self._active_count
        if not self._batch_final:
            self._maintenance_thread = Thread(target=self._maintain_batches, name="skyseed-runner-maintenance")
            self._maintenance_thread.start()
        try:
            while True:
                if activation_limit is not None and self._active_count - start_count >= activation_limit:
                    break
                index = self._active_count - self._prepared_base
                seam = index == len(self._prepared_plans)
                if seam and self._batch_final:
                    break
                timestamp = (self._batch_end + self._batch_interval if seam
                             else self._prepared_plans[index].nominal_time)
                ideal = wall_origin + (timestamp - nominal_origin).total_seconds()
                self._helper._set_main_deadline(ideal)
                remaining = ideal - monotonic()
                if remaining > 0:
                    sleep(remaining)
                if self._maintenance_error is not None:
                    raise RuntimeError("Runner Maintenance failed; session is incomplete") from self._maintenance_error
                if seam:
                    with self._condition:
                        ready = self._ready_batch
                        if ready is None or ready[2] > ideal:
                            self._state = "failed"
                            self._record("batch_exhausted", batch_index=self._batch_index + 1,
                                         nominal_timestamp=timestamp.isoformat(),
                                         deadline_monotonic_seconds=ideal, clock_shifted=False)
                            raise RuntimeError("next sealed batch was not ready by its activation deadline")
                        plans, contract, ready_at = ready
                        self._pending_retirement = self._prepared_plans, self._artifact
                        self._artifact = None
                        self._prepared_plans = plans
                        self._prepared_base = self._active_count
                        self._batch_index = contract["delivery"]["index"]
                        self._batch_final = contract["delivery"]["final"]
                        self._batch_end = datetime.fromisoformat(contract["timeline"]["end"])
                        self._ready_batch = None
                        self._condition.notify_all()
                    self._record("batch_activation_start", batch_index=self._batch_index,
                                 boundary_timestamp=timestamp.isoformat(),
                                 nominal_timestamp=timestamp.isoformat(), ready_monotonic_seconds=ready_at,
                                 deadline_monotonic_seconds=ideal)
                self._activate_prepared(ideal_activation=ideal, paced_not_before=ideal)
                if self._pending_retirement is not None:
                    with self._condition:
                        self._retired_plan_batches.append(self._pending_retirement)
                        self._pending_retirement = None
                self._helper._set_main_deadline(None)
                with self._condition:
                    self._condition.notify_all()
            self._record("automatic_run_end", active_frontier=self._iso(self._active_frontier),
                         activation_limit=activation_limit,
                         stopped_by_activation_limit=activation_limit is not None and self._active_count - start_count >= activation_limit)
        except BaseException as error:
            self._state = "failed"
            self._record_failure("automatic-run", error, active_frontier=self._iso(self._active_frontier))
            raise
        finally:
            self._stop_maintenance()

    def _stop_maintenance(self):
        with self._condition:
            self._maintenance_stop = True
            self._condition.notify_all()
        if self._helper is not None:
            self._helper._set_main_deadline(None)
        if self._maintenance_thread is not None:
            self._maintenance_thread.join()
            self._maintenance_thread = None

    def _maintenance_checkpoint(self, deadline=None):
        if self._maintenance_stop:
            raise RuntimeError("Runner Maintenance stopped")
        if deadline is not None and monotonic() >= deadline:
            raise RuntimeError("batch loading exceeded its fixed activation deadline")
        sleep(0)

    def _maintain_batches(self):
        try:
            while not self._maintenance_stop:
                retired = None
                with self._condition:
                    if self._retired_plan_batches:
                        retired = self._retired_plan_batches.popleft()
                    candidate = self._pending_batches[0] if self._pending_batches and self._ready_batch is None else None
                if retired is not None:
                    retired_started = monotonic()
                    old_plans, old_artifact = retired
                    count = len(old_plans)
                    if old_artifact is not None:
                        old_artifact.close()
                    del old_plans, old_artifact, retired
                    self._record("batch_plan_reclaimed", before_batch_index=self._batch_index,
                                 released_boundaries=count, historical_evidence_preserved=True,
                                 duration_seconds=monotonic() - retired_started)
                if candidate is not None:
                    start = datetime.fromisoformat(candidate["timeline"]["start"])
                    end = datetime.fromisoformat(candidate["timeline"]["end"])
                    duration = (end - start).total_seconds() + (0 if candidate["delivery"]["final"] else self._batch_interval.total_seconds())
                    duration = max(duration, self._batch_interval.total_seconds())
                    deadline = self._wall_origin + (start - self._nominal_origin).total_seconds()
                    scheduled = deadline - self._batch_lead_fraction * duration
                    if monotonic() >= scheduled:
                        self._load_batch(candidate, scheduled, deadline)
                        continue
                reclaim_started = monotonic()
                result = self._runtime_realization.reclaim()
                if result.get("work_units") or result.get("reader_wait"):
                    self._record("generation_reclaim", **result, duration_seconds=monotonic() - reclaim_started)
                if result.get("work_units"):
                    self._maintenance_checkpoint()
                    continue
                with self._condition:
                    if not self._maintenance_stop:
                        self._condition.wait(0.05)
        except BaseException as error:
            if not self._maintenance_stop:
                self._maintenance_error = error
                self._record_failure("maintenance", error)
                with self._condition:
                    self._condition.notify_all()

    def _load_batch(self, contract, scheduled, deadline):
        started = monotonic()
        index = contract["delivery"]["index"]
        self._record("batch_load_start", batch_index=index,
                     boundary_timestamp=contract["timeline"]["start"],
                     scheduled_monotonic_seconds=scheduled, deadline_monotonic_seconds=deadline,
                     lead_fraction=self._batch_lead_fraction)
        if contract["timeline"]["boundary_count"] > self._runtime_realization._batch_capacity_basis["boundary_count"] * self._batch_capacity_factor:
            raise RuntimeError("batch exceeds resident boundary admission before decoding")
        if sha256(contract["path"].read_bytes()).hexdigest() != contract["manifest_sha256"]:
            raise ValueError("registered batch manifest was replaced")
        artifact = _open_runner_artifact(contract["path"])
        try:
            if artifact._snapshot["manifest_sha256"] != contract["manifest_sha256"]:
                raise ValueError("registered batch manifest was replaced")
            loaded = monotonic()
            self._runtime_realization._admit_batch(artifact.runtime_capacity_demand)
            boundaries = []
            for boundary in artifact.boundaries():
                self._maintenance_checkpoint(deadline)
                boundaries.append(boundary)
            decoded = monotonic()
            plans = self._runtime_realization.prepare_all(
                boundaries, logical_ports=artifact.logical_ports,
                work_checkpoint=lambda: self._maintenance_checkpoint(deadline))
            constructed = monotonic()
            self._maintenance_checkpoint(deadline)
            with self._condition:
                self._pending_batches.popleft()
                self._artifact_boundary_count += len(plans)
                self._artifact_end = artifact.timeline_end
                self._ready_batch = plans, contract, constructed
                self._condition.notify_all()
            self._record("batch_ready", batch_index=index, boundary_count=len(plans),
                         boundary_timestamp=contract["timeline"]["start"],
                         ready_monotonic_seconds=constructed, deadline_monotonic_seconds=deadline,
                         readiness_margin_seconds=deadline - constructed,
                         load_seconds=loaded - started, decode_seconds=decoded - loaded,
                         construction_prewrite_seconds=constructed - decoded,
                         total_seconds=constructed - started,
                         phases=self._runtime_realization._preparation_timings,
                         resident_capacity=self._runtime_realization.working_set_counts(),
                         manifest_sha256=contract["manifest_sha256"])
        finally:
            artifact.close()

    def _activate_prepared(self, *, ideal_activation, paced_not_before, preparation=False) -> None:
        with self._condition:
            if self._state != "prepared" or self._main_in_flight:
                raise RuntimeError("BpfRunner is not available for activation")
            if self._active_count - self._prepared_base >= len(self._prepared_plans):
                raise RuntimeError("BpfRunner finite experiment has no next boundary")
            plan = self._prepared_plans[self._active_count - self._prepared_base]
            self._main_in_flight = True
        started = monotonic()
        timestamp = plan.nominal_time.isoformat()
        self._record("boundary_scheduled", boundary_timestamp=timestamp,
                     ideal_activation_monotonic_seconds=ideal_activation,
                     paced_not_before_monotonic_seconds=paced_not_before,
                     preparation=preparation, pacing_mode="fixed-origin")
        try:
            result = dict(self._runtime_realization.activate(plan))
            completed = monotonic()
            with self._condition:
                self._active = plan
                self._active_frontier = plan.nominal_time
                self._active_count += 1
                raw_anchor = result.pop("activation_anchor_monotonic_ns", None)
                anchor_valid = type(raw_anchor) is int and 0 < raw_anchor <= sys.maxsize
            commit_ended = monotonic_ns()
            identities = result.pop("interface_identities", ())
            withdrawn = result.pop("withdrawn_logical_ports", ())
            enqueue_started = monotonic_ns()
            if self._traffic_telemetry is not None:
                try:
                    self._traffic_telemetry.enqueue_generation_identity(
                        plan.telemetry_identities, identities, withdrawn,
                        {
                            "boundary_timestamp": timestamp,
                            "active_generation": plan.generation,
                            "previous_generation": plan.expected_active_generation,
                            "generation_publication_occurred": plan.generation_changed,
                            "preparation": preparation,
                            "main_start_monotonic_ns": round(started * 1e9),
                            "main_end_monotonic_ns": round(completed * 1e9),
                            **{key: result[key] for key in (
                                "generation_switch_start_monotonic_ns",
                                "generation_switch_end_monotonic_ns",
                            ) if key in result},
                        },
                    )
                except Exception as error:
                    self._traffic_telemetry.record_failure(error)
            enqueue_ended = monotonic_ns()
            result.pop("boundary_timestamp", None)
            record_started = monotonic_ns()
            self._record("activation_end", **result,
                         boundary_timestamp=timestamp, target_boundary=timestamp,
                         active_frontier=timestamp, preparation=preparation,
                         main_start_monotonic_seconds=started,
                         main_end_monotonic_seconds=completed,
                         main_seconds=completed - started, runtime_apply_seconds=completed - started,
                         helper_pacing_anchor_monotonic_ns=raw_anchor,
                         activation_anchor_timing_complete=anchor_valid,
                         ideal_start_lag_seconds=None if ideal_activation is None else started - ideal_activation,
                         ideal_completion_lag_seconds=None if ideal_activation is None else completed - ideal_activation,
                         semantic_outcome="activated")
            record_ended = monotonic_ns()
            self._record("activation_observation_end", boundary_timestamp=timestamp,
                         preparation=preparation,
                         observation_end_monotonic_ns=record_ended,
                         formal_commit_seconds=(commit_ended - round(completed * 1e9)) / 1e9,
                         identity_enqueue_seconds=(enqueue_ended - enqueue_started) / 1e9,
                         activation_event_record_seconds=(record_ended - record_started) / 1e9,
                         post_main_observation_seconds=(record_ended - round(completed * 1e9)) / 1e9)
        except BaseException as error:
            self._state = "failed"
            evidence = getattr(error, "evidence", {})
            self._record_failure("activation", error, target_boundary=timestamp,
                                 active_frontier=self._iso(self._active_frontier),
                                 target_transition_complete=False, runtime_failure_evidence=evidence,
                                 preparation=preparation)
            raise
        finally:
            with self._condition:
                self._main_in_flight = False
                self._condition.notify_all()


    def cleanup(self) -> None:
        """Finally tear down owned network, BPF, telemetry, helper and artifact resources."""

        if self._state == "cleaned":
            return
        started = monotonic()
        phases = {}
        self._stop_maintenance()
        with self._condition:
            if self._main_in_flight:
                raise RuntimeError("Runner cleanup requires the current Main operation to finish")
        errors: list[BaseException] = []
        runtime_clean = self._runtime_realization is None
        if self._runtime_realization is not None:
            runtime_started = monotonic_ns()
            try:
                result = self._runtime_realization.cleanup()
                phases["runtime_teardown_seconds"] = (monotonic_ns() - runtime_started) / 1e9
                runtime_clean = True
                self._record("runtime_cleanup_end", **result)
            except BaseException as error:
                errors.append(error)
                self._record_failure(
                    "runtime-teardown", error, ownership_preserved=True,
                    duration_seconds=(monotonic_ns() - runtime_started) / 1e9,
                )
        if runtime_clean and self._traffic_telemetry is not None:
            telemetry_started = monotonic_ns()
            telemetry_result = self._traffic_telemetry.stop_and_drain()
            phases["telemetry_drain_seconds"] = (monotonic_ns() - telemetry_started) / 1e9
            self._record(
                "traffic_telemetry_stopped",
                **telemetry_result,
            )
        if runtime_clean and self._helper is not None:
            helper_started = monotonic_ns()
            try:
                self._helper.stop()
                self._helper = None
            except BaseException as error:
                errors.append(error)
            finally:
                phases["helper_shutdown_seconds"] = (monotonic_ns() - helper_started) / 1e9
        if errors:
            self._state = "teardown-failed"
            raise RuntimeError(
                "BpfRunner teardown failed: "
                + "; ".join(str(error) for error in errors)
            )
        if self._artifact is not None:
            artifact_started = monotonic_ns()
            self._artifact.close()
            phases["artifact_handle_close_seconds"] = (monotonic_ns() - artifact_started) / 1e9
        if self._pending_retirement is not None:
            self._retired_plan_batches.append(self._pending_retirement)
            self._pending_retirement = None
        while self._retired_plan_batches:
            _, artifact = self._retired_plan_batches.popleft()
            if artifact is not None:
                artifact.close()
        self._state = "cleaned"
        self._record(
            "cleanup_end",
            duration_seconds=monotonic() - started,
            phases=phases,
        )
        if self._event_history is not None:
            history_started = monotonic_ns()
            self._event_history.close()
            self._cleanup_finalization = {
                "event_history_drain_seconds": (monotonic_ns() - history_started) / 1e9,
                "scope": "after-cleanup-end",
            }

    def inspection_snapshot(self) -> dict[str, Any]:
        # Capture the formal frontier under a constant-size lock. Detaching bulk
        # inspection/telemetry facts must never hold Main's publication lock.
        with self._condition:
            lifecycle_state = self._state
            execution_mode = self._execution_mode
            active_count = self._active_count
            active_frontier = self._active_frontier
            delivery = {"current_batch_index": self._batch_index,
                        "current_batch_final": self._batch_final,
                        "next_batch_ready": self._ready_batch is not None,
                        "pending_batch_count": len(self._pending_batches),
                        "lead_fraction": self._batch_lead_fraction,
                        "capacity_factor": self._batch_capacity_factor,
                        "maintenance_failed": self._maintenance_error is not None}
        with self._event_lock:
            events = tuple(self._events)
            event_count = self._event_count
        runtime_addresses = (
            {} if self._runtime_realization is None
            else self._runtime_realization.runtime_node_addresses()
        )
        runtime_working_set = (
            {} if self._runtime_realization is None
            else self._runtime_realization.working_set_counts()
        )
        event_history = (
            None if self._event_history is None
            else self._event_history.snapshot()
        )
        return deepcopy({
            "format_version": 2,
            "runner": {
                "lifecycle_state": lifecycle_state,
                "execution_mode": execution_mode,
                "delivery": delivery,
                "execution_contract": "sealed-precomputed-only",
                "cleanup_finalization": getattr(self, "_cleanup_finalization", None),
                "frontiers": {
                    "last_active": self._iso(active_frontier),
                    "artifact_end": self._artifact_end,
                },
                "runtime_worker_count": self._runtime_worker_count,
                "activation_count": active_count,
                "prepared_boundary_count": len(self._prepared_plans),
                "artifact": self._artifact_snapshot,
                "runtime_foundation": {
                    "compose_project_name": self._compose_project_name,
                    "foundation_token": self._foundation_token,
                    "expected_node_count": (
                        self._artifact_expected_node_count
                    ),
                    "bound_node_count": len(self._bindings),
                    "bindings": tuple({
                        "node_id": node_id.value,
                        "service": binding.service_name,
                        "container_id": binding.container_id,
                        "container_name": binding.container_name,
                        "image_id": binding.image_id,
                        "pid": binding.pid,
                        "process_start_time_ticks": binding.process_start_time_ticks,
                        "netns_inode": binding.netns_inode,
                        "started_at": binding.started_at,
                        "runtime_ipv4": runtime_addresses.get(node_id),
                    } for node_id, binding in sorted(
                        self._bindings.items(), key=lambda item: item[0].value
                    )),
                },
                "working_set": {
                    "artifact_boundary_count": self._artifact_boundary_count,
                    "retained_rich_realization_count": 0,
                    "recent_event_count": len(events),
                    "recorded_event_count": event_count,
                    "durable_event_count": (
                        0 if event_history is None
                        else event_history["written_count"]
                    ),
                    **runtime_working_set,
                },
                "traffic_telemetry": (
                    {"enabled": False}
                    if self._traffic_telemetry is None
                    else self._traffic_telemetry.snapshot()
                ),
            },
            "event_history": {
                "scope": (
                    "complete" if len(events) == event_count
                    else "recent-only"
                ),
                "returned_count": len(events),
                "total_count": event_count,
                "durable_history_enabled": self._event_history is not None,
                "durable": event_history,
            },
            "events": events,
        })

    def _record(self, event: str, **facts: Any) -> None:
        capture_started = monotonic()
        compact = _compact_event_value(facts)
        assert isinstance(compact, dict)
        record = {
            "event": event,
            "monotonic_seconds": monotonic(),
            "wall_time_seconds": time(),
            **compact,
        }
        record["observation_capture_seconds"] = monotonic() - capture_started
        with self._event_lock:
            self._events.append(record)
            self._event_count += 1
            history = self._event_history
        if history is not None:
            history.enqueue({"record_type": "runner_event", **record})

    def _record_failure(
        self, phase: str, error: BaseException, **facts: Any
    ) -> None:
        facts.setdefault("command_timing", getattr(error, "command_timing", None)
                         or getattr(error.__cause__, "command_timing", None))
        facts.setdefault("runtime_failure_evidence", getattr(error, "remote_evidence", None)
                         or getattr(error, "evidence", None))
        self._record(
            "failure", phase=phase, error_type=type(error).__name__,
            error=str(error), **facts,
        )

    def _record_resource_health_checkpoint(self, checkpoint: str) -> None:
        rss_bytes = None
        thread_count = None
        try:
            for line in Path("/proc/self/status").read_text(
                encoding="utf-8"
            ).splitlines():
                if line.startswith("VmRSS:"):
                    rss_bytes = int(line.split()[1]) * 1024
                    break
            thread_count = sum(1 for _ in Path("/proc/self/task").iterdir())
        except (OSError, ValueError, IndexError):
            pass
        runtime = self._runtime_realization
        self._record(
            "resource_health_checkpoint",
            checkpoint=checkpoint,
            runner_rss_bytes=rss_bytes,
            process_count=1,
            thread_count=thread_count,
            helper_owned=self._helper is not None,
            runtime_owned=runtime is not None,
            runtime_working_set=(
                {} if runtime is None else runtime.working_set_counts()
            ),
        )

    @staticmethod
    def _iso(value: object) -> str | None:
        return None if value is None else value.isoformat()

    def _discover_runtime_bindings(
        self, projection: _PhysicalWorldProjection
    ) -> dict[NodeId, _RuntimeBinding]:
        result = _require_success(
            _execute((
                self._docker, "ps", "--all", "--quiet", "--filter",
                f"label={_PROJECT_LABEL}={self._compose_project_name}",
            )),
            context="Runner deployment discovery",
        )
        container_ids = tuple(
            value.strip() for value in result.stdout.splitlines() if value.strip()
        )
        if not container_ids:
            raise RuntimeError(
                "current Compose project has no containers; start the generated deployment first"
            )
        inspected = self._inspect(container_ids)
        candidates = []
        seen_node_ids: set[NodeId] = set()
        for value in inspected:
            labels = value.get("Config", {}).get("Labels") or {}
            if labels.get(_PROJECT_LABEL) != self._compose_project_name:
                raise RuntimeError("discovered container has the wrong project")
            entity_type = labels.get(_ENTITY_TYPE_LABEL)
            if entity_type not in {"Satellite", "GroundStation"}:
                continue
            service_name = labels.get(_SERVICE_LABEL)
            if not isinstance(service_name, str) or not service_name:
                raise RuntimeError("Skyseed entity container has no Compose service")
            state = value.get("State") or {}
            pid = state.get("Pid")
            if not state.get("Running") or not isinstance(pid, int) or pid <= 0:
                raise RuntimeError("Skyseed entity container is not running")
            try:
                asn = int(labels[_ASN_LABEL])
                node_id = (
                    NodeId(asn, "satellite", labels[_SATELLITE_ID_LABEL])
                    if entity_type == "Satellite"
                    else NodeId(
                        asn, "ground-station",
                        labels[_GROUND_STATION_ID_LABEL],
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "Skyseed entity container labels are incomplete"
                ) from error
            if node_id in seen_node_ids:
                raise RuntimeError(f"duplicate live binding for {node_id.value}")
            seen_node_ids.add(node_id)
            candidates.append((
                node_id, service_name, value["Id"],
                value["Name"].removeprefix("/"), value["Image"], pid,
                state.get("StartedAt", ""),
            ))
        expected = set(projection.node_ids)
        if seen_node_ids != expected:
            missing = sorted(value.value for value in expected - seen_node_ids)
            unexpected = sorted(value.value for value in seen_node_ids - expected)
            raise RuntimeError(
                "live NodeId bindings do not match compiled PhysicalWorld: "
                f"missing={missing}, unexpected={unexpected}"
            )
        first_image = min(candidates, key=lambda value: value[0].value)[4]
        incarnations = self._capture_privileged_incarnations(
            image_id=first_image,
            pids=tuple(candidate[5] for candidate in candidates),
        )
        bindings = {
            node_id: _RuntimeBinding(
                node_id=node_id, service_name=service_name,
                container_id=container_id, container_name=container_name,
                image_id=image_id, pid=pid,
                process_start_time_ticks=start_ticks,
                netns_inode=netns_inode, started_at=started_at,
            )
            for (
                node_id, service_name, container_id, container_name,
                image_id, pid, started_at,
            ), (start_ticks, netns_inode) in zip(candidates, incarnations)
        }
        if set(bindings) != expected:
            missing = sorted(value.value for value in expected - set(bindings))
            unexpected = sorted(value.value for value in set(bindings) - expected)
            raise RuntimeError(
                "live NodeId bindings do not match compiled PhysicalWorld: "
                f"missing={missing}, unexpected={unexpected}"
            )
        return bindings

    def _capture_privileged_incarnations(
        self, *, image_id: str, pids: tuple[int, ...]
    ) -> list[tuple[int, int]]:
        """Use one ephemeral privileged process to capture the complete PID set."""

        if not pids:
            raise RuntimeError("Runner discovered no process incarnations to capture")
        result = _execute(
            (
                self._docker,
                "run",
                "--rm",
                "--interactive",
                "--label",
                f"{_PROJECT_LABEL}=",
                "--label",
                f"{_SERVICE_LABEL}=",
                "--privileged",
                "--pid",
                "host",
                "--network",
                "none",
                "--mount",
                "type=bind,source=/,target=/host,readonly",
                "--entrypoint",
                "chroot",
                image_id,
                "/host",
                sys.executable,
                "-B",
                str(_PRIVILEGED_COMMAND_PATH),
                "capture_incarnations",
            ),
            input_text=json.dumps({"pids": list(pids)}, sort_keys=True),
        )
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "Runner privileged incarnation capture returned invalid evidence; "
                f"stdout={result.stdout!r}; stderr={result.stderr!r}"
            ) from error
        if result.returncode != 0 or response.get("status") != "ok":
            raise RuntimeError(
                "Runner privileged incarnation capture failed: "
                f"{response.get('error', response)!r}; "
                f"traceback={response.get('traceback', '')!r}"
            )
        payload = response.get("result")
        values = payload.get("incarnations") if isinstance(payload, Mapping) else None
        if (
            not isinstance(payload, Mapping)
            or payload.get("capture_mode") != "single-privileged-batch"
            or payload.get("requested_pid_count") != len(pids)
            or payload.get("captured_pid_count") != len(pids)
            or not isinstance(values, list)
            or len(values) != len(pids)
        ):
            raise RuntimeError("Runner privileged incarnation capture is incomplete")
        by_pid: dict[int, tuple[int, int]] = {}
        for value in values:
            if not isinstance(value, Mapping):
                raise RuntimeError("Runner privileged incarnation evidence is malformed")
            pid = value.get("pid")
            start = value.get("process_start_time_ticks")
            netns = value.get("netns_inode")
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or isinstance(start, bool)
                or not isinstance(start, int)
                or start <= 0
                or isinstance(netns, bool)
                or not isinstance(netns, int)
                or netns <= 0
                or pid in by_pid
            ):
                raise RuntimeError("Runner privileged incarnation evidence is invalid")
            by_pid[pid] = (start, netns)
        if set(by_pid) != set(pids) or len(by_pid) != len(pids):
            raise RuntimeError("Runner privileged incarnation PID set is incomplete")
        if len({value[1] for value in by_pid.values()}) != len(by_pid):
            raise RuntimeError(
                "Runner privileged incarnation netns evidence is duplicated"
            )
        return [by_pid[pid] for pid in pids]

    def _foundation_identity(
        self, bindings: Mapping[NodeId, _RuntimeBinding]
    ) -> str:
        facts = [
            self._compose_project_name,
            *(f"{node_id.value}|{binding.container_id}|{binding.image_id}|"
              f"{binding.pid}|{binding.process_start_time_ticks}|"
              f"{binding.netns_inode}|{binding.started_at}"
              for node_id, binding in sorted(
                  bindings.items(), key=lambda item: item[0].value
              )),
        ]
        return "foundation-" + sha256("\n".join(facts).encode()).hexdigest()

    def _inspect(self, container_ids: Sequence[str]) -> list[dict[str, Any]]:
        result = _require_success(
            _execute((self._docker, "inspect", *container_ids)),
            context="Runner container inspection",
        )
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != len(container_ids):
            raise RuntimeError("Runner container inspection is incomplete")
        return values
