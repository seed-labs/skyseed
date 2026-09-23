"""Authoritative incremental Network Planner and bounded Plan publisher."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from math import isfinite
import sys
from time import perf_counter

from skyseed.shared import NodeId
from skyseed.shared._shutdown import _shutdown_owned
from skyseed.shared.recording import PerformanceRecorder, record_performance
from skyseed.shared.time import normalize_utc_datetime

from ..physical import (
    GroundStationAccessCandidate,
    InterSatelliteLinkCandidate,
    PhysicalPositionSnapshot,
    PhysicalLinkQueryResult,
    PhysicalLinkRequest,
    PhysicalWorld,
    ZoneServiceCandidate,
)
from ._physical_queries import _PhysicalQueries
from ._transit import _route_step_allows_transit
from .configuration import PlannerConfiguration
from .selection import ConnectionSelectionState
from .inter_satellite import InterSatelliteSelection, InterSatelliteSelectionDiagnostics, InterSatelliteConnectionPlanningModel
from .ground_station import GroundStationAccessPlanningModel
from .zone_service import ZoneServicePlanningModel, ZoneServicePlanningResult
from .parameters import (
    ConnectionParameterObservation,
    ParameterPlanningModel,
    ParameterPlanningResult,
)
from .routing import RoutePlanningModel, RoutePlanningResult
from .diagnostics import (
    CandidateDecision, CandidateDecisionKind, PlanningInspection,
    PlanningFrontiers, InterSatelliteLifecycleEvidence, GroundStationLifecycleEvidence,
    PlanningCandidateHistogram, PlanningCandidateSummary, PlanningFrameEvidence,
    PlanningHistogramBin, PublishedPlanningEvidence,
)
from .values import (
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    Plan,
    PlanTransition,
    PlannedOperationalState,
    GroundStationRouteDemandTarget, SatelliteRouteDemandTarget,
    ReachableRouteOutcome, LocalRouteOutcome, LogicalExit,
    _policy_references, _route_demand_key,
    RouteDemand,
    RouteOutcomeTransition,
    RouteUnreachableReason,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    ZoneServiceAssignment,
    ZoneServiceTransition,
    _create_plan_with_output_state,
    _updated_operational_state,
    plan_transition_fingerprint,
    planned_operational_state_fingerprint,
    semantic_event_references,
)


_IslPair = tuple[NodeId, NodeId]
_STAGES = (
    "physical-input-validation",
    "isl-candidate-selection",
    "acquisition/lifecycle-advancement",
    "ground-station-planning",
    "zone-planning",
    "parameter-planning",
    "route-planning",
    "coordination",
    "state-advancement",
)


@dataclass(frozen=True, slots=True)
class _ConnectionPreparation:
    candidate: InterSatelliteLinkCandidate | GroundStationAccessCandidate
    selected_since: datetime
    operational: bool


@dataclass(frozen=True, slots=True)
class _FramePlanningResult:
    transition: PlanTransition
    evidence: PlanningFrameEvidence

    @property
    def inspection(self) -> PlanningInspection:
        return self.evidence.inspection


class Planner:
    """Own semantic planning state and bounded windowed Plan publication.

    Layer: public Simulator operational-planning lifecycle owner. It is public
    because ``Simulator`` composition and the common realization producer need
    one authoritative Model-injected planning boundary. It is not a Timeline
    cursor, PhysicalWorld owner, Compiler, Runner, or wrapper around a second
    planning state machine.
    """

    __slots__ = (
        "_physical_world",
        "_physical_queries",
        "_physical_world_fingerprint",
        "_observation_interval",
        "_inter_satellite_model",
        "_ground_station_model",
        "_zone_service_model",
        "_parameter_model",
        "_route_model",
        "_configuration",
        "_required_prehistory",
        "_execution_observer",
        "_lifecycle_by_pair",
        "_ground_lifecycle_by_pair",
        "_parameter_memory",
        "_route_demands",
        "_route_demands_dirty",
        "_physical_observation_frontier",
        "_planning_frontier",
        "_publication_frontier",
        "_expected_formal_start",
        "_formal_started",
        "_finished_end",
        "_shutdown",
        "_current_state",
        "_last_published_state",
        "_last_published_plan_id",
        "_last_transition",
        "_last_inspection",
        "_last_route_demands",
        "_route_recomputation_count",
        "_unpublished",
        "_published_evidence",
        "_maximum_retained_evidence",
    )

    def __init__(
        self,
        *,
        physical_world: PhysicalWorld,
        observation_interval: timedelta,
        inter_satellite_model: InterSatelliteConnectionPlanningModel,
        ground_station_model: GroundStationAccessPlanningModel,
        zone_service_model: ZoneServicePlanningModel,
        parameter_model: ParameterPlanningModel,
        route_model: RoutePlanningModel,
        route_demands: Iterable[RouteDemand] | None = None,
        configuration: PlannerConfiguration = PlannerConfiguration(),
        execution_observer: PerformanceRecorder | None = None,
    ) -> None:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(observation_interval, timedelta):
            raise TypeError("observation_interval must be a timedelta")
        if observation_interval <= timedelta(0):
            raise ValueError("observation_interval must be positive")
        _require_methods(
            inter_satellite_model,
            "inter_satellite_model",
            (
                "create_inter_satellite_candidate_pairs",
                "select_inter_satellite_links",
            ),
        )
        _require_methods(
            ground_station_model,
            "ground_station_model",
            ("select_ground_station_accesses",),
        )
        _require_methods(
            zone_service_model,
            "zone_service_model",
            ("assign_zone_service",),
        )
        _require_methods(
            parameter_model,
            "parameter_model",
            ("plan_effective_parameters",),
        )
        _require_methods(
            route_model,
            "route_model",
            ("plan_route_outcomes",),
        )
        if not isinstance(configuration, PlannerConfiguration):
            raise TypeError("configuration must be a PlannerConfiguration")
        preparation = max(configuration.inter_satellite_acquisition_duration,
                          configuration.ground_station_acquisition_duration)
        # Round the actual preparation dependency up to formal samples.
        required_prehistory = observation_interval * (
            (preparation + observation_interval - timedelta.resolution)
            // observation_interval
        )
        self._physical_world = physical_world
        self._physical_world_fingerprint = (
            physical_world.semantic_fingerprint()
        )
        self._observation_interval = observation_interval
        self._inter_satellite_model = inter_satellite_model
        self._ground_station_model = ground_station_model
        self._zone_service_model = zone_service_model
        self._parameter_model = parameter_model
        self._route_model = route_model
        self._physical_queries = _PhysicalQueries(physical_world, inter_satellite_model,
            ground_station_model, configuration, observation_interval)
        self._configuration = configuration
        self._required_prehistory = required_prehistory
        self._execution_observer = execution_observer
        self._lifecycle_by_pair: dict[
            _IslPair, _ConnectionPreparation
        ] = {}
        self._ground_lifecycle_by_pair = {}
        self._parameter_memory: object = None
        self._route_demands = None if route_demands is None else _validated_route_demands(route_demands)
        self._route_demands_dirty = True
        self._physical_observation_frontier: datetime | None = None
        self._planning_frontier: datetime | None = None
        self._publication_frontier: datetime | None = None
        self._expected_formal_start: datetime | None = None
        self._formal_started = False
        self._finished_end: datetime | None = None
        self._shutdown = False
        self._current_state: PlannedOperationalState | None = None
        self._last_published_state: PlannedOperationalState | None = None
        self._last_published_plan_id: str | None = None
        self._last_transition: PlanTransition | None = None
        self._last_inspection: PlanningInspection | None = None
        self._last_route_demands: tuple[RouteDemand, ...] = ()
        self._route_recomputation_count = 0
        self._unpublished: list[_FramePlanningResult] = []
        self._published_evidence: list[PublishedPlanningEvidence] = []
        self._maximum_retained_evidence = 2

    @property
    def configuration(self) -> PlannerConfiguration:
        """Return this Planner's immutable preparation/publication configuration."""
        return self._configuration

    def get_inter_satellite_model(self) -> InterSatelliteConnectionPlanningModel:
        return self._inter_satellite_model

    def get_ground_station_model(self) -> GroundStationAccessPlanningModel:
        return self._ground_station_model

    def get_zone_service_model(self) -> ZoneServicePlanningModel:
        return self._zone_service_model

    def get_parameter_model(self) -> ParameterPlanningModel:
        return self._parameter_model

    def get_route_model(self) -> RoutePlanningModel:
        return self._route_model

    def _set_execution_observer(self, observer: PerformanceRecorder) -> None:
        if self._execution_observer is not None:
            raise RuntimeError("Network Planner observation is already enabled")
        self._execution_observer = observer

    def _get_execution_observer(self) -> PerformanceRecorder | None:
        """Return the private recorder used by the common producer."""

        return self._execution_observer

    def _producer_input_contract(self) -> tuple[str, timedelta]:
        """Return exact immutable provenance required by the producer."""

        return self._physical_world_fingerprint, self._observation_interval

    def _physical_prediction_requirements(self):
        return self._physical_queries._physical_prediction_requirements()

    def _selection_state(self, timestamp, lifecycle, kind, duration):
        times, operational = {}, set()
        for pair, preparation in lifecycle.items():
            identifier = OperationalConnectionId(kind, *pair)
            times[identifier] = preparation.selected_since
            if preparation.operational:
                operational.add(identifier)
        return ConnectionSelectionState(timestamp, self._observation_interval, duration,
                                        times, frozenset(operational))

    def create_physical_link_request(self) -> PhysicalLinkRequest:
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        return self._physical_queries.create_physical_link_request()

    def _candidate_search_request(self, base, *, supports_ground_station_limits=False):
        return self._physical_queries._candidate_search_request(base,
            supports_ground_station_limits=supports_ground_station_limits,
            retained_ground_station_pairs=self._ground_lifecycle_by_pair,
            retained_inter_satellite_pairs=self._lifecycle_by_pair)

    def _expanded_candidate_request(self, request, observation):
        state = None
        if (request.ground_station_candidate_limit is not None
                and observation.ground_station_search_complete is False
                and callable(getattr(self._ground_station_model, "expand_candidate_search", None))):
            state = self._selection_state(observation.timestamp, self._ground_lifecycle_by_pair,
                OperationalConnectionKind.GROUND_STATION_ACCESS, self._configuration.ground_station_acquisition_duration)
        return self._physical_queries._expanded_candidate_request(request, observation, state)

    def get_route_demands(self):
        """Return the demands supplied at the latest completed formal step."""
        return self._last_route_demands

    def set_route_demands(self, demands: Iterable[RouteDemand]) -> None:
        if self._finished_end is not None or self._shutdown:
            raise RuntimeError("cannot change Route demands after completion")
        values = _validated_route_demands(demands)
        if values != self._route_demands:
            self._route_demands = values
            self._route_demands_dirty = True

    @property
    def required_prehistory(self) -> timedelta:
        """Return the bounded warm-start duration needed by the producer."""

        return self._required_prehistory

    def prime(
        self,
        physical_frame: PhysicalPositionSnapshot,
        observation: PhysicalLinkQueryResult,
        *,
        formal_start: datetime,
    ) -> None:
        """Advance one ordered bounded prehistory frame without a transition."""

        if self._formal_started or self._finished_end is not None:
            raise RuntimeError("prehistory must precede formal observation")
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        if not isinstance(physical_frame, PhysicalPositionSnapshot):
            raise TypeError("physical_frame must be a PhysicalPositionSnapshot")
        start = normalize_utc_datetime(formal_start, "formal_start")
        if (
            self._expected_formal_start is not None
            and start != self._expected_formal_start
        ):
            raise ValueError("formal_start must remain identical across prime calls")
        if physical_frame.timestamp >= start:
            raise ValueError("prehistory timestamp must precede formal_start")
        if start - physical_frame.timestamp > self._required_prehistory:
            raise ValueError("prehistory exceeds the bounded initialization window")
        if (
            self._physical_observation_frontier is not None
            and physical_frame.timestamp
            != self._physical_observation_frontier + self._observation_interval
        ):
            raise ValueError(
                "prehistory frames must follow observation_interval"
            )
        snapshot = self._snapshot_private_state()
        try:
            self._expected_formal_start = start
            self._advance(
                physical_frame,
                observation,
                publish=False,
                phase="bootstrap",
            )
        except BaseException:
            self._restore_private_state(snapshot)
            raise
        return None

    def observe(
        self,
        physical_frame: PhysicalPositionSnapshot,
        observation: PhysicalLinkQueryResult,
    ) -> tuple[Plan, ...]:
        """Plan one formal frame and return every newly publishable Plan."""

        if self._finished_end is not None:
            raise RuntimeError("Planner is already finished")
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        if not isinstance(physical_frame, PhysicalPositionSnapshot):
            raise TypeError("physical_frame must be a PhysicalPositionSnapshot")
        snapshot = self._snapshot_private_state()
        try:
            if not self._formal_started:
                if self._expected_formal_start is None:
                    self._expected_formal_start = physical_frame.timestamp
                elif physical_frame.timestamp != self._expected_formal_start:
                    raise ValueError(
                        "first formal frame must equal the declared formal_start"
                    )
                if (
                    self._physical_observation_frontier is not None
                    and physical_frame.timestamp
                    != self._physical_observation_frontier
                    + self._observation_interval
                ):
                    raise ValueError(
                        "first formal frame must follow prehistory by "
                        "observation_interval"
                    )
            elif (
                self._planning_frontier is None
                or physical_frame.timestamp
                != self._planning_frontier + self._observation_interval
            ):
                raise ValueError(
                    "formal frames must follow observation_interval"
                )
            result = self._advance(
                physical_frame, observation, publish=True, phase="steady"
            )
            assert isinstance(result, _FramePlanningResult)
            self._formal_started = True
            self._last_transition = result.transition
            self._last_inspection = result.inspection
            self._unpublished.append(result)
            return self._publish_ready()
        except BaseException:
            self._restore_private_state(snapshot)
            raise

    def flush(self) -> tuple[Plan, ...]:
        """Publish the observed prefix without ending acquisition or retention.

        A delivered coverage seam may cut an internal publication batch. No
        observation, contact ending, state reset or synthetic event is added.
        """
        if self._shutdown or self._finished_end is not None:
            raise RuntimeError("Planner is closed for publication")
        snapshot = self._snapshot_private_state()
        try:
            plans = []
            while self._unpublished:
                plans.append(self._publish_one_prefix(phase="steady"))
            return tuple(plans)
        except BaseException:
            self._restore_private_state(snapshot)
            raise

    def finish(self, *, end_timestamp: datetime) -> tuple[Plan, ...]:
        """Verify Timeline completion and flush every remaining prefix once."""

        end = normalize_utc_datetime(end_timestamp, "end_timestamp")
        if self._shutdown:
            raise RuntimeError("Planner is shut down")
        if self._finished_end is not None:
            if end != self._finished_end:
                raise ValueError("finished Planner has a different end timestamp")
            return ()
        if not self._formal_started or self._planning_frontier is None:
            raise RuntimeError("Planner has no formal observations to finish")
        if self._planning_frontier != end:
            raise ValueError("end_timestamp must equal the planning frontier")
        snapshot = self._snapshot_private_state()
        try:
            plans: list[Plan] = []
            while self._unpublished:
                plans.append(self._publish_one_prefix(phase="one-shot"))
            if self._publication_frontier != end:
                raise RuntimeError("publication did not reach Timeline end")
            self._finished_end = end
            return tuple(plans)
        except BaseException:
            self._restore_private_state(snapshot)
            raise

    def get_state(self) -> PlannedOperationalState:
        if self._current_state is None:
            raise RuntimeError("Planner has not observed a formal frame")
        return self._current_state

    def get_last_transition(self) -> PlanTransition:
        if self._last_transition is None:
            raise RuntimeError("Planner has not observed a formal transition")
        return self._last_transition

    def get_state_fingerprint(self) -> str:
        return planned_operational_state_fingerprint(self.get_state())

    def get_last_transition_fingerprint(self) -> str:
        return plan_transition_fingerprint(self.get_last_transition())

    def get_frontiers(self) -> PlanningFrontiers:
        return PlanningFrontiers(
            physical_observation=self._physical_observation_frontier,
            planning=self._planning_frontier,
            publication=self._publication_frontier,
        )

    def get_route_recomputation_count(self) -> int:
        return self._route_recomputation_count

    def get_published_evidence(
        self, plan_id: str
    ) -> PublishedPlanningEvidence:
        for evidence in self._published_evidence:
            if evidence.plan.plan_id == plan_id:
                return evidence
        raise KeyError(f"published Plan evidence not retained: {plan_id}")

    def release_published_through(self, plan_id: str) -> dict[str, int]:
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError("plan_id must be a non-empty string")
        for index, evidence in enumerate(self._published_evidence):
            if evidence.plan.plan_id == plan_id:
                released = self._published_evidence[: index + 1]
                del self._published_evidence[: index + 1]
                return {
                    "plans": len(released),
                    "transitions": sum(
                        len(value.plan.transitions) for value in released
                    ),
                    "inspections": sum(
                        len(value.inspections) for value in released
                    ),
                }
        raise KeyError(f"published Plan evidence not retained: {plan_id}")

    def shutdown(self, *, wait: bool = True) -> None:
        if not isinstance(wait, bool):
            raise TypeError("wait must be a bool")
        if self._shutdown:
            return
        self._shutdown = True
        _shutdown_owned((self._inter_satellite_model, self._ground_station_model,
            self._zone_service_model, self._parameter_model, self._route_model), wait=wait)

    def _publish_ready(self) -> tuple[Plan, ...]:
        plans: list[Plan] = []
        while len(self._unpublished) >= self._configuration.frames_per_batch:
            plans.append(self._publish_one_prefix(phase="steady"))
        return tuple(plans)

    def _publish_one_prefix(self, *, phase: str) -> Plan:
        publication_started = perf_counter()
        if not self._unpublished:
            raise RuntimeError("no unpublished transition is available")
        if len(self._published_evidence) >= self._maximum_retained_evidence:
            raise RuntimeError(
                "published Planner evidence must be released before progression"
            )
        count = min(len(self._unpublished), self._configuration.frames_per_batch)
        prefix = tuple(self._unpublished[:count])
        transitions = tuple(value.transition for value in prefix)
        plan_started = perf_counter()
        plan, published_state = _create_plan_with_output_state(
            self._last_published_state,
            previous_plan_id=self._last_published_plan_id,
            physical_world_fingerprint=self._physical_world_fingerprint,
            observation_interval=self._observation_interval,
            start_timestamp=transitions[0].timestamp,
            transitions=transitions,
            _constructed_state=self._current_state,
        )
        plan_scope = {
            "plan_id": plan.plan_id,
            "previous_plan_id": plan.previous_plan_id,
            "start_timestamp": plan.start_timestamp.isoformat(),
            "end_timestamp": plan.end_timestamp.isoformat(),
        }
        self._record(
            "plan-creation",
            phase,
            plan_started,
            plan_scope,
            {
                "plans": 1,
                "transitions": len(plan.transitions),
                "semantic_precedences": sum(len(t.semantic_event_precedences) for t in plan.transitions),
            },
        )
        reuse_started = perf_counter()
        self._record(
            "publication-output-state-reuse",
            phase,
            reuse_started,
            plan_scope,
            {"replayed_transitions_avoided": len(plan.transitions)},
        )
        self._last_published_state = published_state
        self._last_published_plan_id = plan.plan_id
        self._publication_frontier = plan.end_timestamp
        del self._unpublished[:count]
        frontiers = self.get_frontiers()
        evidence = PublishedPlanningEvidence(
            plan=plan,
            transition_fingerprints=tuple(
                plan_transition_fingerprint(value) for value in transitions
            ),
            frames=tuple(value.evidence for value in prefix),
            frontiers=frontiers,
        )
        self._published_evidence.append(evidence)
        self._record(
            "publication-total",
            phase,
            publication_started,
            plan_scope,
            {
                "published": 1,
                "transitions": len(plan.transitions),
                "semantic_precedences": sum(len(t.semantic_event_precedences) for t in plan.transitions),
            },
            duration_role="rollup",
        )
        return plan

    def continuation(self):
        """Framework inputs at a drained publication boundary, not Model memory.

        Parameter memory has the existing immutable-value contract and is retained
        by reference. User Model instances and their private state are never copied.
        """
        if self._unpublished or self._published_evidence or self._finished_end is not None:
            raise RuntimeError('Planner continuation requires a drained, unfinished boundary')
        return (self._snapshot_private_state(), self._route_demands)

    def restore_continuation(self, continuation):
        if self._shutdown:
            raise RuntimeError('Planner is shut down')
        snapshot, self._route_demands = continuation
        # Fresh containers make this checkpoint reusable; accepted immutable values
        # and opaque parameter memory remain unchanged.
        snapshot = (*snapshot[:-2], list(snapshot[-2]), list(snapshot[-1]))
        self._restore_private_state(snapshot)
        self._route_demands_dirty = True

    def _snapshot_private_state(self) -> tuple:
        return (
            self._lifecycle_by_pair,
            self._ground_lifecycle_by_pair,
            self._parameter_memory,
            self._route_demands_dirty,
            self._physical_observation_frontier,
            self._planning_frontier,
            self._publication_frontier,
            self._expected_formal_start,
            self._formal_started,
            self._finished_end,
            self._current_state,
            self._last_published_state,
            self._last_published_plan_id,
            self._last_transition,
            self._last_inspection,
            self._last_route_demands,
            self._route_recomputation_count,
            list(self._unpublished),
            list(self._published_evidence),
        )

    def _restore_private_state(self, snapshot: tuple) -> None:
        (
            lifecycle,
            self._ground_lifecycle_by_pair,
            self._parameter_memory,
            self._route_demands_dirty,
            self._physical_observation_frontier,
            self._planning_frontier,
            self._publication_frontier,
            self._expected_formal_start,
            self._formal_started,
            self._finished_end,
            self._current_state,
            self._last_published_state,
            self._last_published_plan_id,
            self._last_transition,
            self._last_inspection,
            self._last_route_demands,
            self._route_recomputation_count,
            unpublished,
            published_evidence,
        ) = snapshot
        self._lifecycle_by_pair = lifecycle
        self._unpublished = unpublished
        self._published_evidence = published_evidence

    def _advance(
        self,
        physical_frame: PhysicalPositionSnapshot,
        observation: PhysicalLinkQueryResult,
        *,
        publish: bool,
        phase: str,
    ) -> _FramePlanningResult | None:
        scope: dict[str, object] = {}
        total_started = perf_counter()
        stage_started = perf_counter()
        timestamp = _validate_physical_inputs(
            self._physical_world,
            self._physical_observation_frontier,
            physical_frame,
            observation,
        )
        scope["timestamp"] = timestamp.isoformat()
        self._physical_observation_frontier = timestamp
        self._record(
            "physical-input-validation",
            phase,
            stage_started,
            scope,
            {
                "feasible_isl_candidates": len(
                    observation.inter_satellite_candidates
                ),
                "feasible_ground_station_candidates": len(
                    observation.ground_station_access_candidates
                ),
                "feasible_zone_candidates": len(
                    observation.zone_service_candidates
                ),
                **({"physical_candidate_pairs_evaluated": (
                    observation.work_counts.inter_satellite_pairs_evaluated
                    + observation.work_counts.ground_station_pairs_evaluated
                    + observation.work_counts.zone_service_pairs_evaluated
                )} if observation.work_counts is not None else {}),
            },
        )

        stage_started = perf_counter()
        observed_isl = {_candidate_pair(c): c for c in observation.inter_satellite_candidates}
        state = self._selection_state(timestamp, self._lifecycle_by_pair,
            OperationalConnectionKind.INTER_SATELLITE, self._configuration.inter_satellite_acquisition_duration)
        selection = self._inter_satellite_model.select_inter_satellite_links(
            self._physical_world, observation.inter_satellite_candidates, state)
        selected_candidates = _validate_inter_satellite_selection(
            self._physical_world, selection, authoritative=observed_isl)
        underfilled, decision_histogram = (_selection_diagnostics(self._inter_satellite_model)
            if publish or self._execution_observer is not None else (None, None))
        feasible_pairs = observed_isl.keys()
        selected_pairs = {
            _candidate_pair(value) for value in selected_candidates
        }
        if self._execution_observer is not None:
            self._record(
                "isl-candidate-selection",
                phase,
                stage_started,
                scope,
                {
                    **_model_statistics(self._inter_satellite_model, "selection_statistics"),
                    "feasible_candidates": len(feasible_pairs),
                    "selected_candidates": len(selected_pairs),
                    "rejected_candidates": len(feasible_pairs) - len(selected_pairs),
                    **({"underfilled_slots": underfilled} if underfilled is not None else {}),
                },
            )

        stage_started = perf_counter()
        (
            lifecycle,
            acquisition_started,
            acquisition_completed,
            acquisition_reset,
        ) = self._advance_isl_lifecycle(
            timestamp, selected_candidates
        )
        self._lifecycle_by_pair = lifecycle
        operational_isl_candidates = tuple(
            value.candidate
            for value in lifecycle.values()
            if value.operational
        )
        acquiring_count = sum(
            not value.operational for value in lifecycle.values()
        )
        self._record(
            "acquisition/lifecycle-advancement",
            phase,
            stage_started,
            scope,
            {
                "selected_isls": len(lifecycle),
                "acquiring_isls": acquiring_count,
                "operational_isls": len(operational_isl_candidates),
                "acquisition_started": acquisition_started,
                "acquisition_completed": acquisition_completed,
                "acquisition_reset": acquisition_reset,
            },
            resources={"lifecycle_entries": len(lifecycle)},
        )

        ground_station_started = perf_counter()
        stage_started = ground_station_started
        ground_state = self._selection_state(timestamp, self._ground_lifecycle_by_pair,
            OperationalConnectionKind.GROUND_STATION_ACCESS, self._configuration.ground_station_acquisition_duration)
        proposed_ground = self._ground_station_model.select_ground_station_accesses(
            self._physical_world, observation.ground_station_access_candidates, ground_state)
        if self._execution_observer is not None:
            self._record(
                "ground-station-selection-and-ranking",
                phase,
                stage_started,
                scope,
                {
                    **_model_statistics(self._ground_station_model, "allocation_statistics"),
                    "feasible_candidates": len(
                        observation.ground_station_access_candidates
                    ),
                    "proposed_accesses": (
                        len(proposed_ground)
                        if isinstance(proposed_ground, tuple)
                        else 0
                    ),
                },
            )

        stage_started = perf_counter()
        selected_ground, ground_cardinality = _validate_selected_ground_candidates(
            self._physical_world,
            proposed_ground,
            observation.ground_station_access_candidates,
        )
        self._record(
            "ground-station-validation",
            phase,
            stage_started,
            scope,
            ground_cardinality,
        )
        previous_ground = self._ground_lifecycle_by_pair
        duration = self._configuration.ground_station_acquisition_duration
        next_ground = {}
        for candidate in selected_ground:
            key = (candidate.ground_station_id, candidate.satellite_id)
            prior = previous_ground.get(key)
            since = timestamp if prior is None else prior.selected_since
            # Selected preparations consume the same endpoint capacities as
            # operational links. Reassignment releases before acquiring anew.
            next_ground[key] = _ConnectionPreparation(
                candidate, since, timestamp-since >= duration)
        self._ground_lifecycle_by_pair = next_ground
        selected_ground = tuple(v.candidate for v in next_ground.values() if v.operational)
        self._record(
            "ground-station-planning",
            phase,
            ground_station_started,
            scope,
            {
                "feasible_candidates": len(
                    observation.ground_station_access_candidates
                ),
                "operational_accesses": len(selected_ground),
            },
            duration_role="rollup",
        )

        if not publish:
            self._record(
                "planning-total",
                phase,
                total_started,
                scope,
                {
                    "formal_transitions": 0,
                    "prehistory_frames": 1,
                    "selected_isls": len(lifecycle),
                    "operational_isls": len(operational_isl_candidates),
                },
                duration_role="rollup",
            )
            return None

        stage_started = perf_counter()
        previous_state = self._current_state
        previous_zone_assignments = (
            ()
            if previous_state is None
            else previous_state.zone_service_assignments
        )
        zone_result = self._zone_service_model.assign_zone_service(
            self._physical_world,
            observation.zone_service_candidates,
            previous_zone_assignments,
            timestamp=timestamp,
        )
        zone_changes = _validate_zone_result(
            self._physical_world,
            observation.zone_service_candidates,
            previous_zone_assignments,
            zone_result,
        )
        zone_assignments = zone_result.assignments
        served_count = sum(
            value.satellite_id is not None for value in zone_assignments
        )
        self._record(
            "zone-planning",
            phase,
            stage_started,
            scope,
            {
                "feasible_candidates": len(
                    observation.zone_service_candidates
                ),
                "zones_served": served_count,
                "zones_unserved": len(zone_assignments) - served_count,
            },
        )

        stage_started = perf_counter()
        parameter_observations = _parameter_observations(
            operational_isl_candidates, selected_ground
        )
        parameter_result = self._parameter_model.plan_effective_parameters(
            timestamp, parameter_observations, self._parameter_memory
        )
        established, withdrawn, updated = _validate_parameter_result(
            parameter_observations,
            previous_state,
            parameter_result,
        )
        parameter_updates = len(updated)
        self._record(
            "parameter-planning",
            phase,
            stage_started,
            scope,
            {
                "operational_connections": len(parameter_result.connections),
                "parameter_updates": parameter_updates,
            },
        )

        stage_started = perf_counter()
        active_route_demands = self._route_demands
        if active_route_demands is None:
            demand_factory = getattr(self._route_model, "create_route_demands", None)
            active_route_demands = (_validated_route_demands(demand_factory(
                self._physical_world, zone_assignments, timestamp=timestamp))
                if callable(demand_factory) else ())
        reuse = getattr(self._route_model, "can_reuse_unchanged_inputs", None)
        reusable = reuse() if callable(reuse) else False
        if not isinstance(reusable, bool):
            raise TypeError("can_reuse_unchanged_inputs must return bool")
        route_relevant = bool(not reusable or established or withdrawn or zone_changes
                              or self._route_demands_dirty or active_route_demands != self._last_route_demands)
        if previous_state is not None and not route_relevant and updated:
            depends = getattr(self._route_model, "depends_on_connection_parameters", None)
            route_relevant = True if depends is None else depends()
            if not isinstance(route_relevant, bool):
                raise TypeError("depends_on_connection_parameters must return bool")
        if previous_state is None or route_relevant:
            route_result = self._route_model.plan_route_outcomes(
                self._physical_world,
                parameter_result.connections,
                zone_assignments,
                active_route_demands,
                ()
                if previous_state is None
                else previous_state.route_outcomes,
                timestamp=timestamp,
            )
            previous_route_outcomes = (
                () if previous_state is None else previous_state.route_outcomes
            )
            validated_outcomes = _validate_route_result(
                active_route_demands,
                route_result,
            )
            route_outcomes = route_result.outcomes
            _validate_route_outcomes(
                self._physical_world,
                parameter_result.connections,
                zone_assignments,
                active_route_demands,
                route_outcomes,
                by_demand=validated_outcomes,
            )
            self._route_recomputation_count += 1
            route_recomputed = True
            self._route_demands_dirty = False
            previous_by_demand = {value.demand: value for value in previous_route_outcomes}
            route_changes = tuple(
                RouteOutcomeTransition(demand, previous_by_demand.get(demand), validated_outcomes.get(demand))
                for demand in previous_by_demand.keys() | validated_outcomes.keys()
                if previous_by_demand.get(demand) != validated_outcomes.get(demand)
            )
        else:
            route_outcomes = previous_state.route_outcomes
            route_recomputed = False
            route_changes = ()
        reachable_count = sum(
            isinstance(value, ReachableRouteOutcome)
            for value in route_outcomes
        )
        if self._execution_observer is not None:
            self._record(
                "route-planning",
                phase,
                stage_started,
                scope,
                {
                    **(_model_statistics(self._route_model, "route_work_statistics")
                       if route_recomputed else {}),
                    "route_demands": len(active_route_demands),
                    "route_recomputed": int(route_recomputed),
                    "reachable_routes": reachable_count,
                    "unreachable_routes": sum(isinstance(v, UnreachableRouteOutcome) for v in route_outcomes),
                },
                resources={
                    **_model_statistics(self._route_model, "route_resource_statistics"),
                    "route_outcome_entries": len(route_outcomes),
                    "route_recomputation_count": self._route_recomputation_count,
                },
            )

        authoritative_state = _updated_operational_state(
            previous_state, timestamp,
            parameter_result.connections if established or withdrawn or updated or previous_state is None else None,
            zone_assignments if zone_changes or previous_state is None else None,
            route_outcomes if route_changes or previous_state is None else None,
        )
        stage_started = perf_counter()
        transition = PlanTransition(
            timestamp=timestamp,
            established_connections=established,
            withdrawn_connection_ids=withdrawn,
            parameter_updated_connections=updated,
            zone_service_transitions=zone_changes,
            route_outcome_transitions=route_changes,
        )
        canonical_events = semantic_event_references(transition)
        precedences = transition.semantic_event_precedences
        self._record(
            "coordination",
            phase,
            stage_started,
            scope,
            {
                "semantic_events": len(canonical_events),
                "semantic_precedences": len(precedences),
            },
        )

        stage_started = perf_counter()
        self._current_state = authoritative_state
        self._parameter_memory = parameter_result.memory
        self._last_transition = transition
        self._last_route_demands = active_route_demands
        self._planning_frontier = timestamp
        self._record(
            "state-advancement",
            phase,
            stage_started,
            scope,
            {
                "operational_connections": len(
                    authoritative_state.operational_connections
                ),
                "zone_assignments": len(
                    authoritative_state.zone_service_assignments
                ),
                "route_outcomes": len(authoritative_state.route_outcomes),
            },
            resources={
                "selected_isl_entries": len(self._lifecycle_by_pair),
            },
        )

        rejected_count = len(feasible_pairs) - len(selected_pairs)
        inspection = PlanningInspection(
            timestamp=timestamp,
            feasible_isl_candidates=len(feasible_pairs),
            selected_isls=len(lifecycle),
            acquiring_isls=acquiring_count,
            operational_isls=len(operational_isl_candidates),
            rejected_isls=rejected_count,
            underfilled_isl_slots=underfilled,
            ground_station_accesses=len(selected_ground),
            zones_served=served_count,
            zones_unserved=len(zone_assignments) - served_count,
            routes_reachable=reachable_count,
            routes_unreachable=sum(isinstance(v, UnreachableRouteOutcome) for v in route_outcomes),
            route_recomputed=route_recomputed,
            acquisition_started=acquisition_started,
            acquisition_completed=acquisition_completed,
            acquisition_reset=acquisition_reset,
            stages=_STAGES,
        )
        lifecycle_evidence = tuple(
            InterSatelliteLifecycleEvidence(
                candidate=value.candidate,
                selected_since=value.selected_since,
                acquisition_duration=(
                    self._configuration.inter_satellite_acquisition_duration
                ),
                remaining_acquisition=(
                    timedelta(0)
                    if value.operational
                    else max(
                        timedelta(0),
                        self._configuration.inter_satellite_acquisition_duration
                        - (timestamp - value.selected_since),
                    )
                ),
                operational=value.operational,
            )
            for _, value in sorted(
                lifecycle.items(), key=lambda item: _pair_key(item[0])
            )
        )
        stage_started = perf_counter()
        ground_candidate_summary = _ground_station_candidate_summary(
            observation.ground_station_access_candidates
        )
        self._record(
            "ground-station-evidence-projection",
            phase,
            stage_started,
            scope,
            {
                "candidate_entries_projected": len(
                    observation.ground_station_access_candidates
                ),
                "histograms": len(ground_candidate_summary.histograms),
            },
        )
        zone_candidates_by_identity = {
            (value.zone_id, value.satellite_id): value
            for value in observation.zone_service_candidates
        }
        selected_zone = tuple(
            zone_candidates_by_identity[
                (assignment.zone_id, assignment.satellite_id)
            ]
            for assignment in zone_assignments
            if assignment.satellite_id is not None
        )
        evidence = PlanningFrameEvidence(
            timestamp=timestamp,
            inter_satellite_candidate_summary=(
                _inter_satellite_candidate_summary(
                    observation.inter_satellite_candidates,
                    decision_histogram,
                )
            ),
            ground_station_access_candidate_summary=ground_candidate_summary,
            zone_service_candidate_summary=(
                _zone_service_candidate_summary(
                    observation.zone_service_candidates
                )
            ),
            physical_link_work_counts=observation.work_counts,
            selected_inter_satellite_links=lifecycle_evidence,
            selected_ground_station_accesses=selected_ground,
            ground_station_preparations=tuple(
                GroundStationLifecycleEvidence(
                    candidate=value.candidate,
                    selected_since=value.selected_since,
                    acquisition_duration=duration,
                    remaining_acquisition=max(timedelta(0), duration-(timestamp-value.selected_since)),
                    operational=value.operational,
                ) for value in self._ground_lifecycle_by_pair.values()
            ),
            selected_zone_service_candidates=selected_zone,
            inspection=inspection,
        )
        result = _FramePlanningResult(transition, evidence)
        self._record(
            "planning-total",
            phase,
            total_started,
            scope,
            {
                "formal_transitions": 1,
                "prehistory_frames": 0,
                "operational_connections": len(
                    authoritative_state.operational_connections
                ),
                "route_outcomes": len(authoritative_state.route_outcomes),
                "semantic_events": len(canonical_events),
            },
            duration_role="rollup",
        )
        return result

    def _advance_isl_lifecycle(
        self,
        timestamp: datetime,
        selected: tuple[InterSatelliteLinkCandidate, ...],
    ) -> tuple[dict[_IslPair, _ConnectionPreparation], int, int, int]:
        previous = self._lifecycle_by_pair
        by_pair = {_candidate_pair(value): value for value in selected}
        reset = len(set(previous) - set(by_pair))
        started = 0
        completed = 0
        next_values: dict[_IslPair, _ConnectionPreparation] = {}
        acquisition_duration = self._configuration.inter_satellite_acquisition_duration
        for pair in sorted(by_pair, key=_pair_key):
            candidate = by_pair[pair]
            prior = previous.get(pair)
            if prior is None:
                started += 1
                operational = acquisition_duration.total_seconds() == 0.0
                if operational:
                    completed += 1
                value = _ConnectionPreparation(
                    candidate, timestamp, operational
                )
            else:
                operational = prior.operational or (
                    timestamp - prior.selected_since >= acquisition_duration
                )
                if operational and not prior.operational:
                    completed += 1
                value = _ConnectionPreparation(
                    candidate, prior.selected_since, operational
                )
            next_values[pair] = value
        return next_values, started, completed, reset

    def _record(
        self,
        stage: str,
        phase: str,
        started: float,
        scope: dict[str, object],
        work: dict[str, int | float],
        *,
        resources: dict[str, int | float] | None = None,
        duration_role: str = "stage",
    ) -> None:
        if self._execution_observer is None:
            return
        record_performance(self._execution_observer,
            component="planner",
            stage=stage,
            phase=phase,
            duration_seconds=perf_counter() - started,
            duration_role=duration_role,
            scope=scope,
            work=work,
            resources=resources,
            started_monotonic=started,
        )


