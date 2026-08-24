"""Detach Plan and compiled realization values into Tool-owned primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skyseed_v2.backend.bpf import BpfCompiledRealization
from skyseed_v2.simulator import (
    GroundStationRouteTarget,
    Plan,
    ZoneRouteTarget,
)
from tools._history import read_jsonl_records


@dataclass(frozen=True, slots=True)
class BpfCompilerInspectionRead:
    """Hold only detached scalar/container data read from formal values."""

    snapshot: dict[str, Any]


def read_bpf_compiler_history(path: Path) -> dict[str, Any]:
    """Read complete one-shot Compiler handoffs from durable JSONL."""

    records = read_jsonl_records(path)
    headers = tuple(
        value for value in records if value.get("record_type") == "history_header"
    )
    if len(headers) != 1 or headers[0].get("owner") != "BPF Compiler handoff":
        raise ValueError("durable BPF Compiler history header is invalid")
    realizations = tuple(
        value
        for value in records
        if value.get("record_type") == "compiled_realization"
    )
    if tuple(value.get("realization_index") for value in realizations) != tuple(
        range(len(realizations))
    ):
        raise ValueError("durable compiled realization history is not continuous")
    return {"header": headers[0], "realizations": realizations}


def read_bpf_compiler_inspection(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
) -> BpfCompilerInspectionRead:
    """Read an aligned continuous Plan/compiled sequence without compiling."""

    plan_values = tuple(plans)
    realization_values = tuple(realizations)
    if not plan_values or any(not isinstance(value, Plan) for value in plan_values):
        raise TypeError("plans must contain at least one Plan")
    if len(realization_values) != len(plan_values) or any(
        not isinstance(value, BpfCompiledRealization)
        for value in realization_values
    ):
        raise TypeError(
            "realizations must contain one BpfCompiledRealization per Plan"
        )
    _require_aligned_continuous_sequence(plan_values, realization_values)
    return BpfCompilerInspectionRead(
        {
            "format_version": 1,
            "tool": "bpf-compiler-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "plans": "immutable public Plan publications",
                "compiled": "already-produced BpfCompiledRealization values",
                "compiler": "not invoked by this Tool",
                "simulator": "not queried or advanced by this Tool",
                "runtime": "not bound or inferred by this Tool",
            },
            "intervals": tuple(
                _read_interval(index, plan, realization)
                for index, (plan, realization) in enumerate(
                    zip(plan_values, realization_values, strict=True)
                )
            ),
            "inspection_limits": {
                "zone_origin": (
                    "Zone edges are compiled mechanical output; no exact Plan "
                    "transition provenance is claimed"
                ),
                "runtime": (
                    "Concrete runtime and kernel binding facts are outside "
                    "this symbolic result"
                ),
                "order": (
                    "Only explicit constraints express order; tuple position "
                    "is deterministic representation"
                ),
            },
        }
    )


def _read_interval(
    index: int,
    plan: Plan,
    realization: BpfCompiledRealization,
) -> dict[str, object]:
    plan_connections = tuple(
        {
            "transition_id": f"p{index}-plan-connection-{position}",
            "nominal_time": value.nominal_time.isoformat(),
            "operation": value.operation.value,
            "connection_ref": _connection_ref(value.connection_id),
            "connection_id": _connection_id(value.connection_id),
        }
        for position, value in enumerate(plan.connection_transitions)
    )
    plan_parameters = tuple(
        {
            "transition_id": f"p{index}-plan-parameter-{position}",
            "nominal_time": value.nominal_time.isoformat(),
            "connection_ref": _connection_ref(value.connection_id),
            "connection_id": _connection_id(value.connection_id),
            "direction": value.direction.value,
            "targets": _sparse_parameters(value),
        }
        for position, value in enumerate(plan.parameter_transitions)
    )
    plan_forwarding = tuple(
        {
            "transition_id": f"p{index}-plan-forwarding-{position}",
            "nominal_time": value.nominal_time.isoformat(),
            "operation": value.operation.value,
            "route_ref": _route_ref(value.route_key),
            "route_key": _route_key(value.route_key),
            "next_hop": (
                None if value.next_hop is None else value.next_hop.value
            ),
        }
        for position, value in enumerate(plan.forwarding_transitions)
    )
    connection_origins = {
        (value["nominal_time"], value["operation"], value["connection_ref"]): (
            value["transition_id"]
        )
        for value in plan_connections
    }
    parameter_origins = {
        (value["nominal_time"], value["connection_ref"], value["direction"]): (
            value["transition_id"]
        )
        for value in plan_parameters
    }
    forwarding_origins = {
        (value["nominal_time"], value["operation"], value["route_ref"]): (
            value["transition_id"]
        )
        for value in plan_forwarding
    }

    resources = tuple(
        {
            "intent_id": f"p{index}-resource-{position}",
            "origin_transition_id": connection_origins.get(
                (
                    value.nominal_time.isoformat(),
                    value.operation.value,
                    _connection_ref(value.connection_id),
                )
            ),
            "nominal_time": value.nominal_time.isoformat(),
            "operation": value.operation.value,
            "connection_ref": _connection_ref(value.connection_id),
            "connection_id": _connection_id(value.connection_id),
            "attachments": tuple(
                {
                    "direction": attachment.direction.value,
                    "source": attachment.source.value,
                    "target": attachment.target.value,
                }
                for attachment in value.attachments
            ),
        }
        for position, value in enumerate(realization.resource_realizations)
    )
    parameters = tuple(
        {
            "intent_id": f"p{index}-parameter-{position}",
            "origin_transition_id": (
                connection_origins.get(
                    (
                        value.nominal_time.isoformat(),
                        "establish",
                        _connection_ref(value.connection_id),
                    )
                )
                if value.reason == "connection-establish"
                else parameter_origins.get(
                    (
                        value.nominal_time.isoformat(),
                        _connection_ref(value.connection_id),
                        value.direction.value,
                    )
                )
            ),
            "nominal_time": value.nominal_time.isoformat(),
            "connection_ref": _connection_ref(value.connection_id),
            "connection_id": _connection_id(value.connection_id),
            "direction": value.direction.value,
            "source": value.source.value,
            "target": value.target.value,
            "reason": value.reason,
            "targets": {
                "bandwidth_bps": value.bandwidth_bps,
                "delay_us": value.delay_us,
                "packet_loss_ppm": value.packet_loss_ppm,
            },
        }
        for position, value in enumerate(realization.parameter_realizations)
    )
    forwarding = tuple(
        {
            "intent_id": f"p{index}-forwarding-{position}",
            "origin_transition_id": forwarding_origins.get(
                (
                    value.nominal_time.isoformat(),
                    value.operation.value,
                    _route_ref(value.route_key),
                )
            ),
            "nominal_time": value.nominal_time.isoformat(),
            "operation": value.operation.value,
            "route_ref": _route_ref(value.route_key),
            "route_key": _route_key(value.route_key),
            "previous": _binding(value.previous),
            "current": _binding(value.current),
            "same_next_hop_changed": bool(
                value.operation.value == "changed"
                and value.previous is not None
                and value.current is not None
                and value.previous.next_hop == value.current.next_hop
            ),
        }
        for position, value in enumerate(realization.forwarding_realizations)
    )
    zone_edges = tuple(
        {
            "intent_id": f"p{index}-zone-edge-{position}",
            "nominal_time": value.nominal_time.isoformat(),
            "operation": value.operation.value,
            "zone_id": value.zone_id,
            "packet_prefix": value.packet_prefix,
            "previous_service_satellite": (
                None
                if value.previous_service_satellite_id is None
                else value.previous_service_satellite_id.value
            ),
            "service_satellite": (
                None
                if value.service_satellite_id is None
                else value.service_satellite_id.value
            ),
            "origin": "compiled-mechanical-zone-edge",
        }
        for position, value in enumerate(realization.zone_edge_realizations)
    )
    constraints = tuple(
        {
            "constraint_id": f"p{index}-constraint-{position}",
            "category": (
                "plan-forwarding-precedence"
                if value.reason == "plan-forwarding-precedence"
                else "backend-technical"
            ),
            "reason": value.reason,
            "predecessor": {
                "domain": value.predecessor.domain,
                "index": value.predecessor.index,
                "intent_id": _intent_id(
                    index,
                    value.predecessor.domain,
                    value.predecessor.index,
                ),
            },
            "successor": {
                "domain": value.successor.domain,
                "index": value.successor.index,
                "intent_id": _intent_id(
                    index,
                    value.successor.domain,
                    value.successor.index,
                ),
            },
        }
        for position, value in enumerate(realization.constraints)
    )
    return {
        "plan_index": index,
        "label": f"Plan {index + 1}",
        "coverage": f"({plan.start_slice_id},{plan.end_slice_id}]",
        "start_slice_id": plan.start_slice_id,
        "start_timestamp": (
            None
            if plan.start_timestamp is None
            else plan.start_timestamp.isoformat()
        ),
        "end_slice_id": plan.end_slice_id,
        "end_timestamp": plan.end_timestamp.isoformat(),
        "plan": {
            "connection_transitions": plan_connections,
            "parameter_transitions": plan_parameters,
            "forwarding_transitions": plan_forwarding,
            "forwarding_precedence_count": len(plan.forwarding_precedences),
        },
        "compiled": {
            "resources": resources,
            "parameters": parameters,
            "forwarding": forwarding,
            "zone_edges": zone_edges,
            "constraints": constraints,
        },
        "end_state": {
            "resources": tuple(
                {
                    "connection_ref": _connection_ref(value.connection.connection_id),
                    "connection_id": _connection_id(value.connection.connection_id),
                }
                for value in realization.intended_resources
            ),
            "parameters": tuple(
                {
                    "connection_ref": _connection_ref(value.connection_id),
                    "connection_id": _connection_id(value.connection_id),
                    "direction": value.direction.value,
                    "targets": _full_parameters(value.parameters),
                }
                for value in realization.intended_parameters
            ),
            "forwarding": tuple(
                _binding(value) for value in realization.intended_forwarding
            ),
            "zone_edges": tuple(
                {
                    "zone_id": value.zone_id,
                    "packet_prefix": value.packet_prefix,
                    "service_satellite": value.service_satellite_id.value,
                }
                for value in realization.intended_zone_edges
            ),
            "zone_prefixes": tuple(
                {"zone_id": zone_id, "packet_prefix": prefix}
                for zone_id, prefix in realization.zone_prefixes
            ),
        },
    }


def _intent_id(plan_index: int, domain: str, index: int) -> str:
    return f"p{plan_index}-{domain}-{index}"


def _connection_ref(value: object) -> str:
    return "|".join(
        (
            value.kind.value,
            value.endpoint_a.value,
            value.endpoint_b.value,
        )
    )


def _connection_id(value: object) -> dict[str, str]:
    return {
        "kind": value.kind.value,
        "endpoint_a": value.endpoint_a.value,
        "endpoint_b": value.endpoint_b.value,
    }


def _route_ref(value: object) -> str:
    target = value.target
    suffix = (
        f"G|{target.ground_station_id.value}"
        if isinstance(target, GroundStationRouteTarget)
        else f"Z|{target.zone_id}"
    )
    return f"{value.source.value}|{suffix}"


def _route_key(value: object) -> dict[str, object]:
    target = value.target
    if isinstance(target, GroundStationRouteTarget):
        target_data = {
            "kind": "ground-station",
            "ground_station_id": target.ground_station_id.value,
        }
    elif isinstance(target, ZoneRouteTarget):
        target_data = {"kind": "zone", "zone_id": target.zone_id}
    else:  # pragma: no cover - RouteKey enforces this
        raise TypeError("unsupported RouteTarget")
    return {"source": value.source.value, "target": target_data}


def _binding(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "route_ref": _route_ref(value.route_key),
        "route_key": _route_key(value.route_key),
        "next_hop": value.next_hop.value,
        "connection_ref": _connection_ref(value.connection_id),
        "connection_id": _connection_id(value.connection_id),
        "direction": value.direction.value,
        "packet_prefix": value.packet_prefix,
    }


def _sparse_parameters(value: object) -> dict[str, int]:
    return {
        field: target
        for field in ("bandwidth_bps", "delay_us", "packet_loss_ppm")
        if (target := getattr(value, field)) is not None
    }


def _full_parameters(value: object) -> dict[str, int]:
    return {
        "bandwidth_bps": value.bandwidth_bps,
        "delay_us": value.delay_us,
        "packet_loss_ppm": value.packet_loss_ppm,
    }


def _require_aligned_continuous_sequence(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
) -> None:
    for plan, realization in zip(plans, realizations, strict=True):
        if (
            plan.start_slice_id != realization.start_slice_id
            or plan.start_timestamp != realization.start_timestamp
            or plan.end_slice_id != realization.end_slice_id
            or plan.end_timestamp != realization.end_timestamp
        ):
            raise ValueError("compiled realization coverage does not match Plan")
    for previous, current in zip(realizations, realizations[1:]):
        if (
            previous.end_slice_id != current.start_slice_id
            or previous.end_timestamp != current.start_timestamp
        ):
            raise ValueError("compiled realizations must have continuous coverage")


__all__ = [
    "BpfCompilerInspectionRead",
    "read_bpf_compiler_history",
    "read_bpf_compiler_inspection",
]
