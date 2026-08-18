"""Project detached BPF Compiler reads into human-inspection view data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .reader import BpfCompilerInspectionRead


def project_bpf_compiler_data(
    observation: BpfCompilerInspectionRead,
) -> dict[str, Any]:
    """Build summaries and exact Plan-to-intent relationships only."""

    if not isinstance(observation, BpfCompilerInspectionRead):
        raise TypeError("observation must be a BpfCompilerInspectionRead")
    data = deepcopy(observation.snapshot)
    intervals = data["intervals"]
    assert isinstance(intervals, tuple)
    nodes = set()
    zones = set()
    for interval in intervals:
        _collect_identities(interval, nodes, zones)
        interval["summary"] = _interval_summary(interval)
        interval["lowering"] = _lowering_rows(interval)
        interval["constraint_groups"] = _constraint_groups(interval)
    data["display_aliases"] = {
        "nodes": {value: _node_alias(value) for value in sorted(nodes)},
        "zones": {value: f"Z:{value}" for value in sorted(zones)},
    }
    data["inventory"] = {
        "plans": len(intervals),
        "resource_intents": sum(
            len(value["compiled"]["resources"]) for value in intervals
        ),
        "parameter_intents": sum(
            len(value["compiled"]["parameters"]) for value in intervals
        ),
        "forwarding_intents": sum(
            len(value["compiled"]["forwarding"]) for value in intervals
        ),
        "zone_edge_intents": sum(
            len(value["compiled"]["zone_edges"]) for value in intervals
        ),
        "constraints": sum(
            len(value["compiled"]["constraints"]) for value in intervals
        ),
    }
    data["observed_behavior"] = {
        "same_next_hop_changed": sum(
            intent["same_next_hop_changed"]
            for interval in intervals
            for intent in interval["compiled"]["forwarding"]
        ),
        "plan_forwarding_precedences": sum(
            constraint["category"] == "plan-forwarding-precedence"
            for interval in intervals
            for constraint in interval["compiled"]["constraints"]
        ),
        "backend_technical_constraints": sum(
            constraint["category"] == "backend-technical"
            for interval in intervals
            for constraint in interval["compiled"]["constraints"]
        ),
        "unique_zone_prefixes": len(
            {
                value["packet_prefix"]
                for interval in intervals
                for value in interval["end_state"]["zone_prefixes"]
            }
        ),
        "runtime_bindings_claimed": False,
    }
    return data


def _interval_summary(interval: dict[str, Any]) -> dict[str, object]:
    compiled = interval["compiled"]
    resources = compiled["resources"]
    forwarding = compiled["forwarding"]
    zones = compiled["zone_edges"]
    constraints = compiled["constraints"]
    return {
        "resources_establish": sum(
            value["operation"] == "establish" for value in resources
        ),
        "resources_retire": sum(
            value["operation"] == "withdraw" for value in resources
        ),
        "resources_active": len(interval["end_state"]["resources"]),
        "parameter_changes": len(compiled["parameters"]),
        "forwarding_add": sum(
            value["operation"] == "added" for value in forwarding
        ),
        "forwarding_change": sum(
            value["operation"] == "changed" for value in forwarding
        ),
        "forwarding_remove": sum(
            value["operation"] == "removed" for value in forwarding
        ),
        "zone_changes": len(zones),
        "zone_edges_active": len(interval["end_state"]["zone_edges"]),
        "plan_precedence": sum(
            value["category"] == "plan-forwarding-precedence"
            for value in constraints
        ),
        "technical_constraints": sum(
            value["category"] == "backend-technical"
            for value in constraints
        ),
    }


def _lowering_rows(interval: dict[str, Any]) -> tuple[dict[str, object], ...]:
    plan = interval["plan"]
    compiled = interval["compiled"]
    rows = []
    for family, transitions, intent_families in (
        (
            "connection",
            plan["connection_transitions"],
            (compiled["resources"], compiled["parameters"]),
        ),
        (
            "parameter",
            plan["parameter_transitions"],
            (compiled["parameters"],),
        ),
        (
            "forwarding",
            plan["forwarding_transitions"],
            (compiled["forwarding"],),
        ),
    ):
        for transition in transitions:
            matched = tuple(
                intent
                for intent_family in intent_families
                for intent in intent_family
                if intent["origin_transition_id"]
                == transition["transition_id"]
            )
            rows.append(
                {
                    "family": family,
                    "plan_transition": transition,
                    "compiled_intents": matched,
                    "mapping": "exact-fields",
                }
            )
    rows.extend(
        {
            "family": "zone-edge",
            "plan_transition": None,
            "compiled_intents": (intent,),
            "mapping": "compiled-mechanical-output-no-plan-origin-claimed",
        }
        for intent in compiled["zone_edges"]
    )
    return tuple(rows)


def _constraint_groups(interval: dict[str, Any]) -> dict[str, object]:
    compiled = interval["compiled"]
    intents = {
        value["intent_id"]: _intent_label(value, family)
        for family, values in (
            ("resource", compiled["resources"]),
            ("parameter", compiled["parameters"]),
            ("forwarding", compiled["forwarding"]),
            ("zone-edge", compiled["zone_edges"]),
        )
        for value in values
    }
    groups = {"plan-forwarding-precedence": [], "backend-technical": []}
    for constraint in compiled["constraints"]:
        enriched = deepcopy(constraint)
        enriched["predecessor"]["label"] = intents.get(
            constraint["predecessor"]["intent_id"],
            constraint["predecessor"]["intent_id"],
        )
        enriched["successor"]["label"] = intents.get(
            constraint["successor"]["intent_id"],
            constraint["successor"]["intent_id"],
        )
        groups[constraint["category"]].append(enriched)
    return {key: tuple(value) for key, value in groups.items()}


def _intent_label(value: dict[str, Any], family: str) -> str:
    if family == "resource":
        return f"{value['operation'].upper()} {value['connection_ref']}"
    if family == "parameter":
        return f"QDISC {value['connection_ref']} {value['direction']}"
    if family == "forwarding":
        return f"{value['operation'].upper()} {value['route_ref']}"
    return f"{value['operation'].upper()} Zone {value['zone_id']}"


def _collect_identities(
    value: object,
    nodes: set[str],
    zones: set[str],
    key: str | None = None,
) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _collect_identities(child, nodes, zones, child_key)
    elif isinstance(value, (tuple, list)):
        for child in value:
            _collect_identities(child, nodes, zones, key)
    elif isinstance(value, str):
        if key in {
            "endpoint_a",
            "endpoint_b",
            "source",
            "target",
            "next_hop",
            "previous_service_satellite",
            "service_satellite",
            "ground_station_id",
        } and "/" in value:
            nodes.add(value)
        elif key == "zone_id":
            zones.add(value)


def _node_alias(value: str) -> str:
    return value.rsplit("/", 1)[-1]


__all__ = ["project_bpf_compiler_data"]