def _model_statistics(model: object, capability: str) -> dict[str, int | float]:
    """Consume optional counters inside the auxiliary error boundary only.

    Callers gate this on recording (and route work on recomputation). Materialize
    numeric values here so malformed counters cannot poison the receiver and
    suppress valid Planner-owned or other Model measurements.
    """
    try:
        read = getattr(model, capability, None)
        if read is None:
            return {}
        raw = read()
        if not isinstance(raw, Mapping):
            raise TypeError("statistics must be a mapping")
        result = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not key:
                raise ValueError("statistics keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("statistics values must be numeric")
            number = int(value) if isinstance(value, int) else float(value)
            if not isfinite(number):
                raise ValueError("statistics values must be finite")
            result[str(key)] = number
        return result
    except Exception as error:
        # Same best-effort stderr convention as record_performance; cancellation
        # and other BaseExceptions still propagate, including from diagnostics.
        try:
            print(f"performance recording degraded: {capability}: "
                  f"{type(error).__name__}: {error}", file=sys.stderr)
        except Exception:
            pass
        return {}


def _selection_diagnostics(model):
    try:
        read = getattr(model, "selection_diagnostics", None)
        if read is None:
            return None, None
        raw = read()
        if not isinstance(raw, InterSatelliteSelectionDiagnostics):
            raise TypeError("selection_diagnostics must return InterSatelliteSelectionDiagnostics")
        underfilled = raw.underfilled_slots
        if underfilled is not None:
            if isinstance(underfilled, bool) or not isinstance(underfilled, int) or underfilled < 0:
                raise ValueError("underfilled_slots must be non-negative or None")
            underfilled = int(underfilled)
        return underfilled, _decision_histogram(raw.decisions)
    except Exception as error:
        try:
            print(f"planning diagnostics degraded: selection_diagnostics: "
                  f"{type(error).__name__}: {error}", file=sys.stderr)
        except Exception:
            pass
        return None, None


def _validate_physical_inputs(
    physical_world: PhysicalWorld,
    previous_timestamp: datetime | None,
    physical_frame: PhysicalPositionSnapshot,
    observation: PhysicalLinkQueryResult,
) -> datetime:
    if not isinstance(physical_frame, PhysicalPositionSnapshot):
        raise TypeError("physical_frame must be a PhysicalPositionSnapshot")
    if not isinstance(observation, PhysicalLinkQueryResult):
        raise TypeError("observation must be a PhysicalLinkQueryResult")
    if physical_frame.timestamp != observation.timestamp:
        raise ValueError(
            "PhysicalPositionSnapshot and PhysicalLinkQueryResult timestamps must match"
        )
    timestamp = physical_frame.timestamp
    if previous_timestamp is not None and timestamp <= previous_timestamp:
        raise ValueError("physical observations must be strictly increasing")
    if physical_frame._node_inventory is not physical_world._nodes_by_id:
        raise ValueError("PhysicalPositionSnapshot belongs to another PhysicalWorld")
    return timestamp


def _parameter_observations(
    operational_isls: tuple[InterSatelliteLinkCandidate, ...],
    ground_accesses: tuple[GroundStationAccessCandidate, ...],
) -> tuple[ConnectionParameterObservation, ...]:
    observations = []
    for candidate in operational_isls:
        observations.append(
            ConnectionParameterObservation(
                OperationalConnectionId(
                    OperationalConnectionKind.INTER_SATELLITE,
                    candidate.endpoint_a,
                    candidate.endpoint_b,
                ),
                candidate.propagation_delay_us,
                candidate,
            )
        )
    for candidate in ground_accesses:
        observations.append(
            ConnectionParameterObservation(
                OperationalConnectionId(
                    OperationalConnectionKind.GROUND_STATION_ACCESS,
                    candidate.ground_station_id,
                    candidate.satellite_id,
                ),
                candidate.propagation_delay_us,
                candidate,
            )
        )
    return tuple(
        sorted(
            observations,
            key=lambda value: _connection_key(value.connection_id),
        )
    )


def _validate_inter_satellite_selection(
    physical_world: PhysicalWorld,
    selection: object,
    *, authoritative,
) -> tuple[InterSatelliteLinkCandidate, ...]:
    if not isinstance(selection, InterSatelliteSelection):
        raise TypeError(
            "InterSatelliteConnectionPlanningModel must return "
            "InterSatelliteSelection"
        )
    selected = []
    for candidate in selection.selected_candidates:
        physical_world.get_satellite(candidate.endpoint_a)
        physical_world.get_satellite(candidate.endpoint_b)
        pair = _candidate_pair(candidate)
        observed_candidate = authoritative.get(pair)
        if observed_candidate is None:
            raise ValueError(
                "ISL model selected a relationship absent from physical facts"
            )
        selected.append(observed_candidate)
    values = tuple(selected)
    _validate_selection_capabilities(physical_world, values)
    return values


def _validate_selection_capabilities(
    physical_world: PhysicalWorld,
    selected: tuple[InterSatelliteLinkCandidate, ...],
) -> None:
    degree: dict[NodeId, int] = {}
    for candidate in selected:
        for endpoint in _candidate_pair(candidate):
            degree[endpoint] = degree.get(endpoint, 0) + 1
    for node_id, value in degree.items():
        satellite = physical_world.get_satellite(node_id)
        capability = satellite.inter_satellite_link_capability
        if capability is None:
            raise ValueError(
                "Network Planner requires explicit ISL capability for "
                f"{node_id.value}"
            )
        if value > capability.maximum_simultaneous_links:
            raise ValueError("ISL model exceeded PhysicalWorld capability")


def _validate_selected_ground_candidates(
    physical_world: PhysicalWorld,
    selected: object,
    feasible: tuple[GroundStationAccessCandidate, ...],
) -> tuple[tuple[GroundStationAccessCandidate, ...], dict[str, int]]:
    if not isinstance(selected, tuple):
        raise TypeError("GS model must return a tuple of candidates")
    if any(
        not isinstance(value, GroundStationAccessCandidate)
        for value in selected
    ):
        raise TypeError("GS model returned an invalid candidate")
    authoritative = {}
    candidates_per_ground_station: Counter[NodeId] = Counter()
    candidates_per_satellite: Counter[NodeId] = Counter()
    for value in feasible:
        authoritative[(value.ground_station_id, value.satellite_id)] = value
        candidates_per_ground_station[value.ground_station_id] += 1
        candidates_per_satellite[value.satellite_id] += 1
    selected_pairs: set[tuple[NodeId, NodeId]] = set()
    canonical = []
    station_degree: dict[NodeId, int] = {}
    satellite_degree: dict[NodeId, int] = {}
    station_caps: dict[NodeId, int | None] = {}
    satellite_caps: dict[NodeId, int | None] = {}
    for candidate in selected:
        pair = (candidate.ground_station_id, candidate.satellite_id)
        if pair in selected_pairs:
            raise ValueError("GS model returned a duplicate candidate pair")
        selected_pairs.add(pair)
        observed_candidate = authoritative.get(pair)
        if observed_candidate is None:
            raise ValueError("GS model selected a physically infeasible candidate")
        if candidate.ground_station_id not in station_caps:
            station_caps[candidate.ground_station_id] = (
                physical_world.get_ground_station(
                    candidate.ground_station_id
                ).max_simultaneous_satellite_accesses
            )
        if candidate.satellite_id not in satellite_caps:
            satellite_caps[candidate.satellite_id] = (
                physical_world.get_satellite(
                    candidate.satellite_id
                ).max_simultaneous_ground_station_accesses
            )
        station_degree[candidate.ground_station_id] = (
            station_degree.get(candidate.ground_station_id, 0) + 1
        )
        satellite_degree[candidate.satellite_id] = (
            satellite_degree.get(candidate.satellite_id, 0) + 1
        )
        station_cap = station_caps[candidate.ground_station_id]
        satellite_cap = satellite_caps[candidate.satellite_id]
        if (
            station_cap is not None
            and station_degree[candidate.ground_station_id] > station_cap
        ):
            raise ValueError("GS model exceeded GroundStation physical capability")
        if (
            satellite_cap is not None
            and satellite_degree[candidate.satellite_id] > satellite_cap
        ):
            raise ValueError("GS model exceeded Satellite GS-access capability")
        canonical.append(observed_candidate)
    ground_counts = tuple(candidates_per_ground_station.values())
    satellite_counts = tuple(candidates_per_satellite.values())
    return tuple(canonical), {
        "candidate_entries_scanned": len(feasible),
        "candidate_ground_stations": len(ground_counts),
        "candidate_satellites": len(satellite_counts),
        "minimum_candidates_per_ground_station": min(ground_counts, default=0),
        "maximum_candidates_per_ground_station": max(ground_counts, default=0),
        "minimum_candidates_per_satellite": min(satellite_counts, default=0),
        "maximum_candidates_per_satellite": max(satellite_counts, default=0),
        "selected_entries_validated": len(canonical),
    }


def _validate_parameter_result(
    observations: tuple[ConnectionParameterObservation, ...],
    previous_state: PlannedOperationalState | None,
    result: object,
) -> tuple[tuple[OperationalConnection, ...], tuple[OperationalConnectionId, ...], tuple[OperationalConnection, ...]]:
    if not isinstance(result, ParameterPlanningResult):
        raise TypeError(
            "ParameterPlanningModel must return ParameterPlanningResult"
        )
    expected = {value.connection_id for value in observations}
    connections = _unique_by_connection_id(
        result.connections,
        OperationalConnection,
        "Parameter model connections",
    )
    if set(connections) != expected:
        raise ValueError("Parameter model must return each operational Connection exactly once")
    previous = {
        value.connection_id: value
        for value in (
            ()
            if previous_state is None
            else previous_state.operational_connections
        )
    }
    expected_established = {
        identifier: connections[identifier]
        for identifier in set(connections) - set(previous)
    }
    expected_withdrawn = set(previous) - set(connections)
    expected_updated = {
        identifier: connections[identifier]
        for identifier in set(connections) & set(previous)
        if connections[identifier] != previous[identifier]
    }
    return tuple(expected_established.values()), tuple(expected_withdrawn), tuple(expected_updated.values())


def _validate_zone_result(
    physical_world: PhysicalWorld,
    candidates: tuple[ZoneServiceCandidate, ...],
    previous_assignments: tuple[ZoneServiceAssignment, ...],
    result: object,
) -> tuple[ZoneServiceTransition, ...]:
    if not isinstance(result, ZoneServicePlanningResult):
        raise TypeError(
            "ZoneServicePlanningModel must return ZoneServicePlanningResult"
        )
    try:
        zones = physical_world.get_zone_layout().zones
    except RuntimeError:
        zones = ()
    zone_ids = {value.zone_id for value in zones}
    assignments = _unique_by(
        result.assignments,
        ZoneServiceAssignment,
        lambda value: value.service_key,
        "Zone model assignments",
    )
    service_keys = {(scope, zone) for scope in ({v.network_scope for v in result.assignments} or {""}) for zone in zone_ids}
    if set(assignments) != service_keys:
        raise ValueError(
            "Zone model must return exactly one explicit assignment per Zone"
        )
    candidate_pairs = set()
    for candidate in candidates:
        if candidate.zone_id not in zone_ids:
            raise ValueError("Zone physical candidate is foreign to PhysicalWorld")
        physical_world.get_satellite(candidate.satellite_id)
        candidate_pairs.add((candidate.zone_id, candidate.satellite_id))
    for assignment in assignments.values():
        if assignment.satellite_id is not None:
            physical_world.get_satellite(assignment.satellite_id)
            if (assignment.zone_id, assignment.satellite_id) not in candidate_pairs:
                raise ValueError(
                    "Zone model selected a Satellite absent from physical candidates"
                )
    previous = {value.service_key: value.satellite_id for value in previous_assignments}
    expected_changes = {
        zone_id: ZoneServiceTransition(
            zone_id[1],
            previous.get(zone_id),
            assignments[zone_id].satellite_id,
            zone_id[0],
        )
        for zone_id in service_keys
        if zone_id not in previous
        or previous[zone_id] != assignments[zone_id].satellite_id
    }
    if set(previous) - service_keys:
        raise ValueError("Zone model cannot remove an existing service scope")
    return tuple(expected_changes.values())


def _validate_route_result(
    demands: tuple[RouteDemand, ...],
    result: object,
) -> dict:
    if not isinstance(result, RoutePlanningResult):
        raise TypeError(
            "RoutePlanningModel must return RoutePlanningResult"
        )
    outcomes = _unique_by(
        result.outcomes,
        (ReachableRouteOutcome, UnreachableRouteOutcome, LocalRouteOutcome),
        lambda value: value.demand,
        "Route model outcomes",
    )
    requested = frozenset(demands)
    if any(not isinstance(v, LocalRouteOutcome) and d not in requested for d, v in outcomes.items()):
        raise ValueError("complete-path outcomes must correspond to requested demands")
    return outcomes


def _validate_route_outcomes(
    physical_world: PhysicalWorld,
    connections: tuple[OperationalConnection, ...],
    zone_assignments: tuple[ZoneServiceAssignment, ...],
    demands: tuple[RouteDemand, ...],
    outcomes,
    *, by_demand=None,
) -> None:
    if by_demand is None:
        values = tuple(outcomes)
        if any(
            not isinstance(value, (ReachableRouteOutcome, UnreachableRouteOutcome, LocalRouteOutcome))
            for value in values
        ):
            raise TypeError("Route model returned an invalid outcome")
        by_demand = {value.demand: value for value in values}
        if len(by_demand) != len(values):
            raise ValueError("duplicate Route outcome")
    assignments = {value.service_key: value.satellite_id for value in zone_assignments}
    edges = {
        pair for value in connections
        for pair in ((value.connection_id.endpoint_a, value.connection_id.endpoint_b),
                     (value.connection_id.endpoint_b, value.connection_id.endpoint_a))
    }
    operational = {c.connection_id for c in connections}
    for demand in dict.fromkeys((*demands, *by_demand)):
        outcome = by_demand.get(demand)
        physical_world.get_node(demand.source)
        ground_target = isinstance(demand.target, GroundStationRouteDemandTarget)
        if ground_target:
            terminal = demand.target.ground_station_id
            physical_world.get_ground_station(terminal)
            unserved = False
        elif isinstance(demand.target, SatelliteRouteDemandTarget):
            terminal = demand.target.satellite_id
            physical_world.get_satellite(terminal)
            unserved = False
        else:
            if not isinstance(demand.target, ZoneRouteDemandTarget):
                raise TypeError("Route demand target is invalid")
            if demand.target.service_key not in assignments:
                raise ValueError("Route demand references a foreign Zone")
            terminal = assignments[demand.target.service_key]
            unserved = terminal is None
        if outcome is None:
            continue
        if isinstance(outcome, LocalRouteOutcome):
            references = [c.exit for c in outcome.candidates]
            references.extend(v for record in (outcome.data, *(c.data for c in outcome.candidates))
                              for v in _policy_references(record))
            for value in references:
                if isinstance(value, LogicalExit):
                    if value.source != demand.source or value.connection not in operational:
                        raise ValueError("local arrangement references a foreign or non-operational exit")
                else:
                    physical_world.get_node(value)
        elif isinstance(outcome, ReachableRouteOutcome):
            if unserved:
                raise ValueError("an unserved Zone cannot have a reachable Route")
            assert terminal is not None
            if outcome.path[-1] != terminal:
                raise ValueError("reachable Route terminates at the wrong target")
            for current, next_hop in zip(
                outcome.path, outcome.path[1:], strict=False
            ):
                if (current, next_hop) not in edges:
                    raise ValueError(
                        "reachable Route uses a non-operational Connection"
                    )
                if not _route_step_allows_transit(
                    source=demand.source,
                    current=current,
                    next_hop=next_hop,
                    terminal=terminal,
                    ground_station_target=ground_target,
                ):
                    raise ValueError(
                        "reachable Route violates GroundStation transit restrictions"
                    )
        else:
            if unserved:
                if outcome.reason is not RouteUnreachableReason.ZONE_UNSERVED:
                    raise ValueError("unserved Zone requires ZONE_UNSERVED reason")
                continue
            if outcome.reason is not RouteUnreachableReason.NO_ELIGIBLE_PATH:
                raise ValueError("reachable target requires NO_ELIGIBLE_PATH reason")


def _unique_by_connection_id(values, expected_type, field_name: str) -> dict:
    return _unique_by(
        values,
        expected_type,
        lambda value: value.connection_id,
        field_name,
    )


def _unique_by(values, expected_type, key, field_name: str) -> dict:
    try:
        copied = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in copied):
        raise TypeError(f"{field_name} contains an invalid value")
    result = {}
    for value in copied:
        identity = key(value)
        if identity in result:
            raise ValueError(f"{field_name} contains a duplicate identity")
        result[identity] = value
    return result


