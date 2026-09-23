"""Detach self-contained Plans and compiled realization values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from skyseed.backend.bpf import BpfCompilationResult
from skyseed.experiment.output.history import (
    _iter_verified_domain_history_stream,
    _read_domain_history_manifest,
)
from skyseed.simulator import Plan, GroundStationRouteDemandTarget, SatelliteRouteDemandTarget, ZoneRouteDemandTarget
from skyseed.simulator.planner import (
    plan_to_data,
    plan_transition_to_data,
    planned_operational_state_to_data,
)


@dataclass(frozen=True, slots=True)
class BpfCompilerInspectionRead:
    """Hold only detached scalar/container data read from formal values."""

    snapshot: dict[str, Any]


def read_bpf_compiler_history(path: Path) -> dict[str, Any]:
    """Read one terminal compiler-through domain-history lineage stream."""

    manifest = _read_domain_history_manifest(
        Path(path), minimum_through="compiler"
    )
    rows = _iter_verified_domain_history_stream(manifest, "compiler")
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError("Compiler evidence stream is empty") from error
    legacy_header = {
        "record_type": "compiled_realization_history_header",
        "format_version": 1,
        "semantic_payload_owner": "canonical realization handoff",
    }
    compact_header = {
        "record_type": "compiled_realization_history_header",
        "format_version": 3,
        "semantic_payload_owner": "sealed Runner artifact",
        "projection": "compact-exact-compiler-inspection-v2",
    }
    if header not in (legacy_header, compact_header):
        raise ValueError("Compiler evidence header is invalid")
    realizations = tuple(rows)
    if not realizations:
        raise ValueError("Compiler evidence contains no realization")
    previous = None
    world_fingerprint = None
    for index, row in enumerate(realizations):
        _validate_history_record(
            row, index, compact=header["format_version"] == 3
        )
        if index == 0:
            if (
                row["source_previous_plan_id"] is not None
                or row["input_state_fingerprint"] is not None
            ):
                raise ValueError("first Compiler evidence must be bootstrap")
            world_fingerprint = row["physical_world_fingerprint"]
        else:
            assert previous is not None
            if row["source_previous_plan_id"] != previous["source_plan_id"]:
                raise ValueError("Compiler evidence Plan lineage is not continuous")
            if (
                row["input_state_fingerprint"]
                != previous["output_state_fingerprint"]
            ):
                raise ValueError(
                    "Compiler input_state_fingerprint differs from previous output"
                )
            if row["physical_world_fingerprint"] != world_fingerprint:
                raise ValueError("Compiler evidence mixes PhysicalWorld provenance")
            if _history_timestamp(row["start_timestamp"]) <= _history_timestamp(
                previous["end_timestamp"]
            ):
                raise ValueError("Compiler evidence timestamp coverage overlaps")
        previous = row
    return {"header": header, "realizations": realizations}


_HISTORY_FIELDS = {
    "record_type",
    "realization_index",
    "source_plan_id",
    "source_previous_plan_id",
    "physical_world_fingerprint",
    "input_state_fingerprint",
    "output_state_fingerprint",
    "start_timestamp",
    "end_timestamp",
    "activation_timestamps",
    "intent_counts",
    "semantic_precedence_folding",
}
_COMPACT_HISTORY_FIELDS = {
    "compact_summary",
    "operation_counts",
    "boundary_workloads",
    "intended_state_timestamp",
    "zone_prefixes",
}
_INTENT_COUNT_FIELDS = {
    "resource",
    "parameter",
    "forwarding",
    "zone_edge",
    "constraints",
}


def _validate_history_record(
    row: dict[str, Any], index: int, *, compact: bool = False
) -> None:
    expected = _HISTORY_FIELDS | (_COMPACT_HISTORY_FIELDS if compact else set())
    if set(row) != expected:
        raise ValueError("Compiler evidence row has unknown fields")
    if (
        row["record_type"] != "compiled_realization"
        or row["realization_index"] != index
    ):
        raise ValueError("Compiler evidence lineage is not continuous")
    for name in (
        "source_plan_id",
        "physical_world_fingerprint",
        "output_state_fingerprint",
    ):
        _history_string(row[name], name)
    for name in ("source_previous_plan_id", "input_state_fingerprint"):
        value = row[name]
        if value is not None:
            _history_string(value, name)
    if (row["source_previous_plan_id"] is None) != (
        row["input_state_fingerprint"] is None
    ):
        raise ValueError(
            "Compiler evidence previous Plan and input state are all-or-none"
        )
    start = _history_timestamp(row["start_timestamp"])
    end = _history_timestamp(row["end_timestamp"])
    if end < start:
        raise ValueError("Compiler evidence end precedes start")
    activation_values = row["activation_timestamps"]
    if not isinstance(activation_values, list) or not activation_values:
        raise ValueError("Compiler evidence requires activation timestamps")
    activations = tuple(_history_timestamp(value) for value in activation_values)
    if activations != tuple(sorted(set(activations))):
        raise ValueError("Compiler activation timestamps are not ordered and unique")
    if activations[0] != start or activations[-1] != end:
        raise ValueError("Compiler activation timestamps differ from coverage")
    intent_counts = row["intent_counts"]
    if not isinstance(intent_counts, dict) or set(intent_counts) != _INTENT_COUNT_FIELDS:
        raise ValueError("Compiler evidence intent counts are malformed")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in intent_counts.values()
    ):
        raise ValueError("Compiler evidence intent counts must be non-negative")
    folding = row["semantic_precedence_folding"]
    if not isinstance(folding, dict) or any(
        not isinstance(name, str)
        or not name
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        for name, count in folding.items()
    ):
        raise ValueError("Compiler semantic precedence folding is malformed")
    if compact:
        _validate_compact_history_record(row)


def _validate_compact_history_record(row: dict[str, Any]) -> None:
    summary = row["compact_summary"]
    if not isinstance(summary, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in summary.values()
    ):
        raise ValueError("Compiler compact summary is malformed")
    operations = row["operation_counts"]
    if not isinstance(operations, dict) or any(
        not isinstance(counts, dict)
        or any(
            not isinstance(name, str)
            or not name
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for name, count in counts.items()
        )
        for counts in operations.values()
    ):
        raise ValueError("Compiler compact operation counts are malformed")
    boundaries = row["boundary_workloads"]
    if not isinstance(boundaries, list) or len(boundaries) != len(
        row["activation_timestamps"]
    ):
        raise ValueError("Compiler compact boundary workloads are malformed")
    if [value.get("timestamp") for value in boundaries] != row[
        "activation_timestamps"
    ]:
        raise ValueError("Compiler compact boundary timestamps differ")
    _history_timestamp(row["intended_state_timestamp"])
    prefixes = row["zone_prefixes"]
    if not isinstance(prefixes, list) or any(
        not isinstance(value, dict)
        or set(value) != {"zone_id", "network_scope", "packet_prefix"}
        or not all(isinstance(item, str) and (key == "network_scope" or item) for key, item in value.items())
        for value in prefixes
    ):
        raise ValueError("Compiler compact Zone prefixes are malformed")


def read_compact_bpf_compiler_evidence(
    compiler_history_path: Path,
) -> dict[str, Any]:
    """Read the complete compact Compiler history without runtime payload access."""

    history = read_bpf_compiler_history(compiler_history_path)
    if history["header"]["format_version"] != 3:
        raise ValueError(
            "legacy Compiler history cannot be projected from the Runner artifact; "
            "regenerate the bounded experiment"
        )
    return history

def _history_timestamp(value: object) -> datetime:
    text = _history_string(value, "timestamp")
    try:
        timestamp = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("Compiler evidence timestamp is invalid") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Compiler evidence timestamp must be timezone-aware")
    return timestamp


def _history_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Compiler evidence {name} must be a non-empty string")
    return value


def read_bpf_compiler_inspection(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompilationResult, ...],
) -> BpfCompilerInspectionRead:
    """Read an aligned continuous Plan/realization sequence without compiling."""

    plan_values = tuple(plans)
    realization_values = tuple(realizations)
    if not plan_values or any(not isinstance(value, Plan) for value in plan_values):
        raise TypeError("plans must contain at least one Plan")
    if len(realization_values) != len(plan_values) or any(
        not isinstance(value, BpfCompilationResult)
        for value in realization_values
    ):
        raise TypeError(
            "realizations must contain one BpfCompilationResult per Plan"
        )
    _require_aligned_sequence(plan_values, realization_values)
    return BpfCompilerInspectionRead(
        {
            "format_version": 2,
            "tool": "bpf-compiler-inspection",
            "forwarding_backend": realization_values[0].forwarding_backend,
            "authority": {
                "status": "non-authoritative-view",
                "plans": "immutable self-contained Plan publications",
                "compiled": "already-produced CompilationResult values",
                "compiler": "not invoked by this Tool",
                "planned_state": (
                    "intended semantic state, not realized runtime state"
                ),
            },
            "realizations": tuple(
                _read_realization(index, plan, realization)
                for index, (plan, realization) in enumerate(
                    zip(plan_values, realization_values, strict=True)
                )
            ),
        }
    )


def _require_aligned_sequence(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompilationResult, ...],
) -> None:
    world = plans[0].physical_world_fingerprint
    backend = realizations[0].forwarding_backend
    for index, (plan, realization) in enumerate(
        zip(plans, realizations, strict=True)
    ):
        if plan.physical_world_fingerprint != world:
            raise ValueError("Plan sequence mixes PhysicalWorld provenance")
        if realization.forwarding_backend != backend:
            raise ValueError("Compiler sequence mixes forwarding backends")
        if realization.source_plan_id != plan.plan_id:
            raise ValueError("realization source Plan does not match")
        for field_name in (
            "source_previous_plan_id",
            "physical_world_fingerprint",
            "input_state_fingerprint",
            "output_state_fingerprint",
            "start_timestamp",
            "end_timestamp",
        ):
            plan_name = (
                "previous_plan_id"
                if field_name == "source_previous_plan_id"
                else field_name
            )
            if getattr(realization, field_name) != getattr(plan, plan_name):
                raise ValueError(
                    f"realization {field_name} differs from source Plan"
                )
        if tuple(realization.activation_timestamps) != tuple(
            value.timestamp for value in plan.transitions
        ):
            raise ValueError("compiled activation boundaries differ from Plan")
        if index == 0:
            if plan.previous_plan_id is not None:
                raise ValueError("first Plan/realization must be bootstrap")
        else:
            previous_plan = plans[index - 1]
            previous_result = realizations[index - 1]
            if plan.previous_plan_id != previous_plan.plan_id:
                raise ValueError("Plan lineage is not continuous")
            if plan.input_state_fingerprint != previous_result.output_state_fingerprint:
                raise ValueError("realization semantic-state lineage is not continuous")


def _read_realization(
    index: int, plan: Plan, realization: BpfCompilationResult
) -> dict[str, object]:
    return {
        "realization_index": index,
        "source_plan": plan_to_data(plan),
        "lineage": {
            "source_plan_id": realization.source_plan_id,
            "source_previous_plan_id": realization.source_previous_plan_id,
            "physical_world_fingerprint": realization.physical_world_fingerprint,
            "input_state_fingerprint": realization.input_state_fingerprint,
            "output_state_fingerprint": realization.output_state_fingerprint,
            "start_timestamp": realization.start_timestamp.isoformat(),
            "end_timestamp": realization.end_timestamp.isoformat(),
        },
        "activation_boundaries": tuple(
            {
                "timestamp": boundary.timestamp.isoformat(),
                "transition_fingerprint": boundary.transition_fingerprint,
                "transition": plan_transition_to_data(transition),
            }
            for transition, boundary in zip(
                plan.transitions,
                realization.compiled_activation_boundaries,
                strict=True,
            )
        ),
        "compiled_intents": {
            "resources": tuple(
                _resource_intent(value)
                for value in realization.resource_realizations
            ),
            "parameters": tuple(
                _parameter_intent(value)
                for value in realization.parameter_realizations
            ),
            "forwarding": tuple(
                _forwarding_intent(value)
                for value in realization.forwarding_realizations
            ),
            "zone_edges": tuple(
                _zone_edge_intent(value)
                for value in realization.zone_edge_realizations
            ),
            "constraints": tuple(
                _constraint(value) for value in realization.constraints
            ),
            "semantic_precedence_folding": tuple(
                {"mode": mode, "count": count}
                for mode, count in realization.semantic_precedence_folding
            ),
        },
        "intended_state": {
            "semantic": planned_operational_state_to_data(
                realization.output_planned_state
            ),
            "resources": tuple(
                {"connection": _connection(value.connection)}
                for value in realization.intended_resources
            ),
            "parameters": tuple(
                {
                    "connection_id": _connection_id(value.connection_id),
                    "source": _node(value.source),
                    "target": _node(value.target),
                    "parameters": _parameters(value.parameters),
                }
                for value in realization.intended_parameters
            ),
            "forwarding": tuple(
                _binding(value) for value in realization.intended_forwarding
            ),
            "zone_edges": tuple(
                {
                    "zone_id": value.zone_id,
                "network_scope": value.network_scope,
                    "packet_prefix": value.packet_prefix,
                    "service_satellite_id": _node(value.service_satellite_id),
                }
                for value in realization.intended_zone_edges
            ),
            "zone_prefixes": tuple(
                {"zone_id": zone_id[1], "network_scope": zone_id[0], "packet_prefix": packet_prefix}
                for zone_id, packet_prefix in realization.zone_prefixes
            ),
        },
    }


def _resource_intent(value: object) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "connection_id": _connection_id(value.connection_id),
        "attachments": tuple(
            {
                "source": _node(item.source),
                "target": _node(item.target),
            }
            for item in value.attachments
        ),
    }


def _parameter_intent(value: object) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "connection_id": _connection_id(value.connection_id),
        "source": _node(value.source),
        "target": _node(value.target),
        "reason": value.reason,
        "parameters": {
            "bandwidth_bps": value.bandwidth_bps,
            "delay_us": value.delay_us,
            "packet_loss_ppm": value.packet_loss_ppm,
        },
    }


def _forwarding_intent(value: object) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "route_key": _forwarding_key(value.route_key),
        "previous": None if value.previous is None else _binding(value.previous),
        "current": None if value.current is None else _binding(value.current),
    }


def _zone_edge_intent(value: object) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "phase": value.phase.value,
        "zone_id": value.zone_id,
                "network_scope": value.network_scope,
        "packet_prefix": value.packet_prefix,
        "previous_service_satellite_id": (
            None
            if value.previous_service_satellite_id is None
            else _node(value.previous_service_satellite_id)
        ),
        "service_satellite_id": (
            None
            if value.service_satellite_id is None
            else _node(value.service_satellite_id)
        ),
    }


def _constraint(value: object) -> dict[str, object]:
    return {
        "predecessor": {
            "domain": value.predecessor.domain,
            "index": value.predecessor.index,
        },
        "successor": {
            "domain": value.successor.domain,
            "index": value.successor.index,
        },
        "reason": value.reason,
    }


def _binding(value: object) -> dict[str, object]:
    return {
        "route_key": _forwarding_key(value.route_key),
        "next_hop": None if value.next_hop is None else _node(value.next_hop),
        "connection_id": None if value.connection_id is None else _connection_id(value.connection_id),
        "packet_prefix": value.packet_prefix,
    }


def _forwarding_key(value: object) -> dict[str, object]:
    return {"source": _node(value.source), "target": _route_target(value.target)}


def _route_target(value: object) -> dict[str, object]:
    if isinstance(value, GroundStationRouteDemandTarget):
        return {
            "kind": "ground-station",
            "network_scope": value.network_scope,
            "ground_station_id": _node(value.ground_station_id),
        }
    if isinstance(value, SatelliteRouteDemandTarget):
        return {"kind": "satellite", "satellite_id": _node(value.satellite_id), "network_scope": value.network_scope}
    if isinstance(value, ZoneRouteDemandTarget):
        return {"kind": "zone", "zone_id": value.zone_id, "network_scope": value.network_scope}
    raise TypeError("unsupported Route demand target")


def _connection(value: object) -> dict[str, object]:
    return {
        "connection_id": _connection_id(value.connection_id),
        "endpoint_a_to_b": _parameters(value.endpoint_a_to_b),
        "endpoint_b_to_a": _parameters(value.endpoint_b_to_a),
    }


def _connection_id(value: object) -> dict[str, object]:
    return {
        "kind": value.kind.value,
        "endpoint_a": _node(value.endpoint_a),
        "endpoint_b": _node(value.endpoint_b),
    }


def _parameters(value: object) -> dict[str, int]:
    return {
        "bandwidth_bps": value.bandwidth_bps,
        "delay_us": value.delay_us,
        "packet_loss_ppm": value.packet_loss_ppm,
    }


def _node(value: object) -> dict[str, object]:
    return {
        "asn": value.asn,
        "entity_kind": value.entity_kind,
        "entity_id": value.entity_id,
    }


__all__ = [
    "BpfCompilerInspectionRead",
    "read_compact_bpf_compiler_evidence",
    "read_bpf_compiler_history",
    "read_bpf_compiler_inspection",
]
