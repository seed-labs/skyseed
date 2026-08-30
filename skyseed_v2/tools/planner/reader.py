"""Read formal Slicer and Planner results into one detached Tool snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from pathlib import Path

from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionTransition,
    ConnectionTransitionOperation,
    DifferenceOperation,
    ForwardingTransition,
    ForwardingTransitionPrecedence,
    GroundStationRouteTarget,
    ParameterTransition,
    Plan,
    PlanningDecision,
    RouteKey,
    Slicer,
    TopologyDifference,
    ZoneRouteTarget,
)
from skyseed_v2.simulator._history_codec import (
    _difference_from_data,
    _planning_decision_from_data,
)

from tools.slice.reader import (
    _read_durable_slice_values,
    _read_connection,
    _read_difference,
    _read_route,
    _read_static_nodes,
    _read_zones,
    _connection_from_semantic,
    _connection_id_from_semantic,
    _mapping,
    _node_id_from_semantic,
    _route_target_from_semantic,
    _sequence,
)
from tools._history import iter_jsonl_records, read_jsonl_records


@dataclass(frozen=True, slots=True)
class PlannerInspectionRead:
    """Hold only Tool-owned scalars and containers read from formal values."""

    snapshot: dict[str, Any]


def read_planner_history(path: Path) -> dict[str, Any]:
    """Read complete durable Planner publications after working release."""

    records = read_jsonl_records(path)
    headers = tuple(
        value for value in records if value.get("record_type") == "history_header"
    )
    if len(headers) != 1 or headers[0].get("owner") != "Planner":
        raise ValueError("durable Planner history header is invalid")
    return {
        "header": headers[0],
        "differences": tuple(
            value
            for value in records
            if value.get("record_type") == "difference_consumed"
        ),
        "plans": tuple(
            value for value in records if value.get("record_type") == "plan_published"
        ),
        "decisions": tuple(
            value for value in records if value.get("record_type") == "planning_decision"
        ),
    }


def read_durable_plan_sequence(path: Path) -> tuple[Plan, ...]:
    """Reconstruct exact immutable publications from canonical raw JSONL."""

    plans = tuple(
        _plan_from_semantic(value["semantic"])
        for value in iter_jsonl_records(path)
        if value.get("record_type") == "plan_published"
    )
    _require_continuous_plans(plans)
    return plans


def _plan_from_semantic(value: object) -> Plan:
    semantic = _mapping(value, "Plan")
    connections = tuple(
        _connection_transition_from_semantic(item)
        for item in _sequence(
            semantic["connection_transitions"], "connection transitions"
        )
    )
    parameters = tuple(
        _parameter_transition_from_semantic(item)
        for item in _sequence(
            semantic["parameter_transitions"], "parameter transitions"
        )
    )
    forwarding = tuple(
        _forwarding_transition_from_semantic(item)
        for item in _sequence(
            semantic["forwarding_transitions"], "forwarding transitions"
        )
    )
    by_value = {value: value for value in forwarding}
    precedences = []
    for item in _sequence(
        semantic["forwarding_precedences"], "forwarding precedences"
    ):
        precedence = _mapping(item, "ForwardingTransitionPrecedence")
        predecessor = _forwarding_transition_from_semantic(
            precedence["predecessor"]
        )
        successor = _forwarding_transition_from_semantic(precedence["successor"])
        try:
            precedences.append(
                ForwardingTransitionPrecedence(
                    by_value[predecessor],
                    by_value[successor],
                )
            )
        except KeyError as error:
            raise ValueError(
                "durable Plan precedence does not reference its forwarding values"
            ) from error
    return Plan(
        start_slice_id=int(semantic["start_slice_id"]),
        start_timestamp=_optional_semantic_datetime(semantic["start_timestamp"]),
        end_slice_id=int(semantic["end_slice_id"]),
        end_timestamp=_semantic_datetime(semantic["end_timestamp"]),
        connection_transitions=connections,
        parameter_transitions=parameters,
        forwarding_transitions=forwarding,
        forwarding_precedences=tuple(precedences),
    )


def _connection_transition_from_semantic(value: object) -> ConnectionTransition:
    semantic = _mapping(value, "ConnectionTransition")
    operation = _mapping(semantic["operation"], "ConnectionTransitionOperation")
    return ConnectionTransition(
        nominal_time=_semantic_datetime(semantic["nominal_time"]),
        operation=ConnectionTransitionOperation(str(operation["value"])),
        connection_id=_connection_id_from_semantic(semantic["connection_id"]),
        connection=(
            None
            if semantic["connection"] is None
            else _connection_from_semantic(semantic["connection"])
        ),
    )


def _parameter_transition_from_semantic(value: object) -> ParameterTransition:
    semantic = _mapping(value, "ParameterTransition")
    direction = _mapping(semantic["direction"], "ConnectionDirection")
    return ParameterTransition(
        nominal_time=_semantic_datetime(semantic["nominal_time"]),
        connection_id=_connection_id_from_semantic(semantic["connection_id"]),
        direction=ConnectionDirection(str(direction["value"])),
        bandwidth_bps=_optional_int(semantic["bandwidth_bps"]),
        delay_us=_optional_int(semantic["delay_us"]),
        packet_loss_ppm=_optional_int(semantic["packet_loss_ppm"]),
    )


def _forwarding_transition_from_semantic(value: object) -> ForwardingTransition:
    semantic = _mapping(value, "ForwardingTransition")
    route_key = _mapping(semantic["route_key"], "RouteKey")
    operation = _mapping(semantic["operation"], "DifferenceOperation")
    return ForwardingTransition(
        nominal_time=_semantic_datetime(semantic["nominal_time"]),
        route_key=RouteKey(
            _node_id_from_semantic(route_key["source"]),
            _route_target_from_semantic(route_key["target"]),
        ),
        operation=DifferenceOperation(str(operation["value"])),
        next_hop=(
            None
            if semantic["next_hop"] is None
            else _node_id_from_semantic(semantic["next_hop"])
        ),
    )


def _semantic_datetime(value: object) -> datetime:
    semantic = _mapping(value, "datetime")
    if semantic.get("$type") != "datetime":
        raise ValueError("durable datetime marker is invalid")
    return datetime.fromisoformat(str(semantic["value"]))


def _optional_semantic_datetime(value: object) -> datetime | None:
    return None if value is None else _semantic_datetime(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def read_planner_inspection(
    slicer: Slicer,
    plans: tuple[Plan, ...],
    differences: tuple[TopologyDifference, ...],
    decisions: tuple[PlanningDecision, ...],
    configuration: Mapping[str, object],
) -> PlannerInspectionRead:
    """Detach exact Slicer, Plan, and PlanningDecision public results."""

    if not isinstance(slicer, Slicer):
        raise TypeError("slicer must be a Slicer")
    plan_values = tuple(plans)
    if not plan_values or any(not isinstance(value, Plan) for value in plan_values):
        raise TypeError("plans must contain at least one Plan")
    decision_values = tuple(decisions)
    if any(not isinstance(value, PlanningDecision) for value in decision_values):
        raise TypeError("decisions must contain PlanningDecision values")
    difference_values = tuple(differences)
    if any(
        not isinstance(value, TopologyDifference)
        for value in difference_values
    ):
        raise TypeError("differences must contain TopologyDifference values")
    if not isinstance(configuration, Mapping):
        raise TypeError("configuration must be a Mapping")
    _require_continuous_plans(plan_values)
    retained_ids = slicer.get_retained_slice_ids()
    if retained_ids != tuple(range(retained_ids[-1] + 1)):
        raise ValueError("retained Slice IDs must form one continuous prefix")
    if tuple(value.current_slice_id for value in difference_values) != tuple(
        retained_ids[1:]
    ):
        raise ValueError(
            "exact consumed Differences must align with retained real Slices"
        )
    difference_by_slice_id = {
        value.current_slice_id: value for value in difference_values
    }
    topology = slicer.get_topology()
    timeline = slicer.get_timeline()
    nodes, orbits = _read_static_nodes(topology)
    zones, _ = _read_zones(topology)
    slices = tuple(
        _read_planner_slice(
            slicer,
            slice_id,
            difference_by_slice_id.get(slice_id),
        )
        for slice_id in retained_ids
    )
    return PlannerInspectionRead(
        {
            "format_version": 1,
            "tool": "planner-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "slicer": "retained public Slicer results",
                "differences": "exact caller-provided Planner-consumed Differences",
                "plans": "immutable public Plan publications",
                "decisions": "Planner.get_planning_decisions result",
                "models": "not invoked by this Tool",
            },
            "configuration": deepcopy(dict(configuration)),
            "timeline": {
                "retained_slice_ids": retained_ids,
                "slice_count_including_zero": len(retained_ids),
                "real_slice_count": len(retained_ids) - 1,
                "artifact_end_time": next(
                    (
                        str(value["timestamp"])
                        for value in reversed(slices)
                        if value["timestamp"] is not None
                    ),
                    timeline.start_time.isoformat(),
                ),
                "start_time": timeline.start_time.isoformat(),
                "end_time": (
                    None
                    if timeline.end_time is None
                    else timeline.end_time.isoformat()
                ),
                "slice_interval_seconds": timeline.slice_interval.total_seconds(),
            },
            "topology": {"nodes": nodes, "orbits": orbits, "zones": zones},
            "slices": slices,
            "plans": tuple(
                _read_plan(index, plan) for index, plan in enumerate(plan_values)
            ),
            "decisions": tuple(_read_decision(value) for value in decision_values),
            "inspection_limits": {
                "rejected_suppression_candidates": (
                    "not exposed by the current public Planner inspection surface"
                ),
                "tuple_order": "deterministic representation, not execution order",
                "precedence": (
                    "semantic same-time relation, not backend/runtime scheduling"
                ),
            },
        }
    )


def read_durable_planner_inspection(
    slice_history_path: Path,
    planner_history_path: Path,
    plans: tuple[Plan, ...],
    configuration: Mapping[str, object],
    *,
    topology: object,
    timeline: object,
) -> PlannerInspectionRead:
    """Read released exact histories without advancing Slicer or Planner."""

    plan_values = tuple(plans)
    if not plan_values or any(not isinstance(value, Plan) for value in plan_values):
        raise TypeError("plans must contain at least one Plan")
    _require_continuous_plans(plan_values)
    differences = []
    decisions = []
    for value in iter_jsonl_records(planner_history_path):
        if value.get("record_type") == "difference_consumed":
            differences.append(_difference_from_data(value["semantic"]))
        elif value.get("record_type") == "planning_decision":
            decisions.append(_planning_decision_from_data(value["semantic"]))
    differences = tuple(differences)
    decisions = tuple(decisions)
    from tools.slice.reader import read_slice_history

    slice_snapshot = _read_durable_slice_values(
        read_slice_history(slice_history_path),
        {value.current_slice_id: value for value in differences},
        topology=topology,
        timeline=timeline,
    ).snapshot
    retained_ids = tuple(slice_snapshot["timeline"]["retained_slice_ids"])
    if tuple(value.current_slice_id for value in differences) != retained_ids[1:]:
        raise ValueError("durable Planner Differences do not align with Slices")
    slices = tuple(
        {
            **value,
            "difference_from_previous": _expand_difference(
                value["difference_from_previous"]
            ),
        }
        for value in slice_snapshot["slices"]
    )
    timeline_snapshot = deepcopy(slice_snapshot["timeline"])
    timeline_snapshot["artifact_end_time"] = next(
        (
            str(value["timestamp"])
            for value in reversed(slices)
            if value["timestamp"] is not None
        ),
        timeline_snapshot["start_time"],
    )
    return PlannerInspectionRead(
        {
            "format_version": 1,
            "tool": "planner-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "slicer": "complete durable semantic Slicer history",
                "differences": "complete durable Planner-consumed Differences",
                "plans": "immutable persisted Plan publications",
                "decisions": "complete durable Planner decisions",
                "models": "not invoked by this Tool",
            },
            "configuration": deepcopy(dict(configuration)),
            "timeline": timeline_snapshot,
            "topology": deepcopy(slice_snapshot["topology"]),
            "slices": slices,
            "plans": tuple(
                _read_plan(index, plan) for index, plan in enumerate(plan_values)
            ),
            "decisions": tuple(_read_decision(value) for value in decisions),
            "inspection_limits": {
                "rejected_suppression_candidates": (
                    "not exposed by the current public Planner inspection surface"
                ),
                "tuple_order": "deterministic representation, not execution order",
                "precedence": (
                    "semantic same-time relation, not backend/runtime scheduling"
                ),
            },
        }
    )
def _read_planner_slice(
    slicer: Slicer,
    slice_id: int,
    difference: TopologyDifference | None,
) -> dict[str, object]:
    value = slicer.get_slice(slice_id)
    return {
        "slice_id": value.slice_id,
        "timestamp": (
            None if value.timestamp is None else value.timestamp.isoformat()
        ),
        "positions": tuple(
            {
                "node_id": node_id.value,
                "ecef_m": {
                    "x": position.x_m,
                    "y": position.y_m,
                    "z": position.z_m,
                },
            }
            for node_id, position in value.positions.items()
        ),
        "connections": tuple(
            _read_connection(connection)
            for connection in value.connection_slice.connections
        ),
        "zone_service_assignments": tuple(
            {
                "zone_id": zone_id,
                "service_satellite_id": satellite_id.value,
            }
            for zone_id, satellite_id in (
                value.connection_slice.zone_service_assignments.items()
            )
        ),
        "routes": tuple(
            _read_route(RouteKey(source, target), next_hop)
            for source, targets in value.route_slice.routes.items()
            for target, next_hop in targets.items()
        ),
        "difference_from_previous": (
            None
            if difference is None
            else _expand_difference(_read_difference(difference))
        ),
    }


def _expand_routes(
    routes: tuple[tuple[str, str, str], ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "route_ref": f"{source}|{target_reference}",
            "source": source,
            "target": _expand_target(target_reference),
            "next_hop": next_hop,
        }
        for source, target_reference, next_hop in routes
    )


def _expand_difference(
    difference: object,
) -> dict[str, object] | None:
    if difference is None:
        return None
    assert isinstance(difference, dict)
    result = deepcopy(difference)
    result["route_patches"] = tuple(
        {
            "route_ref": f"{source}|{target_reference}",
            "source": source,
            "target": _expand_target(target_reference),
            "operation": operation,
            "next_hop": next_hop,
        }
        for operation, source, target_reference, next_hop in difference[
            "route_patches"
        ]
    )
    return result


def _expand_target(reference: str) -> dict[str, str]:
    kind, identity = reference.split("|", 1)
    if kind == "G":
        return {"kind": "G", "ground_station_id": identity}
    if kind == "Z":
        return {"kind": "Z", "zone_id": identity}
    raise ValueError("compact Slice route target is invalid")


def _read_plan(index: int, plan: Plan) -> dict[str, object]:
    connection_values = tuple(
        _read_connection_transition(index, position, value)
        for position, value in enumerate(plan.connection_transitions)
    )
    parameter_values = tuple(
        _read_parameter_transition(index, position, value)
        for position, value in enumerate(plan.parameter_transitions)
    )
    forwarding_values = tuple(
        _read_forwarding_transition(index, position, value)
        for position, value in enumerate(plan.forwarding_transitions)
    )
    identifiers = {
        id(value): record["transition_id"]
        for values, records in (
            (plan.connection_transitions, connection_values),
            (plan.parameter_transitions, parameter_values),
            (plan.forwarding_transitions, forwarding_values),
        )
        for value, record in zip(values, records, strict=True)
    }
    temporal_groups = tuple(
        {
            "nominal_time": timestamp.isoformat(),
            "transition_ids": tuple(identifiers[id(value)] for value in values),
        }
        for timestamp, values in plan.temporal_index.items()
    )
    precedences = tuple(
        {
            "predecessor": identifiers[id(value.predecessor)],
            "successor": identifiers[id(value.successor)],
            "nominal_time": value.predecessor.nominal_time.isoformat(),
        }
        for value in plan.forwarding_precedences
    )
    return {
        "plan_index": index,
        "label": f"P{index}",
        "start_slice_id": plan.start_slice_id,
        "start_timestamp": (
            None if plan.start_timestamp is None else plan.start_timestamp.isoformat()
        ),
        "end_slice_id": plan.end_slice_id,
        "end_timestamp": plan.end_timestamp.isoformat(),
        "coverage": f"({plan.start_slice_id},{plan.end_slice_id}]",
        "connection_transitions": connection_values,
        "parameter_transitions": parameter_values,
        "forwarding_transitions": forwarding_values,
        "forwarding_precedences": precedences,
        "temporal_groups": temporal_groups,
    }


def _read_connection_transition(
    plan_index: int,
    position: int,
    value: ConnectionTransition,
) -> dict[str, object]:
    return {
        "transition_id": f"p{plan_index}-c{position}",
        "family": "connection",
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "connection_ref": _connection_reference(value.connection_id),
        "connection_id": _connection_id_data(value.connection_id),
        "connection": (
            None if value.connection is None else _connection_data(value.connection)
        ),
    }


def _read_parameter_transition(
    plan_index: int,
    position: int,
    value: ParameterTransition,
) -> dict[str, object]:
    return {
        "transition_id": f"p{plan_index}-p{position}",
        "family": "parameter",
        "nominal_time": value.nominal_time.isoformat(),
        "connection_ref": _connection_reference(value.connection_id),
        "connection_id": _connection_id_data(value.connection_id),
        "direction": value.direction.value,
        "targets": {
            key: target
            for key, target in (
                ("bandwidth_bps", value.bandwidth_bps),
                ("delay_us", value.delay_us),
                ("packet_loss_ppm", value.packet_loss_ppm),
            )
            if target is not None
        },
    }


def _read_forwarding_transition(
    plan_index: int,
    position: int,
    value: ForwardingTransition,
) -> dict[str, object]:
    return {
        "transition_id": f"p{plan_index}-f{position}",
        "family": "forwarding",
        "nominal_time": value.nominal_time.isoformat(),
        "route_ref": _route_reference(value.route_key),
        "source": value.route_key.source.value,
        "target": _target_data(value.route_key),
        "operation": value.operation.value,
        "next_hop": None if value.next_hop is None else value.next_hop.value,
    }


def _read_decision(value: PlanningDecision) -> dict[str, object]:
    return {
        "source_difference_ids": value.source_difference_ids,
        "decision": value.decision,
        "decided_by": value.decided_by,
        "reason": value.reason,
    }


def _connection_data(value: Connection) -> dict[str, object]:
    return {
        "connection_ref": _connection_reference(value.connection_id),
        "connection_id": _connection_id_data(value.connection_id),
        "endpoint_a_to_b": _parameters_data(value.endpoint_a_to_b),
        "endpoint_b_to_a": _parameters_data(value.endpoint_b_to_a),
    }


def _connection_id_data(value: ConnectionId) -> dict[str, str]:
    return {
        "kind": value.kind.value,
        "endpoint_a": value.endpoint_a.value,
        "endpoint_b": value.endpoint_b.value,
    }


def _parameters_data(value: object) -> dict[str, int]:
    return {
        "bandwidth_bps": value.bandwidth_bps,
        "delay_us": value.delay_us,
        "packet_loss_ppm": value.packet_loss_ppm,
    }


def _connection_reference(value: ConnectionId) -> str:
    return "|".join(
        (value.kind.value, value.endpoint_a.value, value.endpoint_b.value)
    )


def _route_reference(value: RouteKey) -> str:
    target = value.target
    suffix = (
        f"G|{target.ground_station_id.value}"
        if isinstance(target, GroundStationRouteTarget)
        else f"Z|{target.zone_id}"
    )
    return f"{value.source.value}|{suffix}"


def _target_data(value: RouteKey) -> dict[str, str]:
    target = value.target
    if isinstance(target, GroundStationRouteTarget):
        return {"kind": "G", "ground_station_id": target.ground_station_id.value}
    if isinstance(target, ZoneRouteTarget):
        return {"kind": "Z", "zone_id": target.zone_id}
    raise TypeError("unsupported RouteTarget")


def _require_continuous_plans(plans: tuple[Plan, ...]) -> None:
    for previous, current in zip(plans, plans[1:]):
        if (
            previous.end_slice_id != current.start_slice_id
            or previous.end_timestamp != current.start_timestamp
        ):
            raise ValueError("plans must have continuous non-overlapping coverage")


__all__ = [
    "PlannerInspectionRead",
    "read_planner_history",
    "read_durable_plan_sequence",
    "read_planner_inspection",
    "read_durable_planner_inspection",
]