def _validated_route_demands(
    demands: Iterable[RouteDemand],
) -> tuple[RouteDemand, ...]:
    values = tuple(demands)
    if any(not isinstance(value, RouteDemand) for value in values):
        raise TypeError("route_demands must contain RouteDemand values")
    ordered = tuple(sorted(values, key=_route_demand_key))
    if len(set(ordered)) != len(ordered):
        raise ValueError("route_demands contains a duplicate")
    return ordered


def _inter_satellite_candidate_summary(
    candidates: tuple[InterSatelliteLinkCandidate, ...],
    decision_histogram: PlanningCandidateHistogram | None,
) -> PlanningCandidateSummary:
    return PlanningCandidateSummary(
        feasible_count=len(candidates),
        histograms=(
            _categorical_histogram(
                "relationship",
                tuple(
                    "same-plane" if value.same_plane else "cross-plane"
                    for value in candidates if value.same_plane is not None
                ),
            ),
            _categorical_histogram(
                "cyclic-plane-separation",
                tuple(
                    str(value.cyclic_plane_separation)
                    if value.cyclic_plane_separation < 4
                    else "4+"
                    for value in candidates if value.cyclic_plane_separation is not None
                ),
            ),
            _numeric_histogram(
                "distance-m",
                tuple(value.distance_m for value in candidates if value.distance_m is not None),
                (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000),
            ),
            _numeric_histogram(
                "propagation-delay-us",
                tuple(value.propagation_delay_us for value in candidates if value.propagation_delay_us is not None),
                (1_000, 5_000, 10_000, 25_000, 50_000),
            ),
        ) + ((decision_histogram,) if decision_histogram is not None else ()) ,
    )


