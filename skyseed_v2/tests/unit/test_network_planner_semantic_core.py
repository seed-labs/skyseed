from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    EarthFixedPosition,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from skyseed_v2.simulator.physical import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalLinkObservation,
    PhysicalLinkWorkCounts,
    ZoneServiceCandidate,
)
from skyseed_v2.simulator.planning import (
    AccumulatedDeadbandParameterPlanningModel,
    CandidateDecision,
    CandidateDecisionKind,
    DefaultCoordinationPolicy,
    EffectiveConnectionParameters,
    GatewayHopCountRoutePlanningModel,
    GroundStationRouteDemandTarget,
    HighestElevationZoneServicePlanningModel,
    InterSatelliteLifecycleEvidence,
    InterSatelliteSelection,
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    ParameterKindConfiguration,
    Plan,
    Planner,
    PlanningCandidateHistogram,
    PlanningCandidateSummary,
    PlanningFrameEvidence,
    PlanningFrontiers,
    PlanningHistogramBin,
    PlanningInspection,
    PlanTransition,
    PublishedPlanningEvidence,
    PlannedOperationalState,
    CoverageFirstGroundStationAccessPlanningModel,
    ReachableRouteOutcome,
    RetainFirstInterSatelliteConfiguration,
    RetainFirstInterSatelliteConnectionPlanningModel,
    RouteDemand,
    RouteOutcomeTransition,
    RoutePlanningResult,
    RouteUnreachableReason,
    SemanticEventKind,
    SemanticEventPrecedence,
    SemanticEventReference,
    FrameBatchPlanningWindowPolicy,
    UnreachableRouteOutcome,
    ZoneServiceAssignment,
    ZoneServicePlanningResult,
    ZoneServiceTransition,
    apply_plan,
    apply_plan_transition,
    create_plan,
    plan_from_data,
    plan_to_data,
    plan_transition_fingerprint,
    plan_transition_from_data,
    plan_transition_to_data,
    planned_operational_state_fingerprint,
    planned_operational_state_from_data,
    planned_operational_state_to_data,
    published_planning_evidence_from_data,
    published_planning_evidence_to_data,
    semantic_event_references,
)


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FixedPositionModel:
    def __init__(self, node_ids) -> None:
        values = tuple(node_ids)
        ordinal = {
            node_id: index
            for index, node_id in enumerate(
                sorted(
                    values,
                    key=lambda value: (
                        value.asn,
                        value.entity_kind,
                        value.entity_id,
                    ),
                )
            )
        }
        self._positions = {
            node_id: EarthFixedPosition(float(ordinal[node_id] + 1), 0.0, 0.0)
            for node_id in values
        }

    def compute_positions(self, physical_world, timestamp):
        del physical_world, timestamp
        return self._positions


class _NoInterSatelliteModel:
    acquisition_duration = timedelta(0)

    def create_inter_satellite_candidate_pairs(self, physical_world):
        del physical_world
        return ()

    def select_inter_satellite_links(
        self, physical_world, candidates, retained_pairs
    ):
        del physical_world, retained_pairs
        if candidates:
            raise AssertionError("test Model expected no ISL candidates")
        return InterSatelliteSelection((), (), 0)


class _NoGroundStationModel:
    def select_ground_station_accesses(self, physical_world, candidates, retained_pairs=frozenset()):
        del physical_world
        if candidates:
            raise AssertionError("test Model expected no GS candidates")
        return ()


class _NoRouteModel:
    def __init__(self) -> None:
        self.shutdown_calls: list[bool] = []

    def plan_route_outcomes(
        self,
        physical_world,
        operational_connections,
        zone_service_assignments,
        demands,
        previous_outcomes,
    ):
        del physical_world, operational_connections, zone_service_assignments
        if demands or previous_outcomes:
            raise AssertionError("test Model expected no Route demand")
        return RoutePlanningResult((), ())

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


def _satellite(
    index: int,
    *,
    capability: int = 4,
    plane: int = 0,
    orbit_index: int | None = None,
    altitude_km: float = 550.0,
) -> SatelliteSimulationInput:
    slot = index if orbit_index is None else orbit_index
    return SatelliteSimulationInput(
        node_id=NodeId(65001, "satellite", f"sat-{index}"),
        satellite_id=f"sat-{index}",
        shell_id="shell",
        orbit_id=f"orbit-{plane}",
        orbit_index=slot,
        altitude_km=altitude_km,
        inclination_deg=53.0,
        right_ascension_deg=plane * 10.0,
        mean_anomaly_deg=slot * 10.0,
        epoch_utc=EPOCH,
        maximum_simultaneous_inter_satellite_links=capability,
        max_simultaneous_ground_station_accesses=2,
    )


def _station(index: int = 0) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(65001, "ground-station", f"ground-{index}"),
        ground_station_id=f"ground-{index}",
        latitude_deg=0.0,
        longitude_deg=float(index),
        elevation_m=0.0,
        station_type="gateway",
        operator=None,
        max_simultaneous_satellite_accesses=2,
    )


def _world(
    *,
    satellites: tuple[SatelliteSimulationInput, ...] | None = None,
    stations: tuple[GroundStationSimulationInput, ...] | None = None,
    reverse_position_order: bool = False,
    zone_north_latitude_deg: float = 1.0,
):
    satellite_values = satellites or (_satellite(0), _satellite(1))
    station_values = stations or (_station(),)
    static_input = SimulatorStaticInput(satellite_values, station_values)
    definition = SimulationDefinition(
        static_input,
        SimulationTimeline(
            EPOCH - timedelta(seconds=20),
            EPOCH + timedelta(seconds=600),
            timedelta(seconds=5),
        ),
    )
    node_ids = tuple(value.node_id for value in static_input.list_nodes())
    if reverse_position_order:
        node_ids = tuple(reversed(node_ids))
    physical_world = Simulator(definition).create_physical_world(
        position_model=_FixedPositionModel(node_ids),
        zone_layout_definition=ZoneLayoutDefinition(
            zones=(
                ZoneDefinition(
                    zone_id="zone-a",
                    geometry=LatitudeLongitudeRectangleDefinition(
                        south_latitude_deg=-1.0,
                        north_latitude_deg=zone_north_latitude_deg,
                        west_longitude_deg=-1.0,
                        east_longitude_deg=1.0,
                    ),
                ),
            ),
            allow_overlap=False,
        ),
    )
    return physical_world, satellite_values, station_values


def _parameter_model():
    return AccumulatedDeadbandParameterPlanningModel(
        {
            OperationalConnectionKind.INTER_SATELLITE: (
                ParameterKindConfiguration(1_000_000, 10)
            ),
            OperationalConnectionKind.GROUND_STATION_ACCESS: (
                ParameterKindConfiguration(2_000_000, 20)
            ),
        }
    )


def _planner(
    physical_world,
    *,
    inter_satellite_model=None,
    ground_station_model=None,
    zone_service_model=None,
    parameter_model=None,
    route_model=None,
    coordination_policy=None,
    route_demands=(),
    planning_window_policy=None,
):
    route = route_model or _NoRouteModel()
    planner = Planner(
        physical_world=physical_world,
        observation_interval=timedelta(seconds=5),
        inter_satellite_model=(
            inter_satellite_model or _NoInterSatelliteModel()
        ),
        ground_station_model=ground_station_model or _NoGroundStationModel(),
        zone_service_model=(
            zone_service_model or HighestElevationZoneServicePlanningModel()
        ),
        parameter_model=parameter_model or _parameter_model(),
        route_model=route,
        coordination_policy=coordination_policy or DefaultCoordinationPolicy(),
        route_demands=route_demands,
        planning_window_policy=(
            planning_window_policy or FrameBatchPlanningWindowPolicy()
        ),
    )
    return planner, route


def _observation(
    timestamp: datetime,
    *,
    isls=(),
    ground=(),
    zones=(),
) -> PhysicalLinkObservation:
    return PhysicalLinkObservation(
        timestamp=timestamp,
        inter_satellite_candidates=tuple(isls),
        ground_station_access_candidates=tuple(ground),
        zone_service_candidates=tuple(zones),
        work_counts=PhysicalLinkWorkCounts(
            inter_satellite_pairs_requested=len(isls),
            inter_satellite_pairs_evaluated=len(isls),
            inter_satellite_pairs_feasible=len(isls),
            ground_station_pairs_requested=len(ground),
            ground_station_pairs_evaluated=len(ground),
            ground_station_pairs_feasible=len(ground),
            zone_service_pairs_requested=len(zones),
            zone_service_pairs_evaluated=len(zones),
            zone_service_pairs_feasible=len(zones),
        ),
    )


def _isl_candidate(
    first: SatelliteSimulationInput,
    second: SatelliteSimulationInput,
    *,
    delay_us: int = 100,
) -> InterSatelliteLinkCandidate:
    return InterSatelliteLinkCandidate(
        first.node_id,
        second.node_id,
        True,
        0,
        1_000.0,
        delay_us,
    )


def _ground_candidate(
    station: GroundStationSimulationInput,
    satellite: SatelliteSimulationInput,
    *,
    delay_us: int = 10,
) -> GroundStationAccessCandidate:
    return GroundStationAccessCandidate(
        station.node_id,
        satellite.node_id,
        2_000.0,
        45.0,
        delay_us,
    )


def _ground_pairs(candidates) -> tuple[tuple[NodeId, NodeId], ...]:
    return tuple(
        (value.ground_station_id, value.satellite_id) for value in candidates
    )


class _InjectedInterSatelliteModel:
    acquisition_duration = timedelta(0)

    def __init__(self, selection_factory, *, pairs=()) -> None:
        self._selection_factory = selection_factory
        self._pairs = tuple(pairs)

    def create_inter_satellite_candidate_pairs(self, physical_world):
        del physical_world
        return self._pairs

    def select_inter_satellite_links(
        self, physical_world, candidates, retained_pairs
    ):
        return self._selection_factory(
            physical_world, candidates, retained_pairs
        )


class _InjectedGroundStationModel:
    def __init__(self, selection_factory) -> None:
        self._selection_factory = selection_factory

    def select_ground_station_accesses(self, physical_world, candidates, retained_pairs=frozenset()):
        return self._selection_factory(physical_world, candidates)


class _InjectedZoneServiceModel:
    def __init__(self, result_factory) -> None:
        self._result_factory = result_factory

    def assign_zone_service(
        self, physical_world, candidates, previous_assignments
    ):
        return self._result_factory(
            physical_world, candidates, previous_assignments
        )


