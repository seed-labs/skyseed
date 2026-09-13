"""Project detached Planner evidence into an offline semantic inspection."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from collections.abc import Callable
from typing import Any

from skyseed_v2.simulator import (
    PlannedOperationalState,
    ReachableRouteOutcome,
    apply_plan_transition,
    planned_operational_state_fingerprint,
    semantic_event_references,
)
from skyseed_v2.simulator.planning import (
    plan_transition_to_data,
    planned_operational_state_to_data,
)

from skyseed_v2.simulator.planning.evidence import (
    _frame_evidence_to_data, _frontiers_to_data, _timedelta_to_data,
    _candidate_summary_to_data, _work_counts_to_data, _inspection_to_data,
)

from skyseed_v2.simulator.planning.values import _route_demand_material

from .reader import PlannerInspectionRead
from tools._inspection_evidence import iter_physical_link_inspection


def project_planner_data(
    observation: PlannerInspectionRead,
    *,
    physical_frames: tuple[dict[str, Any], ...] = (),
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
    frame_chunk_sink: Callable[[dict[str, Any]], Path] | None = None,
    retain_internal_detail: bool = True,
) -> dict[str, Any]:
    """Replay transitions into compact summaries plus one exact final state."""

    if not isinstance(observation, PlannerInspectionRead):
        raise TypeError("observation must be a PlannerInspectionRead")
    data = deepcopy(observation.snapshot)
    data["format_version"] = 4
    physical_by_timestamp = {
        value["timestamp"]: value for value in physical_frames
    }
    candidate_records = None
    candidate_header: dict[str, Any] | None = None
    if physical_link_inspection_path is not None:
        candidate_records = iter_physical_link_inspection(
            Path(physical_link_inspection_path)
        )
        candidate_header = next(candidate_records)
        node_records = tuple(candidate_header["nodes"])
        zone_records = tuple(candidate_header.get("zone_records", ()))
    else:
        first_positions = () if not physical_frames else physical_frames[0][
            "positions"
        ]
        node_records = tuple(
            {
                "node_id": value["node_id"],
                "kind": value["kind"],
            }
            for value in first_positions
        )
        zone_records = ()
    node_ids = tuple(str(value["node_id"]) for value in node_records)
    node_index = {value: index for index, value in enumerate(node_ids)}
    tool_chunks: list[dict[str, Any]] = []
    streamed_chunk_paths: list[Path] = []
    frame_index_rows: list[dict[str, Any]] = []
    route_timelines: dict[str, dict[str, Any]] = {}
    demand_records = {}
    publications = []
    frames = []
    state = None
    previous_plan_id = None
    total_events = 0
    total_precedences = 0
    formal_transition_count = 0
    physical_isl_candidates = 0
    selected_isl_samples = 0
    acquiring_isl_samples = 0
    seen_timestamps: list[str] = []
    first_plan_id = None
    physical_world_fingerprint = None
    final_output_state_fingerprint = None
    for plan_index, evidence in enumerate(observation.evidence):
        plan = evidence.plan
        if first_plan_id is None:
            first_plan_id = plan.plan_id
            physical_world_fingerprint = plan.physical_world_fingerprint
        final_output_state_fingerprint = plan.output_state_fingerprint
        first_frame_index = formal_transition_count
        if state is None:
            if plan.input_state_fingerprint is not None:
                raise ValueError("bootstrap Plan has an input state fingerprint")
        elif (
            planned_operational_state_fingerprint(state)
            != plan.input_state_fingerprint
        ):
            raise ValueError(
                "Planner Tool replay differs from Plan input fingerprint"
            )
        plan_frame_count = 0
        for transition_index, (transition, frame_evidence) in enumerate(
            zip(plan.transitions, evidence.frames, strict=True)
        ):
            previous_state = state
            state = apply_plan_transition(state, transition)
            frame_data = (_frame_evidence_to_data(frame_evidence) if retain_internal_detail
                          else _frame_projection_facts(frame_evidence))
            event_count = len(semantic_event_references(transition))
            total_events += event_count
            selected = frame_data["selected_inter_satellite_links"]
            state_summary = _state_summary(state)
            frame = {
                "plan_index": plan_index,
                "plan_id": plan.plan_id,
                "transition_index": transition_index,
                "timestamp": transition.timestamp.isoformat(),
                "transition_fingerprint": evidence.transition_fingerprints[
                    transition_index
                ],
                "physical_candidates": {
                    "inter_satellite": frame_data[
                        "inter_satellite_candidate_summary"
                    ],
                    "ground_station": frame_data[
                        "ground_station_access_candidate_summary"
                    ],
                    "zone_service": frame_data[
                        "zone_service_candidate_summary"
                    ],
                    "work_counts": frame_data["physical_link_work_counts"],
                },
                "selected": {
                    "inter_satellite": selected,
                    "acquiring_inter_satellite_count": sum(
                        not value.operational for value in frame_evidence.selected_inter_satellite_links
                    ),
                    "operational_inter_satellite_count": sum(
                        value.operational for value in frame_evidence.selected_inter_satellite_links
                    ),
                    "ground_station_accesses": frame_data[
                        "selected_ground_station_accesses"
                    ],
                    "ground_station_preparations": frame_data[
                        "ground_station_preparations"
                    ],
                    "zone_service_candidates": frame_data[
                        "selected_zone_service_candidates"
                    ],
                },
                "state_summary": state_summary,
                "transition": plan_transition_to_data(transition) if retain_internal_detail else None,
                "semantic_event_count": event_count,
                "inspection": frame_data["inspection"],
                "state_fingerprint": planned_operational_state_fingerprint(
                    state
                ),
            }
            frame["stages"] = _stage_chain(frame, transition, frame_evidence)
            candidate_record = None
            if candidate_records is not None:
                try:
                    candidate_record = next(candidate_records)
                except StopIteration as error:
                    raise ValueError(
                        "physical-link inspection ended before Planner evidence"
                    ) from error
                if candidate_record["timestamp"] != frame["timestamp"]:
                    raise ValueError(
                        "physical-link inspection and Planner timestamps differ"
                    )
                for name, summary_name in (
                    ("inter_satellite", "inter_satellite"),
                    ("ground_station", "ground_station"),
                    ("zone_service", "zone_service"),
                ):
                    if len(candidate_record[name]) != frame[
                        "physical_candidates"
                    ][summary_name]["feasible_count"]:
                        raise ValueError(
                            "physical-link exact candidates and Planner "
                            f"{name} summary differ"
                        )
                if candidate_record["work_counts"] != frame[
                    "physical_candidates"
                ]["work_counts"]:
                    raise ValueError(
                        "physical-link work counts and Planner evidence differ"
                    )
            global_frame_index = formal_transition_count
            compact_transition = _compact_transition(
                transition,
                previous_state,
                node_index,
            )
            _extend_route_timelines(
                route_timelines, demand_records,
                transition.route_outcome_transitions,
                global_frame_index,
                node_index,
            )
            positions = physical_by_timestamp.get(frame["timestamp"], {}).get(
                "positions", ()
            )
            tool_chunk = {
                    "frame_index": global_frame_index,
                    "timestamp": frame["timestamp"],
                    "plan_index": plan_index,
                    "plan_id": plan.plan_id,
                    "transition_index": transition_index,
                    "transition_fingerprint": frame[
                        "transition_fingerprint"
                    ],
                    "state_fingerprint": frame["state_fingerprint"],
                    "positions": [
                        [
                            node_index[value["node_id"]],
                            value["ecef_m"]["x"],
                            value["ecef_m"]["y"],
                            value["ecef_m"]["z"],
                        ]
                        for value in positions
                    ],
                    "candidates": (
                        {
                            "inter_satellite": [],
                            "ground_station": [],
                            "zone_service": [],
                            "work_counts": frame["physical_candidates"][
                                "work_counts"
                            ],
                        }
                        if candidate_record is None
                        else {
                            key: candidate_record[key]
                            for key in (
                                "inter_satellite",
                                "ground_station",
                                "zone_service",
                                "work_counts",
                            )
                        }
                    ),
                    "selected": _compact_selected(frame_evidence, node_index),
                    "operational_connections": tuple(
                        _compact_connection(value, node_index)
                        for value in state.operational_connections
                    ),
                    "zone_assignments": tuple(
                        [
                            value.zone_id,
                            None
                            if value.satellite_id is None
                            else node_index[value.satellite_id.value],
                            value.network_scope,
                        ]
                        for value in state.zone_service_assignments
                    ),
                    "transition": compact_transition,
                    "state_summary": frame["state_summary"],
                    "inspection": frame["inspection"],
                    "semantic_event_count": frame["semantic_event_count"],
                    "stages": frame["stages"],
                }
            frame_index_rows.append(
                {
                    "frame_index": tool_chunk["frame_index"],
                    "timestamp": tool_chunk["timestamp"],
                    "plan_index": tool_chunk["plan_index"],
                    "chunk": f"data/frame-{global_frame_index:03d}.js",
                    "transition_fingerprint": tool_chunk[
                        "transition_fingerprint"
                    ],
                    "state_fingerprint": tool_chunk["state_fingerprint"],
                    "state_summary": tool_chunk["state_summary"],
                    "stage_counts": tuple(
                        {
                            "stage": stage["stage"],
                            "result": stage["result"],
                            "change": stage["change"],
                        }
                        for stage in tool_chunk["stages"]
                    ),
                }
            )
            if frame_chunk_sink is None:
                tool_chunks.append(tool_chunk)
            else:
                streamed_chunk_paths.append(frame_chunk_sink(tool_chunk))
            if retain_internal_detail:
                frames.append(frame)
            seen_timestamps.append(frame["timestamp"])
            physical_isl_candidates += frame["physical_candidates"][
                "inter_satellite"
            ]["feasible_count"]
            selected_isl_samples += len(frame["selected"]["inter_satellite"])
            acquiring_isl_samples += frame["selected"][
                "acquiring_inter_satellite_count"
            ]
            formal_transition_count += 1
            plan_frame_count += 1
        if state is None:
            raise ValueError("Planner evidence produced no transition state")
        if (
            planned_operational_state_fingerprint(state)
            != plan.output_state_fingerprint
        ):
            raise ValueError("Planner Tool replay differs from Plan output fingerprint")
        if retain_internal_detail:
            event_indexes = {event: i for i, event in enumerate(
                event for transition in plan.transitions for event in semantic_event_references(transition))}
            precedences = tuple([event_indexes[p.predecessor], event_indexes[p.successor]]
                                for p in plan.semantic_event_precedences)
        else:
            precedences = plan.semantic_event_precedences
        total_precedences += len(precedences)
        publications.append(
            {
                "plan_index": plan_index,
                "plan_id": plan.plan_id,
                "previous_plan_id": plan.previous_plan_id,
                "physical_world_fingerprint": plan.physical_world_fingerprint,
                "input_state_fingerprint": plan.input_state_fingerprint,
                "output_state_fingerprint": plan.output_state_fingerprint,
                "observation_interval": _timedelta_to_data(plan.observation_interval),
                "start_timestamp": plan.start_timestamp.isoformat(),
                "end_timestamp": plan.end_timestamp.isoformat(),
                "transition_count": len(plan.transitions),
                "frontiers": _frontiers_to_data(evidence.frontiers),
                **(
                    {"semantic_event_precedences": precedences}
                    if retain_internal_detail
                    else {"semantic_event_precedence_count": len(precedences)}
                ),
                "frame_indexes": tuple(
                    range(
                        first_frame_index,
                        first_frame_index + plan_frame_count,
                    )
                ),
            }
        )
        previous_plan_id = plan.plan_id

    assert state is not None
    final_state = planned_operational_state_to_data(state) if retain_internal_detail else None
    final_summary = _state_summary(state)
    data["semantic_layers"] = (
        {
            "label": "PHYSICAL CANDIDATE",
            "meaning": (
                "exact feasible candidate identities for every formal frame"
                if physical_link_inspection_path is not None
                else "bounded count/quality/reason summary of feasible facts"
            ),
            "source": (
                "Tool-owned sealed PhysicalLinkObservation stream"
                if physical_link_inspection_path is not None
                else "PlanningFrameEvidence candidate summaries"
            ),
        },
        {
            "label": "SELECTED / ACQUIRING",
            "meaning": (
                "Planner lifecycle choice; not operational until acquisition "
                "completes"
            ),
            "source": "InterSatelliteLifecycleEvidence",
        },
        {
            "label": "OPERATIONAL CONNECTION",
            "meaning": "connection present in replayed PlannedOperationalState",
            "source": "canonical PlanTransition replay",
        },
        {
            "label": "ROUTED / UNREACHABLE",
            "meaning": "explicit reachable path or unreachable reason",
            "source": "replayed RouteOutcome values",
        },
        {
            "label": "COMPILED",
            "meaning": "outside Planner evidence; inspect Compiler Tool",
            "source": None,
        },
        {
            "label": "REALIZED",
            "meaning": "outside planned and compiled intent; inspect Runner evidence",
            "source": None,
        },
    )
    data["publications"] = tuple(publications)
    data["frames"] = tuple(frames)
    if physical_frames:
        physical_by_timestamp = {
            value["timestamp"]: value for value in physical_frames
        }
        if tuple(physical_by_timestamp) != tuple(seen_timestamps):
            raise ValueError(
                "Planner and PhysicalFrame history timestamp coverage differ"
            )
        for frame in frames:
            frame["physical_positions"] = physical_by_timestamp[
                frame["timestamp"]
            ]["positions"]
    else:
        for frame in frames:
            frame["physical_positions"] = ()
    data["final_state"] = final_state
    data["inventory"] = {
        "plans": len(publications),
        "formal_transitions": formal_transition_count,
        "semantic_events": total_events,
        "semantic_precedences": total_precedences,
        "physical_isl_candidates": physical_isl_candidates,
        "selected_isl_samples": selected_isl_samples,
        "acquiring_isl_samples": acquiring_isl_samples,
        "final_operational_connections": final_summary[
            "operational_connection_count"
        ],
        "final_zones": final_summary["zone_assignment_count"],
        "final_routes_reachable": final_summary["reachable_route_count"],
        "final_routes_unreachable": final_summary["unreachable_route_count"],
    }
    data["lineage"] = {
        "physical_world_fingerprint": physical_world_fingerprint,
        "first_plan_id": first_plan_id,
        "last_plan_id": previous_plan_id,
        "final_output_state_fingerprint": final_output_state_fingerprint,
    }
    data["processing_chain"] = (
        "physical input validation",
        "ISL candidate selection",
        "acquisition / lifecycle advancement",
        "GroundStation selection / ranking",
        "GroundStation validation",
        "Zone service planning",
        "parameter planning",
        "Route planning",
        "coordination / semantic precedence",
        "planned-state advancement",
        "Plan creation / publication",
    )
    data["model_provenance"] = {
        "source": "sealed PublishedPlanningEvidence",
        "implementation_identity": (
            "not persisted in accepted planning evidence"
        ),
        "configuration": {
            "observation_interval": publications[0]["observation_interval"],
            "candidate_retention": (
                "exact per-boundary candidates in on-demand Tool chunks"
                if physical_link_inspection_path is not None
                else "bounded summaries plus exact selected facts"
            ),
            "publication_windows": tuple(
                {
                    "plan_id": value["plan_id"],
                    "start": value["start_timestamp"],
                    "end": value["end_timestamp"],
                    "formal_boundaries": value["transition_count"],
                }
                for value in publications
            ),
        },
        "limit": (
            "The Tool does not inspect private Planner fields or infer a "
            "model implementation from result shape."
        ),
        "descriptors": (
            {} if model_descriptors is None else deepcopy(model_descriptors)
        ),
    }
    if candidate_records is not None:
        try:
            next(candidate_records)
        except StopIteration:
            pass
        else:
            raise ValueError(
                "physical-link inspection continues after Planner evidence"
            )
    known_records = {str(value["node_id"]): value for value in node_records}
    data["spatial_index"] = {
        "nodes": tuple(
            deepcopy(
                known_records.get(
                    value,
                    {"node_id": value, "kind": value.split("/", 2)[1]},
                )
            )
            for value, _ in sorted(node_index.items(), key=lambda item: item[1])
        ),
        "zones": zone_records,
    }
    data["frame_index"] = tuple(frame_index_rows)
    data["route_index"] = tuple(
        {
            "key": key,
            "label": value["label"],
            "shard": value["shard"],
            "source": node_index[_node_value(value["demand"]["source"])],
            "network_scope": value["demand"]["target"]["network_scope"],
            "target": ({"kind": "zone", "zone_id": value["demand"]["target"]["zone_id"], "network_scope": value["demand"]["target"]["network_scope"]}
                       if value["demand"]["target"]["kind"] == "zone" else
                       {"kind": "ground-station", "network_scope": value["demand"]["target"]["network_scope"], "node": node_index[_node_value(value["demand"]["target"]["ground_station_id"])]}),
            # Activity/status changes are tiny; full paths stay in focused shards.
            "activity": _route_activity(value["timeline"]),
        }
        for key, value in sorted(route_timelines.items())
    )
    data["pair_index"] = _pair_index(data["route_index"], data["spatial_index"]["nodes"])
    data["large_data_layout"] = {
        "frame_chunks": formal_transition_count,
        "route_shards": len(
            {value["shard"] for value in route_timelines.values()}
        ),
        "browser_policy": (
            "load one formal boundary and at most two focused directional Route shards"
        ),
        "exact_candidate_stream": physical_link_inspection_path is not None,
    }
    data["_tool_frame_chunks"] = tuple(tool_chunks)
    data["_streamed_chunk_paths"] = tuple(streamed_chunk_paths)
    data["_route_timelines"] = route_timelines
    return data


def _node_value(value: dict[str, Any]) -> str:
    return f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"


def _node_number(node_index: dict[str, int], value: str) -> int:
    result = node_index.get(value)
    if result is None:
        result = len(node_index)
        node_index[value] = result
    return result


def _compact_parameters(value) -> list[int]:
    return [value.bandwidth_bps, value.delay_us, value.packet_loss_ppm]


def _compact_connection(value, node_index: dict[str, int]) -> list[Any]:
    identity = value.connection_id
    return [
        identity.kind.value,
        _node_number(node_index, identity.endpoint_a.value),
        _node_number(node_index, identity.endpoint_b.value),
        *_compact_parameters(value.endpoint_a_to_b),
        *_compact_parameters(value.endpoint_b_to_a),
    ]


def _compact_connection_id(value, node_index: dict[str, int]) -> list[Any]:
    return [
        value.kind.value,
        _node_number(node_index, value.endpoint_a.value),
        _node_number(node_index, value.endpoint_b.value),
    ]


def _compact_transition(
    transition,
    previous_state: PlannedOperationalState | None,
    node_index: dict[str, int],
) -> dict[str, Any]:
    previous = ({value.connection_id: value for value in previous_state.operational_connections}
                if previous_state is not None and transition.parameter_updated_connections else {})
    parameter_changes = []
    for value in transition.parameter_updated_connections:
        identity = value.connection_id
        prior = previous.get(identity)
        parameter_changes.append(
            {
                "connection": _compact_connection_id(identity, node_index),
                "previous": (
                    None
                    if prior is None
                    else [
                        *_compact_parameters(prior.endpoint_a_to_b),
                        *_compact_parameters(prior.endpoint_b_to_a),
                    ]
                ),
                "current": [
                    *_compact_parameters(value.endpoint_a_to_b),
                    *_compact_parameters(value.endpoint_b_to_a),
                ],
            }
        )
    return {
        "established": tuple(
            _compact_connection(value, node_index)
            for value in transition.established_connections
        ),
        "withdrawn": tuple(
            _compact_connection_id(value, node_index)
            for value in transition.withdrawn_connection_ids
        ),
        "parameter_changes": tuple(parameter_changes),
        "zone_changes": tuple(
            [
                value.zone_id,
                None
                if value.previous_satellite_id is None
                else _node_number(
                    node_index, value.previous_satellite_id.value
                ),
                None
                if value.current_satellite_id is None
                else _node_number(node_index, value.current_satellite_id.value),
                value.network_scope,
            ]
            for value in transition.zone_service_transitions
        ),
        "route_change_count": len(transition.route_outcome_transitions),
    }


def _frame_projection_facts(evidence):
    """Serialize small summaries; keep validated selections in their native form."""
    return {
        "inter_satellite_candidate_summary": _candidate_summary_to_data(evidence.inter_satellite_candidate_summary),
        "ground_station_access_candidate_summary": _candidate_summary_to_data(evidence.ground_station_access_candidate_summary),
        "zone_service_candidate_summary": _candidate_summary_to_data(evidence.zone_service_candidate_summary),
        "physical_link_work_counts": _work_counts_to_data(evidence.physical_link_work_counts),
        "selected_inter_satellite_links": evidence.selected_inter_satellite_links,
        "selected_ground_station_accesses": evidence.selected_ground_station_accesses,
        "selected_zone_service_candidates": evidence.selected_zone_service_candidates,
        "ground_station_preparations": evidence.ground_station_preparations,
        "inspection": _inspection_to_data(evidence.inspection),
    }


def _compact_selected(evidence, node_index):
    def node(value):
        return _node_number(node_index, value.value)
    return {
        "inter_satellite": tuple([
            node(v.candidate.endpoint_a), node(v.candidate.endpoint_b), int(v.operational),
            v.selected_since.isoformat(), _timedelta_to_data(v.acquisition_duration),
            _timedelta_to_data(v.remaining_acquisition), v.candidate.distance_m,
            v.candidate.propagation_delay_us,
        ] for v in evidence.selected_inter_satellite_links),
        "ground_station": tuple([
            node(v.ground_station_id), node(v.satellite_id), v.distance_m,
            v.elevation_deg, v.propagation_delay_us,
        ] for v in evidence.selected_ground_station_accesses),
        "ground_station_preparations": tuple([
            node(v.candidate.ground_station_id), node(v.candidate.satellite_id), int(v.operational),
            v.selected_since.isoformat(), _timedelta_to_data(v.acquisition_duration),
            _timedelta_to_data(v.remaining_acquisition),
        ] for v in evidence.ground_station_preparations),
        "zone_service": tuple([
            v.zone_id, node(v.satellite_id), v.distance_m, v.elevation_deg, v.propagation_delay_us,
        ] for v in evidence.selected_zone_service_candidates),
        "acquiring_inter_satellite_count": sum(not v.operational for v in evidence.selected_inter_satellite_links),
        "operational_inter_satellite_count": sum(v.operational for v in evidence.selected_inter_satellite_links),
    }


def _extend_route_timelines(timelines, demand_records, transitions, frame_index, node_index):
    for change in transitions:
        record = demand_records.get(change.demand)
        if record is None:
            demand = _route_demand_material(change.demand)
            canonical = json.dumps(demand, sort_keys=True, separators=(",", ":"))
            key = sha256(canonical.encode("utf-8")).hexdigest()
            source, target = _node_value(demand["source"]), demand["target"]
            label = (f"{target.get('network_scope', '')} {source} → Zone {target['zone_id']}" if target["kind"] == "zone"
                     else f"{source} → {_node_value(target['ground_station_id'])}")
            record = {"label": label, "demand": demand, "shard": int(key[:2], 16) % 32, "timeline": []}
            demand_records[change.demand] = timelines[key] = record
        current = change.current
        if current is None:
            compact = None
        elif isinstance(current, ReachableRouteOutcome):
            compact = ["reachable", _node_number(node_index, current.next_hop.value),
                       tuple(_node_number(node_index, node.value) for node in current.path)]
        else:
            compact = ["unreachable", current.reason.value]
        record["timeline"].append([frame_index, compact])


def _stage_chain(frame: dict[str, Any], transition, evidence) -> tuple[dict[str, Any], ...]:
    """Expose the accepted order using only already-retained stage facts."""

    physical = frame["physical_candidates"]
    selected = frame["selected"]
    state = frame["state_summary"]
    established_by_kind: dict[str, int] = {}
    for value in transition.established_connections:
        kind = value.connection_id.kind.value
        established_by_kind[kind] = established_by_kind.get(kind, 0) + 1
    withdrawn_by_kind: dict[str, int] = {}
    for value in transition.withdrawn_connection_ids:
        kind = value.kind.value
        withdrawn_by_kind[kind] = withdrawn_by_kind.get(kind, 0) + 1
    return (
        {
            "stage": "physical input validation",
            "input": physical["work_counts"],
            "model": "Planner input contract",
            "result": {
                "isl_feasible": physical["inter_satellite"]["feasible_count"],
                "gs_feasible": physical["ground_station"]["feasible_count"],
                "zone_feasible": physical["zone_service"]["feasible_count"],
            },
            "change": {},
        },
        {
            "stage": "ISL candidate selection",
            "input": physical["inter_satellite"],
            "model": "InterSatelliteLinkPlanningModel",
            "result": {"selected": len(selected["inter_satellite"])},
            "change": {},
        },
        {
            "stage": "acquisition / lifecycle advancement",
            "input": {
                "selected": len(selected["inter_satellite"]),
                "acquiring": selected["acquiring_inter_satellite_count"],
            },
            "model": "accepted acquisition lifecycle",
            "result": {
                "operational": selected["operational_inter_satellite_count"]
            },
            "change": {
                "established": established_by_kind.get("inter-satellite", 0),
                "withdrawn": withdrawn_by_kind.get("inter-satellite", 0),
            },
        },
        {
            "stage": "GroundStation selection / ranking",
            "input": physical["ground_station"],
            "model": "GroundStationAccessPlanningModel",
            "result": {
                "selected": len(selected["ground_station_accesses"]),
                "reserved": len(selected["ground_station_preparations"]),
                "preparing": sum(not value.operational for value in evidence.ground_station_preparations),
            },
            "change": {},
        },
        {
            "stage": "GroundStation validation",
            "input": {"selected": len(selected["ground_station_accesses"])},
            "model": "Planner GroundStation validation",
            "result": {"validated": len(selected["ground_station_accesses"])},
            "change": {
                "established": established_by_kind.get("ground-station", 0),
                "withdrawn": withdrawn_by_kind.get("ground-station", 0),
            },
        },
        {
            "stage": "Zone service planning",
            "input": physical["zone_service"],
            "model": "ZoneServicePlanningModel",
            "result": {
                "selected": len(selected["zone_service_candidates"]),
                "served": state["zones_served"],
                "unserved": state["zones_unserved"],
            },
            "change": {
                "transitions": len(transition.zone_service_transitions)
            },
        },
        {
            "stage": "parameter planning",
            "input": {
                "connections": state[
                    "operational_connection_count"
                ]
            },
            "model": "ConnectionParameterPlanningModel",
            "result": {},
            "change": {
                "updates": len(
                    transition.parameter_updated_connections
                )
            },
        },
        {
            "stage": "Route planning",
            "input": {
                "connections": state[
                    "operational_connection_count"
                ]
            },
            "model": "RoutePlanningModel",
            "result": {
                "reachable": state["reachable_route_count"],
                "unreachable": state["unreachable_route_count"],
            },
            "change": {
                "transitions": len(transition.route_outcome_transitions)
            },
        },
        {
            "stage": "coordination / semantic precedence",
            "input": {"semantic_events": frame["semantic_event_count"]},
            "model": "CoordinationModel",
            "result": {"ordered_events": frame["semantic_event_count"]},
            "change": {},
        },
        {
            "stage": "planned-state advancement",
            "input": {"transition": frame["transition_fingerprint"]},
            "model": "canonical PlanTransition application",
            "result": {"state": frame["state_fingerprint"]},
            "change": {
                "established": len(transition.established_connections),
                "withdrawn": len(transition.withdrawn_connection_ids),
            },
        },
    )


def _state_summary(value: PlannedOperationalState) -> dict[str, int]:
    assignments = value.zone_service_assignments
    reachable = sum(
        isinstance(outcome, ReachableRouteOutcome)
        for outcome in value.route_outcomes
    )
    served = sum(
        assignment.satellite_id is not None for assignment in assignments
    )
    return {
        "operational_connection_count": len(value.operational_connections),
        "zone_assignment_count": len(assignments),
        "zones_served": served,
        "zones_unserved": len(assignments) - served,
        "reachable_route_count": reachable,
        "unreachable_route_count": len(value.route_outcomes) - reachable,
    }


__all__ = ["project_planner_data"]


def _route_activity(timeline):
    result = []
    for index, outcome in timeline:
        status = None if outcome is None else outcome[0]
        if not result or result[-1][1] != status:
            result.append([index, status])
    return result


def _pair_index(routes, nodes):
    """Index recorded GS→Zone demands; serving identities remain frame facts.

    The reverse demand is resolved against the current Zone assignment by the
    view, never manufactured by reversing a path or interpreting a display name.
    """
    pairs = {}
    for i, route in enumerate(routes):
        source, target = route['source'], route['target']
        if nodes[source]['kind'] != 'ground-station' or target['kind'] != 'zone':
            continue
        identity = (target.get('network_scope', ''), target['zone_id'], nodes[source]['node_id'])
        if identity in pairs:
            raise ValueError('multiple recorded demands for one typed Zone–GS pair')
        pairs[identity] = {'key': json.dumps(identity, separators=(',', ':')),
                           'zone_id': target['zone_id'], 'network_scope': target.get('network_scope', ''), 'ground_station': source,
                           'forward': i}
    return tuple(pairs[key] for key in sorted(pairs))