def _ground_station_candidate_summary(
    candidates: tuple[GroundStationAccessCandidate, ...],
) -> PlanningCandidateSummary:
    return PlanningCandidateSummary(
        feasible_count=len(candidates),
        histograms=_access_quality_histograms(candidates),
    )


def _zone_service_candidate_summary(
    candidates: tuple[ZoneServiceCandidate, ...],
) -> PlanningCandidateSummary:
    return PlanningCandidateSummary(
        feasible_count=len(candidates),
        histograms=_access_quality_histograms(candidates),
    )


def _access_quality_histograms(candidates) -> tuple[PlanningCandidateHistogram, ...]:
    specifications = (
        (
            "distance-m",
            "distance_m",
            (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000),
        ),
        ("elevation-deg", "elevation_deg", (0, 10, 30, 60, 80)),
        (
            "propagation-delay-us",
            "propagation_delay_us",
            (1_000, 5_000, 10_000, 25_000, 50_000),
        ),
    )
    counts = [Counter() for _ in specifications]
    sample_count = 0
    for candidate in candidates:
        sample_count += 1
        for index, (_name, attribute, thresholds) in enumerate(specifications):
            measure = getattr(candidate, attribute)
            if measure is not None:
                counts[index][_numeric_histogram_label(measure, thresholds)] += 1
    return tuple(
        PlanningCandidateHistogram(
            name=name,
            sample_count=sum(counts[index].values()),
            bins=tuple(
                PlanningHistogramBin(label, count)
                for label, count in sorted(counts[index].items())
            ),
        )
        for index, (name, _attribute, _thresholds) in enumerate(specifications)
    )