class _InjectedRouteModel:
    def __init__(self, result_factory) -> None:
        self._result_factory = result_factory
        self.calls = 0
        self.shutdown_calls: list[bool] = []

    def plan_route_outcomes(
        self,
        physical_world,
        operational_connections,
        zone_service_assignments,
        demands,
        previous_outcomes,
    ):
        self.calls += 1
        return self._result_factory(
            physical_world,
            operational_connections,
            zone_service_assignments,
            demands,
            previous_outcomes,
        )

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


class _TransformingParameterModel:
    def __init__(self, transform) -> None:
        self._delegate = _parameter_model()
        self._transform = transform

    def plan_effective_parameters(
        self, timestamp, observations, previous_memory
    ):
        result = self._delegate.plan_effective_parameters(
            timestamp, observations, previous_memory
        )
        return self._transform(result)


class _FailOnceRouteModel:
    def __init__(self) -> None:
        self.failed = False
        self.shutdown_calls: list[bool] = []

    def plan_route_outcomes(
        self,
        physical_world,
        operational_connections,
        zone_service_assignments,
        demands,
        previous_outcomes,
    ):
        del physical_world, operational_connections, zone_service_assignments
        if not self.failed:
            self.failed = True
            raise RuntimeError("injected route failure")
        outcomes = tuple(
            UnreachableRouteOutcome(
                demand, RouteUnreachableReason.NO_OPERATIONAL_PATH
            )
            for demand in demands
        )
        previous = {value.demand: value for value in previous_outcomes}
        transitions = tuple(
            RouteOutcomeTransition(demand, previous.get(demand), outcome)
            for demand, outcome in zip(demands, outcomes, strict=True)
            if previous.get(demand) != outcome
        )
        return RoutePlanningResult(outcomes, transitions)

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


def _planner_public_snapshot(planner: Planner):
    try:
        state = planner.get_state()
    except RuntimeError:
        state = None
    try:
        transition = planner.get_last_transition()
    except RuntimeError:
        transition = None
    return (
        planner.get_frontiers(),
        state,
        transition,
        planner.get_route_recomputation_count(),
    )


def _oracle_apply(
    previous: PlannedOperationalState | None,
    transition: PlanTransition,
) -> PlannedOperationalState:
    connections = {
        value.connection_id: value
        for value in (() if previous is None else previous.operational_connections)
    }
    for value in transition.established_connections:
        connections[value.connection_id] = value
    for value in transition.parameter_updated_connections:
        connections[value.connection_id] = value
    for value in transition.withdrawn_connection_ids:
        del connections[value]
    assignments = {
        value.zone_id: value
        for value in (() if previous is None else previous.zone_service_assignments)
    }
    for value in transition.zone_service_transitions:
        assignments[value.zone_id] = ZoneServiceAssignment(
            value.zone_id, value.current_satellite_id
        )
    outcomes = {
        value.demand: value
        for value in (() if previous is None else previous.route_outcomes)
    }
    for value in transition.route_outcome_transitions:
        if value.current is None:
            del outcomes[value.demand]
        else:
            outcomes[value.demand] = value.current
    return PlannedOperationalState(
        transition.timestamp,
        tuple(connections.values()),
        tuple(assignments.values()),
        tuple(outcomes.values()),
    )


def test_planner_observation_names_validation_and_real_rollups_truthfully():
    physical_world, _, _ = _world()
    planner, _ = _planner(
        physical_world,
        planning_window_policy=FrameBatchPlanningWindowPolicy(
            frames_per_batch=2,
        ),
    )
    observer = _ExecutionObserver()
    planner._set_execution_observer(observer)
    try:
        assert planner.observe(
            physical_world.frame_at(EPOCH), _observation(EPOCH)
        ) == ()
        timestamp = EPOCH + timedelta(seconds=5)
        plans = planner.observe(
            physical_world.frame_at(timestamp), _observation(timestamp)
        )

        assert len(plans) == 1
        records = observer.snapshot()["records"]
        validations = tuple(
            value
            for value in records
            if value["stage"] == "physical-input-validation"
        )
        planning_totals = tuple(
            value for value in records if value["stage"] == "planning-total"
        )
        creations = tuple(
            value for value in records if value["stage"] == "plan-creation"
        )
        publications = tuple(
            value
            for value in records
            if value["stage"] == "publication-total"
        )
        assert len(validations) == len(planning_totals) == 2
        assert all(
            value["duration_role"] == "rollup" for value in planning_totals
        )
        assert not any(
            value["stage"] in {"physical-link-observation", "publication"}
            for value in records
        )
        assert len(creations) == len(publications) == 1
        assert publications[0]["duration_role"] == "rollup"
        assert publications[0]["scope"]["plan_id"] == plans[0].plan_id
        inspection_stages = planner.get_published_evidence(
            plans[0].plan_id
        ).frames[-1].inspection.stages
        assert "physical-input-validation" in inspection_stages
        assert "physical-link-observation" not in inspection_stages
        assert "publication" not in inspection_stages
    finally:
        planner.shutdown()


def test_public_plan_transition_replay_fingerprints_and_codecs_are_one_truth():
    satellite_a = NodeId(65001, "satellite", "a")
    satellite_b = NodeId(65001, "satellite", "b")
    ground = NodeId(65001, "ground-station", "g")
    identifier = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE,
        satellite_b,
        satellite_a,
    )
    parameters = EffectiveConnectionParameters(1_000_000, 100, 10)
    connection = OperationalConnection(identifier, parameters, parameters)
    demand = RouteDemand(
        satellite_a, GroundStationRouteDemandTarget(ground)
    )
    outcome = ReachableRouteOutcome(
        demand, satellite_b, (satellite_a, satellite_b, ground)
    )
    transition = PlanTransition(
        timestamp=EPOCH,
        established_connections=(connection,),
        zone_service_transitions=(
            ZoneServiceTransition("zone-a", None, None),
        ),
        route_outcome_transitions=(
            RouteOutcomeTransition(demand, None, outcome),
        ),
    )

    state = apply_plan_transition(None, transition)
    assert state == _oracle_apply(None, transition)
    assert state.timestamp == EPOCH
    assert plan_transition_fingerprint(transition) == (
        "79aa4f659a358329b86f0b845f1b4c766efa3707e841a10501deae5f28453301"
    )
    assert planned_operational_state_fingerprint(state) == (
        "cff2d0a0440c85339e02745b1eacc31f844645d8686d174779d4442fef53f73e"
    )
    assert plan_transition_from_data(plan_transition_to_data(transition)) == transition
    assert (
        planned_operational_state_from_data(
            planned_operational_state_to_data(state)
        )
        == state
    )

    references = semantic_event_references(transition)
    assert {value.kind for value in references} == {
        SemanticEventKind.CONNECTION_ESTABLISH,
        SemanticEventKind.PARAMETER_READY,
        SemanticEventKind.ROUTE_REACHABLE,
    }
    assert all(value.timestamp == EPOCH for value in references)
    assert all(value is not other for value, other in zip(references, references[1:]))


def test_plan_has_exact_final_fields_self_contained_lineage_and_no_overlap():
    bootstrap = PlanTransition(
        EPOCH,
        zone_service_transitions=(
            ZoneServiceTransition("zone-a", None, None),
        ),
    )
    first = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        observation_interval=timedelta(seconds=5),
        start_timestamp=EPOCH,
        transitions=(bootstrap,),
    )
    first_state = apply_plan(
        None, first, physical_world_fingerprint="a" * 64
    )
    detached_first = plan_to_data(first)
    assert first.plan_id == (
        "52d3ccc7dae5bf6d06272531260fc50eb96ddc15818e1ee49a458024c1a09f99"
    )
    assert plan_from_data(detached_first) == first
    second_transition = PlanTransition(EPOCH + timedelta(seconds=5))
    second = create_plan(
        first_state,
        previous_plan_id=first.plan_id,
        physical_world_fingerprint="a" * 64,
        observation_interval=timedelta(seconds=5),
        start_timestamp=EPOCH + timedelta(seconds=5),
        transitions=(second_transition,),
    )

    assert {value.name for value in fields(Plan)} == {
        "plan_id",
        "previous_plan_id",
        "physical_world_fingerprint",
        "input_state_fingerprint",
        "output_state_fingerprint",
        "observation_interval",
        "start_timestamp",
        "end_timestamp",
        "transitions",
        "semantic_event_precedences",
    }
    assert second.start_timestamp == second.transitions[0].timestamp
    assert first.end_timestamp < second.start_timestamp
    assert second.previous_plan_id == first.plan_id
    assert second.input_state_fingerprint == first.output_state_fingerprint
    assert apply_plan(
        first_state, second, physical_world_fingerprint="a" * 64
    ).timestamp == second.end_timestamp
    assert plan_from_data(plan_to_data(first)) == first
    with pytest.raises(ValueError, match="different PhysicalWorld"):
        apply_plan(None, first, physical_world_fingerprint="b" * 64)
    with pytest.raises(ValueError, match="canonical Plan content"):
        replace(first, plan_id="0" * 64)
    with pytest.raises(FrozenInstanceError):
        first.end_timestamp = EPOCH + timedelta(seconds=1)


def test_create_plan_rejects_invalid_physical_world_fingerprint() -> None:
    with pytest.raises(ValueError, match="physical_world_fingerprint"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="not-a-canonical-sha256",
            observation_interval=timedelta(seconds=5),
            start_timestamp=EPOCH,
            transitions=(PlanTransition(EPOCH),),
        )


