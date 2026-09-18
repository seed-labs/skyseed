"""Normalize compiled actions into deterministic ordered delivery boundaries."""

from __future__ import annotations

from .actions import _intent_phase

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import heapq
from typing import TypeAlias
from skyseed_v2.simulator import GroundStationRouteDemandTarget, ZoneRouteDemandTarget
from skyseed_v2.backend.bpf.execution_materials.actions import (
    _ForwardingIntent,
    _PhysicalWorldProjection,
    _QdiscIntent,
    _ResourceIntent,
    _ZoneEdgeIntent,
)


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
    results: tuple[BpfCompilationResult, ...],
    *,
    realization_index_offset: int = 0,
) -> dict[_Reference, _BoundaryOperation]:
    result: dict[_Reference, _BoundaryOperation] = {}
    for realization_index, compilation_result in enumerate(
        results, start=realization_index_offset
    ):
        for domain, attribute in _DOMAIN_ATTRIBUTES:
            for index, intent in enumerate(getattr(compilation_result, attribute)):
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
        target_key = ("zone", target.service_key)
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
            intent.service_key,
            intent.phase.value,
            intent.operation.value,
        )
    else:  # pragma: no cover - inventory is closed above
        raise TypeError("unknown compiled boundary operation")
    phase = _intent_phase(intent)
    return (phase, _DOMAIN_RANK[operation.domain], *semantic)


def _operation_label(operation: _BoundaryOperation) -> str:
    return "|".join(
        (
            operation.intent.nominal_time.isoformat(),
            *(str(value) for value in _stable_operation_key(operation)),
        )
    )


def _resolve_nominal_boundaries(
    results: tuple[BpfCompilationResult, ...],
    *,
    realization_index_offset: int = 0,
) -> tuple[_NominalBoundary, ...]:
    """Normalize compiled work into dependency-ordered Runner boundaries."""

    if not results:
        raise ValueError("nominal boundary resolution requires compiled work")
    inventory = _operation_inventory(
        results, realization_index_offset=realization_index_offset
    )
    declarations: dict[datetime, int] = {}
    for realization_index, compilation_result in enumerate(
        results, start=realization_index_offset
    ):
        timestamps = compilation_result.activation_timestamps
        if not timestamps:
            raise ValueError("compilation result has no activation boundaries")
        timestamp_set = set(timestamps)
        if timestamps != tuple(sorted(timestamp_set)):
            raise ValueError(
                "compiled activation boundaries must be unique and ordered"
            )
        if (
            timestamps[0] != compilation_result.start_timestamp
            or timestamps[-1] != compilation_result.end_timestamp
        ):
            raise ValueError(
                "compiled activation boundaries differ from result interval"
            )
        for timestamp in timestamps:
            if timestamp in declarations:
                raise ValueError(
                    "compilation results duplicate a formal activation boundary"
                )
            declarations[timestamp] = realization_index
        if any(
            intent.nominal_time not in timestamp_set
            for attribute in (
                "resource_realizations",
                "parameter_realizations",
                "forwarding_realizations",
                "zone_edge_realizations",
            )
            for intent in getattr(compilation_result, attribute)
        ):
            raise ValueError(
                "compiled operation has no declared activation boundary"
            )
    nominal_times = tuple(sorted(declarations))
    operations_by_time = {timestamp: {} for timestamp in nominal_times}
    constraints_by_time = {timestamp: [] for timestamp in nominal_times}
    outgoing_by_time = {timestamp: 0 for timestamp in nominal_times}
    for reference, operation in inventory.items():
        operations_by_time[operation.intent.nominal_time][reference] = operation
    inspected = 0
    for realization_index, compilation_result in enumerate(
        results, start=realization_index_offset
    ):
        for constraint in compilation_result.constraints:
            inspected += 1
            predecessor = (realization_index, constraint.predecessor.domain,
                           constraint.predecessor.index)
            successor = (realization_index, constraint.successor.domain,
                         constraint.successor.index)
            try:
                predecessor_time = inventory[predecessor].intent.nominal_time
                successor_time = inventory[successor].intent.nominal_time
            except KeyError as error:
                raise ValueError(
                    "compiled constraint references an unknown operation"
                ) from error
            if predecessor_time > successor_time:
                message = (
                    "nominal boundary depends on later compiled work"
                    if successor_time == nominal_times[0]
                    else "compiled constraint reverses nominal time"
                )
                raise ValueError(message)
            if predecessor_time == successor_time:
                constraints_by_time[predecessor_time].append(
                    (predecessor, successor, constraint.reason)
                )
            else:
                outgoing_by_time[predecessor_time] += 1
    return tuple(
        _resolve_nominal_boundary(
            results,
            selected=operations_by_time[nominal_time],
            constraints=constraints_by_time[nominal_time],
            inspected=inspected,
            outgoing=outgoing_by_time[nominal_time],
            nominal_time=nominal_time,
            declared_realization_index=declarations[nominal_time],
            realization_index_offset=realization_index_offset,
        )
        for nominal_time in nominal_times
    )


def _resolve_nominal_boundary(
    results: tuple[BpfCompilationResult, ...],
    *,
    selected: dict[_Reference, _BoundaryOperation],
    constraints: list[tuple[_Reference, _Reference, str]],
    inspected: int,
    outgoing: int,
    nominal_time: datetime,
    declared_realization_index: int,
    realization_index_offset: int = 0,
) -> _NominalBoundary:
    if any(
        operation.realization_index != declared_realization_index
        for operation in selected.values()
    ):
        raise ValueError("activation boundary contains foreign result work")
    source_constraint_count = len(
        results[declared_realization_index - realization_index_offset].constraints
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
    applied = 0
    # Preserve the established evidence counts, including duplicate constraints
    # and constraints belonging to other boundaries, without inspecting them again.
    later = inspected - len(constraints) - outgoing
    reasons: dict[str, int] = {}
    constraint_edges: list[tuple[_Reference, _Reference, str]] = []
    for predecessor, successor, reason in constraints:
        if successor not in successors[predecessor]:
            successors[predecessor].add(successor)
            indegree[successor] += 1
            applied += 1
            reasons[reason] = reasons.get(reason, 0) + 1
            constraint_edges.append((predecessor, successor, reason))

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
    projection = results[
        declared_realization_index - realization_index_offset
    ]._physical_world_projection
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
                results[index - realization_index_offset].source_plan_id,
                results[
                    index - realization_index_offset
                ].start_timestamp.isoformat(),
                results[
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
