"""Deterministic detached codecs for Planner-owned semantic evidence."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from skyseed_v2.shared import NodeId

from ..physical import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalLinkWorkCounts,
    PhysicalContactWindow,
    ZoneServiceCandidate,
)
from .diagnostics import (
    PlanningInspection,
    InterSatelliteLifecycleEvidence,
    GroundStationLifecycleEvidence,
    PlanningCandidateHistogram,
    PlanningCandidateSummary,
    PlanningFrameEvidence,
    PlanningFrontiers,
    PlanningHistogramBin,
    PublishedPlanningEvidence,
)
from .values import (
    EffectiveConnectionParameters,
    GroundStationRouteDemandTarget,
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    Plan,
    PlanTransition,
    PlannedOperationalState,
    ReachableRouteOutcome,
    RouteDemand,
    RouteOutcome,
    RouteOutcomeTransition,
    RouteUnreachableReason,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    ZoneServiceTransition,
    _plan_transition_material,
    _planned_operational_state_material,
)


def plan_transition_to_data(value: PlanTransition) -> dict[str, object]:
    """Return a detached canonical JSON-ready PlanTransition record."""

    if not isinstance(value, PlanTransition):
        raise TypeError("value must be a PlanTransition")
    result = _plan_transition_material(value)
    result["schema"] = "skyseed-v2-plan-transition-evidence-v5"
    return result


def plan_transition_from_data(value: Mapping[str, object]) -> PlanTransition:
    """Reconstruct and validate one canonical PlanTransition record."""

    record = _record(
        value,
        {
            "schema",
            "timestamp",
            "established_connections",
            "withdrawn_connection_ids",
            "parameter_updated_connections",
            "zone_service_transitions",
            "route_outcome_transitions",
            "semantic_event_precedences",
        },
        "PlanTransition",
    )
    _schema(
        record,
        "skyseed-v2-plan-transition-evidence-v5",
        "PlanTransition",
    )
    return PlanTransition._from_indexed_precedences(
        timestamp=_timestamp(record["timestamp"], "timestamp"),
        established_connections=tuple(
            _connection_from_data(item)
            for item in _records(
                record["established_connections"],
                "established_connections",
            )
        ),
        withdrawn_connection_ids=tuple(
            _connection_id_from_data(item)
            for item in _records(
                record["withdrawn_connection_ids"],
                "withdrawn_connection_ids",
            )
        ),
        parameter_updated_connections=tuple(
            _connection_from_data(item)
            for item in _records(
                record["parameter_updated_connections"],
                "parameter_updated_connections",
            )
        ),
        zone_service_transitions=tuple(
            _zone_transition_from_data(item)
            for item in _records(
                record["zone_service_transitions"],
                "zone_service_transitions",
            )
        ),
        route_outcome_transitions=tuple(
            _route_transition_from_data(item)
            for item in _records(
                record["route_outcome_transitions"],
                "route_outcome_transitions",
            )
        ),
        precedence_indexes=_precedence_indexes_from_data(record["semantic_event_precedences"]),
    )


def planned_operational_state_to_data(
    value: PlannedOperationalState,
) -> dict[str, object]:
    """Return a detached canonical JSON-ready semantic state record."""

    if not isinstance(value, PlannedOperationalState):
        raise TypeError("value must be a PlannedOperationalState")
    return _planned_operational_state_material(value)


def planned_operational_state_from_data(
    value: Mapping[str, object],
) -> PlannedOperationalState:
    """Reconstruct and validate one complete operational state record."""

    record = _record(
        value,
        {
            "schema",
            "timestamp",
            "operational_connections",
            "zone_service_assignments",
            "route_outcomes",
        },
        "PlannedOperationalState",
    )
    _schema(
        record,
        "skyseed-v2-planned-operational-state-v2",
        "PlannedOperationalState",
    )
    return PlannedOperationalState(
        timestamp=_timestamp(record["timestamp"], "timestamp"),
        operational_connections=tuple(
            _connection_from_data(item)
            for item in _records(
                record["operational_connections"],
                "operational_connections",
            )
        ),
        zone_service_assignments=tuple(
            _zone_assignment_from_data(item)
            for item in _records(
                record["zone_service_assignments"],
                "zone_service_assignments",
            )
        ),
        route_outcomes=tuple(
            _route_outcome_from_data(item)
            for item in _records(record["route_outcomes"], "route_outcomes")
        ),
    )


def plan_to_data(value: Plan) -> dict[str, object]:
    """Return a detached canonical JSON-ready self-contained Plan record."""

    if not isinstance(value, Plan):
        raise TypeError("value must be a Plan")
    return {
        "schema": "skyseed-v2-plan-evidence-v5",
        "plan_id": value.plan_id,
        "previous_plan_id": value.previous_plan_id,
        "physical_world_fingerprint": value.physical_world_fingerprint,
        "input_state_fingerprint": value.input_state_fingerprint,
        "output_state_fingerprint": value.output_state_fingerprint,
        "observation_interval": _timedelta_to_data(
            value.observation_interval
        ),
        "start_timestamp": value.start_timestamp.isoformat(),
        "end_timestamp": value.end_timestamp.isoformat(),
        "transitions": [
            plan_transition_to_data(item) for item in value.transitions
        ],

    }


def plan_from_data(value: Mapping[str, object]) -> Plan:
    """Reconstruct a Plan and revalidate its canonical ID and references."""

    record = _record(
        value,
        {
            "schema",
            "plan_id",
            "previous_plan_id",
            "physical_world_fingerprint",
            "input_state_fingerprint",
            "output_state_fingerprint",
            "observation_interval",
            "start_timestamp",
            "end_timestamp",
            "transitions",
        },
        "Plan",
    )
    _schema(record, "skyseed-v2-plan-evidence-v5", "Plan")
    transitions = tuple(
        plan_transition_from_data(item)
        for item in _records(record["transitions"], "transitions")
    )
    return Plan(
        plan_id=_string(record["plan_id"], "plan_id"),
        previous_plan_id=_optional_string(
            record["previous_plan_id"], "previous_plan_id"
        ),
        physical_world_fingerprint=_string(
            record["physical_world_fingerprint"],
            "physical_world_fingerprint",
        ),
        input_state_fingerprint=_optional_string(
            record["input_state_fingerprint"],
            "input_state_fingerprint",
        ),
        output_state_fingerprint=_string(
            record["output_state_fingerprint"],
            "output_state_fingerprint",
        ),
        observation_interval=_timedelta_from_data(
            _mapping(record["observation_interval"], "observation_interval")
        ),
        start_timestamp=_timestamp(
            record["start_timestamp"], "start_timestamp"
        ),
        end_timestamp=_timestamp(record["end_timestamp"], "end_timestamp"),
        transitions=transitions,
    )


def published_planning_evidence_to_data(
    value: PublishedPlanningEvidence,
) -> dict[str, object]:
    """Return one detached Planner evidence envelope."""

    if not isinstance(value, PublishedPlanningEvidence):
        raise TypeError("value must be PublishedPlanningEvidence")
    return {
        "schema": "skyseed-v2-published-planning-evidence-v5",
        "plan": plan_to_data(value.plan),
        "transition_fingerprints": list(value.transition_fingerprints),
        "frames": [
            _frame_evidence_to_data(item) for item in value.frames
        ],
        "frontiers": _frontiers_to_data(value.frontiers),
    }


def published_planning_evidence_from_data(
    value: Mapping[str, object],
) -> PublishedPlanningEvidence:
    """Reconstruct and cross-validate one detached evidence envelope."""

    record = _record(
        value,
        {
            "schema",
            "plan",
            "transition_fingerprints",
            "frames",
            "frontiers",
        },
        "PublishedPlanningEvidence",
    )
    _schema(record, "skyseed-v2-published-planning-evidence-v5", "PublishedPlanningEvidence")
    fingerprint_values = _list(
        record["transition_fingerprints"], "transition_fingerprints"
    )
    return PublishedPlanningEvidence(
        plan=plan_from_data(_mapping(record["plan"], "plan")),
        transition_fingerprints=tuple(
            _string(item, "transition_fingerprint")
            for item in fingerprint_values
        ),
        frames=tuple(
            _frame_evidence_from_data(item)
            for item in _records(record["frames"], "frames")
        ),
        frontiers=_frontiers_from_data(
            _mapping(record["frontiers"], "frontiers")
        ),
    )


def _connection_from_data(value: Mapping[str, object]) -> OperationalConnection:
    record = _record(
        value,
        {"connection_id", "endpoint_a_to_b", "endpoint_b_to_a"},
        "OperationalConnection",
    )
    return OperationalConnection(
        connection_id=_connection_id_from_data(
            _mapping(record["connection_id"], "connection_id")
        ),
        endpoint_a_to_b=_parameters_from_data(
            _mapping(record["endpoint_a_to_b"], "endpoint_a_to_b")
        ),
        endpoint_b_to_a=_parameters_from_data(
            _mapping(record["endpoint_b_to_a"], "endpoint_b_to_a")
        ),
    )


def _connection_id_from_data(
    value: Mapping[str, object],
) -> OperationalConnectionId:
    record = _record(
        value,
        {"kind", "endpoint_a", "endpoint_b"},
        "OperationalConnectionId",
    )
    return OperationalConnectionId(
        kind=OperationalConnectionKind(_string(record["kind"], "kind")),
        endpoint_a=_node_from_data(
            _mapping(record["endpoint_a"], "endpoint_a")
        ),
        endpoint_b=_node_from_data(
            _mapping(record["endpoint_b"], "endpoint_b")
        ),
    )


def _parameters_from_data(
    value: Mapping[str, object],
) -> EffectiveConnectionParameters:
    record = _record(
        value,
        {"bandwidth_bps", "delay_us", "packet_loss_ppm"},
        "EffectiveConnectionParameters",
    )
    return EffectiveConnectionParameters(
        bandwidth_bps=_integer(record["bandwidth_bps"], "bandwidth_bps"),
        delay_us=_integer(record["delay_us"], "delay_us"),
        packet_loss_ppm=_integer(
            record["packet_loss_ppm"], "packet_loss_ppm"
        ),
    )


def _zone_assignment_from_data(
    value: Mapping[str, object],
) -> ZoneServiceAssignment:
    record = _record(
        value,
        {"zone_id", "satellite_id", "network_scope"},
        "ZoneServiceAssignment",
    )
    return ZoneServiceAssignment(
        zone_id=_string(record["zone_id"], "zone_id"),
        network_scope=record["network_scope"],
        satellite_id=_optional_node_from_data(
            record["satellite_id"], "satellite_id"
        ),
    )


def _zone_transition_from_data(
    value: Mapping[str, object],
) -> ZoneServiceTransition:
    record = _record(
        value,
        {"zone_id", "previous_satellite_id", "current_satellite_id", "network_scope"},
        "ZoneServiceTransition",
    )
    return ZoneServiceTransition(
        zone_id=_string(record["zone_id"], "zone_id"),
        network_scope=record["network_scope"],
        previous_satellite_id=_optional_node_from_data(
            record["previous_satellite_id"], "previous_satellite_id"
        ),
        current_satellite_id=_optional_node_from_data(
            record["current_satellite_id"], "current_satellite_id"
        ),
    )


def _route_transition_from_data(
    value: Mapping[str, object],
) -> RouteOutcomeTransition:
    record = _record(
        value,
        {"demand", "previous", "current"},
        "RouteOutcomeTransition",
    )
    demand = _route_demand_from_data(_mapping(record["demand"], "demand"))
    return RouteOutcomeTransition(
        demand=demand,
        previous=_optional_compact_route_outcome_from_data(
            record["previous"], "previous", demand=demand
        ),
        current=_optional_compact_route_outcome_from_data(
            record["current"], "current", demand=demand
        ),
    )


def _route_demand_from_data(value: Mapping[str, object]) -> RouteDemand:
    record = _record(value, {"source", "target"}, "RouteDemand")
    target = _record(
        _mapping(record["target"], "target"),
        None,
        "RouteDemandTarget",
    )
    kind = _string(target.get("kind"), "target.kind")
    if kind == "ground-station":
        _exact_keys(target, {"kind", "ground_station_id", "network_scope"}, "RouteDemandTarget")
        route_target = GroundStationRouteDemandTarget(
            _node_from_data(
                _mapping(
                    target["ground_station_id"], "target.ground_station_id"
                )
            ), target["network_scope"]
        )
    elif kind == "zone":
        _exact_keys(target, {"kind", "zone_id", "network_scope"}, "RouteDemandTarget")
        route_target = ZoneRouteDemandTarget(
            _string(target["zone_id"], "target.zone_id"), target["network_scope"]
        )
    else:
        raise ValueError("target.kind is unsupported")
    return RouteDemand(
        source=_node_from_data(_mapping(record["source"], "source")),
        target=route_target,
    )


def _route_outcome_from_data(value: Mapping[str, object], *, demand=None) -> RouteOutcome:
    record = _record(value, None, "RouteOutcome")
    kind = _string(record.get("kind"), "RouteOutcome.kind")
    demand_fields = {"demand"} if demand is None else set()
    if demand is None:
        demand = _route_demand_from_data(_mapping(record.get("demand"), "demand"))
    if kind == "reachable":
        _exact_keys(
            record,
            demand_fields | {"kind", "next_hop", "path"},
            "ReachableRouteOutcome",
        )
        return ReachableRouteOutcome(
            demand=demand,
            next_hop=_node_from_data(
                _mapping(record["next_hop"], "next_hop")
            ),
            path=tuple(
                _node_from_data(item)
                for item in _records(record["path"], "path")
            ),
        )
    if kind == "unreachable":
        _exact_keys(
            record,
            demand_fields | {"kind", "reason"},
            "UnreachableRouteOutcome",
        )
        return UnreachableRouteOutcome(
            demand=demand,
            reason=RouteUnreachableReason(
                _string(record["reason"], "reason")
            ),
        )
    raise ValueError("RouteOutcome.kind is unsupported")


def _optional_compact_route_outcome_from_data(
    value: object,
    field_name: str,
    *,
    demand: RouteDemand,
) -> RouteOutcome | None:
    if value is None:
        return None
    compact = _record(
        _mapping(value, field_name),
        None,
        field_name,
    )
    return _route_outcome_from_data(compact, demand=demand)


def _precedence_indexes_from_data(value: object) -> tuple[tuple[int, int], ...]:
    pairs = []
    for pair in _list(value, "semantic_event_precedences"):
        values = _list(pair, "semantic precedence index pair")
        if len(values) != 2:
            raise ValueError("semantic precedence must contain two event indexes")
        pairs.append((_integer(values[0], "predecessor index"),
                      _integer(values[1], "successor index")))
    return tuple(pairs)


def _frame_evidence_to_data(
    value: PlanningFrameEvidence,
) -> dict[str, object]:
    return {
        "timestamp": value.timestamp.isoformat(),
        "inter_satellite_candidate_summary": _candidate_summary_to_data(
            value.inter_satellite_candidate_summary
        ),
        "ground_station_access_candidate_summary": _candidate_summary_to_data(
            value.ground_station_access_candidate_summary
        ),
        "zone_service_candidate_summary": _candidate_summary_to_data(
            value.zone_service_candidate_summary
        ),
        "physical_link_work_counts": _work_counts_to_data(
            value.physical_link_work_counts
        ),
        "selected_inter_satellite_links": [
            _lifecycle_evidence_to_data(item)
            for item in value.selected_inter_satellite_links
        ],
        "selected_ground_station_accesses": [
            _ground_candidate_to_data(item)
            for item in value.selected_ground_station_accesses
        ],
        "selected_zone_service_candidates": [
            _zone_candidate_to_data(item)
            for item in value.selected_zone_service_candidates
        ],
        "ground_station_preparations": [
            _ground_lifecycle_to_data(item) for item in value.ground_station_preparations
        ],
        "inspection": _inspection_to_data(value.inspection),
    }


def _frame_evidence_from_data(
    value: Mapping[str, object],
) -> PlanningFrameEvidence:
    record = _record(
        value,
        {
            "ground_station_preparations",
            "timestamp",
            "inter_satellite_candidate_summary",
            "ground_station_access_candidate_summary",
            "zone_service_candidate_summary",
            "physical_link_work_counts",
            "selected_inter_satellite_links",
            "selected_ground_station_accesses",
            "selected_zone_service_candidates",
            "inspection",
        },
        "PlanningFrameEvidence",
    )
    return PlanningFrameEvidence(
        timestamp=_timestamp(record["timestamp"], "timestamp"),
        inter_satellite_candidate_summary=_candidate_summary_from_data(
            _mapping(
                record["inter_satellite_candidate_summary"],
                "inter_satellite_candidate_summary",
            )
        ),
        ground_station_access_candidate_summary=_candidate_summary_from_data(
            _mapping(
                record["ground_station_access_candidate_summary"],
                "ground_station_access_candidate_summary",
            )
        ),
        zone_service_candidate_summary=_candidate_summary_from_data(
            _mapping(
                record["zone_service_candidate_summary"],
                "zone_service_candidate_summary",
            )
        ),
        physical_link_work_counts=_work_counts_from_data(record["physical_link_work_counts"]),
        selected_inter_satellite_links=tuple(
            _lifecycle_evidence_from_data(item)
            for item in _records(
                record["selected_inter_satellite_links"],
                "selected_inter_satellite_links",
            )
        ),
        selected_ground_station_accesses=tuple(
            _ground_candidate_from_data(item)
            for item in _records(
                record["selected_ground_station_accesses"],
                "selected_ground_station_accesses",
            )
        ),
        selected_zone_service_candidates=tuple(
            _zone_candidate_from_data(item)
            for item in _records(
                record["selected_zone_service_candidates"],
                "selected_zone_service_candidates",
            )
        ),
        ground_station_preparations=tuple(
            _ground_lifecycle_from_data(item)
            for item in _records(record["ground_station_preparations"], "ground_station_preparations")
        ),
        inspection=_inspection_from_data(
            _mapping(record["inspection"], "inspection")
        ),
    )


def _isl_candidate_to_data(
    value: InterSatelliteLinkCandidate,
) -> dict[str, object]:
    return {
        "contact_window": _contact_window_to_data(value.contact_window),
        "endpoint_a": _node_to_data(value.endpoint_a),
        "endpoint_b": _node_to_data(value.endpoint_b),
        "same_plane": value.same_plane,
        "cyclic_plane_separation": value.cyclic_plane_separation,
        "distance_m": value.distance_m,
        "propagation_delay_us": value.propagation_delay_us,
    }


def _candidate_summary_to_data(
    value: PlanningCandidateSummary,
) -> dict[str, object]:
    return {
        "feasible_count": value.feasible_count,
        "histograms": [
            {
                "name": histogram.name,
                "sample_count": histogram.sample_count,
                "bins": [
                    {"label": item.label, "count": item.count}
                    for item in histogram.bins
                ],
            }
            for histogram in value.histograms
        ],
    }


def _candidate_summary_from_data(
    value: Mapping[str, object],
) -> PlanningCandidateSummary:
    record = _record(
        value, {"feasible_count", "histograms"}, "PlanningCandidateSummary"
    )
    histograms = []
    for item in _records(record["histograms"], "histograms"):
        histogram = _record(
            item,
            {"name", "sample_count", "bins"},
            "PlanningCandidateHistogram",
        )
        bins = tuple(
            _histogram_bin_from_data(value)
            for value in _records(histogram["bins"], "bins")
        )
        histograms.append(
            PlanningCandidateHistogram(
                name=_string(histogram["name"], "name"),
                sample_count=_integer(
                    histogram["sample_count"], "sample_count"
                ),
                bins=bins,
            )
        )
    return PlanningCandidateSummary(
        feasible_count=_integer(record["feasible_count"], "feasible_count"),
        histograms=tuple(histograms),
    )


def _histogram_bin_from_data(
    value: Mapping[str, object],
) -> PlanningHistogramBin:
    record = _record(
        value, {"label", "count"}, "PlanningHistogramBin"
    )
    return PlanningHistogramBin(
        label=_string(record["label"], "label"),
        count=_integer(record["count"], "count"),
    )


def _isl_candidate_from_data(
    value: Mapping[str, object],
) -> InterSatelliteLinkCandidate:
    record = _record(
        value,
        {
            "contact_window",
            "endpoint_a",
            "endpoint_b",
            "same_plane",
            "cyclic_plane_separation",
            "distance_m",
            "propagation_delay_us",
        },
        "InterSatelliteLinkCandidate",
    )
    return InterSatelliteLinkCandidate(
        contact_window=_contact_window_from_data(record["contact_window"]),
        endpoint_a=_node_from_data(
            _mapping(record["endpoint_a"], "endpoint_a")
        ),
        endpoint_b=_node_from_data(
            _mapping(record["endpoint_b"], "endpoint_b")
        ),
        same_plane=(None if record["same_plane"] is None else _boolean(record["same_plane"], "same_plane")),
        cyclic_plane_separation=(None if record["cyclic_plane_separation"] is None else _integer(record["cyclic_plane_separation"], "cyclic_plane_separation")),
        distance_m=(None if record["distance_m"] is None else _number(record["distance_m"], "distance_m")),
        propagation_delay_us=(None if record["propagation_delay_us"] is None else _integer(record["propagation_delay_us"], "propagation_delay_us")),
    )


def _ground_candidate_to_data(
    value: GroundStationAccessCandidate,
) -> dict[str, object]:
    return {
        "contact_window": _contact_window_to_data(value.contact_window),
        "ground_station_id": _node_to_data(value.ground_station_id),
        "satellite_id": _node_to_data(value.satellite_id),
        "distance_m": value.distance_m,
        "elevation_deg": value.elevation_deg,
        "propagation_delay_us": value.propagation_delay_us,
    }


def _ground_candidate_from_data(
    value: Mapping[str, object],
) -> GroundStationAccessCandidate:
    record = _record(
        value,
        {
            "contact_window",
            "ground_station_id",
            "satellite_id",
            "distance_m",
            "elevation_deg",
            "propagation_delay_us",
        },
        "GroundStationAccessCandidate",
    )
    return GroundStationAccessCandidate(
        contact_window=_contact_window_from_data(record["contact_window"]),
        ground_station_id=_node_from_data(
            _mapping(record["ground_station_id"], "ground_station_id")
        ),
        satellite_id=_node_from_data(
            _mapping(record["satellite_id"], "satellite_id")
        ),
        distance_m=(None if record["distance_m"] is None else _number(record["distance_m"], "distance_m")),
        elevation_deg=(None if record["elevation_deg"] is None else _number(record["elevation_deg"], "elevation_deg")),
        propagation_delay_us=(None if record["propagation_delay_us"] is None else _integer(record["propagation_delay_us"], "propagation_delay_us")),
    )


def _zone_candidate_to_data(
    value: ZoneServiceCandidate,
) -> dict[str, object]:
    return {
        "zone_id": value.zone_id,
        "satellite_id": _node_to_data(value.satellite_id),
        "distance_m": value.distance_m,
        "elevation_deg": value.elevation_deg,
        "propagation_delay_us": value.propagation_delay_us,
    }


def _zone_candidate_from_data(
    value: Mapping[str, object],
) -> ZoneServiceCandidate:
    record = _record(
        value,
        {
            "zone_id",
            "satellite_id",
            "distance_m",
            "elevation_deg",
            "propagation_delay_us",
        },
        "ZoneServiceCandidate",
    )
    return ZoneServiceCandidate(
        zone_id=_string(record["zone_id"], "zone_id"),
        satellite_id=_node_from_data(
            _mapping(record["satellite_id"], "satellite_id")
        ),
        distance_m=(None if record["distance_m"] is None else _number(record["distance_m"], "distance_m")),
        elevation_deg=(None if record["elevation_deg"] is None else _number(record["elevation_deg"], "elevation_deg")),
        propagation_delay_us=(None if record["propagation_delay_us"] is None else _integer(record["propagation_delay_us"], "propagation_delay_us")),
    )


_WORK_COUNT_FIELDS = (
    "inter_satellite_pairs_requested",
    "inter_satellite_pairs_evaluated",
    "inter_satellite_pairs_feasible",
    "ground_station_pairs_requested",
    "ground_station_pairs_evaluated",
    "ground_station_pairs_feasible",
    "zone_service_pairs_requested",
    "zone_service_pairs_evaluated",
    "zone_service_pairs_feasible",
    "ground_station_pairs_not_returned",
    "inter_satellite_pairs_not_returned",
)


def _work_counts_to_data(value: PhysicalLinkWorkCounts | None) -> dict[str, int] | None:
    if value is None:
        return None
    return {name: getattr(value, name) for name in _WORK_COUNT_FIELDS}


def _work_counts_from_data(
    value: Mapping[str, object] | None,
) -> PhysicalLinkWorkCounts | None:
    if value is None:
        return None
    record = _record(
        value,
        set(_WORK_COUNT_FIELDS), "PhysicalLinkWorkCounts"
    )
    return PhysicalLinkWorkCounts(
        **{name: _integer(record[name], name) for name in _WORK_COUNT_FIELDS}
    )


def _lifecycle_evidence_to_data(
    value: InterSatelliteLifecycleEvidence,
) -> dict[str, object]:
    return {
        "candidate": _isl_candidate_to_data(value.candidate),
        "selected_since": value.selected_since.isoformat(),
        "acquisition_duration": _timedelta_to_data(
            value.acquisition_duration
        ),
        "remaining_acquisition": _timedelta_to_data(
            value.remaining_acquisition
        ),
        "operational": value.operational,
    }


def _lifecycle_evidence_from_data(
    value: Mapping[str, object],
) -> InterSatelliteLifecycleEvidence:
    record = _record(
        value,
        {
            "candidate",
            "selected_since",
            "acquisition_duration",
            "remaining_acquisition",
            "operational",
        },
        "InterSatelliteLifecycleEvidence",
    )
    return InterSatelliteLifecycleEvidence(
        candidate=_isl_candidate_from_data(
            _mapping(record["candidate"], "candidate")
        ),
        selected_since=_timestamp(record["selected_since"], "selected_since"),
        acquisition_duration=_timedelta_from_data(
            _mapping(record["acquisition_duration"], "acquisition_duration")
        ),
        remaining_acquisition=_timedelta_from_data(
            _mapping(record["remaining_acquisition"], "remaining_acquisition")
        ),
        operational=_boolean(record["operational"], "operational"),
    )


def _ground_lifecycle_to_data(
    value: GroundStationLifecycleEvidence,
) -> dict[str, object]:
    return {
        "candidate": _ground_candidate_to_data(value.candidate),
        "selected_since": value.selected_since.isoformat(),
        "acquisition_duration": _timedelta_to_data(
            value.acquisition_duration
        ),
        "remaining_acquisition": _timedelta_to_data(
            value.remaining_acquisition
        ),
        "operational": value.operational,
    }


def _ground_lifecycle_from_data(
    value: Mapping[str, object],
) -> GroundStationLifecycleEvidence:
    record = _record(
        value,
        {
            "candidate",
            "selected_since",
            "acquisition_duration",
            "remaining_acquisition",
            "operational",
        },
        "GroundStationLifecycleEvidence",
    )
    return GroundStationLifecycleEvidence(
        candidate=_ground_candidate_from_data(
            _mapping(record["candidate"], "candidate")
        ),
        selected_since=_timestamp(record["selected_since"], "selected_since"),
        acquisition_duration=_timedelta_from_data(
            _mapping(record["acquisition_duration"], "acquisition_duration")
        ),
        remaining_acquisition=_timedelta_from_data(
            _mapping(record["remaining_acquisition"], "remaining_acquisition")
        ),
        operational=_boolean(record["operational"], "operational"),
    )


def _inspection_to_data(value: PlanningInspection) -> dict[str, object]:
    return {
        "timestamp": value.timestamp.isoformat(),
        "feasible_isl_candidates": value.feasible_isl_candidates,
        "selected_isls": value.selected_isls,
        "acquiring_isls": value.acquiring_isls,
        "operational_isls": value.operational_isls,
        "rejected_isls": value.rejected_isls,
        "underfilled_isl_slots": value.underfilled_isl_slots,
        "ground_station_accesses": value.ground_station_accesses,
        "zones_served": value.zones_served,
        "zones_unserved": value.zones_unserved,
        "routes_reachable": value.routes_reachable,
        "routes_unreachable": value.routes_unreachable,
        "route_recomputed": value.route_recomputed,
        "acquisition_started": value.acquisition_started,
        "acquisition_completed": value.acquisition_completed,
        "acquisition_reset": value.acquisition_reset,
        "stages": list(value.stages),
    }


def _inspection_from_data(value: Mapping[str, object]) -> PlanningInspection:
    names = {
        "timestamp",
        "feasible_isl_candidates",
        "selected_isls",
        "acquiring_isls",
        "operational_isls",
        "rejected_isls",
        "underfilled_isl_slots",
        "ground_station_accesses",
        "zones_served",
        "zones_unserved",
        "routes_reachable",
        "routes_unreachable",
        "route_recomputed",
        "acquisition_started",
        "acquisition_completed",
        "acquisition_reset",
        "stages",
    }
    record = _record(value, names, "PlanningInspection")
    integer_names = names - {
        "timestamp",
        "route_recomputed",
        "stages",
    }
    integers = {
        name: (None if name == "underfilled_isl_slots" and record[name] is None
               else _integer(record[name], name)) for name in integer_names
    }
    stage_values = _list(record["stages"], "stages")
    return PlanningInspection(
        timestamp=_timestamp(record["timestamp"], "timestamp"),
        route_recomputed=_boolean(
            record["route_recomputed"], "route_recomputed"
        ),
        stages=tuple(_string(item, "stage") for item in stage_values),
        **integers,
    )


def _frontiers_to_data(value: PlanningFrontiers) -> dict[str, object]:
    return {
        "physical_observation": _optional_timestamp_data(
            value.physical_observation
        ),
        "planning": _optional_timestamp_data(value.planning),
        "publication": _optional_timestamp_data(value.publication),
    }


def _frontiers_from_data(value: Mapping[str, object]) -> PlanningFrontiers:
    record = _record(
        value,
        {"physical_observation", "planning", "publication"},
        "PlanningFrontiers",
    )
    return PlanningFrontiers(
        physical_observation=_optional_timestamp(
            record["physical_observation"], "physical_observation"
        ),
        planning=_optional_timestamp(record["planning"], "planning"),
        publication=_optional_timestamp(
            record["publication"], "publication"
        ),
    )


def _node_to_data(value: NodeId) -> dict[str, object]:
    return {
        "asn": value.asn,
        "entity_kind": value.entity_kind,
        "entity_id": value.entity_id,
    }


def _node_from_data(value: Mapping[str, object]) -> NodeId:
    record = _record(
        value, {"asn", "entity_kind", "entity_id"}, "NodeId"
    )
    return NodeId(
        _integer(record["asn"], "asn"),
        _string(record["entity_kind"], "entity_kind"),
        _string(record["entity_id"], "entity_id"),
    )


def _optional_node_from_data(value: object, field_name: str) -> NodeId | None:
    if value is None:
        return None
    return _node_from_data(_mapping(value, field_name))


def _record(
    value: Mapping[str, object],
    keys: set[str] | None,
    field_name: str,
) -> Mapping[str, object]:
    record = _mapping(value, field_name)
    if keys is not None:
        _exact_keys(record, keys, field_name)
    return record


def _exact_keys(
    value: Mapping[str, object], keys: set[str], field_name: str
) -> None:
    if set(value) != keys:
        raise ValueError(f"{field_name} has missing or unknown fields")


def _schema(
    value: Mapping[str, object], expected: str, field_name: str
) -> None:
    if value["schema"] != expected:
        raise ValueError(f"{field_name} schema is unsupported")


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field_name} keys must be strings")
    return value


def _list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    return value


def _records(
    value: object, field_name: str
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        _mapping(item, field_name) for item in _list(value, field_name)
    )


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    return None if value is None else _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")
    converted = float(value)
    if converted != converted or converted in {float("inf"), float("-inf")}:
        raise ValueError(f"{field_name} must be finite")
    return converted


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    text = _string(value, field_name)
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error


def _optional_timestamp(value: object, field_name: str) -> datetime | None:
    return None if value is None else _timestamp(value, field_name)


def _optional_timestamp_data(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _timedelta_to_data(value: timedelta) -> dict[str, int]:
    return {
        "days": value.days,
        "seconds": value.seconds,
        "microseconds": value.microseconds,
    }


def _timedelta_from_data(value: Mapping[str, object]) -> timedelta:
    record = _record(
        value, {"days", "seconds", "microseconds"}, "timedelta"
    )
    days = _integer(record["days"], "days")
    seconds = _integer(record["seconds"], "seconds")
    microseconds = _integer(record["microseconds"], "microseconds")
    if not 0 <= seconds < 86_400:
        raise ValueError("timedelta seconds must be canonical")
    if not 0 <= microseconds < 1_000_000:
        raise ValueError("timedelta microseconds must be canonical")
    try:
        return timedelta(
            days=days,
            seconds=seconds,
            microseconds=microseconds,
        )
    except OverflowError as error:
        raise ValueError("timedelta is outside the supported range") from error


__all__ = [
    "plan_from_data",
    "plan_to_data",
    "plan_transition_from_data",
    "plan_transition_to_data",
    "planned_operational_state_from_data",
    "planned_operational_state_to_data",
    "published_planning_evidence_from_data",
    "published_planning_evidence_to_data",
]


def _contact_window_to_data(value: PhysicalContactWindow | None):
    if value is None:
        return None
    return {"start_timestamp": value.start_timestamp.isoformat(),
            "end_timestamp": None if value.end_timestamp is None else value.end_timestamp.isoformat()}


def _contact_window_from_data(value):
    if value is None:
        return None
    record = _record(_mapping(value, "contact_window"), {"start_timestamp", "end_timestamp"}, "PhysicalContactWindow")
    return PhysicalContactWindow(
        _timestamp(record["start_timestamp"], "start_timestamp"),
        None if record["end_timestamp"] is None else _timestamp(record["end_timestamp"], "end_timestamp"),
    )
