"""Project detached Planner reads into private human-inspection view data."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .reader import PlannerInspectionRead


def project_planner_data(observation: PlannerInspectionRead) -> dict[str, Any]:
    """Build comparison indexes without recomputing Simulator or Planner facts."""

    if not isinstance(observation, PlannerInspectionRead):
        raise TypeError("observation must be a PlannerInspectionRead")
    data = deepcopy(observation.snapshot)
    slices = data["slices"]
    plans = data["plans"]
    decisions = data["decisions"]
    assert isinstance(slices, tuple)
    assert isinstance(plans, tuple)
    assert isinstance(decisions, tuple)

    transitions = _flatten_transitions(plans)
    differences = tuple(
        value["difference_from_previous"]
        for value in slices
        if value["difference_from_previous"] is not None
    )
    connection_groups = _connection_groups(slices, transitions)
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

    data["connections"] = connection_groups
    data["parameter_series"] = parameter_series
    data["forwarding"] = forwarding_groups
    data["difference_comparisons"] = comparisons
    data["display_aliases"] = _display_aliases(data["topology"])
    data["inventory"] = {
        "satellites": sum(
            value["kind"] == "satellite" for value in data["topology"]["nodes"]
        ),
        "ground_stations": sum(
            value["kind"] == "ground-station"
            for value in data["topology"]["nodes"]
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
    data["observed_behavior"] = {
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
        "synthetic_forwarding_claimed": unmatched_forwarding > 0,
    }
    data["representatives"] = {
        "connection_ref": _representative_connection(connection_groups),
        "parameter_key": _representative_parameter(parameter_series),
        "route_ref": _representative_route(forwarding_groups),
    }
    return data


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
        formal_occurrences = _sample_occurrences(presence.get(reference, ()))
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
            }
        )
    return tuple(result)


def _sample_occurrences(
    samples: list[tuple[int, str]] | tuple[()],
) -> tuple[dict[str, object], ...]:
    result = []
    current: list[tuple[int, str]] = []
    for value in samples:
        if current and value[0] != current[-1][0] + 1:
            result.append(_sample_occurrence(current))
            current = []
        current.append(value)
    if current:
        result.append(_sample_occurrence(current))
    return tuple(result)


def _sample_occurrence(values: list[tuple[int, str]]) -> dict[str, object]:
    return {
        "start_slice_id": values[0][0],
        "end_slice_id": values[-1][0],
        "start_timestamp": values[0][1],
        "end_timestamp": values[-1][1],
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
            }
        )
    return tuple(result)


def _parameter_series(
    slices: tuple[dict[str, object], ...],
    transitions: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    values: dict[str, dict[str, object]] = {}
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
                            "connection_id": connection["connection_id"],
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
        if transition["family"] != "parameter":
            continue
        for field, target in transition["targets"].items():
            key = "|".join(
                (transition["connection_ref"], transition["direction"], field)
            )
            if key not in values:
                continue
            values[key]["planned"].append(
                {
                    "nominal_time": transition["nominal_time"],
                    "value": target,
                    "transition_id": transition["transition_id"],
                    "plan_label": transition["plan_label"],
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
        record["planned_state_occurrences"] = _planned_parameter_occurrences(
            record,
            transitions,
            timeline_end,
        )
    return tuple(values[key] for key in sorted(values))


def _planned_parameter_occurrences(
    series: dict[str, object],
    transitions: tuple[dict[str, object], ...],
    timeline_end: str | None,
) -> tuple[dict[str, object], ...]:
    reference = series["connection_ref"]
    direction = series["direction"]
    field = series["field"]
    source_direction = str(direction).replace("-", "_")
    relevant = tuple(
        value
        for value in transitions
        if value.get("connection_ref") == reference
        and (
            value["family"] == "connection"
            or (
                value["family"] == "parameter"
                and value["direction"] == direction
                and field in value["targets"]
            )
        )
    )
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
                    **patch,
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
        record["planned_transitions"].append(transition)
    for plan in plans:
        for precedence in plan["forwarding_precedences"]:
            predecessor = transition_by_id[precedence["predecessor"]]
            successor = transition_by_id[precedence["successor"]]
            edge = {
                **precedence,
                "plan_label": plan["label"],
                "predecessor_route_ref": predecessor["route_ref"],
                "successor_route_ref": successor["route_ref"],
                "predecessor_transition": predecessor,
                "successor_transition": successor,
            }
            for reference in {
                predecessor["route_ref"], successor["route_ref"]
            }:
                groups[reference]["precedences"].append(edge)
    return tuple(groups[key] for key in sorted(groups))


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
                        value for value in related if value["family"] == family
                    )
                    for family in ("connection", "parameter", "forwarding")
                },
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