def _numeric_histogram_label(
    value: int | float, thresholds: tuple[int, ...]
) -> str:
    return next(
        (f"<{threshold}" for threshold in thresholds if value < threshold),
        f">={thresholds[-1]}",
    )


def _numeric_histogram(
    name: str, values: tuple[int | float, ...], thresholds: tuple[int, ...]
) -> PlanningCandidateHistogram:
    labels = tuple(_numeric_histogram_label(value, thresholds) for value in values)
    return _categorical_histogram(name, labels)


def _categorical_histogram(
    name: str, labels: tuple[str, ...]
) -> PlanningCandidateHistogram:
    counts = Counter(labels)
    return PlanningCandidateHistogram(
        name=name,
        sample_count=len(labels),
        bins=tuple(
            PlanningHistogramBin(label, count)
            for label, count in sorted(counts.items())
        ),
    )


def _decision_histogram(
    decisions: tuple[CandidateDecision, ...],
) -> PlanningCandidateHistogram:
    counts = Counter(
        _bounded_histogram_label(
            f"{value.decision.value}:{value.reason}"
        )
        for value in decisions
    )
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 32:
        kept = ranked[:31]
        kept.append(("__other_reasons__", sum(value for _, value in ranked[31:])))
        ranked = kept
    return PlanningCandidateHistogram(
        name="selection-decision-reason",
        sample_count=len(decisions),
        bins=tuple(
            PlanningHistogramBin(label, count) for label, count in ranked
        ),
    )


def _bounded_histogram_label(value: str) -> str:
    if len(value) <= 120:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[:96]}#{digest}"


def _require_methods(
    value: object, field_name: str, methods: tuple[str, ...]
) -> None:
    if any(not callable(getattr(value, method, None)) for method in methods):
        raise TypeError(f"{field_name} does not implement its planning role")


def _candidate_pair(
    candidate: InterSatelliteLinkCandidate,
) -> _IslPair:
    return candidate.endpoint_a, candidate.endpoint_b


def _canonical_pair(first: NodeId, second: NodeId) -> _IslPair:
    return (
        (first, second)
        if _node_key(first) < _node_key(second)
        else (second, first)
    )


def _pair_key(pair: _IslPair) -> tuple:
    return _node_key(pair[0]), _node_key(pair[1])


def _connection_key(identifier: OperationalConnectionId) -> tuple:
    return (
        identifier.kind.value,
        _node_key(identifier.endpoint_a),
        _node_key(identifier.endpoint_b),
    )


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
