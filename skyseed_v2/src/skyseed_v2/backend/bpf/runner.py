"""Own rolling live execution for one already-started BPF deployment."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
import heapq
import json
from pathlib import Path
import re
import subprocess
import sys
from threading import Condition, RLock, Thread
from time import monotonic, sleep, time
from typing import Any, Callable, Mapping, Sequence, TypeAlias

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    ConnectionId,
    GroundStationRouteTarget,
    ZoneRouteTarget,
)
from skyseed_v2.shared._durable_evidence import (
    _append_jsonl,
    _reset_jsonl,
)

from ._runner.runtime_realization import (
    _CapacityAuditError,
    _TcBpfRuntimeRealization,
)
from ._runner.traffic_telemetry import _TrafficTelemetryRecorder
from .compiled_realization import (
    BpfCompiledRealization,
    _ForwardingIntent,
    _QdiscIntent,
    _ResourceIntent,
    _TopologySignature,
    _ZoneEdgeIntent,
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


_Intent: TypeAlias = (
    _ResourceIntent | _QdiscIntent | _ForwardingIntent | _ZoneEdgeIntent
)
_Reference: TypeAlias = tuple[int, str, int]
_DOMAIN_ATTRIBUTES = (
    ("resource", "resource_realizations"),
    ("parameter", "parameter_realizations"),
    ("forwarding", "forwarding_realizations"),
    ("zone-edge", "zone_edge_realizations"),
)
_DOMAIN_RANK = {
    domain: rank for rank, (domain, _) in enumerate(_DOMAIN_ATTRIBUTES)
}


@dataclass(frozen=True, slots=True)
class _BoundaryOperation:
    realization_index: int
    domain: str
    index: int
    intent: _Intent

    @property
    def reference(self) -> _Reference:
        return self.realization_index, self.domain, self.index


@dataclass(frozen=True, slots=True)
class _NominalBoundary:
    """Represent one dependency-ordered Runner execution boundary."""

    nominal_time: datetime
    operations: tuple[_BoundaryOperation, ...]
    topology_signature: _TopologySignature
    source_realization_indexes: tuple[int, ...]
    coverage: tuple[tuple[int, int], ...]
    inspected_constraint_count: int = field(compare=False)
    source_constraint_count: int
    applied_constraint_count: int
    outgoing_constraint_count: int
    later_constraint_count: int = field(compare=False)
    constraint_reasons: tuple[tuple[str, int], ...]
    constraint_edges: tuple[tuple[_Reference, _Reference, str], ...]
    operation_counts: tuple[tuple[str, int], ...]
    order_sha256: str


def _operation_inventory(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    realization_index_offset: int = 0,
) -> dict[_Reference, _BoundaryOperation]:
    result: dict[_Reference, _BoundaryOperation] = {}
    for realization_index, realization in enumerate(
        realizations, start=realization_index_offset
    ):
        for domain, attribute in _DOMAIN_ATTRIBUTES:
            for index, intent in enumerate(getattr(realization, attribute)):
                operation = _BoundaryOperation(
                    realization_index, domain, index, intent
                )
                result[operation.reference] = operation
    return result


def _connection_key(value: object) -> tuple[str, str, str]:
    connection_id = getattr(value, "connection_id")
    return (
        connection_id.kind.value,
        connection_id.endpoint_a.value,
        connection_id.endpoint_b.value,
    )


def _route_key(intent: _ForwardingIntent) -> tuple[str, str, str]:
    target = intent.route_key.target
    if isinstance(target, GroundStationRouteTarget):
        target_key = ("ground-station", target.ground_station_id.value)
    elif isinstance(target, ZoneRouteTarget):
        target_key = ("zone", target.zone_id)
    else:  # pragma: no cover - RouteKey owns this validation
        raise TypeError("compiled forwarding has an unknown route target")
    return intent.route_key.source.value, *target_key


def _stable_operation_key(operation: _BoundaryOperation) -> tuple[object, ...]:
    intent = operation.intent
    if isinstance(intent, _ResourceIntent):
        semantic = (*_connection_key(intent), intent.operation.value)
    elif isinstance(intent, _QdiscIntent):
        semantic = (
            *_connection_key(intent),
            intent.direction.value,
            intent.reason,
        )
    elif isinstance(intent, _ForwardingIntent):
        semantic = (*_route_key(intent), intent.operation.value)
    elif isinstance(intent, _ZoneEdgeIntent):
        semantic = (intent.zone_id, intent.operation.value)
    else:  # pragma: no cover - inventory is closed above
        raise TypeError("unknown compiled boundary operation")
    return (_DOMAIN_RANK[operation.domain], *semantic)


def _operation_label(operation: _BoundaryOperation) -> str:
    return "|".join(
        (
            operation.intent.nominal_time.isoformat(),
            *(str(value) for value in _stable_operation_key(operation)),
        )
    )


def _resolve_nominal_boundaries(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    realization_index_offset: int = 0,
) -> tuple[_NominalBoundary, ...]:
    """Normalize compiled work into dependency-ordered Runner boundaries."""

    if not realizations:
        raise ValueError("nominal boundary resolution requires compiled work")
    inventory = _operation_inventory(
        realizations, realization_index_offset=realization_index_offset
    )
    if not inventory:
        raise ValueError("compiled future work contains no nominal operations")
    nominal_times = tuple(
        sorted({operation.intent.nominal_time for operation in inventory.values()})
    )
    return tuple(
        _resolve_nominal_boundary(
            realizations,
            inventory=inventory,
            nominal_time=nominal_time,
            realization_index_offset=realization_index_offset,
        )
        for nominal_time in nominal_times
    )


def _resolve_nominal_boundary(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    inventory: dict[_Reference, _BoundaryOperation],
    nominal_time: datetime,
    realization_index_offset: int = 0,
) -> _NominalBoundary:
    selected = {
        reference: operation
        for reference, operation in inventory.items()
        if operation.intent.nominal_time == nominal_time
    }
    selected_realization_indexes = {
        operation.realization_index for operation in selected.values()
    }
    source_constraint_count = sum(
        len(realizations[index - realization_index_offset].constraints)
        for index in selected_realization_indexes
    )
    stable_keys = [_stable_operation_key(value) for value in selected.values()]
    if len(set(stable_keys)) != len(stable_keys):
        raise ValueError(
            "nominal boundary contains operations without a unique semantic key"
        )

    successors: dict[_Reference, set[_Reference]] = {
        reference: set() for reference in selected
    }
    indegree = {reference: 0 for reference in selected}
    inspected = 0
    applied = 0
    outgoing = 0
    later = 0
    reasons: dict[str, int] = {}
    constraint_edges: list[tuple[_Reference, _Reference, str]] = []
    for realization_index, realization in enumerate(
        realizations, start=realization_index_offset
    ):
        for constraint in realization.constraints:
            inspected += 1
            predecessor = (
                realization_index,
                constraint.predecessor.domain,
                constraint.predecessor.index,
            )
            successor = (
                realization_index,
                constraint.successor.domain,
                constraint.successor.index,
            )
            try:
                predecessor_operation = inventory[predecessor]
                successor_operation = inventory[successor]
            except KeyError as error:
                raise ValueError(
                    "compiled constraint references an unknown operation"
                ) from error
            predecessor_selected = predecessor in selected
            successor_selected = successor in selected
            if predecessor_selected and successor_selected:
                if successor not in successors[predecessor]:
                    successors[predecessor].add(successor)
                    indegree[successor] += 1
                    applied += 1
                    reasons[constraint.reason] = (
                        reasons.get(constraint.reason, 0) + 1
                    )
                    constraint_edges.append(
                        (predecessor, successor, constraint.reason)
                    )
                continue
            if successor_selected:
                if predecessor_operation.intent.nominal_time > nominal_time:
                    raise ValueError(
                        "nominal boundary depends on later compiled work"
                    )
                later += 1
                continue
            if predecessor_selected:
                if successor_operation.intent.nominal_time < nominal_time:
                    raise ValueError(
                        "compiled constraint points behind the active frontier"
                    )
                outgoing += 1
            else:
                if predecessor_operation.intent.nominal_time > (
                    successor_operation.intent.nominal_time
                ):
                    raise ValueError(
                        "compiled constraint reverses nominal time"
                    )
                later += 1

    ready: list[tuple[tuple[object, ...], _Reference]] = []
    for reference, degree in indegree.items():
        if degree == 0:
            heapq.heappush(
                ready, (_stable_operation_key(selected[reference]), reference)
            )
    ordered = []
    while ready:
        _, reference = heapq.heappop(ready)
        operation = selected[reference]
        ordered.append(operation)
        for successor in successors[reference]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(
                    ready,
                    (_stable_operation_key(selected[successor]), successor),
                )
    if len(ordered) != len(selected):
        raise ValueError("nominal boundary dependency constraints contain a cycle")

    source_indexes = tuple(
        sorted({operation.realization_index for operation in ordered})
    )
    signature = realizations[
        source_indexes[0] - realization_index_offset
    ]._topology_signature
    if any(
        realizations[index - realization_index_offset]._topology_signature
        != signature
        for index in source_indexes
    ):
        raise ValueError("one nominal boundary spans different topologies")
    operation_counts = tuple(
        (domain, sum(value.domain == domain for value in ordered))
        for domain, _ in _DOMAIN_ATTRIBUTES
    )
    order_payload = "\n".join(_operation_label(value) for value in ordered)
    return _NominalBoundary(
        nominal_time=nominal_time,
        operations=tuple(ordered),
        topology_signature=signature,
        source_realization_indexes=source_indexes,
        coverage=tuple(
            (
                realizations[index - realization_index_offset].start_slice_id,
                realizations[index - realization_index_offset].end_slice_id,
            )
            for index in source_indexes
        ),
        inspected_constraint_count=inspected,
        source_constraint_count=source_constraint_count,
        applied_constraint_count=applied,
        outgoing_constraint_count=outgoing,
        later_constraint_count=later,
        constraint_reasons=tuple(sorted(reasons.items())),
        constraint_edges=tuple(constraint_edges),
        operation_counts=operation_counts,
        order_sha256=sha256(order_payload.encode("utf-8")).hexdigest(),
    )

@dataclass(frozen=True, slots=True)
class _RuntimeBinding:
    node_id: NodeId
    service_name: str
    container_id: str
    container_name: str
    image_id: str
    pid: int
    netns_inode: int
    started_at: str


@dataclass(frozen=True, slots=True)
class _RunnerReadyBoundary:
    """Bind Runner lifecycle evidence to an opaque tc-BPF realization."""

    boundary: _NominalBoundary
    predecessor_time: datetime | None
    foundation_token: str
    runtime_realization: object
    readiness_evidence: dict[str, Any]


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


class _PrivilegedHelper:
    """Own one helper container and invoke bounded one-shot commands in it."""

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

    def execute(self, action: str, request: Mapping[str, Any]) -> dict[str, Any]:
        self.assert_current()
        result = _execute(
            (
                self._docker,
                "exec",
                "--interactive",
                self._name,
                "chroot",
                "/host",
                sys.executable,
                "-B",
                str(_PRIVILEGED_COMMAND_PATH),
                action,
            ),
            input_text=json.dumps(request, sort_keys=True),
        )
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "Runner privileged runtime command returned invalid evidence; "
                f"stdout={result.stdout!r}; stderr={result.stderr!r}"
            ) from error
        if result.returncode != 0 or response.get("status") != "ok":
            raise RuntimeError(
                "Runner privileged runtime command failed: "
                f"{response.get('error', response)!r}; "
                f"traceback={response.get('traceback', '')!r}"
            )
        worker_result = response.get("result")
        if not isinstance(worker_result, dict):
            raise RuntimeError(
                "Runner privileged runtime command omitted operation evidence"
            )
        return worker_result

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
    """Own rolling preparation, active publication, reclaim, and cleanup.

    Layer: public live BPF-backend lifecycle owner. It is public because live
    deployment handoff, execution-mode selection, and scoped cleanup cross the
    backend boundary.

    Rolling execution requires both a non-negative nominal-time
    ``preparation_lookahead`` and a positive ``preparation_boundary_depth``.
    ``preparation_lookahead=None`` selects bounded full-range preparation and
    therefore requires both a terminating input and
    ``preparation_boundary_depth=None``. The callable remains only a pull seam;
    the Runner does not own compilation, planning, slicing, or Compose startup.
    """

    __slots__ = (
        "_compose_project_name",
        "_future_realization_supplier",
        "_preparation_lookahead",
        "_preparation_boundary_depth",
        "_docker",
        "_state",
        "_events",
        "_event_history_path",
        "_event_count",
        "_event_lock",
        "_bindings",
        "_future_realizations",
        "_realization_index_offset",
        "_boundaries",
        "_next_prepare_index",
        "_ready",
        "_active",
        "_active_count",
        "_active_frontier",
        "_ready_frontier",
        "_input_frontier",
        "_input_exhausted",
        "_foundation_token",
        "_helper",
        "_runtime_realization",
        "_traffic_telemetry_directory",
        "_traffic_telemetry",
        "_condition",
        "_stop_maintenance",
        "_maintenance_thread",
        "_maintenance_error",
        "_maintenance_in_flight",
        "_maintenance_revision",
        "_reclaim_forwarding",
        "_reclaim_physical",
        "_execution_mode",
        "_main_in_flight",
        "_prefer_physical_reclaim",
        "_nominal_origin",
    )

    def __init__(
        self,
        *,
        compose_project_name: str,
        future_realization_supplier: Callable[[], BpfCompiledRealization | None],
        preparation_lookahead: timedelta | None = timedelta(0),
        preparation_boundary_depth: int | None,
    ) -> None:
        if not isinstance(compose_project_name, str):
            raise TypeError("compose_project_name must be a string")
        project_name = compose_project_name.strip()
        if not project_name or "\n" in project_name or "\r" in project_name:
            raise ValueError("compose_project_name must be a non-empty single line")
        if not callable(future_realization_supplier):
            raise TypeError("future_realization_supplier must be callable")
        if preparation_lookahead is None:
            if preparation_boundary_depth is not None:
                raise ValueError(
                    "preparation_boundary_depth must be None for full-range preparation"
                )
        else:
            if not isinstance(preparation_lookahead, timedelta):
                raise TypeError(
                    "preparation_lookahead must be a timedelta or None"
                )
            if preparation_lookahead < timedelta(0):
                raise ValueError("preparation_lookahead must be non-negative")
            if isinstance(preparation_boundary_depth, bool) or not isinstance(
                preparation_boundary_depth, int
            ):
                raise TypeError(
                    "preparation_boundary_depth must be a positive int in rolling mode"
                )
            if preparation_boundary_depth <= 0:
                raise ValueError(
                    "preparation_boundary_depth must be positive in rolling mode"
                )
        self._compose_project_name = project_name
        self._future_realization_supplier = future_realization_supplier
        self._preparation_lookahead = preparation_lookahead
        self._preparation_boundary_depth = preparation_boundary_depth
        self._docker = "docker"
        self._state = "new"
        self._events: deque[dict[str, Any]] = deque(maxlen=_RECENT_EVENT_LIMIT)
        self._event_history_path: Path | None = None
        self._event_count = 0
        self._event_lock = RLock()
        self._bindings: dict[NodeId, _RuntimeBinding] = {}
        self._future_realizations: tuple[BpfCompiledRealization, ...] = ()
        self._realization_index_offset = 0
        self._boundaries: tuple[_NominalBoundary, ...] = ()
        self._next_prepare_index = 0
        self._ready: deque[_RunnerReadyBoundary] = deque()
        self._active: _RunnerReadyBoundary | None = None
        self._active_count = 0
        self._active_frontier: datetime | None = None
        self._ready_frontier: datetime | None = None
        self._input_frontier: datetime | None = None
        self._input_exhausted = False
        self._foundation_token: str | None = None
        self._helper: _PrivilegedHelper | None = None
        self._runtime_realization: _TcBpfRuntimeRealization | None = None
        self._traffic_telemetry_directory: Path | None = None
        self._traffic_telemetry: _TrafficTelemetryRecorder | None = None
        self._condition = Condition(RLock())
        self._stop_maintenance = False
        self._maintenance_thread: Thread | None = None
        self._maintenance_error: BaseException | None = None
        self._maintenance_in_flight = False
        self._maintenance_revision = 0
        self._reclaim_forwarding: deque[object] = deque()
        self._reclaim_physical: deque[object] = deque()
        self._execution_mode: str | None = None
        self._main_in_flight = False
        self._prefer_physical_reclaim = False
        self._nominal_origin: datetime | None = None

    def _enable_traffic_telemetry(self, directory: Path) -> None:
        """Enable the approved Runner-private canonical recording capability."""

        if self._state != "new":
            raise RuntimeError(
                "traffic telemetry must be configured before Runner preparation"
            )
        path = Path(directory)
        if path == Path(""):
            raise ValueError("traffic telemetry directory must not be empty")
        self._traffic_telemetry_directory = path

    def _enable_event_history(self, path: Path) -> None:
        """Enable complete durable JSONL while recent memory stays bounded."""

        if self._state != "new" or self._events or self._event_history_path is not None:
            raise RuntimeError(
                "Runner durable event history must be enabled before preparation"
            )
        self._event_history_path = _reset_jsonl(
            path,
            {
                "record_type": "history_header",
                "format_version": 1,
                "owner": "BpfRunner",
            },
        )

    def prepare(self) -> None:
        """Freeze the deployment and publish continuous initial READY work."""

        if self._state != "new":
            raise RuntimeError("BpfRunner preparation may run only once")
        started = monotonic()
        self._state = "preparing"
        self._record(
            "preparation_start",
            preparation_lookahead=self._lookahead_fact(),
            preparation_boundary_depth=self._preparation_boundary_depth,
        )
        try:
            self._pull_one()
            if not self._future_realizations or not self._boundaries:
                raise ValueError("BpfRunner requires compiled nominal work")
            self._prime_input_for_capacity()
            first = self._future_realizations[0]
            if first.start_slice_id != 0:
                raise ValueError("BpfRunner requires a bootstrap realization")
            bindings = self._discover_runtime_bindings(first)
            foundation_token = self._foundation_identity(bindings)
            first_binding = min(bindings.values(), key=lambda value: value.node_id.value)
            helper = _PrivilegedHelper(
                docker_executable=self._docker,
                name=f"skyseed-bpf-{foundation_token[-12:]}",
                foundation_token=foundation_token,
                image_id=first_binding.image_id,
            )
            helper.start()
            runtime_realization = _TcBpfRuntimeRealization(
                executor=helper,
                pin_root=f"/sys/fs/bpf/skyseed-v2/run-{foundation_token[-12:]}",
                bindings=bindings,
                telemetry_enabled=self._traffic_telemetry_directory is not None,
            )
            self._bindings = bindings
            self._foundation_token = foundation_token
            self._helper = helper
            self._runtime_realization = runtime_realization
            self._record(
                "capacity_audit_start",
                scope="initial",
                known_boundaries=len(self._boundaries),
                preparation_lookahead=self._lookahead_fact(),
                preparation_boundary_depth=self._preparation_boundary_depth,
            )
            try:
                foundation = runtime_realization.initialize(
                    first._topology_signature,
                    zone_prefixes=tuple(
                        prefix for _, prefix in first.zone_prefixes
                    ),
                    capacity_boundaries=self._boundaries,
                    preparation_lookahead=self._preparation_lookahead,
                    preparation_boundary_depth=self._preparation_boundary_depth,
                )
            except _CapacityAuditError as error:
                self._record(
                    "capacity_audit_failure",
                    scope="initial",
                    reason=str(error),
                )
                raise
            self._record(
                "capacity_audit_end",
                scope="initial",
                capacity=foundation.get("capacity_audit"),
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
                        foundation=runtime_realization.telemetry_foundation(
                            foundation_token=foundation_token
                        ),
                    )
                except BaseException as error:
                    telemetry.record_failure(error)
                self._record(
                    "traffic_telemetry_started",
                    **telemetry.snapshot(),
                )
            self._maintain_until_satisfied()
            with self._condition:
                if not self._ready:
                    raise RuntimeError("initial preparation published no READY boundary")
                self._state = "prepared"
                self._nominal_origin = self._ready[0].boundary.nominal_time
                self._record(
                    "preparation_end",
                    duration_seconds=monotonic() - started,
                    input_frontier=self._iso(self._input_frontier),
                    ready_frontier=self._iso(self._ready_frontier),
                    active_frontier=None,
                    ready_depth=len(self._ready),
                    ready_limiting_reason=self._ready_limiting_reason_locked(),
                )
                self._maintenance_thread = Thread(
                    target=self._maintenance_loop,
                    name=f"skyseed-maintenance-{foundation_token[-8:]}",
                    daemon=True,
                )
                self._maintenance_thread.start()
        except BaseException as error:
            self._state = "failed"
            self._record_failure("preparation", error)
            raise

    def activate_next(self) -> None:
        """Manually activate the exact next READY nominal boundary."""

        with self._condition:
            if self._execution_mode == "automatic":
                raise RuntimeError("automatic and manual Runner modes are exclusive")
            self._execution_mode = "manual"
        self._activate_ready(scheduled_wall=None)

    def run(self) -> None:
        """Execute all supplied boundaries against a relative monotonic clock."""

        with self._condition:
            if self._execution_mode == "manual":
                raise RuntimeError("manual and automatic Runner modes are exclusive")
            if self._state != "prepared" or self._active is not None:
                raise RuntimeError("automatic run requires a freshly prepared Runner")
            self._execution_mode = "automatic"
            nominal_origin = self._nominal_origin
        assert nominal_origin is not None
        wall_origin = monotonic()
        if self._traffic_telemetry is not None:
            self._traffic_telemetry.write_simulation_clock_anchor(
                nominal_origin=nominal_origin.isoformat(),
                monotonic_origin_seconds=wall_origin,
            )
        self._record(
            "automatic_run_start",
            nominal_origin=nominal_origin.isoformat(),
            wall_origin=wall_origin,
        )
        while True:
            with self._condition:
                self._raise_maintenance_error()
                next_boundary = self._next_unactivated_boundary_locked()
                if next_boundary is None:
                    if self._input_exhausted:
                        break
                    self._condition.wait(timeout=0.01)
                    continue
                due = wall_origin + (
                    next_boundary.nominal_time - nominal_origin
                ).total_seconds()
            remaining = due - monotonic()
            if remaining > 0:
                sleep(remaining)
            with self._condition:
                ready = self._ready[0] if self._ready else None
                if ready is None or ready.boundary.nominal_time != next_boundary.nominal_time:
                    error = RuntimeError(
                        "exact boundary was not READY at its scheduled deadline"
                    )
                    self._state = "failed"
                    self._record(
                        "deadline_miss",
                        boundary_timestamp=next_boundary.nominal_time.isoformat(),
                        scheduled_wall=due,
                        observed_wall=monotonic(),
                    )
                    raise error
            self._activate_ready(scheduled_wall=due)
        self._record("automatic_run_end", active_frontier=self._iso(self._active_frontier))

    def cleanup(self) -> None:
        """Stop Maintenance and remove only resources owned by this Runner."""

        if self._state == "cleaned":
            return
        started = monotonic()
        self._record("cleanup_start")
        with self._condition:
            self._stop_maintenance = True
            self._condition.notify_all()
        if self._maintenance_thread is not None:
            self._maintenance_thread.join()
        errors = []
        if self._runtime_realization is not None:
            try:
                self._record(
                    "runtime_cleanup_end", **self._runtime_realization.cleanup()
                )
            except BaseException as error:
                errors.append(error)
        if self._traffic_telemetry is not None:
            telemetry = self._traffic_telemetry.stop_and_drain()
            self._record("traffic_telemetry_stopped", **telemetry)
        if self._helper is not None:
            try:
                self._helper.stop()
            except BaseException as error:
                errors.append(error)
        if errors:
            self._state = "failed"
            message = "; ".join(str(error) for error in errors)
            self._record("failure", phase="cleanup", error_type="RuntimeError", error=message)
            raise RuntimeError(f"BpfRunner cleanup failed: {message}")
        self._state = "cleaned"
        self._record("cleanup_end", duration_seconds=monotonic() - started)

    def inspection_snapshot(self) -> dict[str, Any]:
        """Return detached lifecycle facts and bounded recent event evidence."""

        with self._condition:
            lookahead = (
                {"mode": "bounded-full-range", "seconds": None}
                if self._preparation_lookahead is None
                else {
                    "mode": "rolling",
                    "seconds": self._preparation_lookahead.total_seconds(),
                }
            )
            expected_node_count = (
                len(self._bindings)
                if self._bindings
                else (
                    None
                    if not self._future_realizations
                    else len(
                        self._future_realizations[0]._topology_signature.node_ids
                    )
                )
            )
            runtime_addresses = (
                {}
                if self._runtime_realization is None
                else self._runtime_realization.runtime_node_addresses()
            )
            runtime_working_set = (
                {}
                if self._runtime_realization is None
                else self._runtime_realization.working_set_counts()
            )
            return deepcopy(
                {
                    "format_version": 1,
                    "runner": {
                        "lifecycle_state": self._state,
                        "execution_mode": self._execution_mode,
                        "frontiers": {
                            "last_active": self._iso(self._active_frontier),
                            "ready_at_capture": self._iso(self._ready_frontier),
                            "input": self._iso(self._input_frontier),
                        },
                        "input_exhausted": self._input_exhausted,
                        "preparation_lookahead": lookahead,
                        "preparation_boundary_depth": (
                            self._preparation_boundary_depth
                        ),
                        "activation_count": self._active_count,
                        "ready_count_at_capture": len(self._ready),
                        "ready_depth_at_capture": len(self._ready),
                        "ready_limiting_reason": (
                            self._ready_limiting_reason_locked()
                        ),
                        "runtime_foundation": {
                            "compose_project_name": self._compose_project_name,
                            "foundation_token": self._foundation_token,
                            "expected_node_count": expected_node_count,
                            "bound_node_count": len(self._bindings),
                            "bindings": tuple(
                                {
                                    "node_id": node_id.value,
                                    "service": binding.service_name,
                                    "container_id": binding.container_id,
                                    "container_name": binding.container_name,
                                    "image_id": binding.image_id,
                                    "pid": binding.pid,
                                    "netns_inode": binding.netns_inode,
                                    "started_at": binding.started_at,
                                    "runtime_ipv4": runtime_addresses.get(node_id),
                                }
                                for node_id, binding in sorted(
                                    self._bindings.items(),
                                    key=lambda item: item[0].value,
                                )
                            ),
                        },
                        "maintenance": {
                            "in_flight": self._maintenance_in_flight,
                            "failed": self._maintenance_error is not None,
                            "pending_forwarding_reclaim": len(
                                self._reclaim_forwarding
                            ),
                            "pending_physical_reclaim": len(
                                self._reclaim_physical
                            ),
                        },
                        "working_set": {
                            "received_realization_count": (
                                self._realization_index_offset
                                + len(self._future_realizations)
                            ),
                            "retained_realization_count": len(
                                self._future_realizations
                            ),
                            "retained_future_boundary_count": len(
                                self._boundaries
                            ),
                            "recent_event_count": len(self._events),
                            "recorded_event_count": self._event_count,
                            "durable_event_count": (
                                self._event_count
                                if self._event_history_path is not None
                                else 0
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
                            "complete"
                            if len(self._events) == self._event_count
                            else "recent-only"
                        ),
                        "returned_count": len(self._events),
                        "total_count": self._event_count,
                        "durable_history_enabled": (
                            self._event_history_path is not None
                        ),
                    },
                    "events": tuple(self._events),
                }
            )

    def _activate_ready(self, *, scheduled_wall: float | None) -> None:
        with self._condition:
            if self._state != "prepared":
                raise RuntimeError("BpfRunner is not prepared for activation")
            self._raise_maintenance_error()
            if not self._ready:
                raise RuntimeError("BpfRunner exact next boundary is not READY")
            if self._main_in_flight:
                self._record(
                    "main_activation_rejected",
                    reason="activation already in flight",
                )
                raise RuntimeError("BpfRunner already has a Main activation in flight")
            ready = self._ready[0]
            if (
                ready.predecessor_time != self._active_frontier
                or ready.foundation_token != self._foundation_token
            ):
                raise RuntimeError("BpfRunner READY predecessor evidence is stale")
            prior = self._active
            self._main_in_flight = True
        try:
            now = monotonic()
            start_lag = None if scheduled_wall is None else now - scheduled_wall
            self._record(
                "boundary_scheduled",
                boundary_timestamp=ready.boundary.nominal_time.isoformat(),
                scheduled_wall=scheduled_wall,
                **ready.readiness_evidence,
            )
            self._record("activation_start", start_lag_seconds=start_lag)
            started = monotonic()
            try:
                self._assert_foundation_current()
                assert self._runtime_realization is not None
                evidence = self._runtime_realization.activate(
                    ready.runtime_realization
                )
                retirements = (
                    None
                    if prior is None
                    else self._runtime_realization.retirement_handles(
                        prior.runtime_realization,
                        ready.runtime_realization,
                    )
                )
            except BaseException as error:
                with self._condition:
                    self._state = "failed"
                    self._record_failure(
                        "activation",
                        error,
                        duration_seconds=monotonic() - started,
                    )
                    self._condition.notify_all()
                raise
            ended = monotonic()
            with self._condition:
                self._ready.popleft()
                self._active = ready
                self._active_count += 1
                self._active_frontier = ready.boundary.nominal_time
                if not self._boundaries or self._boundaries[0] != ready.boundary:
                    raise RuntimeError(
                        "Runner boundary working prefix differs from activated READY"
                    )
                self._boundaries = self._boundaries[1:]
                if self._next_prepare_index <= 0:
                    raise RuntimeError("Runner prepared-boundary count underflow")
                self._next_prepare_index -= 1
                self._prune_input_bookkeeping_locked()
                self._ready_frontier = (
                    self._ready[-1].boundary.nominal_time if self._ready else None
                )
                if retirements is not None:
                    forwarding_retirement, physical_retirement = retirements
                    self._reclaim_forwarding.append(forwarding_retirement)
                    if physical_retirement is not None:
                        self._reclaim_physical.append(physical_retirement)
                completion_lag = (
                    None if scheduled_wall is None else ended - scheduled_wall
                )
                self._record(
                    "activation_end",
                    duration_seconds=ended - started,
                    completion_lag_seconds=completion_lag,
                    active_frontier=self._active_frontier.isoformat(),
                    ready_frontier=self._iso(self._ready_frontier),
                    **evidence,
                )
                self._maintenance_revision += 1
                self._condition.notify_all()
        finally:
            with self._condition:
                self._main_in_flight = False
                self._condition.notify_all()

    def _maintenance_loop(self) -> None:
        while True:
            with self._condition:
                if self._stop_maintenance:
                    return
                observed_revision = self._maintenance_revision
            try:
                changed = self._maintain_until_satisfied()
            except BaseException as error:
                with self._condition:
                    self._maintenance_error = error
                    self._state = "failed"
                    self._record_failure("maintenance", error)
                    self._condition.notify_all()
                return
            with self._condition:
                if self._stop_maintenance:
                    return
                if (
                    not changed
                    and observed_revision == self._maintenance_revision
                ):
                    self._condition.wait()

    def _maintain_until_satisfied(self) -> bool:
        changed = False
        while True:
            action, payload = self._next_maintenance_action()
            if action == "done":
                return changed
            changed = True
            if action == "pull":
                self._set_maintenance_in_flight(True)
                try:
                    self._pull_one()
                finally:
                    self._set_maintenance_in_flight(False)
                continue
            assert self._runtime_realization is not None
            if action == "reclaim-forwarding":
                started = monotonic()
                self._set_maintenance_in_flight(True)
                try:
                    evidence = self._runtime_realization.reclaim_forwarding(payload)
                finally:
                    self._set_maintenance_in_flight(False)
                self._record(
                    "generation_reclaimed",
                    duration_seconds=monotonic() - started,
                    **evidence,
                )
                continue
            if action == "reclaim-physical":
                started = monotonic()
                self._set_maintenance_in_flight(True)
                try:
                    evidence = self._runtime_realization.reclaim_physical(payload)
                finally:
                    self._set_maintenance_in_flight(False)
                self._record(
                    "resources_reclaimed",
                    duration_seconds=monotonic() - started,
                    **evidence,
                )
                continue
            boundary, predecessor = payload
            self._set_maintenance_in_flight(True)
            preparation_succeeded = False
            try:
                pending, preparation_evidence = (
                    self._runtime_realization.start_preparation(boundary)
                )
                started = monotonic()
                self._record(
                    "maintenance_prepare_start",
                    boundary_timestamp=boundary.nominal_time.isoformat(),
                    **preparation_evidence,
                )
                self._record(
                    "capacity_audit_start",
                    scope="boundary",
                    boundary_timestamp=boundary.nominal_time.isoformat(),
                    **preparation_evidence,
                )
                try:
                    realization, readiness_evidence = (
                        self._runtime_realization.finish_preparation(
                            pending,
                            foundation_token=self._foundation_token or "",
                            predecessor_time=predecessor,
                        )
                    )
                    readiness_sha256 = readiness_evidence.get("readiness_sha256")
                    if not isinstance(readiness_sha256, str) or not readiness_sha256:
                        raise RuntimeError(
                            "tc-BPF runtime realization omitted deterministic READY evidence"
                        )
                    ready = _RunnerReadyBoundary(
                        boundary=boundary,
                        predecessor_time=predecessor,
                        foundation_token=self._foundation_token or "",
                        runtime_realization=realization,
                        readiness_evidence=dict(readiness_evidence),
                    )
                    if self._traffic_telemetry is not None:
                        try:
                            identities = (
                                self._runtime_realization
                                .telemetry_generation_identities(realization)
                            )
                            self._traffic_telemetry.write_generation_identity(
                                identities
                            )
                        except BaseException as error:
                            self._traffic_telemetry.record_failure(error)
                except _CapacityAuditError as error:
                    self._record(
                        "capacity_audit_failure",
                        scope="boundary",
                        boundary_timestamp=boundary.nominal_time.isoformat(),
                        reason=str(error),
                        **preparation_evidence,
                    )
                    raise
                preparation_succeeded = True
            finally:
                self._set_maintenance_in_flight(False)
                if not preparation_succeeded:
                    with self._condition:
                        self._condition.notify_all()
            with self._condition:
                self._ready.append(ready)
                self._next_prepare_index += 1
                self._ready_frontier = ready.boundary.nominal_time
                self._record(
                    "capacity_audit_end",
                    scope="boundary",
                    boundary_timestamp=boundary.nominal_time.isoformat(),
                    capacity=ready.readiness_evidence.get("capacity_audit"),
                    **preparation_evidence,
                )
                self._record(
                    "maintenance_prepare_end",
                    boundary_timestamp=boundary.nominal_time.isoformat(),
                    duration_seconds=monotonic() - started,
                    **preparation_evidence,
                )
                self._record(
                    "ready_published",
                    boundary_timestamp=boundary.nominal_time.isoformat(),
                    predecessor=self._iso(predecessor),
                    foundation=self._foundation_token,
                    input_frontier=self._iso(self._input_frontier),
                    ready_frontier=self._iso(self._ready_frontier),
                    active_frontier=self._iso(self._active_frontier),
                    preparation_headroom_seconds=self._headroom_seconds(),
                    ready_depth=len(self._ready),
                    ready_limiting_reason=self._ready_limiting_reason_locked(),
                    **ready.readiness_evidence,
                )
                self._condition.notify_all()

    def _next_maintenance_action(self) -> tuple[str, Any]:
        with self._condition:
            if self._stop_maintenance:
                return "done", None
            forward = self._next_forward_action_locked()
            if forward is not None:
                if forward[0] == "prepare":
                    assert self._runtime_realization is not None
                    capacity = (
                        self._runtime_realization.generation_capacity_status()
                    )
                    if not capacity["can_start_preparation"]:
                        if self._reclaim_forwarding:
                            retirement = self._reclaim_forwarding.popleft()
                            self._record(
                                "maintenance_capacity_pressure",
                                action="reclaim-forwarding",
                                reason="generation slot required for forward preparation",
                                active_frontier=self._iso(self._active_frontier),
                                ready_frontier=self._iso(self._ready_frontier),
                                ready_depth=len(self._ready),
                                **capacity,
                            )
                            return "reclaim-forwarding", retirement
                        if self._main_in_flight:
                            return "done", None
                        reason = (
                            "resident generation capacity cannot prepare exact-next "
                            "or forward READY work and no retired generation is reclaimable"
                        )
                        self._record(
                            "capacity_audit_failure",
                            scope="resident-generation",
                            reason=reason,
                            ready_depth=len(self._ready),
                            **capacity,
                        )
                        raise _CapacityAuditError(reason)
                if self._reclaim_forwarding or self._reclaim_physical:
                    self._record(
                        "maintenance_forward_priority",
                        action=forward[0],
                        active_frontier=self._iso(self._active_frontier),
                        ready_frontier=self._iso(self._ready_frontier),
                        input_frontier=self._iso(self._input_frontier),
                        ready_depth=len(self._ready),
                        ready_limiting_reason=self._ready_limiting_reason_locked(),
                        pending_generation_reclaim=len(self._reclaim_forwarding),
                        pending_physical_reclaim=len(self._reclaim_physical),
                    )
                return forward
            trailing = self._next_trailing_action_locked()
            if trailing is not None:
                return trailing
            return "done", None

    def _next_forward_action_locked(self) -> tuple[str, Any] | None:
        """Return one action required to restore exact-next/dual-bound coverage."""

        if self._preparation_lookahead is None:
            if self._next_prepare_index < len(self._boundaries):
                return "prepare", self._prepare_payload_locked()
            if not self._input_exhausted:
                return "pull", None
            return None
        if not self._boundaries:
            return None if self._input_exhausted else ("pull", None)
        anchor = self._active_frontier or self._boundaries[0].nominal_time
        target = anchor + self._preparation_lookahead
        exact_next_missing = not self._ready
        if self._next_prepare_index < len(self._boundaries):
            candidate = self._boundaries[self._next_prepare_index]
            if exact_next_missing:
                return "prepare", self._prepare_payload_locked()
            assert self._preparation_boundary_depth is not None
            if len(self._ready) >= self._preparation_boundary_depth:
                return None
            if candidate.nominal_time <= target:
                return "prepare", self._prepare_payload_locked()
        if exact_next_missing:
            return None if self._input_exhausted else ("pull", None)
        assert self._preparation_boundary_depth is not None
        if len(self._ready) >= self._preparation_boundary_depth:
            return None
        input_covers = self._input_frontier is not None and self._input_frontier > target
        ready_covers = self._ready_frontier is not None and self._ready_frontier >= target
        if ready_covers or input_covers:
            return None
        if not self._input_exhausted:
            return "pull", None
        return None

    def _next_trailing_action_locked(self) -> tuple[str, Any] | None:
        """Alternate independent reclaim classes once forward coverage is safe."""

        if self._reclaim_forwarding and self._reclaim_physical:
            if self._prefer_physical_reclaim:
                self._prefer_physical_reclaim = False
                return "reclaim-physical", self._reclaim_physical.popleft()
            self._prefer_physical_reclaim = True
            return "reclaim-forwarding", self._reclaim_forwarding.popleft()
        if self._reclaim_forwarding:
            self._prefer_physical_reclaim = True
            return "reclaim-forwarding", self._reclaim_forwarding.popleft()
        if self._reclaim_physical:
            self._prefer_physical_reclaim = False
            return "reclaim-physical", self._reclaim_physical.popleft()
        return None

    def _set_maintenance_in_flight(self, value: bool) -> None:
        with self._condition:
            self._maintenance_in_flight = value
            self._condition.notify_all()

    def _prime_input_for_capacity(self) -> None:
        """Acquire the bounded/all or initial rolling facts used to size maps."""

        if self._preparation_lookahead is None:
            while not self._input_exhausted:
                self._pull_one()
            return
        target = self._boundaries[0].nominal_time + self._preparation_lookahead
        assert self._preparation_boundary_depth is not None
        while not self._input_exhausted:
            if len(self._boundaries) >= self._preparation_boundary_depth:
                return
            boundary_covers = self._boundaries[-1].nominal_time >= target
            input_covers = self._input_frontier is not None and self._input_frontier > target
            if boundary_covers or input_covers:
                return
            self._pull_one()

    def _prepare_payload_locked(self) -> tuple[object, ...]:
        boundary = self._boundaries[self._next_prepare_index]
        predecessor = (
            self._active_frontier
            if self._next_prepare_index == 0
            else self._boundaries[self._next_prepare_index - 1].nominal_time
        )
        return boundary, predecessor

    def _pull_one(self) -> None:
        value = self._future_realization_supplier()
        with self._condition:
            if value is None:
                self._input_exhausted = True
                self._record("input_exhausted", input_frontier=self._iso(self._input_frontier))
                self._condition.notify_all()
                return
            if not isinstance(value, BpfCompiledRealization):
                raise TypeError("future realization supplier must return BpfCompiledRealization or None")
            if self._future_realizations:
                previous = self._future_realizations[-1]
                if (
                    previous.end_slice_id != value.start_slice_id
                    or previous.end_timestamp != value.start_timestamp
                    or previous._topology_signature != value._topology_signature
                ):
                    raise ValueError("future BPF realizations must be continuous and share one topology")
            values = (*self._future_realizations, value)
            resolved = _resolve_nominal_boundaries(
                values,
                realization_index_offset=self._realization_index_offset,
            )
            boundaries = tuple(
                boundary
                for boundary in resolved
                if self._active_frontier is None
                or boundary.nominal_time > self._active_frontier
            )
            if boundaries[: len(self._boundaries)] != self._boundaries:
                raise ValueError("new compiled work changed an existing nominal boundary")
            new_boundaries = boundaries[len(self._boundaries) :]
            self._future_realizations = values
            self._boundaries = boundaries
            self._input_frontier = value.end_timestamp
            for boundary in new_boundaries:
                self._record(
                    "boundary_known",
                    boundary_timestamp=boundary.nominal_time.isoformat(),
                    source_realization_indexes=boundary.source_realization_indexes,
                    coverage=boundary.coverage,
                    operation_counts=boundary.operation_counts,
                    constraint_reasons=boundary.constraint_reasons,
                    applied_constraint_count=boundary.applied_constraint_count,
                    order_sha256=boundary.order_sha256,
                )
            self._record(
                "input_advanced",
                realization_count=(
                    self._realization_index_offset + len(values)
                ),
                input_frontier=value.end_timestamp.isoformat(),
                boundary_count=len(boundaries),
            )
            self._condition.notify_all()

    def _next_unactivated_boundary_locked(self) -> _NominalBoundary | None:
        return None if not self._boundaries else self._boundaries[0]

    def _prune_input_bookkeeping_locked(self) -> None:
        """Retain only realizations still feeding future boundaries plus baseline."""

        if not self._future_realizations:
            return
        last_index = (
            self._realization_index_offset + len(self._future_realizations) - 1
        )
        referenced = {
            index
            for boundary in self._boundaries
            for index in boundary.source_realization_indexes
        }
        retained_from = min((*referenced, last_index))
        release_count = retained_from - self._realization_index_offset
        if release_count <= 0:
            return
        self._future_realizations = self._future_realizations[release_count:]
        self._realization_index_offset += release_count

    def _record(self, event: str, **facts: Any) -> None:
        record = {
            "event": event,
            "monotonic_seconds": monotonic(),
            "wall_time_seconds": time(),
            **deepcopy(facts),
        }
        with self._event_lock:
            if self._event_history_path is not None:
                _append_jsonl(
                    self._event_history_path,
                    {"record_type": "runner_event", **record},
                )
            self._events.append(record)
            self._event_count += 1

    def _record_failure(self, phase: str, error: BaseException, **facts: Any) -> None:
        self._record(
            "failure",
            phase=phase,
            error_type=type(error).__name__,
            error=str(error),
            **facts,
        )

    def _raise_maintenance_error(self) -> None:
        if self._maintenance_error is not None:
            raise RuntimeError("BpfRunner Maintenance failed") from self._maintenance_error

    def _lookahead_fact(self) -> str:
        return "all" if self._preparation_lookahead is None else str(self._preparation_lookahead)

    def _ready_limiting_reason_locked(self) -> str | None:
        if self._preparation_lookahead is None:
            return "input" if self._input_exhausted else None
        if not self._boundaries:
            return "input" if self._input_exhausted else None
        anchor = self._active_frontier or self._boundaries[0].nominal_time
        target = anchor + self._preparation_lookahead
        if self._ready_frontier is not None and self._ready_frontier >= target:
            return "time"
        if self._input_frontier is not None and self._input_frontier > target:
            next_boundary = (
                None
                if self._next_prepare_index >= len(self._boundaries)
                else self._boundaries[self._next_prepare_index]
            )
            if next_boundary is None or next_boundary.nominal_time > target:
                return "time"
        if (
            self._preparation_boundary_depth is not None
            and len(self._ready) >= self._preparation_boundary_depth
        ):
            return "depth"
        if self._input_exhausted:
            return "input"
        return None

    def _headroom_seconds(self) -> float | None:
        if self._ready_frontier is None:
            return None
        anchor = self._active_frontier or self._nominal_origin
        if anchor is None:
            anchor = self._boundaries[0].nominal_time if self._boundaries else None
        return None if anchor is None else (self._ready_frontier - anchor).total_seconds()

    @staticmethod
    def _iso(value: object) -> str | None:
        return None if value is None else value.isoformat()

    def _discover_runtime_bindings(
        self, first: BpfCompiledRealization
    ) -> dict[NodeId, _RuntimeBinding]:
        result = _require_success(
            _execute(
                (
                    self._docker,
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"label={_PROJECT_LABEL}={self._compose_project_name}",
                )
            ),
            context="Runner deployment discovery",
        )
        container_ids = tuple(value.strip() for value in result.stdout.splitlines() if value.strip())
        if not container_ids:
            raise RuntimeError(
                "current Compose project has no containers; "
                "start the generated deployment first"
            )
        inspected = self._inspect(container_ids)
        bindings: dict[NodeId, _RuntimeBinding] = {}
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
                if entity_type == "Satellite":
                    node_id = NodeId(asn, "satellite", labels[_SATELLITE_ID_LABEL])
                else:
                    node_id = NodeId(asn, "ground-station", labels[_GROUND_STATION_ID_LABEL])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("Skyseed entity container labels are incomplete") from error
            if node_id in bindings:
                raise RuntimeError(f"duplicate live binding for {node_id.value}")
            bindings[node_id] = _RuntimeBinding(
                node_id=node_id,
                service_name=service_name,
                container_id=value["Id"],
                container_name=value["Name"].removeprefix("/"),
                image_id=value["Image"],
                pid=pid,
                netns_inode=self._container_netns_inode(value["Id"]),
                started_at=state.get("StartedAt", ""),
            )
        expected = set(first._topology_signature.node_ids)
        if set(bindings) != expected:
            missing = sorted(value.value for value in expected - set(bindings))
            unexpected = sorted(value.value for value in set(bindings) - expected)
            raise RuntimeError(
                f"live NodeId bindings do not match compiled topology: missing={missing}, unexpected={unexpected}"
            )
        return bindings

    def _assert_foundation_current(self) -> None:
        if not self._bindings or self._helper is None:
            raise RuntimeError("BpfRunner has no frozen runtime foundation")
        current = {
            value["Id"]: value
            for value in self._inspect(tuple(binding.container_id for binding in self._bindings.values()))
        }
        for binding in self._bindings.values():
            value = current.get(binding.container_id)
            if value is None:
                raise RuntimeError(f"frozen container disappeared: {binding.node_id.value}")
            state = value.get("State") or {}
            if (
                not state.get("Running")
                or state.get("Pid") != binding.pid
                or state.get("StartedAt", "") != binding.started_at
                or value.get("Image") != binding.image_id
            ):
                raise RuntimeError(f"frozen container incarnation is stale: {binding.node_id.value}")
        self._helper.execute(
            "validate",
            {
                "nodes": [
                    {"key": binding.node_id.value, "pid": binding.pid, "netns_inode": binding.netns_inode}
                    for binding in self._bindings.values()
                ]
            },
        )

    def _foundation_identity(self, bindings: Mapping[NodeId, _RuntimeBinding]) -> str:
        facts = [
            self._compose_project_name,
            *(
                f"{node_id.value}|{binding.container_id}|{binding.image_id}|"
                f"{binding.pid}|{binding.netns_inode}|{binding.started_at}"
                for node_id, binding in sorted(bindings.items(), key=lambda item: item[0].value)
            ),
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

    def _container_netns_inode(self, container_id: str) -> int:
        result = _require_success(
            _execute((self._docker, "exec", container_id, "readlink", "/proc/self/ns/net")),
            context="Runner netns discovery",
        )
        match = re.fullmatch(r"net:\[(\d+)\]\s*", result.stdout)
        if match is None:
            raise RuntimeError("Runner netns discovery returned invalid evidence")
        return int(match.group(1))


__all__ = ["BpfRunner"]
