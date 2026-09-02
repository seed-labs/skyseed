"""Own rolling live execution for one already-started BPF deployment."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
import heapq
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Condition, RLock, Thread
from time import monotonic, sleep, time
from typing import Any, Callable, Mapping, Sequence, TypeAlias

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget,
    ZoneRouteDemandTarget,
)
from ._runner.event_history import _NonBlockingEventHistory
from ._runner.runtime_realization import (
    _CapacityAuditError,
    _TcBpfRuntimeRealization,
)
from ._runner.traffic_telemetry import _TrafficTelemetryRecorder
from .compiled_realization import (
    BpfCompiledRealization,
    _ForwardingIntent,
    _MutationOperation,
    _PhysicalWorldProjection,
    _QdiscIntent,
    _ResourceIntent,
    _ResourceOperation,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
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
_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS = 30.0


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
    physical_world_projection: _PhysicalWorldProjection
    source_realization_indexes: tuple[int, ...]
    coverage: tuple[tuple[str, str, str], ...]
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
    if isinstance(target, GroundStationRouteDemandTarget):
        target_key = ("ground-station", target.ground_station_id.value)
    elif isinstance(target, ZoneRouteDemandTarget):
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
            intent.source.value,
            intent.target.value,
            intent.reason,
        )
    elif isinstance(intent, _ForwardingIntent):
        semantic = (*_route_key(intent), intent.operation.value)
    elif isinstance(intent, _ZoneEdgeIntent):
        semantic = (
            intent.zone_id,
            intent.phase.value,
            intent.operation.value,
        )
    else:  # pragma: no cover - inventory is closed above
        raise TypeError("unknown compiled boundary operation")
    if isinstance(intent, _ResourceIntent):
        phase = 0 if intent.operation is _ResourceOperation.ESTABLISH else 5
    elif isinstance(intent, _QdiscIntent):
        phase = 1
    elif (
        isinstance(intent, _ZoneEdgeIntent)
        and intent.phase is _ZoneEdgePhase.READINESS
    ):
        phase = 2
    elif isinstance(intent, _ForwardingIntent):
        phase = 3
    elif isinstance(intent, _ZoneEdgeIntent):
        phase = 4
    else:  # pragma: no cover - inventory is closed above
        raise TypeError("unknown compiled boundary operation")
    return (phase, _DOMAIN_RANK[operation.domain], *semantic)


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
    declarations: dict[datetime, int] = {}
    for realization_index, realization in enumerate(
        realizations, start=realization_index_offset
    ):
        timestamps = realization.activation_timestamps
        if not timestamps:
            raise ValueError("compiled realization has no activation boundaries")
        if timestamps != tuple(sorted(set(timestamps))):
            raise ValueError(
                "compiled activation boundaries must be unique and ordered"
            )
        if (
            timestamps[0] != realization.start_timestamp
            or timestamps[-1] != realization.end_timestamp
        ):
            raise ValueError(
                "compiled activation boundaries differ from realization interval"
            )
        for timestamp in timestamps:
            if timestamp in declarations:
                raise ValueError(
                    "compiled realizations duplicate a formal activation boundary"
                )
            declarations[timestamp] = realization_index
        if any(
            intent.nominal_time not in set(timestamps)
            for attribute in (
                "resource_realizations",
                "parameter_realizations",
                "forwarding_realizations",
                "zone_edge_realizations",
            )
            for intent in getattr(realization, attribute)
        ):
            raise ValueError(
                "compiled operation has no declared activation boundary"
            )
    nominal_times = tuple(sorted(declarations))
    return tuple(
        _resolve_nominal_boundary(
            realizations,
            inventory=inventory,
            nominal_time=nominal_time,
            declared_realization_index=declarations[nominal_time],
            realization_index_offset=realization_index_offset,
        )
        for nominal_time in nominal_times
    )


def _append_realization_boundaries(
    boundaries: tuple[_NominalBoundary, ...],
    realization: BpfCompiledRealization,
    *,
    realization_index: int,
    active_frontier: datetime | None,
) -> tuple[tuple[_NominalBoundary, ...], tuple[_NominalBoundary, ...]]:
    """Normalize only newly supplied work and append its strict time suffix.

    Compiler constraints are scoped to the containing realization. The
    existing Runner contract already rejects a later realization that changes
    a known nominal boundary, so incremental ingestion may resolve the new
    realization independently and require a strictly later boundary suffix.
    """

    newly_resolved = _resolve_nominal_boundaries(
        (realization,), realization_index_offset=realization_index
    )
    previous_time = boundaries[-1].nominal_time if boundaries else active_frontier
    if previous_time is not None and newly_resolved[0].nominal_time <= previous_time:
        raise ValueError("new compiled work changed an existing nominal boundary")
    return (*boundaries, *newly_resolved), newly_resolved


def _resolve_nominal_boundary(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    inventory: dict[_Reference, _BoundaryOperation],
    nominal_time: datetime,
    declared_realization_index: int,
    realization_index_offset: int = 0,
) -> _NominalBoundary:
    selected = {
        reference: operation
        for reference, operation in inventory.items()
        if operation.intent.nominal_time == nominal_time
    }
    selected_realization_indexes = {declared_realization_index}
    if any(
        operation.realization_index != declared_realization_index
        for operation in selected.values()
    ):
        raise ValueError("activation boundary contains foreign realization work")
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

    source_indexes = (declared_realization_index,)
    projection = realizations[
        declared_realization_index - realization_index_offset
    ]._physical_world_projection
    if any(
        realizations[index - realization_index_offset]._physical_world_projection
        != projection
        for index in source_indexes
    ):
        raise ValueError("one boundary spans different PhysicalWorld projections")
    operation_counts = tuple(
        (domain, sum(value.domain == domain for value in ordered))
        for domain, _ in _DOMAIN_ATTRIBUTES
    )
    order_payload = "\n".join(_operation_label(value) for value in ordered)
    return _NominalBoundary(
        nominal_time=nominal_time,
        operations=tuple(ordered),
        physical_world_projection=projection,
        source_realization_indexes=source_indexes,
        coverage=tuple(
            (
                realizations[index - realization_index_offset].source_plan_id,
                realizations[
                    index - realization_index_offset
                ].start_timestamp.isoformat(),
                realizations[
                    index - realization_index_offset
                ].end_timestamp.isoformat(),
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
    the Runner does not own compilation, planning, observation, or Compose startup.
    """

    __slots__ = (
        "_compose_project_name",
        "_future_realization_supplier",
        "_preparation_lookahead",
        "_preparation_boundary_depth",
        "_runtime_worker_count",
        "_docker",
        "_state",
        "_events",
        "_event_history",
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
        "_work_produced",
        "_work_submitted",
        "_work_completed",
        "_resources_retired",
        "_resources_reclaimed",
        "_generations_reclaimed",
        "_boundary_known_at",
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
        self._runtime_worker_count = _runtime_worker_count()
        self._docker = "docker"
        self._state = "new"
        self._events: deque[dict[str, Any]] = deque(maxlen=_RECENT_EVENT_LIMIT)
        self._event_history: _NonBlockingEventHistory | None = None
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
        self._work_produced = 0
        self._work_submitted = 0
        self._work_completed = 0
        self._resources_retired = 0
        self._resources_reclaimed = 0
        self._generations_reclaimed = 0
        self._boundary_known_at: dict[datetime, float] = {}
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

        if self._state != "new" or self._events or self._event_history is not None:
            raise RuntimeError(
                "Runner durable event history must be enabled before preparation"
            )
        self._event_history = _NonBlockingEventHistory(path)

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
            runtime_worker_count=self._runtime_worker_count,
        )
        try:
            input_started = monotonic()
            self._pull_one()
            if not self._future_realizations or not self._boundaries:
                raise ValueError("BpfRunner requires compiled nominal work")
            self._prime_input_for_capacity()
            self._record(
                "preparation_stage",
                stage="input-and-normalization",
                duration_seconds=monotonic() - input_started,
                realization_count=(
                    self._realization_index_offset + len(self._future_realizations)
                ),
                boundary_count=len(self._boundaries),
            )
            first = self._future_realizations[0]
            if (
                first.source_previous_plan_id is not None
                or first.input_state_fingerprint is not None
            ):
                raise ValueError("BpfRunner requires a bootstrap realization")
            discovery_started = monotonic()
            bindings = self._discover_runtime_bindings(first)
            foundation_token = self._foundation_identity(bindings)
            self._record(
                "preparation_stage",
                stage="runtime-binding-discovery",
                duration_seconds=monotonic() - discovery_started,
                node_count=len(bindings),
                netns_query_count=len(bindings),
                netns_query_worker_limit=min(
                    self._runtime_worker_count, len(bindings)
                ),
            )
            first_binding = min(bindings.values(), key=lambda value: value.node_id.value)
            helper = _PrivilegedHelper(
                docker_executable=self._docker,
                name=f"skyseed-bpf-{foundation_token[-12:]}",
                foundation_token=foundation_token,
                image_id=first_binding.image_id,
            )
            helper_started = monotonic()
            helper.start()
            self._record(
                "preparation_stage",
                stage="privileged-helper-start",
                duration_seconds=monotonic() - helper_started,
            )
            runtime_realization = _TcBpfRuntimeRealization(
                executor=helper,
                pin_root=f"/sys/fs/bpf/skyseed-v2/run-{foundation_token[-12:]}",
                bindings=bindings,
                telemetry_enabled=self._traffic_telemetry_directory is not None,
                worker_count=self._runtime_worker_count,
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
                runtime_initialize_started = monotonic()
                foundation = runtime_realization.initialize(
                    first._physical_world_projection,
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
            self._record(
                "preparation_stage",
                stage="runtime-initialization",
                duration_seconds=monotonic() - runtime_initialize_started,
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
            first_ready_started = monotonic()
            self._maintain_until_satisfied()
            self._record(
                "preparation_stage",
                stage="initial-ready",
                duration_seconds=monotonic() - first_ready_started,
                ready_depth=len(self._ready),
            )
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
            self._record_resource_health_checkpoint("after-preparation")
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

        self._run_bounded(None)

    def _run_bounded(self, activation_limit: int | None) -> None:
        """Execute one canonical prefix for generated runtime qualification."""

        if activation_limit is not None and (
            isinstance(activation_limit, bool)
            or not isinstance(activation_limit, int)
            or activation_limit <= 0
        ):
            raise ValueError("Runner activation limit must be a positive int")

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
            activation_limit=activation_limit,
        )
        activation_count_at_start = self._active_count
        while True:
            with self._condition:
                self._raise_maintenance_error()
                if (
                    activation_limit is not None
                    and self._active_count - activation_count_at_start
                    >= activation_limit
                ):
                    break
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
        self._record(
            "automatic_run_end",
            active_frontier=self._iso(self._active_frontier),
            activation_limit=activation_limit,
            stopped_by_activation_limit=(
                activation_limit is not None
                and self._active_count - activation_count_at_start
                >= activation_limit
            ),
        )

    def cleanup(self) -> None:
        """Bound Maintenance shutdown and tear down only Runner-owned resources."""

        if self._state == "cleaned":
            return
        started = monotonic()
        self._record(
            "cleanup_start",
            retry=self._state == "teardown-failed",
            maintenance_in_flight=self._maintenance_in_flight,
        )
        self._record_resource_health_checkpoint("before-teardown")
        with self._condition:
            self._stop_maintenance = True
            self._condition.notify_all()
        if self._maintenance_thread is not None:
            self._maintenance_thread.join(
                timeout=_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS
            )
            if self._maintenance_thread.is_alive():
                self._state = "teardown-failed"
                error = RuntimeError(
                    "BpfRunner Maintenance did not stop within "
                    f"{_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS:.3f}s; "
                    "runtime ownership is retained for retry/recovery"
                )
                self._record_failure(
                    "maintenance-shutdown",
                    error,
                    maintenance_in_flight=self._maintenance_in_flight,
                    pending_generation_reclaim=len(self._reclaim_forwarding),
                    pending_physical_reclaim=len(self._reclaim_physical),
                )
                raise error
            self._maintenance_thread = None
        errors: list[BaseException] = []
        runtime_clean = self._runtime_realization is None
        if self._runtime_realization is not None:
            try:
                teardown = self._runtime_realization.cleanup()
                runtime_clean = True
                self._record("runtime_cleanup_end", **teardown)
            except BaseException as error:
                errors.append(error)
                self._record_failure(
                    "runtime-teardown",
                    error,
                    ownership_preserved=True,
                    remaining=self._runtime_realization.working_set_counts(),
                )
        if runtime_clean and self._traffic_telemetry is not None:
            telemetry = self._traffic_telemetry.stop_and_drain()
            self._record("traffic_telemetry_stopped", **telemetry)
        if runtime_clean and self._helper is not None:
            try:
                self._helper.stop()
                self._helper = None
            except BaseException as error:
                errors.append(error)
        if errors:
            self._state = "teardown-failed"
            message = "; ".join(str(error) for error in errors)
            self._record(
                "failure",
                phase="teardown",
                error_type="RuntimeError",
                error=message,
                ownership_preserved=not runtime_clean,
            )
            raise RuntimeError(f"BpfRunner teardown failed: {message}")
        self._state = "cleaned"
        self._record(
            "cleanup_end",
            duration_seconds=monotonic() - started,
            bounded_shutdown_seconds=_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS,
        )
        self._record_resource_health_checkpoint("after-teardown")
        if self._event_history is not None:
            self._event_history.close()

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
                        self._future_realizations[
                            0
                        ]._physical_world_projection.node_ids
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
            event_history_status = (
                None
                if self._event_history is None
                else self._event_history.snapshot()
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
                        "runtime_worker_count": self._runtime_worker_count,
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
                            **self._maintenance_facts_locked(),
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
                                event_history_status["written_count"]
                                if event_history_status is not None
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
                            self._event_history is not None
                        ),
                        "durable": event_history_status,
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
                foundation_started = monotonic()
                self._assert_foundation_current()
                foundation_validation_seconds = monotonic() - foundation_started
                assert self._runtime_realization is not None
                runtime_started = monotonic()
                evidence = self._runtime_realization.activate(
                    ready.runtime_realization
                )
                runtime_apply_seconds = monotonic() - runtime_started
                retirement_started = monotonic()
                retirements = (
                    None
                    if prior is None
                    else self._runtime_realization.retirement_handles(
                        prior.runtime_realization,
                        ready.runtime_realization,
                    )
                )
                retirement_seconds = monotonic() - retirement_started
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
            bookkeeping_started = monotonic()
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
                    if forwarding_retirement is not None:
                        self._reclaim_forwarding.append(forwarding_retirement)
                    if physical_retirement is not None:
                        self._reclaim_physical.append(physical_retirement)
                        self._resources_retired += len(
                            getattr(physical_retirement, "resources", ())
                        )
                self._boundary_known_at.pop(ready.boundary.nominal_time, None)
                self._maintenance_revision += 1
                maintenance = self._maintenance_facts_locked()
                ready_frontier_fact = self._iso(self._ready_frontier)
                self._condition.notify_all()
            bookkeeping_seconds = monotonic() - bookkeeping_started
            ended = monotonic()
            completion_lag = (
                None if scheduled_wall is None else ended - scheduled_wall
            )
            self._record(
                "activation_end",
                duration_seconds=ended - started,
                readiness_dependency_wait_seconds=0.0,
                foundation_validation_seconds=foundation_validation_seconds,
                runtime_apply_seconds=runtime_apply_seconds,
                retirement_seconds=retirement_seconds,
                bookkeeping_seconds=bookkeeping_seconds,
                completion_lag_seconds=completion_lag,
                active_frontier=ready.boundary.nominal_time.isoformat(),
                ready_frontier=ready_frontier_fact,
                maintenance=maintenance,
                **evidence,
            )
            if self._active_count in {1, 2, 3, 5, 10}:
                self._record_resource_health_checkpoint(
                    f"after-activation-{self._active_count}"
                )
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
                with self._condition:
                    self._generations_reclaimed += 1
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
                with self._condition:
                    self._resources_reclaimed += int(
                        evidence.get("deleted_connections", 0)
                    )
                continue
            boundary, predecessor = payload
            self._set_maintenance_in_flight(True)
            with self._condition:
                self._work_submitted += 1
            preparation_succeeded = False
            try:
                materialization_started = monotonic()
                pending, preparation_evidence = (
                    self._runtime_realization.start_preparation(boundary)
                )
                materialization_seconds = monotonic() - materialization_started
                preparation_evidence = {
                    **preparation_evidence,
                    "state_materialization_seconds": materialization_seconds,
                }
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
                self._work_completed += 1
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
                    target_active_interface_entries=(
                        ready.readiness_evidence.get(
                            "target_active_interface_entries"
                        )
                    ),
                    target_route_entries=ready.readiness_evidence.get(
                        "target_route_entries"
                    ),
                    generation_map_update_operations=(
                        ready.readiness_evidence.get(
                            "generation_map_update_operations"
                        )
                    ),
                    stable_interface_identity_count=(
                        ready.readiness_evidence.get(
                            "stable_interface_identity_count"
                        )
                    ),
                    new_interface_identity_count=ready.readiness_evidence.get(
                        "new_interface_identity_count"
                    ),
                    execution_metrics=ready.readiness_evidence.get(
                        "execution_metrics"
                    ),
                    phases=ready.readiness_evidence.get(
                        "preparation_phases"
                    ),
                    grouping=ready.readiness_evidence.get("grouping"),
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
                    maintenance=self._maintenance_facts_locked(),
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
                        self._runtime_realization.generation_capacity_status(
                            forward[1][0]
                        )
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
        supplier_started = monotonic()
        value = self._future_realization_supplier()
        supplier_seconds = monotonic() - supplier_started
        with self._condition:
            if value is None:
                self._input_exhausted = True
                self._record(
                    "input_exhausted",
                    input_frontier=self._iso(self._input_frontier),
                    supplier_seconds=supplier_seconds,
                )
                self._condition.notify_all()
                return
            if not isinstance(value, BpfCompiledRealization):
                raise TypeError(
                    "future realization supplier must return "
                    "BpfCompiledRealization or None"
                )
            if self._future_realizations:
                previous = self._future_realizations[-1]
                if (
                    value.source_previous_plan_id != previous.source_plan_id
                    or value.input_state_fingerprint
                    != previous.output_state_fingerprint
                    or previous.end_timestamp >= value.start_timestamp
                    or previous.physical_world_fingerprint
                    != value.physical_world_fingerprint
                    or previous._physical_world_projection
                    != value._physical_world_projection
                ):
                    raise ValueError(
                        "future BPF realizations must preserve Plan/state lineage "
                        "and one PhysicalWorld"
                    )
            realization_index = (
                self._realization_index_offset + len(self._future_realizations)
            )
            normalization_started = monotonic()
            boundaries, new_boundaries = _append_realization_boundaries(
                self._boundaries,
                value,
                realization_index=realization_index,
                active_frontier=self._active_frontier,
            )
            normalization_seconds = monotonic() - normalization_started
            values = (*self._future_realizations, value)
            self._future_realizations = values
            self._boundaries = boundaries
            self._input_frontier = value.end_timestamp
            self._work_produced += len(new_boundaries)
            known_at = monotonic()
            for boundary in new_boundaries:
                self._boundary_known_at[boundary.nominal_time] = known_at
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
                newly_normalized_boundary_count=len(new_boundaries),
                newly_normalized_operation_count=sum(
                    len(boundary.operations) for boundary in new_boundaries
                ),
                newly_inspected_constraint_count=sum(
                    boundary.inspected_constraint_count
                    for boundary in new_boundaries
                ),
                supplier_seconds=supplier_seconds,
                normalization_seconds=normalization_seconds,
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

    def _record_failure(self, phase: str, error: BaseException, **facts: Any) -> None:
        self._record_resource_health_checkpoint(f"failure-{phase}")
        self._record(
            "failure",
            phase=phase,
            error_type=type(error).__name__,
            error=str(error),
            **facts,
        )

    def _record_resource_health_checkpoint(self, checkpoint: str) -> None:
        """Capture sparse process and owned-resource facts without subprocesses."""

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
        try:
            runtime_working_set = (
                {} if runtime is None else runtime.working_set_counts()
            )
        except BaseException as error:
            runtime_working_set = {
                "capture_error_type": type(error).__name__,
                "capture_error": str(error),
            }
        self._record(
            "resource_health_checkpoint",
            checkpoint=checkpoint,
            runner_rss_bytes=rss_bytes,
            process_count=1,
            thread_count=thread_count,
            helper_owned=self._helper is not None,
            runtime_owned=runtime is not None,
            runtime_working_set=runtime_working_set,
        )

    def _raise_maintenance_error(self) -> None:
        if self._maintenance_error is not None:
            raise RuntimeError("BpfRunner Maintenance failed") from self._maintenance_error

    def _maintenance_facts_locked(self) -> dict[str, object]:
        pending_boundaries = max(0, len(self._boundaries) - self._next_prepare_index)
        pending_times = (
            boundary.nominal_time
            for boundary in self._boundaries[self._next_prepare_index :]
        )
        oldest_known = min(
            (
                self._boundary_known_at[value]
                for value in pending_times
                if value in self._boundary_known_at
            ),
            default=None,
        )
        capacity = (
            {}
            if self._runtime_realization is None
            else self._runtime_realization.generation_capacity_status()
        )
        return {
            "work_produced": self._work_produced,
            "work_submitted": self._work_submitted,
            "work_completed": self._work_completed,
            "required_frontier": self._iso(
                None if not self._boundaries else self._boundaries[0].nominal_time
            ),
            "ready_frontier": self._iso(self._ready_frontier),
            "ready_depth": len(self._ready),
            "pending_boundary_count": pending_boundaries,
            "queue_depth": (
                pending_boundaries
                + len(self._reclaim_forwarding)
                + len(self._reclaim_physical)
            ),
            "oldest_pending_age_seconds": (
                None if oldest_known is None else max(0.0, monotonic() - oldest_known)
            ),
            "generation_reclaim_backlog": len(self._reclaim_forwarding),
            "physical_reclaim_backlog": len(self._reclaim_physical),
            "generations_reclaimed": self._generations_reclaimed,
            "resources_retired": self._resources_retired,
            "resources_reclaimed": self._resources_reclaimed,
            "input_exhausted": self._input_exhausted,
            "resident_generations": capacity.get("resident_generations"),
            "resident_generation_capacity": capacity.get(
                "resident_generation_capacity"
            ),
        }

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
        container_ids = tuple(
            value.strip()
            for value in result.stdout.splitlines()
            if value.strip()
        )
        if not container_ids:
            raise RuntimeError(
                "current Compose project has no containers; "
                "start the generated deployment first"
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
                if entity_type == "Satellite":
                    node_id = NodeId(asn, "satellite", labels[_SATELLITE_ID_LABEL])
                else:
                    node_id = NodeId(asn, "ground-station", labels[_GROUND_STATION_ID_LABEL])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("Skyseed entity container labels are incomplete") from error
            if node_id in seen_node_ids:
                raise RuntimeError(f"duplicate live binding for {node_id.value}")
            seen_node_ids.add(node_id)
            candidates.append(
                (
                    node_id,
                    service_name,
                    value["Id"],
                    value["Name"].removeprefix("/"),
                    value["Image"],
                    pid,
                    state.get("StartedAt", ""),
                )
            )
        container_ids = [value[2] for value in candidates]
        worker_count = min(self._runtime_worker_count, max(1, len(container_ids)))
        if worker_count == 1:
            netns_inodes = [
                self._container_netns_inode(container_id)
                for container_id in container_ids
            ]
        else:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="skyseed-binding",
            ) as pool:
                netns_inodes = list(
                    pool.map(self._container_netns_inode, container_ids)
                )
        bindings = {
            node_id: _RuntimeBinding(
                node_id=node_id,
                service_name=service_name,
                container_id=container_id,
                container_name=container_name,
                image_id=image_id,
                pid=pid,
                netns_inode=netns_inode,
                started_at=started_at,
            )
            for (
                node_id,
                service_name,
                container_id,
                container_name,
                image_id,
                pid,
                started_at,
            ), netns_inode in zip(candidates, netns_inodes)
        }
        expected = set(first._physical_world_projection.node_ids)
        if set(bindings) != expected:
            missing = sorted(value.value for value in expected - set(bindings))
            unexpected = sorted(value.value for value in set(bindings) - expected)
            raise RuntimeError(
                "live NodeId bindings do not match compiled PhysicalWorld: "
                f"missing={missing}, unexpected={unexpected}"
            )
        return bindings

    def _assert_foundation_current(self) -> None:
        if not self._bindings or self._helper is None:
            raise RuntimeError("BpfRunner has no frozen runtime foundation")
        current = {
            value["Id"]: value
            for value in self._inspect(
                tuple(
                    binding.container_id
                    for binding in self._bindings.values()
                )
            )
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
                raise RuntimeError(
                    "frozen container incarnation is stale: "
                    f"{binding.node_id.value}"
                )
        # The immediately following activation preflight validates every frozen
        # PID/netns pair before its first mutation. Repeating that same audit in
        # a separate privileged process here adds no foundation evidence.

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
