"""Read formal Slicer and Planner results into one detached Tool snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from pathlib import Path

from skyseed_v2.simulator import (
    Connection,
    ConnectionId,
    ConnectionTransition,
    ForwardingTransition,
    GroundStationRouteTarget,
    ParameterTransition,
    Plan,
    PlanningDecision,
    RouteKey,
    Slicer,
    ZoneRouteTarget,
)

from tools.slice.reader import read_slice_differences
from tools._history import read_jsonl_records


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


def read_planner_inspection(
    slicer: Slicer,
    plans: tuple[Plan, ...],
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
    if not isinstance(configuration, Mapping):
        raise TypeError("configuration must be a Mapping")
    _require_continuous_plans(plan_values)

    slice_evidence = read_slice_differences(slicer).snapshot
    slices = tuple(
        {
            "slice_id": value["slice_id"],
            "timestamp": value["timestamp"],
            "positions": deepcopy(value["positions"]),
            "connections": deepcopy(value["connections"]),
            "routes": _expand_routes(value["routes"]),
            "paths": _restore_paths_for_planner(value),
            "difference_from_previous": _expand_difference(
                value["difference_from_previous"]
            ),
        }
        for value in slice_evidence["slices"]
    )
    return PlannerInspectionRead(
        {
            "format_version": 1,
            "tool": "planner-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "slicer": "retained public Slicer results and Differences",
                "plans": "immutable public Plan publications",
                "decisions": "Planner.get_planning_decisions result",
                "models": "not invoked by this Tool",
            },
            "configuration": deepcopy(dict(configuration)),
            "timeline": deepcopy(slice_evidence["timeline"]),
            "topology": {
                "nodes": deepcopy(slice_evidence["topology"]["nodes"]),
                "orbits": deepcopy(slice_evidence["topology"]["orbits"]),
                "zones": deepcopy(slice_evidence["topology"]["zones"]),
            },
            "slices": slices,
            "plans": tuple(_read_plan(index, plan) for index, plan in enumerate(plan_values)),
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


def _restore_paths_for_planner(
    slice_record: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """Preserve the existing Planner Tool view from compact Slice reads."""

    routes = tuple(slice_record["routes"])
    assignments = {
        str(value["zone_id"]): str(value["service_satellite_id"])
        for value in slice_record["zone_service_assignments"]
    }
    next_hops = {
        (str(source), str(target_reference)): str(next_hop)
        for source, target_reference, next_hop in routes
    }
    paths = []
    for source, target_reference, _next_hop in routes:
        target = _expand_target(target_reference)
        terminal = (
            str(target["ground_station_id"])
            if target["kind"] == "G"
            else assignments.get(str(target["zone_id"]))
        )
        if terminal is None:
            continue
        nodes = [str(source)]
        seen = {str(source)}
        current = str(source)
        while current != terminal:
            next_value = next_hops.get((current, str(target_reference)))
            if next_value is None or next_value in seen:
                nodes = []
                break
            nodes.append(next_value)
            seen.add(next_value)
            current = next_value
        if nodes:
            paths.append(
                {
                    "path_id": f"{source}|{target_reference}",
                    "source": source,
                    "target": target,
                    "nodes": nodes,
                    "hop_count": len(nodes) - 1,
                }
            )
    return tuple(paths)


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
        "label": f"Plan {index + 1}",
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
    "read_planner_inspection",
]