def test_create_plan_enforces_bootstrap_types_cadence_and_precedence_graph() -> None:
    bootstrap = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        observation_interval=timedelta(seconds=5),
        start_timestamp=EPOCH,
        transitions=(PlanTransition(EPOCH),),
    )
    state = apply_plan(
        None, bootstrap, physical_world_fingerprint="a" * 64
    )
    next_timestamp = EPOCH + timedelta(seconds=5)
    with pytest.raises(TypeError, match="previous_state"):
        create_plan(
            object(),
            previous_plan_id=bootstrap.plan_id,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=next_timestamp,
            transitions=(PlanTransition(next_timestamp),),
        )
    with pytest.raises(ValueError, match="bootstrap Plan"):
        create_plan(
            state,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=next_timestamp,
            transitions=(PlanTransition(next_timestamp),),
        )
    with pytest.raises(ValueError, match="previous_plan_id"):
        create_plan(
            state,
            previous_plan_id="invalid",
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=next_timestamp,
            transitions=(PlanTransition(next_timestamp),),
        )
    with pytest.raises(ValueError, match="positive"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(0),
            start_timestamp=EPOCH,
            transitions=(PlanTransition(EPOCH),),
        )
    with pytest.raises(ValueError, match="must not be empty"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=EPOCH,
            transitions=(),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=datetime(2026, 1, 1),
            transitions=(PlanTransition(EPOCH),),
        )

    satellite_a = NodeId(65001, "satellite", "precedence-a")
    satellite_b = NodeId(65001, "satellite", "precedence-b")
    ground = NodeId(65001, "ground-station", "precedence-ground")
    identifier = OperationalConnectionId(
        OperationalConnectionKind.INTER_SATELLITE,
        satellite_a,
        satellite_b,
    )
    parameters = EffectiveConnectionParameters(1_000_000, 100, 10)
    connection = OperationalConnection(identifier, parameters, parameters)
    demand = RouteDemand(
        satellite_a, GroundStationRouteDemandTarget(ground)
    )
    transition = PlanTransition(
        EPOCH,
        established_connections=(connection,),
        route_outcome_transitions=(
            RouteOutcomeTransition(
                demand,
                None,
                ReachableRouteOutcome(
                    demand, satellite_b, (satellite_a, satellite_b, ground)
                ),
            ),
        ),
    )
    first, second, third = semantic_event_references(transition)[:3]
    duplicate = SemanticEventPrecedence(first, second)
    with pytest.raises(ValueError, match="duplicate"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=EPOCH,
            transitions=(transition,),
            semantic_event_precedences=(duplicate, duplicate),
        )
    foreign = SemanticEventReference(
        EPOCH,
        SemanticEventKind.PARAMETER_READY,
        connection_id=OperationalConnectionId(
            OperationalConnectionKind.INTER_SATELLITE,
            satellite_a,
            NodeId(65001, "satellite", "foreign"),
        ),
    )
    with pytest.raises(ValueError, match="transition-derived"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=EPOCH,
            transitions=(transition,),
            semantic_event_precedences=(
                SemanticEventPrecedence(first, foreign),
            ),
        )
    with pytest.raises(ValueError, match="acyclic"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=5),
            start_timestamp=EPOCH,
            transitions=(transition,),
            semantic_event_precedences=(
                SemanticEventPrecedence(first, second),
                SemanticEventPrecedence(second, third),
                SemanticEventPrecedence(third, first),
            ),
        )


def test_plan_cadence_rejects_dropped_duplicate_and_wrong_interval_boundaries():
    every_five_seconds = timedelta(seconds=5)
    with pytest.raises(ValueError, match="observation_interval"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=every_five_seconds,
            start_timestamp=EPOCH,
            transitions=(
                PlanTransition(EPOCH),
                PlanTransition(EPOCH + timedelta(seconds=10)),
            ),
        )
    with pytest.raises(ValueError, match="must follow previous state"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=every_five_seconds,
            start_timestamp=EPOCH,
            transitions=(PlanTransition(EPOCH), PlanTransition(EPOCH)),
        )
    with pytest.raises(ValueError, match="observation_interval"):
        create_plan(
            None,
            previous_plan_id=None,
            physical_world_fingerprint="a" * 64,
            observation_interval=timedelta(seconds=4),
            start_timestamp=EPOCH,
            transitions=(
                PlanTransition(EPOCH),
                PlanTransition(EPOCH + every_five_seconds),
            ),
        )

    bootstrap = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        observation_interval=every_five_seconds,
        start_timestamp=EPOCH,
        transitions=(PlanTransition(EPOCH),),
    )
    state = apply_plan(
        None, bootstrap, physical_world_fingerprint="a" * 64
    )
    with pytest.raises(ValueError, match="one observation_interval"):
        create_plan(
            state,
            previous_plan_id=bootstrap.plan_id,
            physical_world_fingerprint="a" * 64,
            observation_interval=every_five_seconds,
            start_timestamp=EPOCH + timedelta(seconds=10),
            transitions=(PlanTransition(EPOCH + timedelta(seconds=10)),),
        )


def test_plan_codec_cadence_is_exact_versioned_content():
    plan = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        observation_interval=timedelta(
            days=1, seconds=2, microseconds=3
        ),
        start_timestamp=EPOCH,
        transitions=(PlanTransition(EPOCH),),
    )
    data = plan_to_data(plan)
    assert data["observation_interval"] == {
        "days": 1,
        "seconds": 2,
        "microseconds": 3,
    }
    assert plan_from_data(data) == plan
    with pytest.raises(ValueError, match="missing or unknown"):
        plan_from_data(
            {
                key: value
                for key, value in data.items()
                if key != "observation_interval"
            }
        )
    with pytest.raises(ValueError, match="canonical Plan content"):
        plan_from_data(
            {
                **data,
                "observation_interval": {
                    "days": 1,
                    "seconds": 2,
                    "microseconds": 4,
                },
            }
        )
    with pytest.raises(ValueError, match="seconds must be canonical"):
        plan_from_data(
            {
                **data,
                "observation_interval": {
                    "days": 0,
                    "seconds": 86_400,
                    "microseconds": 0,
                },
            }
        )


def test_planner_rejects_dropped_formal_frame_atomically_and_window_alignment():
    physical_world, _, _ = _world()
    planner, _ = _planner(physical_world)
    assert planner.observe(
        physical_world.frame_at(EPOCH), _observation(EPOCH)
    ) == ()
    before = _planner_public_snapshot(planner)
    dropped = EPOCH + timedelta(seconds=10)
    with pytest.raises(ValueError, match="observation_interval"):
        planner.observe(
            physical_world.frame_at(dropped), _observation(dropped)
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()

    arbitrary_cadence = Planner(
            physical_world=physical_world,
            observation_interval=timedelta(seconds=6),
            inter_satellite_model=_NoInterSatelliteModel(),
            ground_station_model=_NoGroundStationModel(),
            zone_service_model=HighestElevationZoneServicePlanningModel(),
            parameter_model=_parameter_model(),
            route_model=_NoRouteModel(),
            coordination_policy=DefaultCoordinationPolicy(),
        )
    rounded_preparation = Planner(
            physical_world=physical_world,
            observation_interval=timedelta(seconds=5),
            inter_satellite_model=RetainFirstInterSatelliteConnectionPlanningModel(
                RetainFirstInterSatelliteConfiguration(
                    desired_same_plane_links=0,
                    desired_cross_plane_links=0,
                    maximum_cross_plane_separation=0,
                    acquisition_duration=timedelta(seconds=7),
                )
            ),
            ground_station_model=_NoGroundStationModel(),
            zone_service_model=HighestElevationZoneServicePlanningModel(),
            parameter_model=_parameter_model(),
            route_model=_NoRouteModel(),
            coordination_policy=DefaultCoordinationPolicy(),
        )

    assert arbitrary_cadence.required_prehistory == timedelta(0)
    assert rounded_preparation.required_prehistory == timedelta(seconds=10)
    arbitrary_cadence.shutdown()
    rounded_preparation.shutdown()

def test_physical_world_fingerprint_is_cached_order_independent_and_semantic():
    satellites = (_satellite(0), _satellite(1))
    stations = (_station(),)
    first, _, _ = _world(satellites=satellites, stations=stations)
    reordered, _, _ = _world(
        satellites=tuple(reversed(satellites)),
        stations=stations,
        reverse_position_order=True,
    )
    changed_capability, _, _ = _world(
        satellites=(_satellite(0, capability=3), _satellite(1)),
        stations=stations,
    )
    changed_orbit, _, _ = _world(
        satellites=(_satellite(0, altitude_km=551.0), _satellite(1, altitude_km=551.0)),
        stations=stations,
    )
    changed_zone, _, _ = _world(
        satellites=satellites,
        stations=stations,
        zone_north_latitude_deg=2.0,
    )

    fingerprint = first.semantic_fingerprint()
    assert len(fingerprint) == 64
    assert first.semantic_fingerprint() is fingerprint
    assert reordered.semantic_fingerprint() == fingerprint
    assert changed_capability.semantic_fingerprint() != fingerprint
    assert changed_orbit.semantic_fingerprint() != fingerprint
    assert changed_zone.semantic_fingerprint() != fingerprint


def test_prime_is_per_frame_strictly_preformal_bounded_and_nonpublishing():
    physical_world, _, _ = _world()
    isl_model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=0,
            desired_cross_plane_links=0,
            maximum_cross_plane_separation=0,
            acquisition_duration=timedelta(seconds=20),
        )
    )
    planner, route_model = _planner(
        physical_world, inter_satellite_model=isl_model
    )
    assert planner.required_prehistory == timedelta(seconds=20)
    with pytest.raises(ValueError, match="bounded initialization"):
        timestamp = EPOCH - timedelta(seconds=25)
        planner.prime(
            physical_world.frame_at(timestamp),
            _observation(timestamp),
            formal_start=EPOCH,
        )
    for seconds in (-20, -15, -10, -5):
        timestamp = EPOCH + timedelta(seconds=seconds)
        assert (
            planner.prime(
                physical_world.frame_at(timestamp),
                _observation(timestamp),
                formal_start=EPOCH,
            )
            is None
        )
    assert planner.get_frontiers() == PlanningFrontiers(
        physical_observation=EPOCH - timedelta(seconds=5),
        planning=None,
        publication=None,
    )
    with pytest.raises(ValueError, match="precede formal_start"):
        planner.prime(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH),
            formal_start=EPOCH,
        )
    assert planner.observe(
        physical_world.frame_at(EPOCH), _observation(EPOCH)
    ) == ()
    planner.shutdown()
    assert route_model.shutdown_calls == [True]


