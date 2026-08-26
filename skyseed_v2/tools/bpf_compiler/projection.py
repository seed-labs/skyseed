"""Project detached Compiler facts into summaries and lazy exact evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from .reader import BpfCompilerInspectionRead


_BASELINE_FAMILIES = (
    "resources",
    "parameters",
    "forwarding",
    "zone_edges",
    "zone_prefixes",
)


def project_bpf_compiler_data(
    observation: BpfCompilerInspectionRead,
    *,
    compile_evidence: Iterable[Mapping[str, object]] = (),
) -> dict[str, Any]:
    """Build compact statistics while retaining one exact payload copy."""

    if not isinstance(observation, BpfCompilerInspectionRead):
        raise TypeError("observation must be a BpfCompilerInspectionRead")
    evidence = tuple(deepcopy(dict(value)) for value in compile_evidence)
    if any(
        value.get("compile_index") != index
        for index, value in enumerate(evidence)
    ):
        raise ValueError("compile_evidence must be continuous and ordered")

    source = observation.snapshot
    source_intervals = source["intervals"]
    assert isinstance(source_intervals, tuple)
    # The detached reader owns these exact primitive payloads.  Projection
    # adds metadata to shallow interval copies and never mutates the nested
    # exact evidence, avoiding a run-wide duplicate of every realization.
    intervals = tuple(dict(value) for value in source_intervals)
    data = {**source, "intervals": intervals}
    if evidence and len(evidence) != len(intervals):
        raise ValueError("compile_evidence must align one-to-one with Plans")

    adjacent_diffs = []
    previous: dict[str, Any] | None = None
    for index, interval in enumerate(intervals):
        interval["summary"] = _interval_summary(interval)
        interval["serialized_bytes"] = _serialized_sizes(interval)
        interval["realization_sha256"] = _json_sha256(
            {
                "compiled": interval["compiled"],
                "end_state": interval["end_state"],
            }
        )
        interval["compile_evidence"] = evidence[index] if evidence else None
        diff = _baseline_diff(previous, interval)
        interval["adjacent_diff_summary"] = diff["summary"]
        adjacent_diffs.append(diff)
        previous = interval

    inventory = {
        "plans": len(intervals),
        "resource_intents": _sum_family(intervals, "resources"),
        "parameter_intents": _sum_family(intervals, "parameters"),
        "forwarding_intents": _sum_family(intervals, "forwarding"),
        "zone_edge_intents": _sum_family(intervals, "zone_edges"),
        "constraints": _sum_family(intervals, "constraints"),
        "baseline_records": sum(
            interval["summary"]["baseline_total"] for interval in intervals
        ),
    }
    data["inventory"] = inventory
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
    data["adjacent_diffs"] = tuple(adjacent_diffs)
    data["run_summary"] = _run_summary(intervals, adjacent_diffs)
    return data


def _sum_family(intervals: tuple[dict[str, Any], ...], family: str) -> int:
    return sum(len(value["compiled"][family]) for value in intervals)


def _interval_summary(interval: dict[str, Any]) -> dict[str, int]:
    plan = interval["plan"]
    compiled = interval["compiled"]
    end_state = interval["end_state"]
    logical = {
        "connection": len(plan["connection_transitions"]),
        "parameter": len(plan["parameter_transitions"]),
        "forwarding": len(plan["forwarding_transitions"]),
        "precedence": len(plan["forwarding_precedences"]),
    }
    intents = {
        "resources": len(compiled["resources"]),
        "parameters": len(compiled["parameters"]),
        "forwarding": len(compiled["forwarding"]),
        "zone_edges": len(compiled["zone_edges"]),
        "constraints": len(compiled["constraints"]),
    }
    baseline = {family: len(end_state[family]) for family in _BASELINE_FAMILIES}
    return {
        **{f"input_{key}": value for key, value in logical.items()},
        "input_transition_total": (
            logical["connection"]
            + logical["parameter"]
            + logical["forwarding"]
        ),
        **{f"intent_{key}": value for key, value in intents.items()},
        "interval_output_total": sum(intents.values()),
        **{f"baseline_{key}": value for key, value in baseline.items()},
        "baseline_total": sum(
            baseline[key]
            for key in ("resources", "parameters", "forwarding", "zone_edges")
        ),
    }


def _serialized_sizes(interval: dict[str, Any]) -> dict[str, object]:
    plan = interval["plan"]
    compiled = interval["compiled"]
    baseline = interval["end_state"]
    baseline_families = {
        family: _json_bytes(baseline[family]) for family in _BASELINE_FAMILIES
    }
    return {
        "logical_input": _json_bytes(plan),
        "interval_output": _json_bytes(compiled),
        "baseline_by_family": baseline_families,
        "baseline_total": sum(baseline_families.values()),
    }


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _baseline_diff(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, object]:
    details: dict[str, object] = {}
    totals = {name: 0 for name in ("unchanged", "changed", "added", "removed")}
    for family in _BASELINE_FAMILIES:
        before_values = () if previous is None else previous["end_state"][family]
        after_values = current["end_state"][family]
        before = {
            _baseline_key(family, value): (index, value)
            for index, value in enumerate(before_values)
        }
        after = {
            _baseline_key(family, value): (index, value)
            for index, value in enumerate(after_values)
        }
        rows = []
        counts = {name: 0 for name in totals}
        for key in sorted(set(before) | set(after)):
            left = before.get(key)
            right = after.get(key)
            if left is None:
                kind = "added"
            elif right is None:
                kind = "removed"
            elif left[1] == right[1]:
                kind = "unchanged"
            else:
                kind = "changed"
            counts[kind] += 1
            totals[kind] += 1
            if kind != "unchanged":
                rows.append(
                    {
                        "kind": kind,
                        "key": key,
                        "previous_index": None if left is None else left[0],
                        "current_index": None if right is None else right[0],
                    }
                )
        details[family] = tuple(rows)
        details[f"{family}_summary"] = counts
    baseline_records = sum(totals.values()) - totals["removed"]
    changed_records = totals["changed"] + totals["added"] + totals["removed"]
    return {
        "plan_index": current["plan_index"],
        "previous_plan_index": (
            None if previous is None else previous["plan_index"]
        ),
        "summary": {
            **totals,
            "baseline_records": baseline_records,
            "churn_records": changed_records,
            "churn_ratio": (
                changed_records / max(1, baseline_records + totals["removed"])
            ),
            "bootstrap_from_empty": previous is None,
            "by_family": {
                family: details[f"{family}_summary"]
                for family in _BASELINE_FAMILIES
            },
        },
        "details": {
            family: details[family] for family in _BASELINE_FAMILIES
        },
    }


def _baseline_key(family: str, value: Mapping[str, object]) -> str:
    if family == "resources":
        return str(value["connection_ref"])
    if family == "parameters":
        return f"{value['connection_ref']}|{value['direction']}"
    if family == "forwarding":
        return str(value["route_ref"])
    if family in {"zone_edges", "zone_prefixes"}:
        return str(value["zone_id"])
    raise ValueError(f"unsupported baseline family: {family}")


def _run_summary(
    intervals: tuple[dict[str, Any], ...],
    diffs: list[dict[str, object]],
) -> dict[str, object]:
    compile_seconds = [
        (
            None
            if interval["compile_evidence"] is None
            else interval["compile_evidence"].get("compile_seconds")
        )
        for interval in intervals
    ]
    baseline_family_bytes = {
        family: sum(
            interval["serialized_bytes"]["baseline_by_family"][family]
            for interval in intervals
        )
        for family in _BASELINE_FAMILIES
    }
    return {
        "plan_count": len(intervals),
        "compile_seconds": tuple(compile_seconds),
        "compile_seconds_total": (
            sum(float(value) for value in compile_seconds if value is not None)
            if any(value is not None for value in compile_seconds)
            else None
        ),
        "baseline_family_bytes": baseline_family_bytes,
        "baseline_bytes_total": sum(baseline_family_bytes.values()),
        "churn_records_total": sum(
            int(value["summary"]["churn_records"]) for value in diffs
        ),
    }


__all__ = ["project_bpf_compiler_data"]
