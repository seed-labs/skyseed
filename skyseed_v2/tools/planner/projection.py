"""Project detached Planner reads into private human-inspection view data."""

from __future__ import annotations

import re
from typing import Any

from .reader import PlannerInspectionRead


def project_planner_data(observation: PlannerInspectionRead) -> dict[str, Any]:
    """Build comparison indexes without recomputing Simulator or Planner facts."""

    if not isinstance(observation, PlannerInspectionRead):
        raise TypeError("observation must be a PlannerInspectionRead")
    source = observation.snapshot
    slices = source["slices"]
    plans = source["plans"]
    decisions = source["decisions"]
    assert isinstance(slices, tuple)
    assert isinstance(plans, tuple)
    assert isinstance(decisions, tuple)

    transitions = _flatten_transitions(plans)
    differences = tuple(
        value["difference_from_previous"]
        for value in slices
        if value["difference_from_previous"] is not None
    )
    connection_groups = _connection_groups(
        slices,
        transitions,
        str(source["timeline"]["artifact_end_time"]),
    )
    parameter_series = _parameter_series(slices, transitions)
    forwarding_groups = _forwarding_groups(differences, transitions, plans)
    comparisons = _difference_comparisons(differences, transitions)
    unmatched_forwarding = _count_unmatched_forwarding(
        differences, transitions
    )
    slice_timestamps = {
        value["timestamp"] for value in slices if value["timestamp"] is not None
    }
    connection_transitions = tuple(
        value for value in transitions if value["family"] == "connection"
    )

    inventory = {
        "satellites": sum(
            value["kind"] == "satellite"
            for value in source["topology"]["nodes"]
        ),
        "ground_stations": sum(
            value["kind"] == "ground-station"
            for value in source["topology"]["nodes"]
        ),
        "real_slices": len(slices) - 1,
        "differences": len(differences),
        "plans": len(plans),
        "connection_transitions": sum(
            len(value["connection_transitions"]) for value in plans
        ),
        "parameter_transitions": sum(
            len(value["parameter_transitions"]) for value in plans
        ),
        "forwarding_transitions": sum(
            len(value["forwarding_transitions"]) for value in plans
        ),
        "forwarding_precedences": sum(
            len(value["forwarding_precedences"]) for value in plans
        ),
        "planning_decisions": len(decisions),
    }
    observed_behavior = {
        "midpoint_connection_transitions": sum(
            value["nominal_time"] not in slice_timestamps
            for value in connection_transitions
        ),
        "repeated_connection_ids": sum(
            len(value["formal_occurrences"]) > 1 for value in connection_groups
        ),
        "parameter_merges": sum(
            value["decision"] == "merged" for value in decisions
        ),
        "accepted_suppressions": sum(
            value["decision"] == "suppressed" for value in decisions
        ),
        "retained_decisions": sum(
            value["decision"] == "retained" for value in decisions
        ),
        "unmatched_forwarding_transitions": unmatched_forwarding,
        "delayed_or_unmatched_forwarding_transitions": (
            unmatched_forwarding
        ),
        # A delayed stable FOLLOW need not match a RoutePatch in the same
        # Difference. This detached projection therefore never re-labels an
        # unmatched transition as an invented Model output.
        "synthetic_forwarding_claimed": False,
    }
    representatives = {
        "connection_ref": _representative_connection(connection_groups),
        "parameter_key": _representative_parameter(parameter_series),
        "route_ref": _representative_route(forwarding_groups),
    }
    chunk_payloads: dict[str, object] = {}
    slice_manifest = []
    for value in slices:
        slice_id = int(value["slice_id"])
        path = f"chunks/slice-{slice_id:06d}.js"
        chunk_payloads[path] = {
            key: field_value
            for key, field_value in value.items()
            if key != "difference_from_previous"
        }
        slice_manifest.append(
            {
                "slice_id": slice_id,
                "timestamp": value["timestamp"],
                "chunk": path,
            }
        )
    plan_catalog = []
    nominal_groups: dict[str, dict[str, object]] = {}
    for plan in plans:
        plan_index = int(plan["plan_index"])
        path = f"chunks/plan-{plan_index:06d}.js"
        chunk_payloads[path] = plan
        plan_catalog.append(
            {
                "plan_index": plan_index,
                "label": plan["label"],
                "start_slice_id": plan["start_slice_id"],
                "start_timestamp": plan["start_timestamp"],
                "end_slice_id": plan["end_slice_id"],
                "end_timestamp": plan["end_timestamp"],
                "coverage": plan["coverage"],
                "connection_transition_count": len(
                    plan["connection_transitions"]
                ),
                "parameter_transition_count": len(
                    plan["parameter_transitions"]
                ),
                "forwarding_transition_count": len(
                    plan["forwarding_transitions"]
                ),
                "forwarding_precedence_count": len(
                    plan["forwarding_precedences"]
                ),
                "chunk": path,
            }
        )
        family_by_id = {
            value["transition_id"]: family
            for family, field_name in (
                ("connection", "connection_transitions"),
                ("parameter", "parameter_transitions"),
                ("forwarding", "forwarding_transitions"),
            )
            for value in plan[field_name]
        }
        for group in plan["temporal_groups"]:
            timestamp = str(group["nominal_time"])
            record = nominal_groups.setdefault(
                timestamp,
                {
                    "nominal_time": timestamp,
                    "plan_indices": [],
                    "counts": {
                        "connection": 0,
                        "parameter": 0,
                        "forwarding": 0,
                    },
                },
            )
            record["plan_indices"].append(plan_index)
            for transition_id in group["transition_ids"]:
                record["counts"][family_by_id[transition_id]] += 1

    connection_catalog = _hierarchical_chunks(
        connection_groups,
        "connection",
        "connection_ref",
        chunk_payloads,
        batch_size=64,
    )
    parameter_catalog = _hierarchical_chunks(
        parameter_series,
        "parameter",
        "parameter_key",
        chunk_payloads,
        batch_size=64,
    )
    forwarding_catalog = _hierarchical_chunks(
        forwarding_groups,
        "forwarding",
        "route_ref",
        chunk_payloads,
        batch_size=32,
    )
    difference_catalog = []
    for comparison in comparisons:
        difference_id = int(comparison["difference_id"])
        path = f"chunks/difference-{difference_id:06d}.js"
        chunk_payloads[path] = comparison
        difference_catalog.append(
            {
                "difference_id": difference_id,
                "interval": comparison["interval"],
                "chunk": path,
            }
        )
    parameter_fields = ("bandwidth_bps", "delay_us", "packet_loss_ppm")
    formal_parameter_fields = {
        field: sum(
            field in patch["changed"]
            for difference in differences
            for patch in difference["connection_parameter_patches"]
        )
        for field in parameter_fields
    }
    published_parameter_fields = {
        field: sum(
            transition["family"] == "parameter"
            and field in transition["targets"]
            for transition in transitions
        )
        for field in parameter_fields
    }
    return {
        "format_version": 3,
        "tool": source["tool"],
        "authority": source["authority"],
        "configuration": source["configuration"],
        "timeline": source["timeline"],
        "topology": source["topology"],
        "decisions": _timeline_decisions(decisions, differences),
        "inspection_limits": source["inspection_limits"],
        "slice_manifest": slice_manifest,
        "plans": plan_catalog,
        "nominal_groups": tuple(
            nominal_groups[key] for key in sorted(nominal_groups)
        ),
        "catalogs": {
            "connection": connection_catalog,
            "parameter": parameter_catalog,
            "forwarding": forwarding_catalog,
        },
        "difference_catalog": difference_catalog,
        "parameter_overview": {
            "formal_patch_count": sum(
                len(value["connection_parameter_patches"])
                for value in differences
            ),
            "published_transition_count": inventory[
                "parameter_transitions"
            ],
            "formal_fields": formal_parameter_fields,
            "published_fields": published_parameter_fields,
        },
        "planner_summary": _planner_summary(
            differences,
            plans,
            decisions,
            inventory,
            source["configuration"],
        ),
        "display_aliases": _display_aliases(source["topology"]),
        "inventory": inventory,
        "observed_behavior": observed_behavior,
        "representatives": representatives,
        "_chunk_payloads": chunk_payloads,
    }