def test_32_frame_batches_publish_without_lookahead_and_preserve_121_transitions():
    physical_world, _, _ = _world()
    planner, route_model = _planner(physical_world)
    plans = []
    publication_calls = {}
    for seconds in range(0, 601, 5):
        timestamp = EPOCH + timedelta(seconds=seconds)
        published = planner.observe(
            physical_world.frame_at(timestamp), _observation(timestamp)
        )
        if published:
            publication_calls[seconds] = planner.get_frontiers()
        for plan in published:
            evidence = planner.get_published_evidence(plan.plan_id)
            assert evidence.plan is plan
            assert published_planning_evidence_from_data(
                published_planning_evidence_to_data(evidence)
            ) == evidence
            planner.release_published_through(plan.plan_id)
            plans.append(plan)

    assert tuple(publication_calls) == (155, 315, 475)
    for seconds, frontiers in publication_calls.items():
        expected = EPOCH + timedelta(seconds=seconds)
        assert frontiers == PlanningFrontiers(expected, expected, expected)
    tails = planner.finish(end_timestamp=EPOCH + timedelta(seconds=600))
    for plan in tails:
        planner.release_published_through(plan.plan_id)
    plans.extend(tails)

    assert len(plans) == 4
    assert sum(len(value.transitions) for value in plans) == 121
    assert tuple(
        value.timestamp
        for plan in plans
        for value in plan.transitions
    ) == tuple(
        EPOCH + timedelta(seconds=seconds) for seconds in range(0, 601, 5)
    )
    assert tuple(len(plan.transitions) for plan in plans) == (32, 32, 32, 25)
    assert plans[0].end_timestamp == EPOCH + timedelta(seconds=155)
    assert plans[1].start_timestamp == EPOCH + timedelta(seconds=160)
    assert plans[-1].end_timestamp == EPOCH + timedelta(seconds=600)
    assert planner.get_frontiers().publication == plans[-1].end_timestamp
    replayed = None
    oracle = None
    previous_plan_id = None
    for plan in plans:
        assert plan.observation_interval == timedelta(seconds=5)
        assert plan.previous_plan_id == previous_plan_id
        for transition in plan.transitions:
            oracle = _oracle_apply(oracle, transition)
        replayed = apply_plan(
            replayed,
            plan,
            physical_world_fingerprint=physical_world.semantic_fingerprint(),
        )
        assert replayed == oracle
        assert planned_operational_state_fingerprint(replayed) == (
            plan.output_state_fingerprint
        )
        previous_plan_id = plan.plan_id
    assert replayed == planner.get_state()
    assert planner.finish(end_timestamp=EPOCH + timedelta(seconds=600)) == ()
    planner.shutdown(wait=False)
    planner.shutdown(wait=True)
    assert route_model.shutdown_calls == [False]


def test_evidence_carries_exact_candidates_lifecycle_remaining_and_gs_selection():
    physical_world, satellites, stations = _world()
    isl_model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=1,
            desired_cross_plane_links=0,
            maximum_cross_plane_separation=0,
            acquisition_duration=timedelta(seconds=20),
        )
    )
    planner, _ = _planner(
        physical_world,
        inter_satellite_model=isl_model,
        planning_window_policy=FrameBatchPlanningWindowPolicy(frames_per_batch=5),
        ground_station_model=CoverageFirstGroundStationAccessPlanningModel(
            default_ground_station_limit=1, ground_station_policy_limit=1
        ),
    )
    isl = InterSatelliteLinkCandidate(
        satellites[0].node_id,
        satellites[1].node_id,
        True,
        0,
        1_000.0,
        4,
    )
    isl = _proven(isl, start=-20)
    ground = GroundStationAccessCandidate(
        stations[0].node_id,
        satellites[0].node_id,
        2_000.0,
        45.0,
        7,
    )
    zone = ZoneServiceCandidate(
        "zone-a",
        satellites[0].node_id,
        2_000.0,
        45.0,
        7,
    )
    first_plan = None
    for seconds in range(0, 51, 5):
        timestamp = EPOCH + timedelta(seconds=seconds)
        published = planner.observe(
            physical_world.frame_at(timestamp),
            _observation(
                timestamp, isls=(isl,), ground=(ground,), zones=(zone,)
            ),
        )
        if published:
            first_plan = published[0]
            break
    assert first_plan is not None
    evidence = planner.get_published_evidence(first_plan.plan_id)
    assert len(evidence.frames) == 5
    first = evidence.frames[0]
    assert first.inter_satellite_candidate_summary.feasible_count == 1
    assert first.ground_station_access_candidate_summary.feasible_count == 1
    assert first.zone_service_candidate_summary.feasible_count == 1
    assert {
        value.name
        for value in first.inter_satellite_candidate_summary.histograms
    } == {
        "cyclic-plane-separation",
        "distance-m",
        "propagation-delay-us",
        "relationship",
        "selection-decision-reason",
    }
    assert first.selected_ground_station_accesses == (ground,)
    assert first.selected_zone_service_candidates == (zone,)
    assert first.selected_inter_satellite_links[0].candidate == isl
    assert first.selected_inter_satellite_links[0].remaining_acquisition == (
        timedelta(seconds=20)
    )
    at_twenty = evidence.frames[-1]
    assert at_twenty.timestamp == EPOCH + timedelta(seconds=20)
    assert at_twenty.selected_inter_satellite_links[0].operational
    assert at_twenty.selected_inter_satellite_links[0].remaining_acquisition == (
        timedelta(0)
    )
    detached = published_planning_evidence_to_data(evidence)
    assert detached["schema"] == "skyseed-v2-published-planning-evidence-v3"
    assert set(detached["frames"][0]) == {
        "timestamp",
        "inter_satellite_candidate_summary",
        "ground_station_access_candidate_summary",
        "zone_service_candidate_summary",
        "physical_link_work_counts",
        "selected_inter_satellite_links",
        "selected_ground_station_accesses",
        "ground_station_preparations",
        "selected_zone_service_candidates",
        "inspection",
    }
    assert "decisions" not in detached["frames"][0]["inspection"]
    assert json.loads(json.dumps(detached)) == detached
    assert published_planning_evidence_from_data(detached) == evidence
    planner.release_published_through(first_plan.plan_id)
    planner.shutdown()


def test_ground_station_selection_is_quality_first_and_deterministic_on_ties():
    physical_world, satellites, stations = _world(
        satellites=(_satellite(0), _satellite(1), _satellite(2)),
        stations=(_station(0), _station(1)),
    )
    model = CoverageFirstGroundStationAccessPlanningModel(
        default_ground_station_limit=1, ground_station_policy_limit=1
    )
    candidates = (
        replace(
            _ground_candidate(stations[1], satellites[0]), elevation_deg=55.0
        ),
        replace(
            _ground_candidate(stations[0], satellites[1]), elevation_deg=60.0
        ),
        replace(
            _ground_candidate(stations[1], satellites[2]), elevation_deg=65.0
        ),
        replace(
            _ground_candidate(stations[0], satellites[0]), elevation_deg=60.0
        ),
    )

    selected = model.select_ground_station_accesses(
        physical_world, candidates
    )

    assert _ground_pairs(selected) == (
        (stations[0].node_id, satellites[0].node_id),
        (stations[1].node_id, satellites[2].node_id),
    )
    assert model.select_ground_station_accesses(
        physical_world, tuple(reversed(candidates))
    ) == selected
    assert model._prepared_world is physical_world


def test_ground_station_selection_preserves_global_equal_cost_identity():
    capacities = (1, 1, 2, 1, 1, 1, 2)
    physical_world, satellites, stations = _world(
        satellites=tuple(
            replace(
                _satellite(index),
                max_simultaneous_ground_station_accesses=capacity,
            )
            for index, capacity in enumerate(capacities)
        ),
        stations=tuple(_station(index) for index in range(8)),
    )
    rows = (
        ((2.0, 4), (0.0, 1)),
        ((2.0, 5), (1.0, 0), (0.0, 2), (0.0, 6)),
        ((0.0, 2), (0.0, 6)),
        ((2.0, 1),),
        ((3.0, 5), (1.0, 2), (0.0, 0)),
        ((3.0, 4), (1.0, 3)),
        ((2.0, 0), (0.0, 6)),
        ((2.0, 2), (2.0, 6)),
    )
    candidates = tuple(
        replace(
            _ground_candidate(stations[ground_index], satellites[satellite_index]),
            elevation_deg=elevation_deg,
        )
        for ground_index, row in enumerate(rows)
        for elevation_deg, satellite_index in row
    )
    model = CoverageFirstGroundStationAccessPlanningModel(
        default_ground_station_limit=1, ground_station_policy_limit=1
    )

    selected = model.select_ground_station_accesses(
        physical_world, tuple(reversed(candidates))
    )

    assert len(selected) == 8
    assert len({c.satellite_id for c in selected}) == 7
    assert model.select_ground_station_accesses(physical_world, candidates) == selected



def test_ground_station_selection_preserves_primary_coverage_under_caps():
    physical_world, satellites, stations = _world(
        satellites=(_satellite(0), _satellite(1)),
        stations=(_station(0), _station(1), _station(2)),
    )
    model = CoverageFirstGroundStationAccessPlanningModel(
        default_ground_station_limit=2
    )
    candidates = tuple(
        replace(
            _ground_candidate(station, satellite),
            elevation_deg=(70.0 if satellite is satellites[0] else 60.0),
        )
        for station in stations
        for satellite in satellites
    )

    selected = model.select_ground_station_accesses(
        physical_world, candidates
    )
    station_counts = {
        station.node_id: sum(
            value.ground_station_id == station.node_id for value in selected
        )
        for station in stations
    }
    satellite_counts = {
        satellite.node_id: sum(
            value.satellite_id == satellite.node_id for value in selected
        )
        for satellite in satellites
    }

    assert len(selected) == 4
    assert all(count >= 1 for count in station_counts.values())
    assert satellite_counts == {
        satellites[0].node_id: 2,
        satellites[1].node_id: 2,
    }


def test_ground_station_selection_consumes_only_sparse_feasible_input():
    satellites = tuple(_satellite(index) for index in range(20))
    stations = tuple(_station(index) for index in range(10))
    physical_world, satellites, stations = _world(
        satellites=satellites,
        stations=stations,
    )
    model = CoverageFirstGroundStationAccessPlanningModel(
        default_ground_station_limit=8
    )
    sparse = (
        _ground_candidate(stations[0], satellites[0]),
        _ground_candidate(stations[-1], satellites[-1]),
    )

    assert model.select_ground_station_accesses(physical_world, ()) == ()
    assert _ground_pairs(
        model.select_ground_station_accesses(physical_world, sparse)
    ) == _ground_pairs(sparse)
    assert len(sparse) == 2
    assert len(stations) * len(satellites) == 200


