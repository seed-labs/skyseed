"""Project compiled realizations into compact trends and focused drill-down."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from .reader import BpfCompilerInspectionRead


def project_bpf_compiler_data(
    observation: BpfCompilerInspectionRead,
    *,
    compile_evidence: Iterable[Mapping[str, object]] = (),
    observation_timing: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Summarize exact compiled values without retaining a giant raw dump."""

    if not isinstance(observation, BpfCompilerInspectionRead):
        raise TypeError("observation must be a BpfCompilerInspectionRead")
    timings = tuple(deepcopy(dict(value)) for value in compile_evidence)
    if not timings and observation_timing is not None:
        samples = observation_timing.get("compile_samples", ())
        if isinstance(samples, (tuple, list)):
            timings = tuple(deepcopy(dict(value)) for value in samples)
    if any(value.get("compile_index") != index for index, value in enumerate(timings)):
        raise ValueError("compile_evidence must be continuous and ordered")
    source = observation.snapshot
    raw_realizations = source["realizations"]
    if timings and len(timings) != len(raw_realizations):
        raise ValueError("compile_evidence must align one-to-one with realizations")

    compact = []
    total_reachable = 0
    total_unreachable = 0
    for index, realization in enumerate(raw_realizations):
        source_plan = realization["source_plan"]
        intents = realization["compiled_intents"]
        state = realization["intended_state"]
        semantic = state["semantic"]
        routes = semantic["route_outcomes"]
        reachable = sum(value["kind"] == "reachable" for value in routes)
        unreachable = len(routes) - reachable
        total_reachable += reachable
        total_unreachable += unreachable
        transition_changes = sum(
            sum(len(transition[name]) for name in _TRANSITION_FIELDS)
            for transition in source_plan["transitions"]
        )
        operation_counts = _operation_counts(intents)
        delta_counts = _delta_counts(operation_counts)
        boundary_workloads = _boundary_workloads(
            realization["activation_boundaries"], intents
        )
        summary = {
            "activation_boundaries": len(realization["activation_boundaries"]),
            "plan_transition_changes": transition_changes,
            "semantic_precedences": len(
                source_plan["semantic_event_precedences"]
            ),
            "resource_intents": len(intents["resources"]),
            "parameter_intents": len(intents["parameters"]),
            "forwarding_intents": len(intents["forwarding"]),
            "zone_edge_intents": len(intents["zone_edges"]),
            "constraints": len(intents["constraints"]),
            "intended_connections": len(
                semantic["operational_connections"]
            ),
            "intended_resources": len(state["resources"]),
            "intended_parameters": len(state["parameters"]),
            "intended_forwarding": len(state["forwarding"]),
            "intended_zone_edges": len(state["zone_edges"]),
            "intended_zones": len(semantic["zone_service_assignments"]),
            "planned_routes_reachable": reachable,
            "planned_routes_unreachable": unreachable,
        }
        compact.append(
            {
                "realization_index": index,
                "lineage": deepcopy(realization["lineage"]),
                "activation_timestamps": tuple(
                    value["timestamp"]
                    for value in realization["activation_boundaries"]
                ),
                "summary": summary,
                "operation_counts": operation_counts,
                "delta_counts": delta_counts,
                "semantic_precedence_folding": deepcopy(
                    intents["semantic_precedence_folding"]
                ),
                "boundary_workloads": boundary_workloads,
                "intended_state_timestamp": semantic["timestamp"],
                "zone_prefixes": deepcopy(state["zone_prefixes"]),
                "compile_evidence": timings[index] if timings else None,
            }
        )

    inventory = {
        "plans": len(compact),
        "realizations": len(compact),
        "activation_boundaries": sum(
            value["summary"]["activation_boundaries"] for value in compact
        ),
        "resource_intents": _summary_sum(compact, "resource_intents"),
        "parameter_intents": _summary_sum(compact, "parameter_intents"),
        "forwarding_intents": _summary_sum(compact, "forwarding_intents"),
        "zone_edge_intents": _summary_sum(compact, "zone_edge_intents"),
        "constraints": _summary_sum(compact, "constraints"),
        "planned_routes_reachable_samples": total_reachable,
        "planned_routes_unreachable_samples": total_unreachable,
    }
    return {
        "format_version": 3,
        "tool": "bpf-compiler-inspection",
        "authority": deepcopy(source["authority"]),
        "inventory": inventory,
        "realizations": tuple(compact),
        "trend": tuple(
            {
                "realization_index": value["realization_index"],
                "source_plan_id": value["lineage"]["source_plan_id"],
                **value["summary"],
                "operations": value["operation_counts"],
                "delta_counts": value["delta_counts"],
            }
            for value in compact
        ),
        "compiler_timing": (
            None if observation_timing is None else deepcopy(observation_timing)
        ),
        "traffic_contract": {
            "description": (
                "compact lineage, activation timestamps and stable Zone "
                "prefixes consumed by the detached Traffic Tool"
            ),
            "realization_count": len(compact),
        },
        "interpretation": {
            "plan": "self-contained semantic delta and lineage authority",
            "compiled": "backend intended operations and activation boundaries",
            "intended_state": (
                "cardinalities and stable Zone prefixes; not live runtime state"
            ),
            "realized": (
                "not claimed by this Tool; Runner evidence owns realized facts"
            ),
            "raw_detail": (
                "primary artifacts intentionally retain aggregate operations, "
                "per-boundary workloads, folding and cardinalities instead of "
                "duplicating every Plan and intended-state object"
            ),
        },
    }