def _hierarchical_chunks(
    values: tuple[dict[str, object], ...],
    prefix: str,
    identity_field: str,
    chunks: dict[str, object],
    *,
    batch_size: int,
) -> dict[str, object]:
    """Write bounded data batches behind compact lazy catalog pages."""

    groups = []
    for offset in range(0, len(values), batch_size):
        batch = values[offset : offset + batch_size]
        chunk_index = offset // batch_size
        path = f"chunks/{prefix}-{chunk_index:06d}.js"
        chunks[path] = batch
        groups.append(
            {
                "chunk": path,
                "identities": tuple(value[identity_field] for value in batch),
            }
        )

    pages = []
    page_size = 16
    for offset in range(0, len(groups), page_size):
        page_groups = tuple(groups[offset : offset + page_size])
        page_index = offset // page_size
        path = f"chunks/catalog-{prefix}-{page_index:06d}.js"
        chunks[path] = page_groups
        identities = tuple(
            identity
            for group in page_groups
            for identity in group["identities"]
        )
        pages.append(
            {
                "chunk": path,
                "count": len(identities),
                "first_identity": identities[0],
                "last_identity": identities[-1],
            }
        )
    return {
        "count": len(values),
        "data_chunk_count": len(groups),
        "index_chunks": tuple(pages),
    }