def test_ground_station_selection_flows_into_operational_connections():
    physical_world, satellites, stations = _world(
        satellites=(_satellite(0), _satellite(1)),
        stations=(_station(0),),
    )
    planner, _ = _planner(
        physical_world,
        ground_station_model=CoverageFirstGroundStationAccessPlanningModel(
            default_ground_station_limit=1, ground_station_policy_limit=1
        ),
    )
    higher = replace(
        _ground_candidate(stations[0], satellites[1]), elevation_deg=70.0
    )
    lower = replace(
        _ground_candidate(stations[0], satellites[0]), elevation_deg=40.0
    )
    try:
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=(lower, higher)),
        )
        ground_connections = tuple(
            value
            for value in planner.get_state().operational_connections
            if value.connection_id.kind
            is OperationalConnectionKind.GROUND_STATION_ACCESS
        )
        assert len(ground_connections) == 1
        assert {
            ground_connections[0].connection_id.endpoint_a,
            ground_connections[0].connection_id.endpoint_b,
        } == {stations[0].node_id, satellites[1].node_id}
        assert ground_connections[0].endpoint_a_to_b.delay_us == (
            higher.propagation_delay_us
        )
        assert ground_connections[0].endpoint_b_to_a.delay_us == (
            higher.propagation_delay_us
        )
    finally:
        planner.shutdown()


def test_canonical_scale_evidence_is_structurally_bounded_and_compact():
    def summary(
        feasible_count: int, name: str
    ) -> PlanningCandidateSummary:
        return PlanningCandidateSummary(
            feasible_count=feasible_count,
            histograms=(
                PlanningCandidateHistogram(
                    name=name,
                    sample_count=feasible_count,
                    bins=(
                        PlanningHistogramBin("representative", feasible_count),
                    ),
                ),
            ),
        )

    transition = PlanTransition(EPOCH)
    plan = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        observation_interval=timedelta(seconds=5),
        start_timestamp=EPOCH,
        transitions=(transition,),
    )
    selected_isls = tuple(
        InterSatelliteLifecycleEvidence(
            candidate=InterSatelliteLinkCandidate(
                NodeId(65001, "satellite", f"sat-{2 * index:04d}"),
                NodeId(65001, "satellite", f"sat-{2 * index + 1:04d}"),
                True,
                0,
                1_000_000.0,
                3_336,
            ),
            selected_since=EPOCH - timedelta(seconds=20),
            acquisition_duration=timedelta(seconds=20),
            remaining_acquisition=timedelta(0),
            operational=True,
        )
        for index in range(1_434)
    )
    selected_ground = tuple(
        GroundStationAccessCandidate(
            NodeId(65001, "ground-station", f"ground-{index:04d}"),
            NodeId(65001, "satellite", f"sat-{index:04d}"),
            2_000_000.0,
            45.0,
            6_672,
        )
        for index in range(705)
    )
    selected_zones = tuple(
        ZoneServiceCandidate(
            f"zone-{index:04d}",
            NodeId(65001, "satellite", f"sat-{index:04d}"),
            1_500_000.0,
            50.0,
            5_004,
        )
        for index in range(714)
    )
    inspection = PlanningInspection(
        timestamp=EPOCH,
        feasible_isl_candidates=12_390,
        selected_isls=1_434,
        acquiring_isls=0,
        operational_isls=1_434,
        rejected_isls=10_956,
        underfilled_isl_slots=0,
        ground_station_accesses=705,
        zones_served=714,
        zones_unserved=0,
        routes_reachable=0,
        routes_unreachable=0,
        route_recomputed=False,
        stages=("physical-input-validation",),
    )
    frame = PlanningFrameEvidence(
        timestamp=EPOCH,
        inter_satellite_candidate_summary=summary(12_390, "distance-m"),
        ground_station_access_candidate_summary=summary(
            6_769, "elevation-deg"
        ),
        zone_service_candidate_summary=summary(3_167, "elevation-deg"),
        physical_link_work_counts=PhysicalLinkWorkCounts(
            inter_satellite_pairs_requested=12_390,
            inter_satellite_pairs_evaluated=12_390,
            inter_satellite_pairs_feasible=12_390,
            ground_station_pairs_requested=6_769,
            ground_station_pairs_evaluated=6_769,
            ground_station_pairs_feasible=6_769,
            zone_service_pairs_requested=3_167,
            zone_service_pairs_evaluated=3_167,
            zone_service_pairs_feasible=3_167,
        ),
        selected_inter_satellite_links=selected_isls,
        selected_ground_station_accesses=selected_ground,
        selected_zone_service_candidates=selected_zones,
        inspection=inspection,
    )
    evidence = PublishedPlanningEvidence(
        plan=plan,
        transition_fingerprints=(plan_transition_fingerprint(transition),),
        frames=(frame,),
        frontiers=PlanningFrontiers(EPOCH, EPOCH, EPOCH),
    )
    encoded = published_planning_evidence_to_data(evidence)
    canonical_frame_bytes = json.dumps(
        encoded["frames"][0],
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    canonical_envelope_bytes = json.dumps(
        encoded,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert len(canonical_frame_bytes) < 1_250_000
    assert len(canonical_envelope_bytes) < 1_275_000
    assert set(value.name for value in fields(PlanningFrameEvidence)) == {
        "timestamp",
        "inter_satellite_candidate_summary",
        "ground_station_access_candidate_summary",
        "zone_service_candidate_summary",
        "physical_link_work_counts",
        "selected_inter_satellite_links",
        "selected_ground_station_accesses",
        "ground_station_preparations",
        "selected_zone_service_candidates",
        "inspection",
    }
    assert set(value.name for value in fields(PlanningInspection)).isdisjoint(
        {"decisions", "inter_satellite_candidates"}
    )
    assert published_planning_evidence_from_data(encoded) == evidence
    with pytest.raises(ValueError, match="at most 64"):
        PlanningCandidateHistogram(
            name="unbounded",
            sample_count=65,
            bins=tuple(
                PlanningHistogramBin(f"reason-{index}", 1)
                for index in range(65)
            ),
        )


def test_gateway_route_reference_shutdown_is_explicit_and_idempotent():
    route_model = GatewayHopCountRoutePlanningModel()
    route_model.shutdown(wait=True)
    route_model.shutdown(wait=False)


def test_plan_and_state_codecs_fail_closed_on_schema_unknown_fields_and_hashes():
    transition = PlanTransition(EPOCH)
    plan = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint="a" * 64,
        observation_interval=timedelta(seconds=5),
        start_timestamp=EPOCH,
        transitions=(transition,),
    )
    plan_data = plan_to_data(plan)
    with pytest.raises(ValueError, match="schema"):
        plan_from_data({**plan_data, "schema": "foreign"})
    with pytest.raises(ValueError, match="missing or unknown"):
        plan_from_data({**plan_data, "unexpected": True})
    with pytest.raises(ValueError, match="canonical Plan content"):
        plan_from_data({**plan_data, "plan_id": "0" * 64})

    state = apply_plan(None, plan, physical_world_fingerprint="a" * 64)
    state_data = planned_operational_state_to_data(state)
    with pytest.raises(ValueError, match="schema"):
        planned_operational_state_from_data(
            {**state_data, "schema": "foreign"}
        )


def test_injected_isl_candidate_truth_and_physical_caps_remain_authoritative():
    physical_world, satellites, _ = _world(
        satellites=tuple(_satellite(index, capability=1) for index in range(3))
    )
    observed = _isl_candidate(satellites[0], satellites[1])
    foreign = _isl_candidate(satellites[0], satellites[2])
    spoofing = _InjectedInterSatelliteModel(
        lambda physical_world, candidates, retained: InterSatelliteSelection(
            (foreign,), (), 0
        )
    )
    planner, _ = _planner(
        physical_world, inter_satellite_model=spoofing
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="absent from physical facts"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, isls=(observed,)),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()

    candidates = (observed, foreign)
    over_cap = _InjectedInterSatelliteModel(
        lambda physical_world, candidates, retained: InterSatelliteSelection(
            tuple(candidates), (), 0
        )
    )
    planner, _ = _planner(
        physical_world, inter_satellite_model=over_cap
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="exceeded PhysicalWorld capability"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, isls=candidates),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_injected_isl_model_is_the_selection_authority_without_replacing_planner():
    physical_world, satellites, _ = _world(
        satellites=tuple(_satellite(index, capability=2) for index in range(3))
    )
    candidates = (
        _isl_candidate(satellites[0], satellites[1]),
        _isl_candidate(satellites[0], satellites[2]),
    )
    pairs = tuple(
        (value.endpoint_a, value.endpoint_b) for value in candidates
    )
    model = _InjectedInterSatelliteModel(
        lambda physical_world, candidates, retained: InterSatelliteSelection(
            tuple(candidates),
            tuple(
                CandidateDecision(
                    CandidateDecisionKind.SELECTED,
                    "injected test selection",
                    value.endpoint_a,
                    value.endpoint_b,
                )
                for value in candidates
            ),
            0,
        ),
        pairs=pairs,
    )
    planner, _ = _planner(
        physical_world, inter_satellite_model=model
    )

    assert planner.create_physical_link_request().inter_satellite_pairs == pairs
    assert planner.observe(
        physical_world.frame_at(EPOCH),
        _observation(EPOCH, isls=candidates),
    ) == ()
    state = planner.get_state()
    assert len(state.operational_connections) == 2
    assert {
        value.connection_id.kind for value in state.operational_connections
    } == {OperationalConnectionKind.INTER_SATELLITE}
    assert len(planner.get_last_transition().established_connections) == 2
    planner.shutdown()


@pytest.mark.parametrize("failure", ("contradiction", "underfilled"))
def test_injected_isl_inspection_evidence_is_validated_atomically(failure):
    physical_world, satellites, _ = _world()
    candidate = _isl_candidate(satellites[0], satellites[1])

    def selection_factory(physical_world, candidates, retained):
        del physical_world, retained
        if failure == "contradiction":
            return InterSatelliteSelection(
                (candidates[0],),
                (
                    CandidateDecision(
                        CandidateDecisionKind.REJECTED,
                        "contradicts selected relationship",
                        candidates[0].endpoint_a,
                        candidates[0].endpoint_b,
                    ),
                ),
                0,
            )
        return InterSatelliteSelection(
            (),
            (
                CandidateDecision(
                    CandidateDecisionKind.REJECTED,
                    "not selected",
                    candidates[0].endpoint_a,
                    candidates[0].endpoint_b,
                ),
                CandidateDecision(
                    CandidateDecisionKind.UNDERFILLED,
                    "impossible underfill claim",
                ),
            ),
            1_000_000_000,
        )

    planner, _ = _planner(
        physical_world,
        inter_satellite_model=_InjectedInterSatelliteModel(selection_factory),
    )
    before = _planner_public_snapshot(planner)
    expected = "contradicts" if failure == "contradiction" else "exceed"
    with pytest.raises(ValueError, match=expected):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, isls=(candidate,)),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