def project_compact_bpf_compiler_data(
    history: Mapping[str, object],
    *,
    observation_timing: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Project producer-owned compact evidence without Plan/handoff objects."""

    raw_rows = history.get("realizations")
    if not isinstance(raw_rows, (tuple, list)) or not raw_rows:
        raise ValueError("compact Compiler history has no realizations")
    samples = {
        int(value["compile_index"]): deepcopy(dict(value))
        for value in (
            ()
            if observation_timing is None
            else observation_timing.get("compile_samples", ())
        )
    }
    compact = []
    activation_timeline = []
    for index, raw in enumerate(raw_rows):
        row = dict(raw)
        summary = deepcopy(row["compact_summary"])
        operations = deepcopy(row["operation_counts"])
        boundary_workloads = deepcopy(row["boundary_workloads"])
        lineage = {
            "source_plan_id": row["source_plan_id"],
            "source_previous_plan_id": row["source_previous_plan_id"],
            "physical_world_fingerprint": row[
                "physical_world_fingerprint"
            ],
            "input_state_fingerprint": row["input_state_fingerprint"],
            "output_state_fingerprint": row["output_state_fingerprint"],
            "start_timestamp": row["start_timestamp"],
            "end_timestamp": row["end_timestamp"],
        }
        compact.append(
            {
                "realization_index": index,
                "lineage": lineage,
                "activation_timestamps": tuple(row["activation_timestamps"]),
                "summary": summary,
                "operation_counts": operations,
                "delta_counts": _delta_counts(operations),
                "semantic_precedence_folding": deepcopy(
                    row["semantic_precedence_folding"]
                ),
                "boundary_workloads": boundary_workloads,
                "intended_state_timestamp": row[
                    "intended_state_timestamp"
                ],
                "zone_prefixes": deepcopy(row["zone_prefixes"]),
                "compile_evidence": samples.get(index),
                "projection_note": row.get("legacy_missing_plan_projection"),
            }
        )
        activation_timeline.extend(
            {
                "realization_index": index,
                "boundary_index": boundary_index,
                **deepcopy(boundary),
            }
            for boundary_index, boundary in enumerate(boundary_workloads)
        )
    inventory = {
        "plans": len(compact),
        "realizations": len(compact),
        "activation_boundaries": sum(
            value["summary"]["activation_boundaries"] for value in compact
        ),
        "resource_intents": _summary_sum(compact, "resource_intents"),
        "parameter_intents": _summary_sum(compact, "parameter_intents"),
        "forwarding_intents": _summary_sum(compact, "forwarding_intents"),
        "zone_edge_intents": _summary_sum(compact, "zone_edge_intents"),
        "constraints": _summary_sum(compact, "constraints"),
        "planned_routes_reachable_samples": _summary_sum(
            compact, "planned_routes_reachable"
        ),
        "planned_routes_unreachable_samples": _summary_sum(
            compact, "planned_routes_unreachable"
        ),
    }
    return {
        "format_version": 4,
        "tool": "bpf-compiler-inspection",
        "authority": {
            "status": "non-authoritative-view",
            "source": "producer-owned compact exact Compiler evidence",
            "compiler": "not invoked by this Tool",
            "handoff": (
                "normal compact-evidence Tool path does not materialize "
                "the runtime handoff"
            ),
            "planned_state": "intended semantic state, not runtime state",
        },
        "inventory": inventory,
        "realizations": tuple(compact),
        "trend": tuple(
            {
                "realization_index": value["realization_index"],
                "source_plan_id": value["lineage"]["source_plan_id"],
                **value["summary"],
                "operations": value["operation_counts"],
                "delta_counts": value["delta_counts"],
            }
            for value in compact
        ),
        "activation_timeline": tuple(activation_timeline),
        "compiler_timing": (
            None if observation_timing is None else deepcopy(observation_timing)
        ),
        "traffic_contract": {
            "description": "compact Compiler evidence; no runtime authority",
            "realization_count": len(compact),
        },
        "interpretation": {
            "plan": "lineage and exact transition-derived aggregate evidence",
            "compiled": "backend operation counts and activation workloads",
            "intended_state": "exact cardinalities and stable Zone prefixes",
            "realized": "Runner evidence alone owns realized facts",
            "raw_detail": (
                "The Tool consumes compact producer evidence and does not "
                "materialize the persisted realization sequence."
            ),
        },
    }


_TRANSITION_FIELDS = (
    "established_connections",
    "withdrawn_connection_ids",
    "parameter_updated_connections",
    "zone_service_transitions",
    "route_outcome_transitions",
)


def _operation_counts(intents: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for family in ("resources", "forwarding", "zone_edges"):
        counts = Counter(str(value["operation"]) for value in intents[family])
        result[family] = dict(sorted(counts.items()))
    result["parameters"] = {"update": len(intents["parameters"])}
    result["constraints"] = {"precedence": len(intents["constraints"])}
    return result


def _delta_counts(
    operations: Mapping[str, Mapping[str, int]]
) -> dict[str, int]:
    """Normalize exact backend verbs into display add/update/remove buckets."""

    result = {"add": 0, "update": 0, "remove": 0}
    for counts in operations.values():
        for operation, count in counts.items():
            if operation in {"add", "establish"}:
                result["add"] += count
            elif operation in {"change", "update"}:
                result["update"] += count
            elif operation in {"remove", "withdraw"}:
                result["remove"] += count
    return result


def _boundary_workloads(
    boundaries: list[dict[str, Any]], intents: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    by_time: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for family in ("resources", "forwarding", "zone_edges"):
        for value in intents[family]:
            by_time[value["nominal_time"]][family][
                str(value["operation"])
            ] += 1
    for value in intents["parameters"]:
        by_time[value["nominal_time"]]["parameters"]["update"] += 1
    rows = []
    for boundary in boundaries:
        timestamp = boundary["timestamp"]
        operations = {
            family: dict(sorted(counts.items()))
            for family, counts in sorted(by_time[timestamp].items())
        }
        rows.append(
            {
                "timestamp": timestamp,
                "transition_fingerprint": boundary["transition_fingerprint"],
                "operations": operations,
                "delta_counts": _delta_counts(operations),
                "operation_count": sum(
                    sum(counts.values()) for counts in by_time[timestamp].values()
                ),
            }
        )
    return tuple(rows)


def _summary_sum(values: list[dict[str, Any]], name: str) -> int:
    return sum(int(value["summary"][name]) for value in values)


__all__ = [
    "project_bpf_compiler_data",
    "project_compact_bpf_compiler_data",
]
