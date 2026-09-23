"""Own Initialization, Execution, Maintenance and final Runner cleanup."""

from __future__ import annotations
import json

from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from threading import Condition, RLock, Thread
from time import monotonic, monotonic_ns, sleep, time
from typing import Any, Mapping, Sequence
from .deployment import _Deployment, _RunnerRuntimeBinding
from skyseed.shared import NodeId
from skyseed.shared._console import _console
from skyseed.backend.runner.event_history import _NonBlockingEventHistory
from skyseed.backend.selection import _backend
from skyseed.backend.execution_materials.boundaries import _ExecutionBoundary
from skyseed.backend.execution_materials.codec import (
    _open_runner_artifact,
    _read_delivery_contract,
)
from skyseed.backend.runner.command_channel import _PrivilegedHelper


_RECENT_EVENT_LIMIT = 512


_RUNTIME_WORKER_ENVIRONMENT = "SKYSEED_BPF_RUNTIME_WORKERS"


_EVENT_SEQUENCE_LIMIT = 64


def _runtime_worker_count(raw=None) -> int:
    raw = os.environ.get(_RUNTIME_WORKER_ENVIRONMENT) if raw is None else raw
    if raw is None:
        return min(8, max(1, os.cpu_count() or 1))
    try:
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise ValueError("non-integer worker count")
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{_RUNTIME_WORKER_ENVIRONMENT} must be an integer"
        ) from error
    if not 1 <= value <= 64:
        raise ValueError(
            f"{_RUNTIME_WORKER_ENVIRONMENT} must be between 1 and 64"
        )
    return value


def _validate_batch_configuration(batch_lead_fraction, batch_capacity_factor) -> None:
    """Check finite delivery settings without constructing a Runner or its resources."""
    if type(batch_lead_fraction) not in (int, float) or not 0 < batch_lead_fraction <= 1:
        raise ValueError("batch_lead_fraction must be in (0, 1]")
    if type(batch_capacity_factor) is not int or not 1 <= batch_capacity_factor <= 16:
        raise ValueError("batch_capacity_factor must be an integer from 1 through 16")


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