@pytest.mark.parametrize(
    ("selection_factory", "message"),
    (
        (
            lambda candidates: (
                replace(
                    candidates[0],
                    propagation_delay_us=(
                        candidates[0].propagation_delay_us + 1
                    ),
                ),
            ),
            "falsified authoritative physical values",
        ),
        (
            lambda candidates: (candidates[0], candidates[0]),
            "duplicate pair",
        ),
    ),
    ids=("falsified-delay", "duplicate"),
)
def test_injected_isl_model_cannot_falsify_or_duplicate_candidates(
    selection_factory, message
):
    physical_world, satellites, _ = _world()
    candidate = _isl_candidate(satellites[0], satellites[1])
    model = _InjectedInterSatelliteModel(
        lambda physical_world, candidates, retained: InterSatelliteSelection(
            selection_factory(candidates), (), 0
        )
    )
    planner, _ = _planner(
        physical_world, inter_satellite_model=model
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match=message):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, isls=(candidate,)),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_injected_ground_candidate_values_and_both_endpoint_caps_are_enforced():
    satellites = (
        replace(_satellite(0), max_simultaneous_ground_station_accesses=1),
        replace(_satellite(1), max_simultaneous_ground_station_accesses=1),
    )
    stations = (
        replace(_station(), max_simultaneous_satellite_accesses=1),
    )
    physical_world, satellites, stations = _world(
        satellites=satellites, stations=stations
    )
    first = _ground_candidate(stations[0], satellites[0])
    falsified = replace(first, propagation_delay_us=first.propagation_delay_us + 1)
    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: (falsified,)
        ),
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="falsified authoritative physical values"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=(first,)),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()

    second = _ground_candidate(stations[0], satellites[1])
    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
    )
    with pytest.raises(ValueError, match="GroundStation physical capability"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=(first, second)),
        )
    planner.shutdown()

    one_satellite = (
        replace(_satellite(0), max_simultaneous_ground_station_accesses=1),
    )
    two_stations = (
        replace(_station(0), max_simultaneous_satellite_accesses=2),
        replace(_station(1), max_simultaneous_satellite_accesses=2),
    )
    physical_world, satellites, stations = _world(
        satellites=one_satellite, stations=two_stations
    )
    candidates = tuple(
        _ground_candidate(station, satellites[0]) for station in stations
    )
    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
    )
    with pytest.raises(ValueError, match="Satellite GS-access capability"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=candidates),
        )
    planner.shutdown()


def test_bounded_prehistory_warms_acquisition_without_preformal_transitions():
    physical_world, satellites, _ = _world()
    candidate = _proven(_isl_candidate(satellites[0], satellites[1]), start=-20)
    model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=1,
            desired_cross_plane_links=0,
            maximum_cross_plane_separation=0,
            acquisition_duration=timedelta(seconds=20),
        )
    )
    planner, _ = _planner(
        physical_world, inter_satellite_model=model
    )
    for seconds in (-20, -15, -10, -5):
        timestamp = EPOCH + timedelta(seconds=seconds)
        planner.prime(
            physical_world.frame_at(timestamp),
            _observation(timestamp, isls=(candidate,)),
            formal_start=EPOCH,
        )
    assert planner.get_frontiers() == PlanningFrontiers(
        EPOCH - timedelta(seconds=5), None, None
    )

    assert planner.observe(
        physical_world.frame_at(EPOCH),
        _observation(EPOCH, isls=(candidate,)),
    ) == ()
    transition = planner.get_last_transition()
    assert transition.timestamp == EPOCH
    assert len(transition.established_connections) == 1
    assert len(planner.get_state().operational_connections) == 1
    planner.shutdown()


def test_isl_acquisition_resets_after_selection_gap_before_reacquisition():
    physical_world, satellites, _ = _world()
    candidate = _proven(_isl_candidate(satellites[0], satellites[1]), start=-20)
    model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=1,
            desired_cross_plane_links=0,
            maximum_cross_plane_separation=0,
            acquisition_duration=timedelta(seconds=20),
        )
    )
    planner, _ = _planner(
        physical_world, inter_satellite_model=model
    )
    planner.observe(
        physical_world.frame_at(EPOCH),
        _observation(EPOCH, isls=(candidate,)),
    )
    assert planner.get_state().operational_connections == ()

    timestamp = EPOCH + timedelta(seconds=5)
    planner.observe(
        physical_world.frame_at(timestamp), _observation(timestamp)
    )
    assert planner.get_state().operational_connections == ()

    for seconds in (10, 15, 20, 25):
        timestamp = EPOCH + timedelta(seconds=seconds)
        planner.observe(
            physical_world.frame_at(timestamp),
            _observation(timestamp, isls=(candidate,)),
        )
        assert planner.get_state().operational_connections == ()
    timestamp = EPOCH + timedelta(seconds=30)
    planner.observe(
        physical_world.frame_at(timestamp),
        _observation(timestamp, isls=(candidate,)),
    )
    assert len(planner.get_state().operational_connections) == 1
    assert len(planner.get_last_transition().established_connections) == 1
    planner.shutdown()


@pytest.mark.parametrize(
    ("result_factory", "zone_candidates", "message"),
    (
        (
            lambda satellites: ZoneServicePlanningResult((), ()),
            lambda satellites: (),
            "exactly one explicit assignment per Zone",
        ),
        (
            lambda satellites: ZoneServicePlanningResult(
                (ZoneServiceAssignment("foreign-zone", None),),
                (ZoneServiceTransition("foreign-zone", None, None),),
            ),
            lambda satellites: (),
            "exactly one explicit assignment per Zone",
        ),
        (
            lambda satellites: ZoneServicePlanningResult(
                (
                    ZoneServiceAssignment(
                        "zone-a", satellites[1].node_id
                    ),
                ),
                (
                    ZoneServiceTransition(
                        "zone-a", None, satellites[1].node_id
                    ),
                ),
            ),
            lambda satellites: (
                ZoneServiceCandidate(
                    "zone-a", satellites[0].node_id, 1_000.0, 45.0, 5
                ),
            ),
            "absent from physical candidates",
        ),
    ),
    ids=("missing-explicit-zone", "foreign-zone", "absent-satellite"),
)
def test_injected_zone_model_cannot_forge_assignment_authority(
    result_factory, zone_candidates, message
):
    physical_world, satellites, _ = _world()
    model = _InjectedZoneServiceModel(
        lambda physical_world, candidates, previous: result_factory(
            satellites
        )
    )
    planner, _ = _planner(
        physical_world, zone_service_model=model
    )
    candidates = zone_candidates(satellites)
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match=message):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, zones=candidates),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_injected_models_must_surface_exact_compact_semantic_changes():
    physical_world, satellites, stations = _world()
    missing_zone_delta = _InjectedZoneServiceModel(
        lambda physical_world, candidates, previous: ZoneServicePlanningResult(
            (ZoneServiceAssignment("zone-a", None),), ()
        )
    )
    planner, _ = _planner(
        physical_world, zone_service_model=missing_zone_delta
    )
    with pytest.raises(ValueError, match="Zone compact changes"):
        planner.observe(
            physical_world.frame_at(EPOCH), _observation(EPOCH)
        )
    planner.shutdown()

    ground = _ground_candidate(stations[0], satellites[0])

    class _MissingParameterDelta:
        def __init__(self) -> None:
            self.delegate = _parameter_model()

        def plan_effective_parameters(
            self, timestamp, observations, previous_memory
        ):
            result = self.delegate.plan_effective_parameters(
                timestamp, observations, previous_memory
            )
            return replace(result, established_connections=())

    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
        parameter_model=_MissingParameterDelta(),
    )
    with pytest.raises(ValueError, match="Parameter compact changes"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=(ground,)),
        )
    planner.shutdown()

    demand = RouteDemand(
        satellites[0].node_id,
        GroundStationRouteDemandTarget(stations[0].node_id),
    )
    missing_route_delta = _InjectedRouteModel(
        lambda physical_world, connections, assignments, demands, previous: (
            RoutePlanningResult(
                tuple(
                    UnreachableRouteOutcome(
                        value, RouteUnreachableReason.NO_OPERATIONAL_PATH
                    )
                    for value in demands
                ),
                (),
            )
        )
    )
    planner, _ = _planner(
        physical_world,
        route_model=missing_route_delta,
        route_demands=(demand,),
    )
    with pytest.raises(ValueError, match="Route compact changes"):
        planner.observe(
            physical_world.frame_at(EPOCH), _observation(EPOCH)
        )
    planner.shutdown()