def _flatten_transitions(plans: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    result = []
    for plan in plans:
        for field in (
            "connection_transitions",
            "parameter_transitions",
            "forwarding_transitions",
        ):
            for transition in plan[field]:
                value = dict(transition)
                value["plan_index"] = plan["plan_index"]
                value["plan_label"] = plan["label"]
                result.append(value)
    return tuple(
        sorted(
            result,
            key=lambda value: (
                str(value["nominal_time"]),
                str(value["family"]),
                str(value["transition_id"]),
            ),
        )
    )


def _connection_groups(
    slices: tuple[dict[str, object], ...],
    transitions: tuple[dict[str, object], ...],
    artifact_end_time: str,
) -> tuple[dict[str, object], ...]:
    metadata: dict[str, dict[str, object]] = {}
    presence: dict[str, list[tuple[int, str]]] = {}
    for slice_record in slices:
        timestamp = slice_record["timestamp"]
        if timestamp is None:
            continue
        for connection in slice_record["connections"]:
            reference = str(connection["connection_ref"])
            metadata.setdefault(reference, connection["connection_id"])
            presence.setdefault(reference, []).append(
                (int(slice_record["slice_id"]), str(timestamp))
            )

    planned_by_ref: dict[str, list[dict[str, object]]] = {}
    for transition in transitions:
        if transition["family"] == "connection":
            reference = str(transition["connection_ref"])
            metadata.setdefault(reference, transition["connection_id"])
            planned_by_ref.setdefault(reference, []).append(transition)

    result = []
    for reference in sorted(metadata):
        formal_occurrences = _sample_occurrences(
            presence.get(reference, ()), artifact_end_time
        )
        planned_occurrences = _planned_occurrences(
            planned_by_ref.get(reference, ())
        )
        result.append(
            {
                "connection_ref": reference,
                "connection_id": metadata[reference],
                "formal_occurrences": formal_occurrences,
                "planned_occurrences": planned_occurrences,
                "transition_count": len(planned_by_ref.get(reference, ())),
                "planned_transitions": tuple(
                    {
                        "transition_id": value["transition_id"],
                        "nominal_time": value["nominal_time"],
                        "operation": value["operation"],
                    }
                    for value in planned_by_ref.get(reference, ())
                ),
            }
        )
    return tuple(result)


def _sample_occurrences(
    samples: list[tuple[int, str]] | tuple[()],
    artifact_end_time: str,
) -> tuple[dict[str, object], ...]:
    result = []
    current: list[tuple[int, str]] = []
    for value in samples:
        if current and value[0] != current[-1][0] + 1:
            result.append(_sample_occurrence(current, artifact_end_time))
            current = []
        current.append(value)
    if current:
        result.append(_sample_occurrence(current, artifact_end_time))
    return tuple(result)


def _sample_occurrence(
    values: list[tuple[int, str]], artifact_end_time: str
) -> dict[str, object]:
    right_censored = values[-1][1] == artifact_end_time
    return {
        "start_slice_id": values[0][0],
        "end_slice_id": values[-1][0],
        "start_timestamp": values[0][1],
        "end_timestamp": values[-1][1],
        "observed_through_timestamp": values[-1][1],
        "right_censored": right_censored,
        "sample_count": len(values),
    }


def _planned_occurrences(
    transitions: list[dict[str, object]] | tuple[()],
) -> tuple[dict[str, object], ...]:
    result = []
    active = None
    for transition in transitions:
        if transition["operation"] == "establish":
            active = transition
            continue
        result.append(
            {
                "establish_time": (
                    None if active is None else active["nominal_time"]
                ),
                "withdraw_time": transition["nominal_time"],
                "establish_transition_id": (
                    None if active is None else active["transition_id"]
                ),
                "withdraw_transition_id": transition["transition_id"],
                "right_censored": False,
            }
        )
        active = None
    if active is not None:
        result.append(
            {
                "establish_time": active["nominal_time"],
                "withdraw_time": None,
                "establish_transition_id": active["transition_id"],
                "withdraw_transition_id": None,
                "right_censored": True,
            }
        )
    return tuple(result)


def _parameter_series(
    slices: tuple[dict[str, object], ...],
    transitions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    values: dict[str, dict[str, object]] = {}
    connection_transitions_by_ref: dict[
        str, list[dict[str, object]]
    ] = {}
    parameter_transitions_by_key: dict[
        str, list[dict[str, object]]
    ] = {}
    direction_fields = (
        ("endpoint_a_to_b", "endpoint-a-to-b"),
        ("endpoint_b_to_a", "endpoint-b-to-a"),
    )
    fields = ("bandwidth_bps", "delay_us", "packet_loss_ppm")
    for slice_record in slices:
        if slice_record["timestamp"] is None:
            continue
        for connection in slice_record["connections"]:
            for source_direction, direction in direction_fields:
                parameters = connection[source_direction]
                for field in fields:
                    key = "|".join(
                        (connection["connection_ref"], direction, field)
                    )
                    record = values.setdefault(
                        key,
                        {
                            "parameter_key": key,
                            "connection_ref": connection["connection_ref"],
                            "direction": direction,
                            "field": field,
                            "samples": [],
                            "planned": [],
                        },
                    )
                    record["samples"].append(
                        {
                            "slice_id": slice_record["slice_id"],
                            "timestamp": slice_record["timestamp"],
                            "value": parameters[field],
                        }
                    )
    for transition in transitions:
        if transition["family"] == "connection":
            connection_transitions_by_ref.setdefault(
                str(transition["connection_ref"]), []
            ).append(transition)
            continue
        if transition["family"] != "parameter":
            continue
        for field, target in transition["targets"].items():
            key = "|".join(
                (transition["connection_ref"], transition["direction"], field)
            )
            parameter_transitions_by_key.setdefault(key, []).append(
                transition
            )
            if key not in values:
                continue
            values[key]["planned"].append(
                {
                    "nominal_time": transition["nominal_time"],
                    "value": target,
                    "transition_id": transition["transition_id"],
                    "plan_label": transition["plan_label"],
                    "bundled_fields": tuple(transition["targets"]),
                }
            )
    timeline_end = next(
        (
            str(value["timestamp"])
            for value in reversed(slices)
            if value["timestamp"] is not None
        ),
        None,
    )
    for record in values.values():
        relevant = tuple(
            sorted(
                (
                    *connection_transitions_by_ref.get(
                        str(record["connection_ref"]), ()
                    ),
                    *parameter_transitions_by_key.get(
                        str(record["parameter_key"]), ()
                    ),
                ),
                key=lambda value: (
                    str(value["nominal_time"]),
                    str(value["family"]),
                    str(value["transition_id"]),
                ),
            )
        )
        record["planned_state_occurrences"] = _planned_parameter_occurrences(
            record,
            relevant,
            timeline_end,
        )
    return tuple(values[key] for key in sorted(values))


def _planned_parameter_occurrences(
    series: dict[str, object],
    relevant: tuple[dict[str, object], ...],
    timeline_end: str | None,
) -> tuple[dict[str, object], ...]:
    field = series["field"]
    source_direction = str(series["direction"]).replace("-", "_")
    result = []
    active = None
    segments = []
    current_time = None
    current_value = None
    for transition in relevant:
        if transition["family"] == "connection":
            if transition["operation"] == "establish":
                connection = transition["connection"]
                current_time = transition["nominal_time"]
                current_value = connection[source_direction][field]
                segments = []
                active = transition
            elif active is not None:
                segments.append(
                    {
                        "start_time": current_time,
                        "end_time": transition["nominal_time"],
                        "value": current_value,
                    }
                )
                result.append(
                    {
                        "establish_time": active["nominal_time"],
                        "withdraw_time": transition["nominal_time"],
                        "segments": tuple(segments),
                    }
                )
                active = None
        elif active is not None:
            segments.append(
                {
                    "start_time": current_time,
                    "end_time": transition["nominal_time"],
                    "value": current_value,
                }
            )
            current_time = transition["nominal_time"]
            current_value = transition["targets"][field]
    if active is not None:
        segments.append(
            {
                "start_time": current_time,
                "end_time": timeline_end,
                "value": current_value,
            }
        )
        result.append(
            {
                "establish_time": active["nominal_time"],
                "withdraw_time": None,
                "segments": tuple(segments),
            }
        )
    return tuple(result)


def _forwarding_groups(
    differences: tuple[dict[str, object], ...],
    transitions: tuple[dict[str, object], ...],
    plans: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    groups: dict[str, dict[str, object]] = {}
    for difference in differences:
        for patch in difference["route_patches"]:
            reference = str(patch["route_ref"])
            record = groups.setdefault(
                reference,
                {
                    "route_ref": reference,
                    "source": patch["source"],
                    "target": patch["target"],
                    "formal_patches": [],
                    "planned_transitions": [],
                    "precedences": [],
                },
            )
            record["formal_patches"].append(
                {
                    "operation": patch["operation"],
                    "next_hop": patch["next_hop"],
                    "difference_id": difference["current_slice_id"],
                    "timestamp": difference["current_timestamp"],
                }
            )
    transition_by_id = {
        value["transition_id"]: value
        for value in transitions
        if value["family"] == "forwarding"
    }
    for transition in transition_by_id.values():
        reference = str(transition["route_ref"])
        record = groups.setdefault(
            reference,
            {
                "route_ref": reference,
                "source": transition["source"],
                "target": transition["target"],
                "formal_patches": [],
                "planned_transitions": [],
                "precedences": [],
            },
        )
        record["planned_transitions"].append(
            _forwarding_transition_summary(transition)
        )
    for plan in plans:
        for precedence in plan["forwarding_precedences"]:
            predecessor = transition_by_id[precedence["predecessor"]]
            successor = transition_by_id[precedence["successor"]]
            edge = {
                "predecessor": precedence["predecessor"],
                "successor": precedence["successor"],
                "nominal_time": precedence["nominal_time"],
            }
            for reference in {
                predecessor["route_ref"], successor["route_ref"]
            }:
                groups[reference]["precedences"].append(edge)
    return tuple(groups[key] for key in sorted(groups))


def _forwarding_transition_summary(
    transition: dict[str, object],
) -> dict[str, object]:
    return {
        key: transition[key]
        for key in (
            "transition_id",
            "nominal_time",
            "operation",
            "next_hop",
            "plan_label",
        )
    }


def _difference_comparisons(
    differences: tuple[dict[str, object], ...],
    transitions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    result = []
    for difference in differences:
        related = tuple(
            value
            for value in transitions
            if _belongs_to_difference(value["nominal_time"], difference)
        )
        result.append(
            {
                "difference_id": difference["current_slice_id"],
                "interval": (
                    f"({difference['previous_slice_id']},"
                    f"{difference['current_slice_id']}]"
                ),
                "previous_timestamp": difference["previous_timestamp"],
                "current_timestamp": difference["current_timestamp"],
                "difference": difference,
                "formal": {
                    "connection_added": len(difference["added_connections"]),
                    "connection_removed": len(
                        difference["removed_connection_ids"]
                    ),
                    "parameter_patches": len(
                        difference["connection_parameter_patches"]
                    ),
                    "route_patches": len(difference["route_patches"]),
                },
                "planned": {
                    family: tuple(
                        value["transition_id"]
                        for value in related
                        if value["family"] == family
                    )
                    for family in ("connection", "parameter", "forwarding")
                },
                "plan_indices": tuple(
                    sorted({int(value["plan_index"]) for value in related})
                ),
            }
        )
    return tuple(result)


def _belongs_to_difference(timestamp: str, difference: dict[str, object]) -> bool:
    previous = difference["previous_timestamp"]
    current = str(difference["current_timestamp"])
    return timestamp == current if previous is None else str(previous) < timestamp <= current


def _count_unmatched_forwarding(
    differences: tuple[dict[str, object], ...],
    transitions: tuple[dict[str, object], ...],
) -> int:
    unmatched = 0
    for difference in differences:
        formal = {
            (patch["route_ref"], patch["operation"], patch["next_hop"])
            for patch in difference["route_patches"]
        }
        for transition in transitions:
            if (
                transition["family"] == "forwarding"
                and _belongs_to_difference(
                    str(transition["nominal_time"]), difference
                )
                and (
                    transition["route_ref"],
                    transition["operation"],
                    transition["next_hop"],
                )
                not in formal
            ):
                unmatched += 1
    return unmatched


def _timeline_decisions(
    decisions: tuple[dict[str, object], ...],
    differences: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    timestamps = {
        int(value["current_slice_id"]): value["current_timestamp"]
        for value in differences
    }
    return tuple(
        {
            **value,
            "timeline_time": timestamps.get(
                max(value["source_difference_ids"])
            ),
        }
        for value in decisions
    )


def _planner_summary(
    differences: tuple[dict[str, object], ...],
    plans: tuple[dict[str, object], ...],
    decisions: tuple[dict[str, object], ...],
    inventory: dict[str, object],
    configuration: dict[str, object],
) -> dict[str, object]:
    policy = configuration.get("policy_evidence")
    policy = policy if isinstance(policy, dict) else {}
    formal_connection_added = sum(
        len(value["added_connections"]) for value in differences
    )
    formal_connection_removed = sum(
        len(value["removed_connection_ids"]) for value in differences
    )
    formal_parameter_patches = sum(
        len(value["connection_parameter_patches"])
        for value in differences
    )
    formal_route_patches = sum(
        len(value["route_patches"]) for value in differences
    )
    decisions_by_result: dict[str, int] = {}
    for value in decisions:
        result = str(value["decision"])
        decisions_by_result[result] = decisions_by_result.get(result, 0) + 1

    per_plan = []
    for plan in plans:
        selected = tuple(
            value
            for value in differences
            if int(plan["start_slice_id"])
            < int(value["current_slice_id"])
            <= int(plan["end_slice_id"])
        )
        plan_decisions = tuple(
            value
            for value in decisions
            if any(
                int(plan["start_slice_id"]) < int(source_id)
                <= int(plan["end_slice_id"])
                for source_id in value["source_difference_ids"]
            )
        )
        forwarding_count = len(plan["forwarding_transitions"])
        precedence_count = len(plan["forwarding_precedences"])
        per_plan.append(
            {
                "plan_index": plan["plan_index"],
                "label": plan["label"],
                "coverage": plan["coverage"],
                "difference_count": len(selected),
                "formal": {
                    "connection_additions": sum(
                        len(value["added_connections"])
                        for value in selected
                    ),
                    "connection_removals": sum(
                        len(value["removed_connection_ids"])
                        for value in selected
                    ),
                    "parameter_patches": sum(
                        len(value["connection_parameter_patches"])
                        for value in selected
                    ),
                    "route_patches": sum(
                        len(value["route_patches"]) for value in selected
                    ),
                },
                "published": {
                    "connection": len(plan["connection_transitions"]),
                    "parameter": len(plan["parameter_transitions"]),
                    "forwarding": forwarding_count,
                    "precedence": precedence_count,
                },
                "planning_decisions": len(plan_decisions),
                "precedence_per_forwarding": (
                    precedence_count / forwarding_count
                    if forwarding_count
                    else 0.0
                ),
            }
        )

    parameter_policy = policy.get("parameter", {})
    forwarding_policy = policy.get("forwarding", {})
    connection_policy = policy.get("connection", {})
    coordination_policy = policy.get("coordination", {})
    return {
        "whole_run": {
            "connection": {
                "formal_additions": formal_connection_added,
                "formal_removals": formal_connection_removed,
                "episode_candidates": connection_policy.get("candidates"),
                "accepted_omissions": connection_policy.get(
                    "accepted_omissions"
                ),
                "materialized_transitions": inventory[
                    "connection_transitions"
                ],
            },
            "parameter": {
                "raw_patches": formal_parameter_patches,
                "emitted_transitions": inventory[
                    "parameter_transitions"
                ],
                "held_updates": max(
                    0,
                    formal_parameter_patches
                    - int(inventory["parameter_transitions"]),
                ),
            },
            "forwarding": {
                "formal_route_changes": formal_route_patches,
                "emitted_transitions": inventory[
                    "forwarding_transitions"
                ],
                "follow": forwarding_policy.get(
                    "formal_change_decisions", {}
                ).get("FOLLOW"),
                "hold": forwarding_policy.get(
                    "formal_change_decisions", {}
                ).get("HOLD"),
                "transition_reduction": forwarding_policy.get(
                    "transition_reduction"
                ),
            },
            "coordination": {
                "candidate_count": connection_policy.get("candidates"),
                "accepted_omissions": connection_policy.get(
                    "accepted_omissions"
                ),
                "decision_count": len(decisions),
                "decisions_by_result": decisions_by_result,
                "coordination_decisions_by_result": (
                    coordination_policy.get("decisions_by_result", {})
                ),
            },
            "precedence": {
                "forwarding_transitions": inventory[
                    "forwarding_transitions"
                ],
                "edges": inventory["forwarding_precedences"],
                "edges_per_forwarding": (
                    int(inventory["forwarding_precedences"])
                    / int(inventory["forwarding_transitions"])
                    if inventory["forwarding_transitions"]
                    else 0.0
                ),
            },
        },
        "distributions": {
            "held_parameter_absolute_deviation": parameter_policy.get(
                "held_absolute_deviation_by_field", {}
            ),
            "hold_duration_seconds": forwarding_policy.get(
                "hold_duration_seconds", {}
            ),
            "held_hop_stretch": forwarding_policy.get(
                "held_hop_stretch", {}
            ),
            "held_directional_delay_stretch_us": forwarding_policy.get(
                "held_directional_propagation_delay_stretch_us", {}
            ),
        },
        "per_plan": tuple(per_plan),
        "measurement_basis": policy.get(
            "measurement_basis",
            "exact detached Differences, Plans, and PlanningDecisions",
        ),
    }


def _representative_connection(groups: tuple[dict[str, object], ...]) -> str | None:
    repeated = next(
        (value for value in groups if len(value["formal_occurrences"]) > 1),
        None,
    )
    if repeated is not None:
        return str(repeated["connection_ref"])
    planned = next((value for value in groups if value["transition_count"]), None)
    return None if planned is None else str(planned["connection_ref"])


def _representative_parameter(series: tuple[dict[str, object], ...]) -> str | None:
    candidates = tuple(value for value in series if value["planned"])
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda value: (
            len({sample["value"] for sample in value["samples"]}),
            len(value["samples"]),
            str(value["parameter_key"]),
        ),
    )
    return str(selected["parameter_key"])


def _representative_route(groups: tuple[dict[str, object], ...]) -> str | None:
    constrained = next((value for value in groups if value["precedences"]), None)
    if constrained is not None:
        return str(constrained["route_ref"])
    planned = next((value for value in groups if value["planned_transitions"]), None)
    return None if planned is None else str(planned["route_ref"])


def _display_aliases(topology: dict[str, object]) -> dict[str, object]:
    nodes = topology["nodes"]
    zones = topology["zones"]
    node_aliases = {}
    for node in nodes:
        node_id = str(node["node_id"])
        if node["kind"] == "satellite":
            orbit_match = re.search(r"orbit-(\d+)", node_id)
            satellite_match = re.search(r"satellite-(\d+)$", node_id)
            if orbit_match and satellite_match:
                alias = (
                    f"O{int(orbit_match.group(1))}-"
                    f"S{int(satellite_match.group(1))}"
                )
            else:
                alias = f"Satellite {node['orbit_index']}"
        else:
            name = node_id.rsplit("/", 1)[-1]
            name = re.sub(r"^gs-", "", name)
            name = re.sub(r"-reference$", "", name)
            alias = "GS-" + "-".join(
                part.capitalize() for part in name.split("-")
            )
        node_aliases[node_id] = alias
    return {
        "nodes": node_aliases,
        "zones": {
            str(zone["zone_id"]): f"Z:{zone['zone_id']}"
            for zone in zones
        },
    }


__all__ = ["project_planner_data"]