class Runner:
    """Own Initialization, Execution, Maintenance and final cleanup.

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
        keep_alive: bool = False,
        forwarding_backend=None,
    ) -> None:
        if not isinstance(compose_project_name, str):
            raise TypeError("compose_project_name must be a string")
        project_name = compose_project_name.strip()
        if not project_name or "\n" in project_name or "\r" in project_name:
            raise ValueError("compose_project_name must be a non-empty single line")
        if not isinstance(runtime_artifact, Path):
            raise TypeError("runtime_artifact must be a pathlib.Path")
        _validate_batch_configuration(batch_lead_fraction, batch_capacity_factor)
        if type(keep_alive) is not bool:
            raise TypeError("keep_alive must be a bool")
        self._forwarding_backend = None if forwarding_backend is None else _backend(forwarding_backend)
        self._compose_project_name = project_name
        self._artifact_path = runtime_artifact.resolve()
        self._artifact = None
        self._artifact_snapshot = {}
        self._connection_observer = None
        self._execution_input = None
        self._artifact_boundary_count = 0
        self._artifact_end = None
        self._artifact_expected_node_count = 0
        self._runtime_worker_count = _runtime_worker_count()
        self._docker = "docker"
        self._deployment = _Deployment(self._docker, project_name)
        self._state = "new"
        self._events: deque[dict[str, Any]] = deque(maxlen=_RECENT_EVENT_LIMIT)
        self._event_history: _NonBlockingEventHistory | None = None
        self._event_count = 0
        self._event_lock = RLock()
        self._bindings: dict[NodeId, _RunnerRuntimeBinding] = {}
        self._boundaries: Sequence[_ExecutionBoundary] = ()
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
        self._initial_preparation_started = False
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
        self._keep_alive = keep_alive
        self._cancel_requested = False
        self._pause_requested = False
        self._pause_at = None
        self._pause_scheduled_at = None
        self._paused = False
        self._waiting = False
        self._hold_started = None
        self._clock_revision = 0
        self._clock_shift = 0.0
        self._preparing_batch = None
        self._activated_zone_addresses = {}
        self._zone_service_addresses = {}
        self._communication_bindings = {}

    def pause(self, *, at=None) -> dict[str, Any]:
        """Request pause; Execution confirms it after the current complete activation."""
        with self._condition:
            if at is not None and (not isinstance(at, datetime) or at.tzinfo is None):
                raise ValueError('pause time must be a timezone-aware datetime')
            if at is not None and self._nominal_origin is not None and self._batch_interval is not None:
                count, remainder = divmod(at - self._nominal_origin, self._batch_interval)
                at = self._nominal_origin + (count + bool(remainder)) * self._batch_interval
            self._pause_at = at
            self._pause_scheduled_at = max(at, self._active_frontier) if at is not None and self._active_frontier is not None else (at or self._active_frontier)
            self._pause_requested = at is None or (self._active_frontier is not None and self._active_frontier >= at)
            self._condition.notify_all()
        return self.status()

    def resume(self) -> dict[str, Any]:
        """Release explicit pause; absent materials can still hold progression."""
        with self._condition:
            self._pause_requested = False
            self._pause_at = None
            self._condition.notify_all()
        return self.status()

    def cancel_runtime(self) -> None:
        """End Execution normally after any in-flight activation; the caller joins and cleans up."""
        with self._condition:
            if not self._cancel_requested:
                _console("Runtime stop requested: draining any in-flight activation and maintenance before cleanup.")
            self._cancel_requested = True
            self._condition.notify_all()

    def status(self) -> dict[str, Any]:
        """Return constant-size execution and clock facts, without decoding evidence."""
        with self._condition:
            seam = self._active_count - self._prepared_base == len(self._prepared_plans)
            return {
                "lifecycle_state": self._state,
                "forwarding_backend": self._forwarding_backend,
                "active_frontier": self._iso(self._active_frontier),
                "activation_count": self._active_count,
                "batch_index": self._batch_index,
                "available_end": self._artifact_end,
                "accepts_batch": (self._state in {"new", "preparing", "prepared"}
                    and not self._cancel_requested and not self._pending_batches and self._ready_batch is None),
                "frozen_through": max(self._batch_index if self._initial_preparation_started else -1,
                    -1 if self._preparing_batch is None else self._preparing_batch,
                    -1 if self._ready_batch is None else self._ready_batch[1]["delivery"]["index"]),
                "batch_lead_fraction": self._batch_lead_fraction,
                "next_batch": ("READY" if self._ready_batch is not None else
                    "PREPARING" if self._preparing_batch is not None else
                    "PENDING" if self._pending_batches else "ABSENT"),
                "pause_requested": self._pause_requested, "pause_at": self._iso(self._pause_at), "pause_scheduled_at": self._iso(self._pause_scheduled_at), "paused": self._paused,
                "paused_timestamp": self._iso(self._active_frontier) if self._paused else None,
                "waiting_for_material": self._state == "prepared" and seam and self._ready_batch is None and (self._keep_alive or not self._batch_final),
                "wait_reasons": (["user-pause"] if self._paused else []) + (["material"] if self._waiting else []),
                "cancel_requested": self._cancel_requested,
                "maintenance_error": None if self._maintenance_error is None else str(self._maintenance_error),
                "clock": {"nominal_origin": self._iso(self._nominal_origin),
                    "wall_origin": self._wall_origin, "revision": self._clock_revision,
                    "shift_seconds": self._clock_shift, "held_since": self._hold_started},
            }

    def append_batch(self, runtime_artifact: Path) -> bool:
        """Register one immutable next sealed batch; Maintenance loads its data.

        Only one pending delivery is admitted. A later append is possible after
        its predecessor has become current. A terminal batch closes delivery.
        """
        if not isinstance(runtime_artifact, Path):
            raise TypeError("runtime_artifact must be a pathlib.Path")
        candidate = _read_delivery_contract(runtime_artifact)
        while True:
            with self._condition:
                initial_path = self._artifact_path if self._delivery_tail is None else None
            initial = None if initial_path is None else _read_delivery_contract(initial_path)
            result = self._append_prevalidated(candidate, initial=initial)
            if result is not None:
                return result

    def _append_prevalidated(self, candidate, *, initial=None):
        # Catalog callers use this only after initial preparation. Standalone
        # append may race replacement of delivery zero; retry its header read.
        with self._condition:
            if self._cancel_requested:
                return False
            if self._state not in {"new", "preparing", "prepared"} or self._pending_batches or self._ready_batch is not None:
                raise RuntimeError("Runner accepts only one next delivery at a time")
            previous = self._delivery_tail
            if previous is None:
                if initial is None or initial['path'] != self._artifact_path:
                    return None
                previous = initial
            if candidate["backend"] != previous["backend"] or (
                    self._forwarding_backend is not None and candidate["backend"] != self._forwarding_backend):
                raise ValueError("Runner continuation uses incompatible forwarding backend materials")
            before, after = previous["delivery"], candidate["delivery"]
            step = before["observation_interval_seconds"]
            previous_generation = (
                (-1 if before["prior_generation"] is None else before["prior_generation"]) + previous["generation_count"]
                if previous["backend"] == "linux" else
                (before["prior_generation"] or 0) + previous["generation_count"] - 1)
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
        return True

    def select_future_materials(self, contracts, select):
        """Select a prevalidated complete suffix against preparation, atomically.

        ``select`` swaps pre-staged selection metadata and the caller's catalog.
        No artifact loading, serialization, computation, network operation or
        kernel work belongs in that short callback.
        Standalone callers may use this local primitive without an experiment.
        """
        first = contracts[0]['delivery']['index']
        with self._condition:
            frozen = max(self._batch_index if self._initial_preparation_started else -1,
                         -1 if self._preparing_batch is None else self._preparing_batch,
                         -1 if self._ready_batch is None else self._ready_batch[1]['delivery']['index'])
            if first <= frozen:
                return {'adopted': False, 'reason': 'preparation won selection', 'next_index': frozen + 1}
            if self._cancel_requested or self._state not in {'new', 'preparing', 'prepared'}:
                return {'adopted': False, 'reason': 'runtime no longer accepts replacement', 'next_index': None}
            select()
            if first == 0:
                self._artifact_path = contracts[0]['path']
            if self._pending_batches:
                pending_index = self._pending_batches[0]['delivery']['index']
                offset = pending_index - first
                if 0 <= offset < len(contracts):
                    self._pending_batches[0] = contracts[offset]
                    self._delivery_tail = contracts[offset]
            self._condition.notify_all()
            return {'adopted': True, 'actual_position': contracts[0]['timeline']['start']}

    def set_preparation_lead(self, fraction):
        _validate_batch_configuration(fraction, self._batch_capacity_factor)
        with self._condition:
            self._batch_lead_fraction = float(fraction)
            self._condition.notify_all()
        return {'batch_lead_fraction': float(fraction), 'effective': 'next-preparation-decision'}

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

    def _bind_execution_input(self, *, physical_world_fingerprint, start_time, end_time):
        """Bind generated scene/coverage facts; the real material reader checks them once."""
        if self._state != "new":
            raise RuntimeError("execution input must be bound before preparation")
        self._execution_input = (physical_world_fingerprint, start_time, end_time)

    def prepare(self) -> None:
        """Prepare the first finite delivery and activate B0 exactly once."""

        with self._condition:
            if self._state != "new":
                raise RuntimeError("Runner preparation may run only once")
            self._initial_preparation_started = True
            self._state = "preparing"
            artifact_path = self._artifact_path
        started = monotonic()
        self._record(
            "preparation_start",
            execution_contract="sealed-precomputed-only",
            runtime_worker_count=self._runtime_worker_count,
            artifact=self._artifact_snapshot,
        )
        try:
            artifact_started = monotonic_ns()
            self._artifact = _open_runner_artifact(artifact_path)
            from ipaddress import ip_interface
            self._zone_service_addresses = {tuple(key): str(ip_interface(prefix).ip)
                for key, prefix in self._artifact._snapshot['zone_prefixes']}
            if self._forwarding_backend is not None and self._artifact.forwarding_backend != self._forwarding_backend:
                raise ValueError("Runner forwarding selection is incompatible with sealed materials")
            self._forwarding_backend = self._artifact.forwarding_backend
            if self._forwarding_backend == "linux" and self._traffic_telemetry_directory is not None:
                raise ValueError("BPF Traffic telemetry is unavailable for Linux forwarding in batch 3A")
            self._artifact_snapshot = self._artifact.snapshot()
            if self._execution_input is not None:
                actual = self._artifact._snapshot
                if self._execution_input != (actual["physical_world_fingerprint"], actual["timeline"]["start"], actual["timeline"]["end"]):
                    raise ValueError("execution materials do not match the bound scene or Timeline")
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
                    self._delivery_tail = self._artifact.delivery_contract()
            self._artifact_boundary_count = self._artifact.boundary_count
            self._artifact_end = self._artifact.timeline_end
            self._artifact_expected_node_count = self._artifact.physical_world_node_count
            self._record("preparation_stage", stage="artifact-loading-validation",
                         duration_seconds=(monotonic_ns() - artifact_started) / 1e9)
            if not self._artifact.boundary_count:
                raise ValueError("Runner artifact contains no nominal boundary")
            discovery_started = monotonic()
            bindings = self._deployment.discover(
                self._artifact.physical_world_projection
            )
            foundation_token = self._deployment.foundation_identity(bindings)
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
                name=f"skyseed-{self._forwarding_backend}-{foundation_token[-12:]}",
                foundation_token=foundation_token,
                image_id=first_binding.image_id,
                forwarding_backend=self._forwarding_backend,
            )
            self._helper = helper
            helper_started = monotonic()
            helper.start()
            self._record(
                "preparation_stage", stage="privileged-helper-start",
                duration_seconds=monotonic() - helper_started,
            )
            if self._forwarding_backend == "bpf":
                from skyseed.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
                runtime = _TcBpfRuntimeRealization(
                    executor=helper,
                    pin_root=f"/sys/fs/bpf/skyseed-v2/run-{foundation_token[-12:]}",
                    bindings=bindings,
                    telemetry_enabled=self._traffic_telemetry_directory is not None,
                    worker_count=self._runtime_worker_count,
                    continuation_enabled=self._keep_alive or (delivery is not None and not delivery["final"]),
                    batch_capacity_factor=self._batch_capacity_factor,
                )
            else:
                from skyseed.backend.linux.runtime import _LinuxRuntime
                runtime = _LinuxRuntime(executor=helper, bindings=bindings)
            self._bindings = bindings
            if self._connection_observer is not None:
                self._connection_observer.enqueue('bindings', {n.value: b.container_id for n, b in bindings.items()})
            self._foundation_token = foundation_token
            self._helper = helper
            self._runtime_realization = runtime
            initialize_started = monotonic()
            foundation = runtime.initialize(
                self._artifact.physical_world_projection,
                zone_prefixes=self._artifact.zone_prefixes,
                runtime_capacity_demand=self._artifact.runtime_capacity_demand,
                dataplane=self._artifact.dataplane,
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
            addresses = runtime.runtime_node_addresses()
            self._communication_bindings = {node.value: {'node_id': node.value,
                'container_id': binding.container_id, 'container_name': binding.container_name,
                'pid': binding.pid, 'netns_inode': binding.netns_inode, 'started_at': binding.started_at,
                'runtime_ipv4': addresses.get(node)} for node, binding in bindings.items()}
            self._record("runtime_foundation_ready", **foundation)
            if self._traffic_telemetry_directory is not None:
                from skyseed.backend.bpf.runner.traffic_telemetry import _TrafficTelemetryRecorder
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
            self._prepared_plans = self._prepare_delivery(self._artifact, batch_index=0)
            with self._condition:
                self._nominal_origin = self._prepared_plans[0].nominal_time
                if self._pause_at is not None:
                    self.pause(at=self._pause_at)
            self._record("preparation_stage", stage="finite-plan-construction-and-prewrite",
                         duration_seconds=(monotonic_ns() - plan_started) / 1e9,
                         phases=runtime.preparation_timings())
            self._boundaries = ()
            self._state = "prepared"
            self._activate_prepared(ideal_activation=None, paced_not_before=None, preparation=True)
            self._record(
                "preparation_end", duration_seconds=monotonic() - started,
                active_frontier=self._iso(self._active_frontier),
                prepared_boundary_count=len(self._prepared_plans),
                semantic_generations_prewritten=self._forwarding_backend == "bpf",
                activation_semantics=("bpf-generation-publication" if self._forwarding_backend == "bpf" else "ordered-linux-operations"),
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
        if self._keep_alive or not self._batch_final:
            self._maintenance_thread = Thread(target=self._maintain_batches, name="skyseed-runner-maintenance")
            self._maintenance_thread.start()
        try:
            while True:
                if activation_limit is not None and self._active_count - start_count >= activation_limit:
                    break
                index = self._active_count - self._prepared_base
                seam = index == len(self._prepared_plans)
                if seam and self._batch_final and not self._keep_alive:
                    break
                timestamp = (self._batch_end + self._batch_interval if seam
                             else self._prepared_plans[index].nominal_time)
                ideal = self._await_activation(timestamp, seam=seam)
                if ideal is None:
                    break
                if seam:
                    with self._condition:
                        ready = self._ready_batch
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

    def _await_activation(self, timestamp, *, seam):
        # This is the only automatic pacing gate. It never owns network mutation.
        with self._condition:
            while not self._cancel_requested:
                if self._maintenance_error is not None:
                    raise RuntimeError("Runner Maintenance failed; session is incomplete") from self._maintenance_error
                if self._pause_at is not None and self._active_frontier is not None and self._active_frontier >= self._pause_at:
                    self._pause_requested = True
                now = monotonic()
                ideal = self._wall_origin + (timestamp - self._nominal_origin).total_seconds()
                missing = seam and self._ready_batch is None
                if self._pause_requested != self._paused:
                    self._paused = self._pause_requested
                    self._record("pause_start" if self._paused else "pause_end",
                                 active_frontier=self._iso(self._active_frontier))
                waiting = missing and now >= ideal
                if waiting != self._waiting:
                    self._waiting = waiting
                    self._record("material_wait_start" if waiting else "material_wait_end",
                                 active_frontier=self._iso(self._active_frontier),
                                 next_timestamp=timestamp.isoformat(), target_monotonic_seconds=ideal)
                held = self._paused or waiting
                if held and self._hold_started is None:
                    self._hold_started = now if self._paused else ideal
                    self._write_clock_event("hold")
                if not held and self._hold_started is not None:
                    shift = max(0.0, now - self._hold_started)
                    self._wall_origin += shift
                    self._clock_shift += shift
                    self._hold_started = None
                    self._clock_revision += 1
                    ideal += shift
                    self._write_clock_event("resume", shift_seconds=shift)
                # No elapsed deadline may monopolize the privileged channel while
                # Execution is waiting: Maintenance must be able to finish its prewrites.
                self._helper._set_main_deadline(None if held else ideal)
                if not held and not missing and now >= ideal:
                    return ideal
                remaining = max(0.001, ideal - now)
                self._condition.wait(0.05 if held or missing else min(0.05, remaining))
            self._helper._set_main_deadline(None)
            return None

    def _write_clock_event(self, action, **facts):
        clock = self.status()["clock"]
        self._record("simulation_clock_shift", action=action, clock=clock,
                     active_frontier=self._iso(self._active_frontier), **facts)
        if self._traffic_telemetry is not None:
            self._traffic_telemetry.write_simulation_clock_anchor(
                nominal_origin=self._nominal_origin.isoformat(),
                monotonic_origin_seconds=self._wall_origin,
                revision=self._clock_revision, held_since=self._hold_started,
                active_frontier=self._iso(self._active_frontier), action=action,
                effective_monotonic_ns=monotonic_ns())

    def _stop_maintenance(self):
        with self._condition:
            self._maintenance_stop = True
            self._condition.notify_all()
        if self._helper is not None:
            self._helper._set_main_deadline(None)
        if self._maintenance_thread is not None:
            self._maintenance_thread.join()
            self._maintenance_thread = None

    def _maintenance_checkpoint(self):
        if self._maintenance_stop:
            raise RuntimeError("Runner Maintenance stopped")
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

    def _prepare_delivery(self, artifact, *, batch_index, maintenance=False):
        """The same incremental decode/bind/prewrite path for every delivery."""
        return self._runtime_realization.prepare_all(
            artifact.boundaries(), logical_ports=artifact.logical_ports, retirements=artifact.retirements,
            runtime_capacity_demand=artifact.runtime_capacity_demand if maintenance else None,
            work_checkpoint=self._maintenance_checkpoint if maintenance else None,
            progress=lambda stage, **fields: self._record(
                'batch_preparation_progress', batch_index=batch_index, stage=stage, **fields))

    def _load_batch(self, contract, scheduled, deadline):
        started = monotonic()
        index = contract["delivery"]["index"]
        with self._condition:
            # A prior observation of PENDING conveys no authority. Selection and
            # entering PREPARING compete at this exact lock boundary.
            if not self._pending_batches or self._pending_batches[0] is not contract:
                return
            self._preparing_batch = index
        self._record("batch_load_start", batch_index=index,
                     manifest=str(contract["path"]), manifest_sha256=contract["manifest_sha256"],
                     boundary_timestamp=contract["timeline"]["start"],
                     scheduled_monotonic_seconds=scheduled, deadline_monotonic_seconds=deadline,
                     lead_fraction=self._batch_lead_fraction)
        self._runtime_realization.admit_boundary_count(contract["timeline"]["boundary_count"])
        artifact = _open_runner_artifact(contract["path"])
        try:
            if artifact.forwarding_backend != self._forwarding_backend:
                raise ValueError("Runner successor has incompatible forwarding backend")
            if artifact.manifest_sha256 != contract["manifest_sha256"]:
                raise ValueError("registered batch manifest was replaced")
            loaded = monotonic()
            self._record('batch_loading_complete', batch_index=index, load_seconds=loaded-started)
            plans = self._prepare_delivery(artifact, batch_index=index, maintenance=True)
            constructed = monotonic()
            self._maintenance_checkpoint()
            with self._condition:
                self._pending_batches.popleft()
                self._artifact_boundary_count += len(plans)
                self._artifact_end = artifact.timeline_end
                self._ready_batch = plans, contract, constructed
                self._preparing_batch = None
                self._condition.notify_all()
            timings = self._runtime_realization.preparation_timings()
            self._record("batch_ready", batch_index=index, boundary_count=len(plans),
                         boundary_timestamp=contract["timeline"]["start"],
                         ready_monotonic_seconds=constructed, deadline_monotonic_seconds=deadline,
                         readiness_margin_seconds=deadline - constructed,
                         load_seconds=loaded - started,
                         decode_seconds=timings['boundary_decode_seconds'],
                         construction_prewrite_seconds=(constructed - loaded - timings['boundary_decode_seconds']),
                         total_seconds=constructed - started,
                         phases=timings,
                         resident_capacity=self._runtime_realization.working_set_counts(),
                         manifest_sha256=contract["manifest_sha256"])
        finally:
            artifact.close()

    def _activate_prepared(self, *, ideal_activation, paced_not_before, preparation=False) -> None:
        with self._condition:
            if self._state != "prepared" or self._main_in_flight:
                raise RuntimeError("Runner is not available for activation")
            if self._active_count - self._prepared_base >= len(self._prepared_plans):
                raise RuntimeError("Runner finite experiment has no next boundary")
            plan = self._prepared_plans[self._active_count - self._prepared_base]
            self._main_in_flight = True
        started = monotonic()
        timestamp = plan.nominal_time.isoformat()
        self._record("boundary_scheduled", boundary_timestamp=timestamp,
                     ideal_activation_monotonic_seconds=ideal_activation,
                     paced_not_before_monotonic_seconds=paced_not_before,
                     preparation=preparation, pacing_mode="runner-adjusted-clock")
        try:
            result = dict(self._runtime_realization.activate(plan))
            completed = monotonic()
            with self._condition:
                changes = plan.zone_changes
                # Remove old addresses before recording new assignments, regardless
                # of the backend's ordered handover overlap inside this boundary.
                for op, node, address in changes:
                    if op == 'address_delete' and self._activated_zone_addresses.get(address) == node:
                        self._activated_zone_addresses.pop(address, None)
                for op, node, address in changes:
                    if op == 'address_add':
                        self._activated_zone_addresses[address] = node
                self._active = plan
                self._active_frontier = plan.nominal_time
                self._active_count += 1
                raw_anchor = result.pop("activation_anchor_monotonic_ns", None)
                anchor_valid = type(raw_anchor) is int and 0 < raw_anchor <= sys.maxsize
            commit_ended = monotonic_ns()
            changes = result.pop('connection_changes', ())
            if self._connection_observer is not None:
                self._connection_observer.enqueue('activation', timestamp, changes)
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
                raise RuntimeError("Runner cleanup requires the current Execution operation to finish")
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
                "Runner teardown failed: "
                + "; ".join(str(error) for error in errors)
            )
        if self._artifact is not None:
            artifact_started = monotonic_ns()
            self._artifact.close()
            self._artifact = None
            phases["artifact_handle_close_seconds"] = (monotonic_ns() - artifact_started) / 1e9
        if self._pending_retirement is not None:
            self._retired_plan_batches.append(self._pending_retirement)
            self._pending_retirement = None
        while self._retired_plan_batches:
            _, artifact = self._retired_plan_batches.popleft()
            if artifact is not None:
                artifact.close()
        self._prepared_plans = ()
        self._prepared_base = self._active_count
        self._ready_batch = None
        self._pending_batches.clear()
        self._preparing_batch = None
        self._active = None
        self._boundaries = ()
        self._artifact_snapshot = _compact_event_value(self._artifact_snapshot)
        if self._maintenance_error is not None:
            # Joined workers no longer need traceback frames holding whole batches.
            self._maintenance_error = RuntimeError(str(self._maintenance_error))
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

    def runtime_bindings(self, *, node_ids=None, offset=0, limit=100):
        """Focused immutable identity facts captured from the prepared foundation."""
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('binding query requires nonnegative offset and limit 1..100')
        with self._condition:
            identities = self._communication_bindings
        keys = tuple(identities) if node_ids is None else tuple(node_ids)
        return {'bindings': [dict(identities[k]) for k in keys[offset:offset+limit] if k in identities],
                'total': len(keys), 'offset': offset, 'limit': limit}

    def communication_endpoints(self, ground_station_id, zone_id, network_scope=''):
        """Return only completed ACTIVE identities/addresses for a logical pair.

        No route or reachability assertion is made. A prepared future Zone move
        cannot change these facts. The query never enumerates Linux networking.
        """
        from ipaddress import ip_interface
        with self._condition:
            if self._runtime_realization is None or self._active_frontier is None:
                return {'available': False, 'reason': 'no completed activation'}
            if not self._zone_service_addresses and self._artifact is not None:
                self._zone_service_addresses = {tuple(key): str(ip_interface(prefix).ip)
                    for key, prefix in self._artifact._snapshot['zone_prefixes']}
            keys = [k for k in self._zone_service_addresses if k[1] == zone_id and (not network_scope or k[0] == network_scope)]
            if len(keys) != 1:
                return {'available': False, 'reason': 'Zone scope absent or ambiguous'}
            address = self._zone_service_addresses[keys[0]]
            service = self._activated_zone_addresses.get(address)
            bindings = self._communication_bindings
            return {'available': ground_station_id in bindings, 'active_frontier': self._iso(self._active_frontier),
                    'ground': bindings.get(ground_station_id), 'service': bindings.get(service),
                    'zone_ipv4': address, 'network_scope': keys[0][0]}

    def inspection_snapshot(self) -> dict[str, Any]:
        # Capture the formal frontier under a constant-size lock. Detaching bulk
        # inspection/telemetry facts must never hold Execution's publication lock.
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
                "progress": self.status(),
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
        # These existing transitions are emitted once per change, never per poll.
        if event == 'preparation_start':
            _console("Runtime preparation starting: loading materials and preparing the initial network; formal clock has not started.")
        elif event == 'preparation_end':
            _console(f"Runtime preparation succeeded after {facts['duration_seconds']:.1f}s; initial network active at {facts['active_frontier']}.")
        elif event == 'automatic_run_start':
            _console(f"Runtime formal clock starting at {facts['nominal_origin']}. "
                     + ("Retained execution: run.sh keeps the final network active while waiting for continuation; use cancel-runtime to finish."
                        if self._keep_alive else "Finite execution: cleanup follows the final available boundary."))
        elif event == 'material_wait_start':
            _console(f"Runtime waiting for execution materials after {facts['active_frontier']}; network remains active. "
                     "Continue calculation if its requested range is ready, or use cancel-runtime to stop.")
        elif event == 'material_wait_end':
            _console("Runtime execution materials ready; progression resumes when any user pause is released.")
        elif event == 'pause_start':
            _console(f"Runtime paused at {facts['active_frontier']}; network remains active. Use resume to release the pause.")
        elif event == 'pause_end':
            _console("Runtime pause released; progression still requires execution materials.")
        elif event == 'automatic_run_end':
            _console(f"Runtime progression ended at {facts['active_frontier']}; owned work must drain before cleanup completes.")
        elif event == 'failure':
            _console(f"Runtime failure: {facts.get('phase')}: {facts.get('error')}; cleanup is still required.")
        if self._connection_observer is not None and event in {
                'pause_start', 'pause_end', 'material_wait_start', 'material_wait_end',
                'automatic_run_start', 'runtime_cleanup_end', 'failure'}:
            self._connection_observer.enqueue(event)
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