@pytest.mark.parametrize(
    ("field_name", "second_ground"),
    (
        ("parameter_updated_connections", "updated"),
        ("withdrawn_connection_ids", "withdrawn"),
    ),
    ids=("parameter-update", "withdrawal"),
)
def test_parameter_model_cannot_omit_update_or_withdrawal_delta(
    field_name, second_ground
):
    physical_world, satellites, stations = _world()

    def remove_target_delta(result):
        if getattr(result, field_name):
            return replace(result, **{field_name: ()})
        return result

    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
        parameter_model=_TransformingParameterModel(remove_target_delta),
    )
    first = _ground_candidate(stations[0], satellites[0], delay_us=1)
    planner.observe(
        physical_world.frame_at(EPOCH),
        _observation(EPOCH, ground=(first,)),
    )
    timestamp = EPOCH + timedelta(seconds=5)
    candidates = (
        (
            _ground_candidate(
                stations[0], satellites[0], delay_us=2
            ),
        )
        if second_ground == "updated"
        else ()
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="Parameter compact changes"):
        planner.observe(
            physical_world.frame_at(timestamp),
            _observation(timestamp, ground=candidates),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_route_model_cannot_publish_a_path_over_a_nonoperational_edge():
    physical_world, satellites, stations = _world()
    demand = RouteDemand(
        satellites[0].node_id,
        GroundStationRouteDemandTarget(stations[0].node_id),
    )

    def invalid_route(
        physical_world, connections, assignments, demands, previous
    ):
        del physical_world, connections, assignments
        outcome = ReachableRouteOutcome(
            demands[0],
            stations[0].node_id,
            (satellites[0].node_id, stations[0].node_id),
        )
        prior = next(
            (value for value in previous if value.demand == demands[0]), None
        )
        return RoutePlanningResult(
            (outcome,),
            (RouteOutcomeTransition(demands[0], prior, outcome),),
        )

    route_model = _InjectedRouteModel(invalid_route)
    planner, _ = _planner(
        physical_world,
        route_model=route_model,
        route_demands=(demand,),
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="non-operational Connection"):
        planner.observe(
            physical_world.frame_at(EPOCH), _observation(EPOCH)
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_route_model_cannot_terminate_at_the_wrong_ground_station():
    physical_world, satellites, stations = _world(
        satellites=(_satellite(0),),
        stations=(_station(0), _station(1)),
    )
    demand = RouteDemand(
        satellites[0].node_id,
        GroundStationRouteDemandTarget(stations[0].node_id),
    )
    selected = _ground_candidate(stations[1], satellites[0])

    def wrong_terminal(
        physical_world, connections, assignments, demands, previous
    ):
        del physical_world, assignments
        assert len(connections) == 1
        outcome = ReachableRouteOutcome(
            demands[0],
            stations[1].node_id,
            (satellites[0].node_id, stations[1].node_id),
        )
        prior = next(
            (value for value in previous if value.demand == demands[0]), None
        )
        return RoutePlanningResult(
            (outcome,),
            (RouteOutcomeTransition(demands[0], prior, outcome),),
        )

    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
        route_model=_InjectedRouteModel(wrong_terminal),
        route_demands=(demand,),
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="wrong target"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=(selected,)),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_coordination_policy_is_precedence_only_and_cannot_reverse_readiness():
    physical_world, satellites, stations = _world()

    class _EventOwningPolicy:
        def coordinate(self, transition, canonical_events, required):
            del transition, required
            return canonical_events

    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
        coordination_policy=_EventOwningPolicy(),
    )
    with pytest.raises(TypeError, match="only SemanticEventPrecedence"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(
                EPOCH,
                ground=(_ground_candidate(stations[0], satellites[0]),),
            ),
        )
    planner.shutdown()

    ground = _ground_candidate(stations[0], satellites[0])
    demand = RouteDemand(
        satellites[0].node_id,
        GroundStationRouteDemandTarget(stations[0].node_id),
    )

    def valid_route(
        physical_world, connections, assignments, demands, previous
    ):
        del physical_world, assignments
        assert len(connections) == 1
        outcome = ReachableRouteOutcome(
            demands[0],
            stations[0].node_id,
            (satellites[0].node_id, stations[0].node_id),
        )
        prior = next(
            (value for value in previous if value.demand == demands[0]), None
        )
        return RoutePlanningResult(
            (outcome,),
            (RouteOutcomeTransition(demands[0], prior, outcome),),
        )

    class _ReverseParameterAndRoute:
        def coordinate(self, transition, canonical_events, required):
            del transition
            parameter = next(
                value
                for value in canonical_events
                if value.kind is SemanticEventKind.PARAMETER_READY
            )
            route = next(
                value
                for value in canonical_events
                if value.kind is SemanticEventKind.ROUTE_REACHABLE
            )
            retained = tuple(
                value
                for value in required
                if value.predecessor != parameter or value.successor != route
            )
            return (*retained, SemanticEventPrecedence(route, parameter))

    planner, _ = _planner(
        physical_world,
        ground_station_model=_InjectedGroundStationModel(
            lambda physical_world, candidates: tuple(candidates)
        ),
        route_model=_InjectedRouteModel(valid_route),
        coordination_policy=_ReverseParameterAndRoute(),
        route_demands=(demand,),
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(ValueError, match="omitted required semantic event ordering"):
        planner.observe(
            physical_world.frame_at(EPOCH),
            _observation(EPOCH, ground=(ground,)),
        )
    assert _planner_public_snapshot(planner) == before
    planner.shutdown()


def test_planner_failure_is_atomic_and_same_timestamp_is_retryable():
    physical_world, satellites, stations = _world()
    demand = RouteDemand(
        satellites[0].node_id,
        GroundStationRouteDemandTarget(stations[0].node_id),
    )
    route_model = _FailOnceRouteModel()
    planner, _ = _planner(
        physical_world,
        route_model=route_model,
        route_demands=(demand,),
    )
    before = _planner_public_snapshot(planner)
    with pytest.raises(RuntimeError, match="injected route failure"):
        planner.observe(
            physical_world.frame_at(EPOCH), _observation(EPOCH)
        )
    assert _planner_public_snapshot(planner) == before

    assert planner.observe(
        physical_world.frame_at(EPOCH), _observation(EPOCH)
    ) == ()
    assert planner.get_state().timestamp == EPOCH
    assert planner.get_frontiers() == PlanningFrontiers(EPOCH, EPOCH, None)
    planner.shutdown()


def test_route_model_is_not_recomputed_without_route_relevant_change():
    physical_world, satellites, stations = _world()

    def unreachable(
        physical_world, connections, assignments, demands, previous
    ):
        del physical_world, connections, assignments
        previous_by_demand = {value.demand: value for value in previous}
        outcomes = tuple(
            UnreachableRouteOutcome(
                demand, RouteUnreachableReason.NO_OPERATIONAL_PATH
            )
            for demand in demands
        )
        outcome_by_demand = {value.demand: value for value in outcomes}
        transitions = tuple(
            RouteOutcomeTransition(
                demand,
                previous_by_demand.get(demand),
                outcome_by_demand.get(demand),
            )
            for demand in set(previous_by_demand) | set(outcome_by_demand)
            if previous_by_demand.get(demand) != outcome_by_demand.get(demand)
        )
        return RoutePlanningResult(outcomes, transitions)

    route_model = _InjectedRouteModel(unreachable)
    planner, _ = _planner(physical_world, route_model=route_model)
    for seconds in (0, 5):
        timestamp = EPOCH + timedelta(seconds=seconds)
        assert planner.observe(
            physical_world.frame_at(timestamp), _observation(timestamp)
        ) == ()
    assert route_model.calls == 1
    assert planner.get_route_recomputation_count() == 1

    demand = RouteDemand(
        satellites[0].node_id,
        GroundStationRouteDemandTarget(stations[0].node_id),
    )
    planner.set_route_demands((demand,))
    for seconds in (10, 15):
        timestamp = EPOCH + timedelta(seconds=seconds)
        assert planner.observe(
            physical_world.frame_at(timestamp), _observation(timestamp)
        ) == ()
    assert route_model.calls == 2
    assert planner.get_route_recomputation_count() == 2
    planner.shutdown()

    ground_model = _InjectedGroundStationModel(
        lambda physical_world, candidates: tuple(candidates)
    )

    def direct_route(
        physical_world, connections, assignments, demands, previous
    ):
        del physical_world, assignments
        assert len(connections) == 1
        previous_by_demand = {value.demand: value for value in previous}
        outcomes = tuple(
            ReachableRouteOutcome(
                value,
                stations[0].node_id,
                (satellites[0].node_id, stations[0].node_id),
            )
            for value in demands
        )
        transitions = tuple(
            RouteOutcomeTransition(
                outcome.demand,
                previous_by_demand.get(outcome.demand),
                outcome,
            )
            for outcome in outcomes
            if previous_by_demand.get(outcome.demand) != outcome
        )
        return RoutePlanningResult(outcomes, transitions)

    route_model = _InjectedRouteModel(direct_route)
    planner, _ = _planner(
        physical_world,
        ground_station_model=ground_model,
        route_model=route_model,
        route_demands=(demand,),
    )
    at_zero = _ground_candidate(stations[0], satellites[0], delay_us=1)
    planner.observe(
        physical_world.frame_at(EPOCH),
        _observation(EPOCH, ground=(at_zero,)),
    )
    assert route_model.calls == 1
    at_five = _ground_candidate(stations[0], satellites[0], delay_us=2)
    timestamp = EPOCH + timedelta(seconds=5)
    planner.observe(
        physical_world.frame_at(timestamp),
        _observation(timestamp, ground=(at_five,)),
    )
    assert len(planner.get_last_transition().parameter_updated_connections) == 1
    assert planner.get_last_transition().route_outcome_transitions == ()
    assert route_model.calls == 1
    assert planner.get_route_recomputation_count() == 1
    planner.shutdown()


def test_gateway_route_worker_telemetry_is_bounded_reused_and_shutdown(monkeypatch):
    import skyseed_v2.simulator._route_computation as route_computation

    monkeypatch.setattr(
        route_computation,
        "_bounded_route_worker_count",
        lambda node_count, job_count: 2,
    )
    satellites = (_satellite(0), _satellite(1))
    stations = (_station(0), _station(1))
    physical_world, satellites, stations = _world(
        satellites=satellites, stations=stations
    )
    parameters = EffectiveConnectionParameters(1_000_000, 1, 0)
    connections = tuple(
        OperationalConnection(
            OperationalConnectionId(
                OperationalConnectionKind.GROUND_STATION_ACCESS,
                station.node_id,
                satellite.node_id,
            ),
            parameters,
            parameters,
        )
        for station, satellite in zip(stations, satellites, strict=True)
    )
    demands = tuple(
        RouteDemand(
            satellite.node_id,
            GroundStationRouteDemandTarget(station.node_id),
        )
        for satellite in satellites
        for station in stations
    )
    assignments = (ZoneServiceAssignment("zone-a", None),)
    model = GatewayHopCountRoutePlanningModel()
    engine = model._route_engine
    try:
        first = model.plan_route_outcomes(
            physical_world, connections, assignments, demands, ()
        )
        first_observation = engine.last_compute_observation
        assert first_observation is not None
        assert first_observation.selected_worker_count == 2
        assert first_observation.executor_created
        assert not first_observation.executor_reused
        assert model._active_compute_worker_count() == 2

        second = model.plan_route_outcomes(
            physical_world,
            connections,
            assignments,
            demands,
            first.outcomes,
        )
        second_observation = engine.last_compute_observation
        assert second_observation is not None
        assert second_observation.selected_worker_count == 1
        assert not second_observation.executor_created
        assert not second_observation.executor_reused
        assert second_observation.reused_request_count == len(demands)
        assert second_observation.worker_activities == ()
        assert model._active_compute_worker_count() == 2
        assert second.outcomes == first.outcomes
        assert second.transitions == ()
        assert engine.executor_lifecycle.executor_generation == 1
        changed_connections = (*connections, OperationalConnection(
            OperationalConnectionId(OperationalConnectionKind.INTER_SATELLITE,
                                    satellites[0].node_id, satellites[1].node_id),
            parameters, parameters,
        ))
        changed = model.plan_route_outcomes(
            physical_world, changed_connections, assignments, demands, second.outcomes,
        )
        assert engine.last_compute_observation.executor_reused
        assert engine.last_compute_observation.reused_request_count == 0
        monkeypatch.setattr(route_computation, "_bounded_route_worker_count", lambda node_count, job_count: 1)
        reference = GatewayHopCountRoutePlanningModel()
        try:
            serial = reference.plan_route_outcomes(
                physical_world, changed_connections, assignments, tuple(reversed(demands)), second.outcomes,
            )
            assert serial == changed
            assert reference._route_engine.last_compute_observation.reused_request_count == 0
        finally:
            reference.shutdown()
    finally:
        model.shutdown(wait=True)
    assert model._active_compute_worker_count() == 0
    assert engine.executor_lifecycle.executor_shutdown_count == 1


def _proven(candidate, *, start=-5, end=300):
    from skyseed_v2.simulator import PhysicalContactWindow
    return replace(candidate, contact_window=PhysicalContactWindow(
        EPOCH + timedelta(seconds=start),
        None if end is None else EPOCH + timedelta(seconds=end)))


def test_forward_admission_reservation_loss_and_real_isl_replacement():
    world, satellites, _ = _world(satellites=tuple(_satellite(i, capability=1) for i in range(3)))
    model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(desired_same_plane_links=1,
            desired_cross_plane_links=0, acquisition_duration=timedelta(seconds=5),
            minimum_service_duration=timedelta(seconds=10)))
    planner, _ = _planner(world, inter_satellite_model=model)
    ab, ac = (_proven(_isl_candidate(satellites[0], satellites[i])) for i in (1, 2))
    def advance(t, candidates):
        timestamp = EPOCH+timedelta(seconds=t)
        planner.observe(world.frame_at(timestamp), _observation(timestamp, isls=candidates))
        return planner.get_state().operational_connections
    assert not advance(0, (ab, ac))  # Preparation, not elapsed minimum service.
    assert advance(5, (ab, ac))[0].connection_id.endpoint_b == satellites[1].node_id
    assert len(planner._lifecycle_by_pair) == 1  # The full port cannot prepare AC.
    assert not advance(10, (ac,))  # Physical loss releases AB; AC still pays preparation.
    assert advance(15, (ac,))[0].connection_id.endpoint_b == satellites[2].node_id
    planner.shutdown()


def test_admission_short_window_cancellation_and_terminal_uncertainty():
    world, satellites, _ = _world()
    model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(desired_same_plane_links=1,
            desired_cross_plane_links=0, acquisition_duration=timedelta(seconds=5),
            minimum_service_duration=timedelta(seconds=10)))
    planner, _ = _planner(world, inter_satellite_model=model)
    candidate = _isl_candidate(*satellites)
    for t, candidates, count in (
        (0, (_proven(candidate, end=14),), 0),  # 5 preparation + 10 service fails.
        (5, (_proven(candidate, end=20),), 0),  # Equality admits at selection time.
        (10, (), 0),                          # Cancel before completion.
        (15, (candidate,), 0),                # Unknown future cannot admit.
        (20, (_proven(candidate, end=None),), 0),
        (25, (_proven(candidate, end=None),), 1),
    ):
        stamp = EPOCH+timedelta(seconds=t)
        planner.observe(world.frame_at(stamp), _observation(stamp, isls=candidates))
        assert len(planner.get_state().operational_connections) == count
    assert planner._lifecycle_by_pair[_candidate_pair_for_test(candidate)].selected_since == EPOCH+timedelta(seconds=20)
    planner.shutdown()


def _candidate_pair_for_test(candidate):
    return candidate.endpoint_a, candidate.endpoint_b


def test_initial_prehistory_prepares_both_kinds_without_extra_formal_transition():
    world, satellites, stations = _world()
    isl_model = RetainFirstInterSatelliteConnectionPlanningModel(
        RetainFirstInterSatelliteConfiguration(desired_same_plane_links=1,
            desired_cross_plane_links=0, acquisition_duration=timedelta(seconds=5),
            minimum_service_duration=timedelta(seconds=180)))
    gs_model = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=1, ground_station_policy_limit=1,
        acquisition_duration=timedelta(seconds=5), minimum_service_duration=timedelta(seconds=60))
    planner, _ = _planner(world, inter_satellite_model=isl_model, ground_station_model=gs_model)
    isl = _proven(_isl_candidate(*satellites))
    gs = _proven(_ground_candidate(stations[0], satellites[0]))
    assert planner.required_prehistory == timedelta(seconds=5)
    before = EPOCH-timedelta(seconds=5)
    planner.prime(world.frame_at(before), _observation(before, isls=(isl,), ground=(gs,)), formal_start=EPOCH)
    assert planner.get_frontiers().planning is None
    planner.observe(world.frame_at(EPOCH), _observation(EPOCH, isls=(isl,), ground=(gs,)))
    assert len(planner.get_state().operational_connections) == 2
    plans = planner.finish(end_timestamp=EPOCH)
    assert sum(len(p.transitions) for p in plans) == 1
    planner.shutdown()


@pytest.mark.parametrize('satellite_capacity,continuing_at_switch', [(1, 0), (2, 0)])
def test_gs_coverage_beats_incumbency_and_respects_preparation_capacity(satellite_capacity, continuing_at_switch):
    satellites = tuple(replace(_satellite(i), max_simultaneous_ground_station_accesses=satellite_capacity) for i in range(2))
    stations = tuple(replace(_station(i), max_simultaneous_satellite_accesses=1) for i in range(2))
    world, satellites, stations = _world(satellites=satellites, stations=stations)
    model = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=1, ground_station_policy_limit=1,
        acquisition_duration=timedelta(seconds=5), minimum_service_duration=timedelta(seconds=60))
    planner, _ = _planner(world, ground_station_model=model)
    ga, gb, ha = (_proven(_ground_candidate(stations[g], satellites[a]))
                  for g, a in ((0,0), (0,1), (1,0)))
    for t, candidates, count in ((0,(ga,),0), (5,(ga,),1),
                                  (10,(gb,ha,ga),continuing_at_switch),
                                  (15,(ha,ga,gb),2)):
        stamp = EPOCH+timedelta(seconds=t)
        planner.observe(world.frame_at(stamp), _observation(stamp, ground=candidates))
        connections = planner.get_state().operational_connections
        assert len(connections) == count
    # Both stations are covered, with capacity held throughout preparations.
    assert {c.connection_id.endpoint_a for c in connections} == {s.node_id for s in stations}
    planner.shutdown()


def test_gs_valid_pending_and_operational_incumbents_ignore_quality_churn():
    world, satellites, stations = _world()
    model = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=1, ground_station_policy_limit=1,
        acquisition_duration=timedelta(seconds=5), minimum_service_duration=timedelta(seconds=60))
    planner, _ = _planner(world, ground_station_model=model)
    first = _proven(_ground_candidate(stations[0], satellites[0]))
    better = replace(_proven(_ground_candidate(stations[0], satellites[1])), elevation_deg=80)
    for t, candidates in ((0,(first,)), (5,(better,first)), (10,(first,better))):
        stamp = EPOCH+timedelta(seconds=t)
        planner.observe(world.frame_at(stamp), _observation(stamp, ground=candidates))
    assert planner.get_state().operational_connections[0].connection_id.endpoint_b == satellites[0].node_id
    assert next(iter(planner._ground_lifecycle_by_pair.values())).selected_since == EPOCH
    planner.shutdown()


def test_formal_semantics_and_incremental_compiler_are_batch_independent(tmp_path):
    from skyseed_v2.backend.bpf import compile_bpf
    from skyseed_v2.backend.bpf._runner_artifact import _RunnerArtifactWriter, _open_runner_artifact, _resolve_nominal_boundaries
    from skyseed_v2.simulator import PhysicalContactWindow
    world, satellites, stations = _world()
    sequences, compiled = [], []
    for batch_size in (1, 7, 32):
        isl_model = RetainFirstInterSatelliteConnectionPlanningModel(
            RetainFirstInterSatelliteConfiguration(desired_same_plane_links=1,
                desired_cross_plane_links=0, maximum_cross_plane_separation=0,
                acquisition_duration=timedelta(seconds=5), minimum_service_duration=timedelta(seconds=30)))
        planner, _ = _planner(world, inter_satellite_model=isl_model,
            ground_station_model=CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=1, ground_station_policy_limit=1,
                acquisition_duration=timedelta(seconds=5), minimum_service_duration=timedelta(seconds=30)),
            planning_window_policy=FrameBatchPlanningWindowPolicy(batch_size))
        previous, reference_previous, state = None, None, None
        transitions, boundaries, expected_nominal, realizations = [], [], [], []
        writer = _RunnerArtifactWriter(tmp_path / str(batch_size))
        def consume(plans):
            nonlocal previous, reference_previous, state
            for plan in plans:
                replay = apply_plan(state, plan, physical_world_fingerprint=world.semantic_fingerprint())
                realization = compile_bpf(plan, world, previous)
                # Detached realizations reconstruct full indexes, providing an
                # independent baseline for the incremental cache path.
                reference = compile_bpf(plan, world, reference_previous)
                assert realization == reference
                assert realization.output_planned_state == replay
                object.__setattr__(reference, '_incremental_indexes', None)
                reference_previous, previous, state = reference, realization, replay
                writer.append(realization)
                transitions.extend(plan.transitions)
                boundaries.extend(realization.compiled_activation_boundaries)
                expected_nominal.extend(_resolve_nominal_boundaries((realization,), realization_index_offset=len(realizations)))
                realizations.append(realization)
                evidence = planner.get_published_evidence(plan.plan_id)
                assert published_planning_evidence_from_data(published_planning_evidence_to_data(evidence)) == evidence
                planner.release_published_through(plan.plan_id)
        try:
            for index in range(53):
                timestamp = EPOCH + timedelta(seconds=5*index)
                isl = _proven(_isl_candidate(satellites[0], satellites[1]), start=0)
                ground = _proven(_ground_candidate(stations[0], satellites[index//17 % 2]), start=0)
                consume(planner.observe(world.frame_at(timestamp), _observation(timestamp,
                    isls=(isl,) if not 12 <= index <= 14 else (), ground=(ground,))))
            consume(planner.finish(end_timestamp=timestamp))
            manifest = writer.finalize()
            reader = _open_runner_artifact(manifest)
            try:
                # Full decode is qualification work, outside generation.
                assert tuple(reader.boundary(i) for i in range(len(boundaries))) == tuple(expected_nominal)
            finally:
                reader.close()
        finally:
            writer.close()
            planner.shutdown()
        sequences.append(tuple(transitions))
        compiled.append(tuple(boundaries))
    assert sequences[0] == sequences[1] == sequences[2]
    assert compiled[0] == compiled[1] == compiled[2]
