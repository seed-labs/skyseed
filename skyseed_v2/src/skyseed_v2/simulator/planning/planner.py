"""Incrementally maintain and publish one semantic Planner snapshot."""

from __future__ import annotations

from collections import ChainMap, Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from time import perf_counter
from types import MappingProxyType

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.shared._durable_evidence import (
    _append_jsonl,
    _iter_jsonl,
    _reset_jsonl,
    _semantic_value,
)

from ..slicing import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DifferenceOperation,
    DirectionalConnectionParameters,
    DirectionalConnectionParametersPatch,
    RouteKey,
    Slicer,
    TopologyDifference,
)
from .algorithms import (
    AllAvailablePlanningWindowPolicy,
    ConnectionPlanningModel,
    CoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    ForwardingPlanningModel,
    ParameterPlanningModel,
    PlanningWindowPolicy,
    StableForwardingPlanningModel,
    _cached_path_nodes,
    _path_nodes,
    _planned_paths_by_slice,
    _planned_forwarding_precedences_from_paths,
    _resolve_planned_paths,
)
from .values import (
    ConnectionTransition,
    ConnectionTransitionOperation,
    ForwardingTransition,
    ForwardingTransitionPrecedence,
    ParameterTransition,
    Plan,
    PlanningDecision,
)
from ._difference_time import _DifferenceTimeResolver
from .._history_codec import (
    _difference_data,
    _difference_from_data,
    _planning_decision_data,
    _planning_decision_from_data,
)


_ParameterKey = tuple[ConnectionId, ConnectionDirection]
_ParameterFieldKey = tuple[ConnectionId, ConnectionDirection, str]
_PARAMETER_FIELDS = (
    "bandwidth_bps",
    "delay_us",
    "packet_loss_ppm",
)


class Planner:
    """Incrementally publish Simulator-owned semantic Plan intervals.

    Layer: Simulator-owned planning component. Simulator creates at most one,
    binds it to the exact sibling Slicer and callers request synchronization
    or the latest immutable publication. Private planned state continues across
    Plan boundaries. It is not a Slicer, route selector, backend
    compiler, runtime orchestrator or resource owner and is public from
    ``skyseed_v2.simulator``.
    """

    __slots__ = (
        "_slicer",
        "_planning_window_policy",
        "_connection_model",
        "_parameter_model",
        "_forwarding_model",
        "_coordination_policy",
        "_processed_through_slice_id",
        "_published_through_slice_id",
        "_differences",
        "_planning_differences",
        "_connection_transitions",
        "_parameter_transitions",
        "_forwarding_transitions",
        "_forwarding_precedences",
        "_connection_translation_faithful",
        "_forwarding_translation_faithful",
        "_forwarding_model_prefix_reusable",
        "_observed_parameters",
        "_planned_parameters",
        "_pending_parameter_ranges",
        "_parameter_drift_started_at",
        "_parameter_persistence_started_at",
        "_published_observed_parameters",
        "_published_planned_parameters",
        "_published_pending_parameter_ranges",
        "_published_parameter_drift_started_at",
        "_published_parameter_persistence_started_at",
        "_route_next_hops",
        "_route_path_connection_ids",
        "_connection_values_by_slice_id",
        "_active_establishments",
        "_published_active_establishments",
        "_planned_route_next_hops",
        "_planned_route_path_connection_ids",
        "_published_route_next_hops",
        "_published_route_path_connection_ids",
        "_formal_route_candidate_started_at",
        "_planned_route_divergence_started_at",
        "_published_formal_route_candidate_started_at",
        "_published_planned_route_divergence_started_at",
        "_decisions",
        "_plan",
        "_publication_count",
        "_durable_history_path",
        "_execution_observer",
    )

    def __init__(self) -> None:
        raise TypeError("Planner is created explicitly by Simulator")

    @classmethod
    def _create(
        cls,
        *,
        slicer: Slicer,
        planning_window_policy: PlanningWindowPolicy | None,
        connection_model: ConnectionPlanningModel,
        parameter_model: ParameterPlanningModel,
        forwarding_model: ForwardingPlanningModel,
        coordination_policy: CoordinationPolicy,
        execution_observer: _ExecutionObserver | None = None,
    ) -> "Planner":
        if not isinstance(slicer, Slicer):
            raise TypeError("slicer must be a Slicer")
        if planning_window_policy is None:
            planning_window_policy = AllAvailablePlanningWindowPolicy()
        if not isinstance(planning_window_policy, PlanningWindowPolicy):
            raise TypeError(
                "planning_window_policy must implement PlanningWindowPolicy"
            )
        if not isinstance(connection_model, ConnectionPlanningModel):
            raise TypeError(
                "connection_model must implement ConnectionPlanningModel"
            )
        if not isinstance(parameter_model, ParameterPlanningModel):
            raise TypeError(
                "parameter_model must implement ParameterPlanningModel"
            )
        if not isinstance(forwarding_model, ForwardingPlanningModel):
            raise TypeError(
                "forwarding_model must implement ForwardingPlanningModel"
            )
        if not isinstance(coordination_policy, CoordinationPolicy):
            raise TypeError(
                "coordination_policy must implement CoordinationPolicy"
            )

        instance = object.__new__(cls)
        instance._slicer = slicer
        instance._planning_window_policy = planning_window_policy
        instance._connection_model = connection_model
        instance._parameter_model = parameter_model
        instance._forwarding_model = forwarding_model
        instance._coordination_policy = coordination_policy
        instance._processed_through_slice_id = 0
        instance._published_through_slice_id = 0
        instance._differences: list[TopologyDifference] = []
        instance._planning_differences: tuple[TopologyDifference, ...] = ()
        instance._connection_transitions: tuple[ConnectionTransition, ...] = ()
        instance._parameter_transitions: tuple[ParameterTransition, ...] = ()
        instance._forwarding_transitions: tuple[ForwardingTransition, ...] = ()
        instance._forwarding_precedences: tuple[
            ForwardingTransitionPrecedence, ...
        ] = ()
        instance._connection_translation_faithful = True
        instance._forwarding_translation_faithful = True
        instance._forwarding_model_prefix_reusable = True
        instance._observed_parameters: dict[
            _ParameterKey, DirectionalConnectionParameters
        ] = {}
        instance._planned_parameters: dict[
            _ParameterKey, DirectionalConnectionParameters
        ] = {}
        instance._pending_parameter_ranges: dict[
            _ParameterKey, tuple[int, int]
        ] = {}
        instance._parameter_drift_started_at: dict[
            _ParameterFieldKey, datetime
        ] = {}
        instance._parameter_persistence_started_at: dict[
            _ParameterFieldKey, datetime
        ] = {}
        instance._published_observed_parameters: dict[
            _ParameterKey, DirectionalConnectionParameters
        ] = {}
        instance._published_planned_parameters: dict[
            _ParameterKey, DirectionalConnectionParameters
        ] = {}
        instance._published_pending_parameter_ranges: dict[
            _ParameterKey, tuple[int, int]
        ] = {}
        instance._published_parameter_drift_started_at: dict[
            _ParameterFieldKey, datetime
        ] = {}
        instance._published_parameter_persistence_started_at: dict[
            _ParameterFieldKey, datetime
        ] = {}
        instance._route_next_hops: dict[
            int, Mapping[RouteKey, NodeId]
        ] = {}
        instance._route_path_connection_ids: dict[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ] = {}
        instance._connection_values_by_slice_id: dict[
            int, Mapping[ConnectionId, Connection]
        ] = {}
        instance._active_establishments: dict[
            ConnectionId, ConnectionTransition
        ] = {}
        instance._published_active_establishments: dict[
            ConnectionId, ConnectionTransition
        ] = {}
        instance._planned_route_next_hops: dict[RouteKey, NodeId] = {}
        instance._planned_route_path_connection_ids: dict[
            RouteKey, tuple[ConnectionId, ...]
        ] = {}
        instance._published_route_next_hops: dict[RouteKey, NodeId] = {}
        instance._published_route_path_connection_ids: dict[
            RouteKey, tuple[ConnectionId, ...]
        ] = {}
        instance._formal_route_candidate_started_at: dict[
            RouteKey, datetime
        ] = {}
        instance._planned_route_divergence_started_at: dict[
            RouteKey, datetime
        ] = {}
        instance._published_formal_route_candidate_started_at: dict[
            RouteKey, datetime
        ] = {}
        instance._published_planned_route_divergence_started_at: dict[
            RouteKey, datetime
        ] = {}
        instance._decisions: list[PlanningDecision] = []
        instance._plan: Plan | None = None
        instance._publication_count = 0
        instance._durable_history_path = None
        instance._execution_observer = execution_observer
        instance._synchronize_available()
        return instance

    def _set_execution_observer(self, observer: _ExecutionObserver) -> None:
        if not isinstance(observer, _ExecutionObserver):
            raise TypeError("observer must be an _ExecutionObserver")
        if self._execution_observer is not None:
            raise RuntimeError("Planner execution observation is already enabled")
        self._execution_observer = observer

    def synchronize(self) -> Plan:
        """Publish or return the latest continuous non-overlapping Plan."""

        self._synchronize_available()
        return self.get_plan()

    def get_plan(self) -> Plan:
        """Return the latest immutable Plan publication."""

        if self._plan is None:
            raise RuntimeError("Plan is unavailable before the first Difference")
        return self._plan

    def get_consumed_differences(self) -> tuple[TopologyDifference, ...]:
        """Return exact immutable Difference facts consumed successfully."""

        if self._durable_history_path is not None:
            return tuple(
                _difference_from_data(value["semantic"])
                for value in _iter_jsonl(self._durable_history_path)
                if value.get("record_type") == "difference_consumed"
            )
        return tuple(self._differences)

    def get_planning_decisions(self) -> tuple[PlanningDecision, ...]:
        """Return compact merge/suppression evidence outside Plan payload."""

        if self._durable_history_path is not None:
            return tuple(
                _planning_decision_from_data(value["semantic"])
                for value in _iter_jsonl(self._durable_history_path)
                if value.get("record_type") == "planning_decision"
            )
        return tuple(self._decisions)

    def _enable_durable_history(self, path: Path) -> None:
        """Enable private append-only history before Planner progression."""

        if self._processed_through_slice_id != 0 or self._durable_history_path is not None:
            raise RuntimeError(
                "Planner durable history must be enabled before progression"
            )
        self._durable_history_path = _reset_jsonl(
            path,
            {
                "record_type": "history_header",
                "format_version": 1,
                "owner": "Planner",
            },
        )

    def _release_compiled_through(self, slice_id: int) -> dict[str, int]:
        """Release published working facts after successful compilation."""

        if (
            isinstance(slice_id, bool)
            or not isinstance(slice_id, int)
            or slice_id < 0
        ):
            raise ValueError("slice_id must be non-negative")
        if slice_id > self._published_through_slice_id:
            raise ValueError("compiled frontier exceeds published Planner coverage")
        if any(
            value.current_slice_id <= slice_id
            for value in self._planning_differences
        ):
            raise RuntimeError(
                "compiled retention frontier overlaps unpublished planning work"
            )
        self._differences = [
            value
            for value in self._differences
            if value.current_slice_id > slice_id
        ]
        for key in tuple(self._route_next_hops):
            if key <= slice_id:
                self._route_next_hops.pop(key, None)
        for key in tuple(self._route_path_connection_ids):
            if key <= slice_id:
                self._route_path_connection_ids.pop(key, None)
        for key in tuple(self._connection_values_by_slice_id):
            if key <= slice_id:
                self._connection_values_by_slice_id.pop(key, None)
        self._decisions = [
            value
            for value in self._decisions
            if any(source > slice_id for source in value.source_difference_ids)
        ]
        return {
            "retained_difference_count": len(self._differences),
            "retained_route_context_count": len(
                set(self._route_next_hops) | set(self._route_path_connection_ids)
                | set(self._connection_values_by_slice_id)
            ),
            "retained_decision_count": len(self._decisions),
        }

    def _synchronize_available(self) -> None:
        observer = self._execution_observer
        total_started = perf_counter() if observer is not None else 0.0
        phase = "bootstrap" if self._publication_count == 0 else "steady"
        stage_started = perf_counter() if observer is not None else 0.0
        latest_slice_id = self._slicer.get_latest_slice_id()
        new_differences = tuple(
            self._slicer.create_difference(slice_id - 1, slice_id)
            for slice_id in range(
                self._processed_through_slice_id + 1,
                latest_slice_id + 1,
            )
        )
        all_differences = (*self._differences, *new_differences)
        available = tuple(
            difference
            for difference in all_differences
            if difference.current_slice_id > self._published_through_slice_id
        )
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        if not available:
            return

        if observer is not None:
            _record_planner_observation(
                observer,
                "difference-acquisition",
                phase,
                stage_elapsed,
                {
                    "processed_through_slice_id": (
                        self._processed_through_slice_id
                    ),
                    "latest_slice_id": latest_slice_id,
                    "publication_index": self._publication_count,
                },
                {
                    "new_differences": len(new_differences),
                    "available_differences": len(available),
                    "input_difference_patches": _difference_patch_count(
                        available
                    ),
                },
            )

        stage_started = perf_counter() if observer is not None else 0.0
        planning_window = tuple(
            self._planning_window_policy.select_planning_differences(available)
        )
        _validate_planning_window(available, planning_window)
        if not _identity_prefix(
            self._planning_differences, planning_window
        ):
            raise ValueError(
                "PlanningWindowPolicy must preserve the already-planned "
                "planning window"
            )
        publication_window = tuple(
            self._planning_window_policy.select_publication_differences(
                planning_window
            )
        )
        _validate_publication_window(planning_window, publication_window)

        affected = planning_window[len(self._planning_differences) :]
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        scope: Mapping[str, object] = {}
        window_work: Mapping[str, int | float] = {}
        if observer is not None:
            scope = {
                "publication_index": self._publication_count,
                "planning_start_slice_id": planning_window[0].previous_slice_id,
                "planning_end_slice_id": planning_window[-1].current_slice_id,
                "publication_start_slice_id": (
                    publication_window[0].previous_slice_id
                ),
                "publication_end_slice_id": (
                    publication_window[-1].current_slice_id
                ),
                "start_timestamp": (
                    planning_window[0].previous_timestamp.isoformat()
                    if planning_window[0].previous_timestamp is not None
                    else None
                ),
                "end_timestamp": (
                    planning_window[-1].current_timestamp.isoformat()
                ),
            }
            window_work = {
                "available_differences": len(available),
                "planning_window_differences": len(planning_window),
                "publication_window_differences": len(publication_window),
                "affected_differences": len(affected),
                "input_difference_patches": _difference_patch_count(
                    planning_window
                ),
            }
            _record_planner_observation(
                observer,
                "window-selection",
                phase,
                stage_elapsed,
                scope,
                window_work,
            )
        previous_connections = self._connection_transitions
        previous_parameters = self._parameter_transitions
        previous_forwarding = self._forwarding_transitions
        observed_before = self._observed_parameters
        planned_before = self._planned_parameters
        drift_started_before = self._parameter_drift_started_at
        persistence_started_before = self._parameter_persistence_started_at
        active_before = self._active_establishments

        stage_started = perf_counter() if observer is not None else 0.0
        (
            new_route_next_hops,
            new_route_paths,
            new_connection_values,
        ) = _collect_route_context(
            self._slicer,
            planning_window,
            self._route_next_hops,
            self._route_path_connection_ids,
            self._connection_values_by_slice_id,
        )
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        if observer is not None:
            _record_planner_observation(
                observer,
                "route-context",
                phase,
                stage_elapsed,
                scope,
                {
                    "route_context_slices": len(
                        set(new_route_next_hops)
                        | set(new_route_paths)
                        | set(new_connection_values)
                    ),
                    "route_entries_processed": sum(
                        len(routes) for routes in new_route_next_hops.values()
                    ),
                    "path_edges_processed": sum(
                        len(path)
                        for routes in new_route_paths.values()
                        for path in routes.values()
                    ),
                },
            )
        route_next_hops = MappingProxyType(
            ChainMap(new_route_next_hops, self._route_next_hops)
        )
        route_paths = MappingProxyType(
            ChainMap(new_route_paths, self._route_path_connection_ids)
        )
        connection_values = MappingProxyType(
            ChainMap(
                new_connection_values,
                self._connection_values_by_slice_id,
            )
        )

        validation_seconds = 0.0
        if affected:
            stage_started = perf_counter() if observer is not None else 0.0
            model_connections = tuple(
                self._connection_model.plan_connection_transitions(
                    differences=planning_window,
                    affected_differences=affected,
                    previous_transitions=previous_connections,
                )
            )
            stage_elapsed = (
                perf_counter() - stage_started
                if observer is not None
                else 0.0
            )
            _record_planner_observation(
                observer,
                "connection-planning",
                phase,
                stage_elapsed,
                scope,
                {
                    "affected_differences": len(affected),
                    "previous_connection_intents": len(previous_connections),
                    "connection_intents": len(model_connections),
                },
            )
            connection_validation = _incremental_validation_region(
                previous_connections,
                model_connections,
                affected,
                planning_window,
            )
            validation_started = perf_counter() if observer is not None else 0.0
            _validate_connection_model_output(*connection_validation)
            connection_validation_faithful = (
                _is_faithful_connection_translation(*connection_validation)
            )
            validation_elapsed = (
                perf_counter() - validation_started
                if observer is not None
                else 0.0
            )
            validation_seconds += validation_elapsed
            _record_planner_observation(
                observer,
                "connection-output-validation",
                phase,
                validation_elapsed,
                scope,
                {
                    "validation_differences": len(connection_validation[0]),
                    "validation_transitions": len(connection_validation[1]),
                    "faithful_formal_translation": int(
                        connection_validation_faithful
                    ),
                },
            )

            stage_started = perf_counter() if observer is not None else 0.0
            model_parameters = tuple(
                self._parameter_model.plan_parameter_transitions(
                    differences=planning_window,
                    affected_differences=affected,
                    previous_transitions=previous_parameters,
                    observed_parameters=MappingProxyType(observed_before),
                    planned_parameters=MappingProxyType(planned_before),
                    parameter_drift_started_at=MappingProxyType(
                        drift_started_before
                    ),
                    parameter_persistence_started_at=MappingProxyType(
                        persistence_started_before
                    ),
                )
            )
            stage_elapsed = (
                perf_counter() - stage_started
                if observer is not None
                else 0.0
            )
            _record_planner_observation(
                observer,
                "parameter-planning",
                phase,
                stage_elapsed,
                scope,
                {
                    "affected_differences": len(affected),
                    "previous_parameter_intents": len(previous_parameters),
                    "parameter_intents": len(model_parameters),
                },
            )
            parameter_validation = _incremental_validation_region(
                previous_parameters,
                model_parameters,
                affected,
                planning_window,
            )
            validation_started = perf_counter() if observer is not None else 0.0
            _validate_parameter_model_output(
                *parameter_validation,
                connection_values_by_slice_id=connection_values,
            )
            validation_elapsed = (
                perf_counter() - validation_started
                if observer is not None
                else 0.0
            )
            validation_seconds += validation_elapsed
            _record_planner_observation(
                observer,
                "parameter-output-validation",
                phase,
                validation_elapsed,
                scope,
                {
                    "validation_differences": len(parameter_validation[0]),
                    "validation_transitions": len(parameter_validation[1]),
                },
            )

            stage_started = perf_counter() if observer is not None else 0.0
            stable_suffix_reuse = (
                type(self._forwarding_model) is StableForwardingPlanningModel
                and self._forwarding_model_prefix_reusable
                and _identity_prefix(previous_connections, model_connections)
            )
            forwarding_model_differences = (
                affected if stable_suffix_reuse else planning_window
            )
            forwarding_model_connections = (
                model_connections[len(previous_connections) :]
                if stable_suffix_reuse
                else model_connections
            )
            model_forwarding_suffix = tuple(
                self._forwarding_model.plan_forwarding_transitions(
                    differences=forwarding_model_differences,
                    affected_differences=(
                        forwarding_model_differences
                        if stable_suffix_reuse
                        else affected
                    ),
                    previous_transitions=(
                        () if stable_suffix_reuse else previous_forwarding
                    ),
                    connection_transitions=forwarding_model_connections,
                    route_next_hops=route_next_hops,
                    route_path_connection_ids=route_paths,
                    connection_values_by_slice_id=connection_values,
                    published_route_next_hops=MappingProxyType(
                        self._planned_route_next_hops
                        if stable_suffix_reuse
                        else self._published_route_next_hops
                    ),
                    published_route_path_connection_ids=MappingProxyType(
                        self._planned_route_path_connection_ids
                        if stable_suffix_reuse
                        else self._published_route_path_connection_ids
                    ),
                    formal_route_candidate_started_at=MappingProxyType(
                        self._formal_route_candidate_started_at
                        if stable_suffix_reuse
                        else self._published_formal_route_candidate_started_at
                    ),
                    planned_route_divergence_started_at=MappingProxyType(
                        self._planned_route_divergence_started_at
                        if stable_suffix_reuse
                        else self._published_planned_route_divergence_started_at
                    ),
                )
            )
            model_forwarding = (
                (*previous_forwarding, *model_forwarding_suffix)
                if stable_suffix_reuse
                else model_forwarding_suffix
            )
            stage_elapsed = (
                perf_counter() - stage_started
                if observer is not None
                else 0.0
            )
            _record_planner_observation(
                observer,
                "forwarding-planning",
                phase,
                stage_elapsed,
                scope,
                {
                    "affected_differences": len(affected),
                    "replayed_differences": len(
                        forwarding_model_differences
                    ),
                    "previous_forwarding_intents": len(previous_forwarding),
                    "reused_forwarding_intents": (
                        len(previous_forwarding) if stable_suffix_reuse else 0
                    ),
                    "forwarding_intents": len(model_forwarding),
                },
            )
            forwarding_model_reuse = _identity_prefix(
                previous_forwarding, model_forwarding
            )
            forwarding_validation_differences = (
                affected if forwarding_model_reuse else planning_window
            )
            forwarding_validation_transitions = (
                model_forwarding[len(previous_forwarding) :]
                if forwarding_model_reuse
                else model_forwarding
            )
            validation_started = perf_counter() if observer is not None else 0.0
            (
                validated_forwarding_paths,
                forwarding_validation_faithful,
                forwarding_validation_work,
            ) = _validate_forwarding_model_output(
                forwarding_validation_differences,
                forwarding_validation_transitions,
                route_next_hops=route_next_hops,
                route_path_connection_ids=route_paths,
                connection_values_by_slice_id=connection_values,
                published_route_next_hops=MappingProxyType(
                    self._planned_route_next_hops
                    if forwarding_model_reuse
                    else self._published_route_next_hops
                ),
                published_route_path_connection_ids=MappingProxyType(
                    self._planned_route_path_connection_ids
                    if forwarding_model_reuse
                    else self._published_route_path_connection_ids
                ),
            )
            validation_elapsed = (
                perf_counter() - validation_started
                if observer is not None
                else 0.0
            )
            validation_seconds += validation_elapsed
            _record_planner_observation(
                observer,
                "forwarding-output-validation",
                phase,
                validation_elapsed,
                scope,
                {
                    "validation_differences": len(
                        forwarding_validation_differences
                    ),
                    "validation_transitions": len(
                        forwarding_validation_transitions
                    ),
                    "reused_validated_transitions": (
                        len(previous_forwarding) if forwarding_model_reuse else 0
                    ),
                    **forwarding_validation_work,
                },
            )

            candidate_started = perf_counter() if observer is not None else 0.0
            connection_model_reuse = _identity_prefix(
                self._connection_transitions, model_connections
            )
            candidate_differences = (
                affected if connection_model_reuse else planning_window
            )
            candidate_transitions = (
                model_connections[len(self._connection_transitions) :]
                if connection_model_reuse
                else model_connections
            )
            candidate_active = (
                active_before
                if connection_model_reuse
                else self._published_active_establishments
            )
            candidates = _suppression_candidates(
                candidate_differences,
                candidate_transitions,
                candidate_active,
                self._connection_model,
            )
            candidate_elapsed = (
                perf_counter() - candidate_started
                if observer is not None
                else 0.0
            )
            _record_planner_observation(
                observer,
                "suppression-candidate-discovery",
                phase,
                candidate_elapsed,
                scope,
                {
                    "candidate_differences": len(candidate_differences),
                    "candidate_connection_transitions": len(
                        candidate_transitions
                    ),
                    "active_establishments_before": len(candidate_active),
                    "suppression_candidates": len(candidates),
                },
            )
            policy_started = perf_counter() if observer is not None else 0.0
            coordinated = self._coordination_policy.coordinate_transitions(
                differences=planning_window,
                suppression_candidates=candidates,
                connection_transitions=model_connections,
                parameter_transitions=model_parameters,
                forwarding_transitions=model_forwarding,
                route_next_hops=route_next_hops,
                route_path_connection_ids=route_paths,
                connection_values_by_slice_id=connection_values,
                published_route_next_hops=MappingProxyType(
                    self._published_route_next_hops
                ),
                published_route_path_connection_ids=MappingProxyType(
                    self._published_route_path_connection_ids
                ),
            )
            policy_elapsed = (
                perf_counter() - policy_started if observer is not None else 0.0
            )
            _record_planner_observation(
                observer,
                "coordination-policy",
                phase,
                policy_elapsed,
                scope,
                {
                    "suppression_candidates": len(candidates),
                    "input_connection_intents": len(model_connections),
                    "input_parameter_intents": len(model_parameters),
                    "input_forwarding_intents": len(model_forwarding),
                },
            )
            validation_started = perf_counter() if observer is not None else 0.0
            (
                connection_transitions,
                parameter_transitions,
                forwarding_transitions,
                coordination_decisions,
            ) = _validate_coordination_output(
                coordinated,
                model_connections,
                model_parameters,
                model_forwarding,
            )
            validation_elapsed = (
                perf_counter() - validation_started
                if observer is not None
                else 0.0
            )
            validation_seconds += validation_elapsed
            coordination_work = {
                "suppression_candidates": len(candidates),
                "input_connection_intents": len(model_connections),
                "input_parameter_intents": len(model_parameters),
                "input_forwarding_intents": len(model_forwarding),
                "coordinated_connection_intents": len(connection_transitions),
                "coordinated_parameter_intents": len(parameter_transitions),
                "coordinated_forwarding_intents": len(forwarding_transitions),
                "coordination_decisions": len(coordination_decisions),
            }
            _record_planner_observation(
                observer,
                "coordination-output-validation",
                phase,
                validation_elapsed,
                scope,
                coordination_work,
            )
            _record_planner_observation(
                observer,
                "coordination",
                phase,
                candidate_elapsed + policy_elapsed,
                scope,
                coordination_work,
                duration_role="rollup",
            )
            stage_started = perf_counter() if observer is not None else 0.0
            coordinated_matches_model = _same_identity_tuple(
                forwarding_transitions, model_forwarding
            )
            coordination_matches_models = all(
                (
                    _same_identity_tuple(
                        connection_transitions, model_connections
                    ),
                    _same_identity_tuple(parameter_transitions, model_parameters),
                    coordinated_matches_model,
                )
            )
            precedence_prefix_reuse = (
                coordinated_matches_model
                and forwarding_model_reuse
                and type(self._forwarding_model)
                in (
                    DependencyAwareForwardingPlanningModel,
                    StableForwardingPlanningModel,
                )
            )
            precedence_differences = (
                affected if precedence_prefix_reuse else planning_window
            )
            precedence_transitions = (
                forwarding_transitions[len(previous_forwarding) :]
                if precedence_prefix_reuse
                else forwarding_transitions
            )
            if (
                precedence_prefix_reuse
                and type(self._forwarding_model)
                is StableForwardingPlanningModel
            ):
                raw_new_forwarding_precedences = (
                    _planned_forwarding_precedences_from_paths(
                        precedence_differences,
                        precedence_transitions,
                        validated_forwarding_paths,
                    )
                )
            else:
                raw_new_forwarding_precedences = tuple(
                    self._forwarding_model.plan_forwarding_precedences(
                        differences=precedence_differences,
                        forwarding_transitions=precedence_transitions,
                        route_next_hops=route_next_hops,
                        route_path_connection_ids=route_paths,
                        connection_values_by_slice_id=connection_values,
                        published_route_next_hops=MappingProxyType(
                            self._planned_route_next_hops
                            if precedence_prefix_reuse
                            else self._published_route_next_hops
                        ),
                        published_route_path_connection_ids=MappingProxyType(
                            self._planned_route_path_connection_ids
                            if precedence_prefix_reuse
                            else self._published_route_path_connection_ids
                        ),
                    )
                )
            precedence_elapsed = (
                perf_counter() - stage_started if observer is not None else 0.0
            )
            validation_started = perf_counter() if observer is not None else 0.0
            new_forwarding_precedences = _validate_forwarding_precedence_output(
                precedence_transitions,
                raw_new_forwarding_precedences,
            )
            forwarding_precedences = (
                (*self._forwarding_precedences, *new_forwarding_precedences)
                if precedence_prefix_reuse
                else new_forwarding_precedences
            )
            precedence_validation_elapsed = (
                perf_counter() - validation_started
                if observer is not None
                else 0.0
            )
            _record_planner_observation(
                observer,
                "forwarding-precedence-output-validation",
                phase,
                precedence_validation_elapsed,
                scope,
                {
                    "validation_transitions": len(precedence_transitions),
                    "validation_precedences": len(
                        new_forwarding_precedences
                    ),
                    "reused_validated_precedences": (
                        len(self._forwarding_precedences)
                        if precedence_prefix_reuse
                        else 0
                    ),
                },
            )
            if observer is not None:
                validation_seconds += precedence_validation_elapsed
                precedence_bucket_sizes = tuple(
                    Counter(
                        (
                            transition.nominal_time,
                            transition.route_key.target,
                        )
                        for transition in precedence_transitions
                    ).values()
                )
                observer.record(
                    component="planner",
                    stage="forwarding-precedence",
                    phase=phase,
                    duration_seconds=precedence_elapsed,
                    scope=scope,
                    work={
                        "forwarding_intents": len(forwarding_transitions),
                        "precedence_input_intents": len(
                            precedence_transitions
                        ),
                        "reused_precedences": (
                            len(self._forwarding_precedences)
                            if precedence_prefix_reuse
                            else 0
                        ),
                        "precedence_buckets": len(precedence_bucket_sizes),
                        "precedence_indexed_transitions": len(
                            precedence_transitions
                        ),
                        "precedence_avoided_candidate_pairs": sum(
                            size * (size - 1)
                            for size in precedence_bucket_sizes
                        ),
                        "emitted_precedences": len(forwarding_precedences),
                        "new_precedences": len(new_forwarding_precedences),
                    },
                )
        else:
            connection_transitions = previous_connections
            parameter_transitions = previous_parameters
            forwarding_transitions = previous_forwarding
            forwarding_precedences = self._forwarding_precedences
            coordination_decisions = ()
            coordinated_matches_model = True
            coordination_matches_models = True
            validated_forwarding_paths = None
            connection_validation_faithful = True
            forwarding_validation_faithful = True

        stage_started = perf_counter() if observer is not None else 0.0
        state_prefix_reuse = all(
            (
                _identity_prefix(previous_connections, connection_transitions),
                _identity_prefix(previous_parameters, parameter_transitions),
                _identity_prefix(previous_forwarding, forwarding_transitions),
            )
        )
        faithful_translation_candidate = all(
            (
                self._connection_translation_faithful,
                self._forwarding_translation_faithful,
                connection_validation_faithful,
                forwarding_validation_faithful,
                coordination_matches_models,
            )
        )
        state_differences = affected if state_prefix_reuse else planning_window
        state_connections = (
            connection_transitions[len(previous_connections) :]
            if state_prefix_reuse
            else connection_transitions
        )
        state_parameters = (
            parameter_transitions[len(previous_parameters) :]
            if state_prefix_reuse
            else parameter_transitions
        )
        state_forwarding = (
            forwarding_transitions[len(previous_forwarding) :]
            if state_prefix_reuse
            else forwarding_transitions
        )
        if state_differences:
            candidate_active_establishments = _advance_active_establishments(
                active_before
                if state_prefix_reuse
                else self._published_active_establishments,
                state_connections,
            )
            (
                candidate_observed,
                candidate_planned,
                candidate_pending,
                candidate_parameter_drift_started,
                candidate_parameter_persistence_started,
                _,
            ) = _advance_parameter_state(
                state_differences,
                state_connections,
                state_parameters,
                observed_before
                if state_prefix_reuse
                else self._published_observed_parameters,
                planned_before
                if state_prefix_reuse
                else self._published_planned_parameters,
                self._pending_parameter_ranges
                if state_prefix_reuse
                else self._published_pending_parameter_ranges,
                drift_started_before
                if state_prefix_reuse
                else self._published_parameter_drift_started_at,
                persistence_started_before
                if state_prefix_reuse
                else self._published_parameter_persistence_started_at,
                self._parameter_model,
                type(self._parameter_model).__name__,
            )
            reusable_planned_paths = (
                validated_forwarding_paths
                if coordinated_matches_model
                and (
                    state_differences
                    == forwarding_validation_differences
                )
                and _same_identity_tuple(
                    state_forwarding,
                    forwarding_validation_transitions,
                )
                else None
            )
            (
                candidate_planned_routes,
                candidate_planned_paths,
                candidate_formal_route_candidate_started,
                candidate_planned_route_divergence_started,
            ) = _advance_forwarding_state(
                state_differences,
                state_forwarding,
                self._planned_route_next_hops
                if state_prefix_reuse
                else self._published_route_next_hops,
                self._planned_route_path_connection_ids
                if state_prefix_reuse
                else self._published_route_path_connection_ids,
                route_next_hops,
                route_paths,
                connection_values,
                self._formal_route_candidate_started_at
                if state_prefix_reuse
                else self._published_formal_route_candidate_started_at,
                self._planned_route_divergence_started_at
                if state_prefix_reuse
                else self._published_planned_route_divergence_started_at,
                planned_paths_by_slice=reusable_planned_paths,
            )
        else:
            candidate_active_establishments = active_before
            candidate_observed = observed_before
            candidate_planned = planned_before
            candidate_pending = self._pending_parameter_ranges
            candidate_parameter_drift_started = drift_started_before
            candidate_parameter_persistence_started = persistence_started_before
            candidate_planned_routes = self._planned_route_next_hops
            candidate_planned_paths = self._planned_route_path_connection_ids
            candidate_formal_route_candidate_started = (
                self._formal_route_candidate_started_at
            )
            candidate_planned_route_divergence_started = (
                self._planned_route_divergence_started_at
            )
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        _record_planner_observation(
            observer,
            "state-advancement",
            phase,
            stage_elapsed,
            scope,
            {
                "connection_intents": len(connection_transitions),
                "parameter_intents": len(parameter_transitions),
                "forwarding_intents": len(forwarding_transitions),
                "replayed_differences": len(state_differences),
                "replayed_connection_intents": len(state_connections),
                "replayed_parameter_intents": len(state_parameters),
                "replayed_forwarding_intents": len(state_forwarding),
                "active_establishments": len(candidate_active_establishments),
                "observed_parameter_entries": len(candidate_observed),
                "planned_parameter_entries": len(candidate_planned),
                "pending_parameter_ranges": len(candidate_pending),
                "planned_route_entries": len(candidate_planned_routes),
            },
        )
        validation_started = perf_counter() if observer is not None else 0.0
        if state_differences:
            faithful_formal_translation = all(
                (
                    state_prefix_reuse,
                    faithful_translation_candidate,
                )
            )
            fixed_validation_work = _validate_plan_region(
                state_differences,
                state_connections,
                state_parameters,
                state_forwarding,
                route_next_hops,
                route_paths,
                connection_values,
                active_before
                if state_prefix_reuse
                else self._published_active_establishments,
                self._planned_route_next_hops
                if state_prefix_reuse
                else self._published_route_next_hops,
                self._planned_route_path_connection_ids
                if state_prefix_reuse
                else self._published_route_path_connection_ids,
                faithful_formal_translation=faithful_formal_translation,
            )
        else:
            fixed_validation_work = {
                "validation_event_timestamps": 0,
                "forwarding_path_reconstructions": 0,
                "reused_forwarding_path_boundaries": 0,
                "validated_forwarding_dependency_ids": 0,
                "faithful_formal_translation": 1,
            }
        fixed_validation_elapsed = (
            perf_counter() - validation_started if observer is not None else 0.0
        )
        _record_planner_observation(
            observer,
            "fixed-plan-region-validation",
            phase,
            fixed_validation_elapsed,
            scope,
            {
                "validation_differences": len(state_differences),
                "validation_connection_transitions": len(state_connections),
                "validation_parameter_transitions": len(state_parameters),
                "validation_forwarding_transitions": len(state_forwarding),
                "reused_validated_differences": (
                    len(planning_window) - len(state_differences)
                    if state_prefix_reuse
                    else 0
                ),
                **fixed_validation_work,
            },
        )
        if observer is not None:
            validation_seconds += fixed_validation_elapsed
            observer.record(
                component="planner",
                stage="validation",
                phase=phase,
                duration_seconds=validation_seconds,
                duration_role="rollup",
                scope=scope,
                work={
                    "planning_window_differences": len(planning_window),
                    "connection_intents": len(connection_transitions),
                    "parameter_intents": len(parameter_transitions),
                    "forwarding_intents": len(forwarding_transitions),
                    "forwarding_precedences": len(forwarding_precedences),
                },
            )

        publication_started = perf_counter() if observer is not None else 0.0
        first = publication_window[0]
        last = publication_window[-1]
        publication_connections, retained_connections = _partition_transitions(
            connection_transitions, last.current_timestamp
        )
        publication_parameters, retained_parameters = _partition_transitions(
            parameter_transitions, last.current_timestamp
        )
        publication_forwarding, retained_forwarding = _partition_transitions(
            forwarding_transitions, last.current_timestamp
        )
        publication_precedences, retained_precedences = _partition_precedences(
            forwarding_precedences, last.current_timestamp
        )
        candidate_plan = Plan._from_validated(
            start_slice_id=first.previous_slice_id,
            start_timestamp=first.previous_timestamp,
            end_slice_id=last.current_slice_id,
            end_timestamp=last.current_timestamp,
            connection_transitions=publication_connections,
            parameter_transitions=publication_parameters,
            forwarding_transitions=publication_forwarding,
            forwarding_precedences=publication_precedences,
        )
        plan_construction_elapsed = (
            perf_counter() - publication_started
            if observer is not None
            else 0.0
        )

        published_state_started = perf_counter() if observer is not None else 0.0
        published_active = _advance_active_establishments(
            self._published_active_establishments,
            candidate_plan.connection_transitions,
        )
        (
            published_observed,
            published_planned,
            published_pending,
            published_parameter_drift_started,
            published_parameter_persistence_started,
            parameter_decisions,
        ) = _advance_parameter_state(
            publication_window,
            candidate_plan.connection_transitions,
            candidate_plan.parameter_transitions,
            self._published_observed_parameters,
            self._published_planned_parameters,
            self._published_pending_parameter_ranges,
            self._published_parameter_drift_started_at,
            self._published_parameter_persistence_started_at,
            self._parameter_model,
            type(self._parameter_model).__name__,
        )
        (
            published_routes,
            published_paths,
            published_formal_route_candidate_started,
            published_planned_route_divergence_started,
        ) = _advance_forwarding_state(
            publication_window,
            candidate_plan.forwarding_transitions,
            self._published_route_next_hops,
            self._published_route_path_connection_ids,
            route_next_hops,
            route_paths,
            connection_values,
            self._published_formal_route_candidate_started_at,
            self._published_planned_route_divergence_started_at,
            planned_paths_by_slice=(
                {
                    publication_window[0].previous_slice_id: route_paths.get(
                        publication_window[0].previous_slice_id, {}
                    ),
                    **{
                        difference.current_slice_id: route_paths.get(
                            difference.current_slice_id, {}
                        )
                        for difference in publication_window
                    },
                }
                if faithful_translation_candidate
                else None
            ),
        )
        retained_decision = _retained_connection_decision(
            publication_window,
            candidate_plan.connection_transitions,
            type(self._connection_model).__name__,
        )
        new_decisions = (
            *((retained_decision,) if retained_decision is not None else ()),
            *parameter_decisions,
            *coordination_decisions,
        )

        retained_planning = planning_window[len(publication_window) :]

        accepted_decisions = []
        for decision in new_decisions:
            if decision not in self._decisions and decision not in accepted_decisions:
                accepted_decisions.append(decision)
        accepted_new_decisions = tuple(accepted_decisions)
        published_state_elapsed = (
            perf_counter() - published_state_started
            if observer is not None
            else 0.0
        )
        evidence_started = perf_counter() if observer is not None else 0.0
        if self._durable_history_path is not None:
            for difference in new_differences:
                _append_jsonl(
                    self._durable_history_path,
                    {
                        "record_type": "difference_consumed",
                        "current_slice_id": difference.current_slice_id,
                        "semantic": _difference_data(difference),
                    },
                )
            _append_jsonl(
                self._durable_history_path,
                {
                    "record_type": "plan_published",
                    "publication_index": self._publication_count,
                    "start_slice_id": candidate_plan.start_slice_id,
                    "end_slice_id": candidate_plan.end_slice_id,
                    "semantic": _semantic_value(candidate_plan),
                },
            )
            for decision in accepted_new_decisions:
                _append_jsonl(
                    self._durable_history_path,
                    {
                        "record_type": "planning_decision",
                        "semantic": _planning_decision_data(decision),
                    },
                )
        evidence_elapsed = (
            perf_counter() - evidence_started if observer is not None else 0.0
        )

        commit_started = perf_counter() if observer is not None else 0.0
        self._route_next_hops.update(new_route_next_hops)
        self._route_path_connection_ids.update(new_route_paths)
        self._connection_values_by_slice_id.update(new_connection_values)
        self._differences.extend(new_differences)
        self._planning_differences = retained_planning
        self._connection_transitions = retained_connections
        self._parameter_transitions = retained_parameters
        self._forwarding_transitions = retained_forwarding
        self._forwarding_precedences = retained_precedences
        if affected:
            self._forwarding_model_prefix_reusable = (
                coordinated_matches_model
            )
            self._connection_translation_faithful = all(
                (
                    self._connection_translation_faithful,
                    connection_validation_faithful,
                    _same_identity_tuple(
                        connection_transitions, model_connections
                    ),
                )
            )
            self._forwarding_translation_faithful = all(
                (
                    self._forwarding_translation_faithful,
                    forwarding_validation_faithful,
                    coordinated_matches_model,
                )
            )
        self._observed_parameters = candidate_observed
        self._planned_parameters = candidate_planned
        self._pending_parameter_ranges = candidate_pending
        self._parameter_drift_started_at = candidate_parameter_drift_started
        self._parameter_persistence_started_at = (
            candidate_parameter_persistence_started
        )
        self._active_establishments = candidate_active_establishments
        self._planned_route_next_hops = candidate_planned_routes
        self._planned_route_path_connection_ids = candidate_planned_paths
        self._formal_route_candidate_started_at = (
            candidate_formal_route_candidate_started
        )
        self._planned_route_divergence_started_at = (
            candidate_planned_route_divergence_started
        )
        self._published_observed_parameters = published_observed
        self._published_planned_parameters = published_planned
        self._published_pending_parameter_ranges = published_pending
        self._published_parameter_drift_started_at = (
            published_parameter_drift_started
        )
        self._published_parameter_persistence_started_at = (
            published_parameter_persistence_started
        )
        self._published_active_establishments = published_active
        self._published_route_next_hops = published_routes
        self._published_route_path_connection_ids = published_paths
        self._published_formal_route_candidate_started_at = (
            published_formal_route_candidate_started
        )
        self._published_planned_route_divergence_started_at = (
            published_planned_route_divergence_started
        )
        self._decisions.extend(accepted_new_decisions)
        self._processed_through_slice_id = latest_slice_id
        self._published_through_slice_id = last.current_slice_id
        self._plan = candidate_plan
        self._publication_count += 1
        commit_elapsed = (
            perf_counter() - commit_started if observer is not None else 0.0
        )
        publication_elapsed = (
            perf_counter() - publication_started if observer is not None else 0.0
        )
        total_elapsed = (
            perf_counter() - total_started if observer is not None else 0.0
        )
        if observer is not None:
            publication_work = {
                "published_differences": len(publication_window),
                "published_connection_transitions": len(
                    publication_connections
                ),
                "published_parameter_transitions": len(
                    publication_parameters
                ),
                "published_forwarding_transitions": len(
                    publication_forwarding
                ),
                "published_forwarding_precedences": len(
                    publication_precedences
                ),
                "accepted_decisions": len(accepted_new_decisions),
                "retained_differences": len(retained_planning),
                "retained_connection_intents": len(retained_connections),
                "retained_parameter_intents": len(retained_parameters),
                "retained_forwarding_intents": len(retained_forwarding),
            }
            _record_planner_observation(
                observer,
                "publication-plan-construction",
                phase,
                plan_construction_elapsed,
                scope,
                {
                    "partitioned_connection_intents": len(
                        connection_transitions
                    ),
                    "partitioned_parameter_intents": len(parameter_transitions),
                    "partitioned_forwarding_intents": len(
                        forwarding_transitions
                    ),
                    "partitioned_precedences": len(forwarding_precedences),
                    "temporal_groups": len(candidate_plan.temporal_index),
                },
            )
            _record_planner_observation(
                observer,
                "publication-state-advancement",
                phase,
                published_state_elapsed,
                scope,
                {
                    "published_differences": len(publication_window),
                    "active_establishments": len(published_active),
                    "observed_parameter_entries": len(published_observed),
                    "planned_parameter_entries": len(published_planned),
                    "planned_route_entries": len(published_routes),
                },
            )
            _record_planner_observation(
                observer,
                "publication-evidence-persistence",
                phase,
                evidence_elapsed,
                scope,
                {
                    "durable_records_written": (
                        len(new_differences) + 1 + len(accepted_new_decisions)
                        if self._durable_history_path is not None
                        else 0
                    ),
                },
            )
            _record_planner_observation(
                observer,
                "publication-state-commit",
                phase,
                commit_elapsed,
                scope,
                {
                    "retained_differences": len(retained_planning),
                    "retained_connection_intents": len(retained_connections),
                    "retained_parameter_intents": len(retained_parameters),
                    "retained_forwarding_intents": len(retained_forwarding),
                },
            )
            _record_planner_observation(
                observer,
                "publication",
                phase,
                publication_elapsed,
                scope,
                publication_work,
                duration_role="rollup",
            )
            observer.record(
                component="planner",
                stage="total-synchronize",
                phase=phase,
                duration_seconds=total_elapsed,
                duration_role="rollup",
                scope=scope,
                work={**window_work, **publication_work},
            )


def _difference_patch_count(
    differences: tuple[TopologyDifference, ...],
) -> int:
    return sum(
        len(difference.added_connections)
        + len(difference.removed_connection_ids)
        + len(difference.connection_parameter_patches)
        + len(difference.zone_service_assignment_patches)
        + len(difference.route_patches)
        for difference in differences
    )


def _record_planner_observation(
    observer: _ExecutionObserver | None,
    stage: str,
    phase: str,
    duration_seconds: float,
    scope: Mapping[str, object],
    work: Mapping[str, int | float],
    *,
    duration_role: str = "stage",
) -> None:
    if observer is None:
        return
    observer.record(
        component="planner",
        stage=stage,
        phase=phase,
        duration_seconds=duration_seconds,
        duration_role=duration_role,
        scope=scope,
        work=work,
    )


def _validate_planning_window(
    available: tuple[TopologyDifference, ...],
    planning: tuple[TopologyDifference, ...],
) -> None:
    if not planning:
        raise ValueError(
            "PlanningWindowPolicy must select a non-empty planning window"
        )
    if any(not isinstance(value, TopologyDifference) for value in planning):
        raise TypeError(
            "PlanningWindowPolicy must return TopologyDifference values"
        )
    available_positions: dict[int, tuple[TopologyDifference, int]] = {}
    duplicate_identities = set()
    for index, candidate in enumerate(available):
        identity = id(candidate)
        if identity in available_positions:
            duplicate_identities.add(identity)
        else:
            available_positions[identity] = (candidate, index)

    positions = []
    for value in planning:
        identity = id(value)
        match = available_positions.get(identity)
        if (
            match is None
            or identity in duplicate_identities
            or match[0] is not value
        ):
            raise ValueError(
                "PlanningWindowPolicy must select exact available Differences"
            )
        positions.append(match[1])
    if positions != list(range(len(positions))):
        raise ValueError(
            "PlanningWindowPolicy must select an available Difference prefix"
        )


def _validate_publication_window(
    planning: tuple[TopologyDifference, ...],
    publication: tuple[TopologyDifference, ...],
) -> None:
    if not publication:
        raise ValueError(
            "PlanningWindowPolicy must select a non-empty publication window"
        )
    if not _identity_prefix(publication, planning):
        raise ValueError(
            "PlanningWindowPolicy publication must be an exact planning-window "
            "prefix"
        )


def _identity_prefix(previous: tuple, current: tuple) -> bool:
    return len(previous) <= len(current) and all(
        old is new for old, new in zip(previous, current)
    )


def _same_identity_tuple(first: tuple, second: tuple) -> bool:
    return len(first) == len(second) and _identity_prefix(first, second)


def _incremental_validation_region(
    previous: tuple,
    current: tuple,
    affected: tuple[TopologyDifference, ...],
    planning: tuple[TopologyDifference, ...],
) -> tuple[tuple[TopologyDifference, ...], tuple]:
    if _identity_prefix(previous, current):
        return affected, current[len(previous) :]
    return planning, current


def _collect_route_context(
    slicer: Slicer,
    differences: tuple[TopologyDifference, ...],
    retained_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    retained_paths: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
    retained_connection_values: Mapping[
        int, Mapping[ConnectionId, Connection]
    ],
) -> tuple[
    dict[int, Mapping[RouteKey, NodeId]],
    dict[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
    dict[int, Mapping[ConnectionId, Connection]],
]:
    slice_ids = {
        differences[0].previous_slice_id,
        *(difference.current_slice_id for difference in differences),
    }
    retained_slice_ids = (
        set(retained_next_hops)
        & set(retained_paths)
        & set(retained_connection_values)
    )
    new_next_hops: dict[int, Mapping[RouteKey, NodeId]] = {}
    new_paths: dict[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ] = {}
    new_connection_values: dict[
        int, Mapping[ConnectionId, Connection]
    ] = {}
    for slice_id in sorted(slice_ids - retained_slice_ids):
        next_hops, paths, connections = _slice_route_state(slicer, slice_id)
        new_next_hops[slice_id] = MappingProxyType(next_hops)
        new_paths[slice_id] = MappingProxyType(paths)
        new_connection_values[slice_id] = MappingProxyType(connections)
    return new_next_hops, new_paths, new_connection_values


def _slice_route_state(
    slicer: Slicer,
    slice_id: int,
) -> tuple[
    dict[RouteKey, NodeId],
    dict[RouteKey, tuple[ConnectionId, ...]],
    dict[ConnectionId, Connection],
]:
    topology_slice = slicer.get_slice(slice_id)
    connection_slice = topology_slice.connection_slice
    next_hops = {
        RouteKey(source, target): next_hop
        for source, targets in topology_slice.route_slice.routes.items()
        for target, next_hop in targets.items()
    }
    connections = {
        connection.connection_id: connection
        for connection in connection_slice.connections
    }
    # Slicer has already validated every immutable published RouteSlice against
    # this exact ConnectionSlice. Restore the same paths with shared suffixes
    # instead of asking Slicer to traverse every overlapping route separately.
    # The resolver still fails closed on loops and missing/ambiguous hops.
    paths = _resolve_planned_paths(next_hops, connections)
    return next_hops, paths, connections


def _path_connection_ids(
    connection_slice: ConnectionSlice,
    path: tuple[NodeId, ...],
) -> tuple[ConnectionId, ...]:
    if len(path) < 2:
        return ()
    result = []
    for first, second in zip(path, path[1:]):
        matches = []
        for kind in ConnectionKind:
            try:
                matches.append(
                    connection_slice.get_connection_for_endpoints(
                        kind, first, second
                    )
                )
            except KeyError:
                continue
        if len(matches) != 1:
            raise ValueError(
                "formal path edge must map to exactly one Connection"
            )
        result.append(matches[0].connection_id)
    return tuple(result)


def _validate_connection_model_output(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ConnectionTransition, ...],
    *,
    time_resolver: _DifferenceTimeResolver | None = None,
) -> None:
    if any(not isinstance(value, ConnectionTransition) for value in transitions):
        raise TypeError(
            "ConnectionPlanningModel must return ConnectionTransition values"
        )
    expected = Counter()
    for difference in differences:
        expected.update(
            (
                difference.current_slice_id,
                ConnectionTransitionOperation.ESTABLISH,
                connection.connection_id,
                connection,
            )
            for connection in difference.added_connections
        )
        expected.update(
            (
                difference.current_slice_id,
                ConnectionTransitionOperation.WITHDRAW,
                connection_id,
                None,
            )
            for connection_id in difference.removed_connection_ids
        )
    actual = Counter()
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    for transition in transitions:
        source = resolver.difference_for_time(transition.nominal_time)
        actual[
            (
                source.current_slice_id,
                transition.operation,
                transition.connection_id,
                transition.connection,
            )
        ] += 1
    if actual != expected:
        raise ValueError(
            "ConnectionPlanningModel must preserve exact planning-window "
            "Difference evolution"
        )


def _is_faithful_connection_translation(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ConnectionTransition, ...],
) -> bool:
    faithful_expected = tuple(
        expected
        for difference in differences
        for expected in (
            *(
                (
                    difference.current_timestamp,
                    ConnectionTransitionOperation.ESTABLISH,
                    connection.connection_id,
                    connection,
                )
                for connection in difference.added_connections
            ),
            *(
                (
                    difference.current_timestamp,
                    ConnectionTransitionOperation.WITHDRAW,
                    connection_id,
                    None,
                )
                for connection_id in difference.removed_connection_ids
            ),
        )
    )
    return len(transitions) == len(faithful_expected) and all(
        transition.nominal_time == expected_time
        and transition.operation is expected_operation
        and transition.connection_id == expected_connection_id
        and (
            transition.connection is expected_connection
            if expected_connection is not None
            else transition.connection is None
        )
        for transition, (
            expected_time,
            expected_operation,
            expected_connection_id,
            expected_connection,
        ) in zip(transitions, faithful_expected, strict=True)
    )


def _validate_parameter_model_output(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ParameterTransition, ...],
    *,
    connection_values_by_slice_id: Mapping[
        int, Mapping[ConnectionId, Connection]
    ],
    time_resolver: _DifferenceTimeResolver | None = None,
) -> None:
    if any(not isinstance(value, ParameterTransition) for value in transitions):
        raise TypeError(
            "ParameterPlanningModel must return ParameterTransition values"
        )
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    identities = set()
    for transition in transitions:
        source = resolver.difference_for_time(transition.nominal_time)
        identity = (
            source.current_slice_id,
            transition.connection_id,
            transition.direction,
        )
        if identity in identities:
            raise ValueError(
                "ParameterPlanningModel returned duplicate transition"
            )
        identities.add(identity)
        connection = connection_values_by_slice_id.get(
            source.current_slice_id, {}
        ).get(transition.connection_id)
        if connection is None:
            raise ValueError(
                "ParameterPlanningModel emitted outside formal Connection state"
            )
        formal = (
            connection.endpoint_a_to_b
            if transition.direction is ConnectionDirection.ENDPOINT_A_TO_B
            else connection.endpoint_b_to_a
        )
        for field_name in (
            "bandwidth_bps",
            "delay_us",
            "packet_loss_ppm",
        ):
            target = getattr(transition, field_name)
            if target is not None and target != getattr(formal, field_name):
                raise ValueError(
                    "ParameterPlanningModel invented a non-formal target"
                )


def _validate_forwarding_model_output(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    *,
    route_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    route_path_connection_ids: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
    connection_values_by_slice_id: Mapping[
        int, Mapping[ConnectionId, Connection]
    ],
    published_route_next_hops: Mapping[RouteKey, NodeId],
    published_route_path_connection_ids: Mapping[
        RouteKey, tuple[ConnectionId, ...]
    ],
    time_resolver: _DifferenceTimeResolver | None = None,
) -> tuple[
    Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
    bool,
    dict[str, int],
]:
    if any(not isinstance(value, ForwardingTransition) for value in transitions):
        raise TypeError(
            "ForwardingPlanningModel must return ForwardingTransition values"
        )
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    for transition in transitions:
        resolver.difference_for_time(transition.nominal_time)
    faithful_expected = []
    for difference in differences:
        previous_paths = route_path_connection_ids.get(
            difference.previous_slice_id, {}
        )
        current_paths = route_path_connection_ids.get(
            difference.current_slice_id, {}
        )
        for patch in difference.route_patches:
            if patch.operation is DifferenceOperation.REMOVED:
                phase = 1
                depth = -len(previous_paths.get(patch.route_key, ()))
            else:
                phase = 0
                depth = len(current_paths.get(patch.route_key, ()))
            faithful_expected.append(
                (
                    (
                        difference.current_timestamp,
                        phase,
                        depth,
                        repr(patch.route_key),
                    ),
                    difference,
                    patch,
                )
            )
    faithful_expected.sort(key=lambda item: item[0])
    faithful_translation = len(transitions) == len(faithful_expected) and all(
        transition.nominal_time == difference.current_timestamp
        and transition.route_key is patch.route_key
        and transition.operation is patch.operation
        and (
            transition.next_hop is patch.next_hop
            if patch.next_hop is not None
            else transition.next_hop is None
        )
        for transition, (_, difference, patch) in zip(
            transitions, faithful_expected, strict=True
        )
    )
    if faithful_translation:
        planned_by_slice = {
            differences[0].previous_slice_id: route_path_connection_ids.get(
                differences[0].previous_slice_id, {}
            ),
            **{
                difference.current_slice_id: route_path_connection_ids.get(
                    difference.current_slice_id, {}
                )
                for difference in differences
            },
        }
        return (
            planned_by_slice,
            True,
            {
                "faithful_formal_translation": 1,
                "formal_route_patches_compared": len(faithful_expected),
                "validated_route_states": 0,
                "validated_path_edges": 0,
            },
        )

    planned_by_slice = _planned_paths_by_slice(
        differences,
        transitions,
        route_next_hops,
        route_path_connection_ids,
        connection_values_by_slice_id,
        published_route_next_hops,
        published_route_path_connection_ids,
    )
    previous_planned = dict(published_route_path_connection_ids)
    validated_route_states = 0
    validated_path_edges = 0
    for difference in differences:
        slice_id = difference.current_slice_id
        planned = planned_by_slice[slice_id]
        formal = route_path_connection_ids.get(slice_id, {})
        if set(planned) != set(formal):
            raise ValueError(
                "ForwardingPlanningModel retained or omitted formal route demand"
            )
        connections = connection_values_by_slice_id.get(slice_id, {})
        path_nodes_cache: dict[
            tuple[NodeId, tuple[ConnectionId, ...]],
            tuple[NodeId, ...] | None,
        ] = {}
        for route_key, path in planned.items():
            validated_route_states += 1
            validated_path_edges += len(path)
            if path not in (
                tuple(formal[route_key]),
                tuple(previous_planned.get(route_key, ())),
            ):
                raise ValueError(
                    "ForwardingPlanningModel invented a hybrid or third path"
                )
            planned_nodes = _cached_path_nodes(
                path_nodes_cache, route_key.source, path, connections
            )
            formal_nodes = _cached_path_nodes(
                path_nodes_cache,
                route_key.source,
                tuple(formal[route_key]),
                connections,
            )
            if (
                planned_nodes is None
                or formal_nodes is None
                or planned_nodes[-1] != formal_nodes[-1]
            ):
                raise ValueError(
                    "ForwardingPlanningModel produced an unsupported or "
                    "terminal-incompatible path"
                )
        previous_planned = dict(planned)
    return (
        planned_by_slice,
        False,
        {
            "faithful_formal_translation": 0,
            "formal_route_patches_compared": len(faithful_expected),
            "validated_route_states": validated_route_states,
            "validated_path_edges": validated_path_edges,
        },
    )


def _validate_forwarding_precedence_output(
    transitions: tuple[ForwardingTransition, ...],
    precedences: tuple[ForwardingTransitionPrecedence, ...],
) -> tuple[ForwardingTransitionPrecedence, ...]:
    transition_positions = {
        id(value): position for position, value in enumerate(transitions)
    }
    seen = set()
    for precedence in precedences:
        if not isinstance(precedence, ForwardingTransitionPrecedence):
            raise TypeError(
                "ForwardingPlanningModel must return "
                "ForwardingTransitionPrecedence values"
            )
        key = (id(precedence.predecessor), id(precedence.successor))
        if (
            key[0] not in transition_positions
            or key[1] not in transition_positions
        ):
            raise ValueError(
                "Forwarding precedence must reference planned transitions"
            )
        if key in seen:
            raise ValueError("ForwardingPlanningModel duplicated precedence")
        seen.add(key)
    successors: dict[int, set[int]] = {}
    indegrees: dict[int, int] = {}
    for predecessor, successor in seen:
        successors.setdefault(predecessor, set()).add(successor)
        successors.setdefault(successor, set())
        indegrees.setdefault(predecessor, 0)
        indegrees[successor] = indegrees.get(successor, 0) + 1
    ready = [node for node, degree in indegrees.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for successor in successors.get(node, ()):
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                ready.append(successor)
    if visited != len(indegrees):
        raise ValueError("Forwarding transition precedence must be acyclic")
    keyed = [
        (
            (
                transition_positions[id(precedence.predecessor)],
                transition_positions[id(precedence.successor)],
            ),
            precedence,
        )
        for precedence in precedences
    ]
    if all(
        previous[0] <= current[0]
        for previous, current in zip(keyed, keyed[1:])
    ):
        ordered = keyed
    else:
        ordered = sorted(keyed, key=lambda item: item[0])
    return tuple(value for _, value in ordered)


def _partition_transitions(
    transitions: tuple, timestamp: datetime
) -> tuple[tuple, tuple]:
    if all(
        previous.nominal_time <= current.nominal_time
        for previous, current in zip(transitions, transitions[1:])
    ):
        low = 0
        high = len(transitions)
        while low < high:
            middle = (low + high) // 2
            if transitions[middle].nominal_time <= timestamp:
                low = middle + 1
            else:
                high = middle
        return transitions[:low], transitions[low:]
    through = []
    after = []
    for transition in transitions:
        (through if transition.nominal_time <= timestamp else after).append(
            transition
        )
    return tuple(through), tuple(after)


def _partition_precedences(
    precedences: tuple[ForwardingTransitionPrecedence, ...],
    timestamp: datetime,
) -> tuple[
    tuple[ForwardingTransitionPrecedence, ...],
    tuple[ForwardingTransitionPrecedence, ...],
]:
    through = []
    after = []
    for precedence in precedences:
        target = (
            through
            if precedence.predecessor.nominal_time <= timestamp
            else after
        )
        target.append(precedence)
    return tuple(through), tuple(after)


def _suppression_candidates(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ConnectionTransition, ...],
    active_before: Mapping[ConnectionId, ConnectionTransition],
    model: ConnectionPlanningModel,
    *,
    time_resolver: _DifferenceTimeResolver | None = None,
) -> tuple[tuple[ConnectionTransition, ConnectionTransition], ...]:
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    active = dict(active_before)
    result = []
    for transition in sorted(
        transitions,
        key=lambda value: (
            value.nominal_time,
            0
            if value.operation is ConnectionTransitionOperation.ESTABLISH
            else 1,
            repr(value.connection_id),
        ),
    ):
        if transition.operation is ConnectionTransitionOperation.ESTABLISH:
            active[transition.connection_id] = transition
            continue
        establish = active.pop(transition.connection_id, None)
        if establish is None:
            continue
        resolver.difference_for_time(transition.nominal_time)
        if model.is_suppression_candidate(establish, transition):
            result.append((establish, transition))
    return tuple(result)


def _advance_active_establishments(
    active_before: Mapping[ConnectionId, ConnectionTransition],
    transitions: tuple[ConnectionTransition, ...],
) -> dict[ConnectionId, ConnectionTransition]:
    active = dict(active_before)
    for transition in sorted(
        transitions,
        key=lambda value: (
            value.nominal_time,
            0
            if value.operation is ConnectionTransitionOperation.ESTABLISH
            else 1,
            repr(value.connection_id),
        ),
    ):
        if transition.operation is ConnectionTransitionOperation.ESTABLISH:
            active[transition.connection_id] = transition
        else:
            active.pop(transition.connection_id, None)
    return active


def _validate_coordination_output(
    output,
    model_connections: tuple[ConnectionTransition, ...],
    model_parameters: tuple[ParameterTransition, ...],
    model_forwarding: tuple[ForwardingTransition, ...],
) -> tuple[
    tuple[ConnectionTransition, ...],
    tuple[ParameterTransition, ...],
    tuple[ForwardingTransition, ...],
    tuple[PlanningDecision, ...],
]:
    if not isinstance(output, tuple) or len(output) != 4:
        raise TypeError(
            "CoordinationPolicy must return four coordinated tuples"
        )
    connections, parameters, forwarding, decisions = output
    if (
        connections is model_connections
        and parameters is model_parameters
        and forwarding is model_forwarding
        and isinstance(decisions, tuple)
        and all(isinstance(value, PlanningDecision) for value in decisions)
    ):
        return connections, parameters, forwarding, decisions
    for values, expected_type, field_name in (
        (connections, ConnectionTransition, "Connection"),
        (parameters, ParameterTransition, "Parameter"),
        (forwarding, ForwardingTransition, "Forwarding"),
        (decisions, PlanningDecision, "PlanningDecision"),
    ):
        if not isinstance(values, tuple) or any(
            not isinstance(value, expected_type) for value in values
        ):
            raise TypeError(
                f"CoordinationPolicy {field_name} output must be a typed tuple"
            )
    model_connection_ids = {id(candidate) for candidate in model_connections}
    if any(id(value) not in model_connection_ids for value in connections):
        raise ValueError("CoordinationPolicy invented a Connection transition")
    model_parameter_ids = {id(candidate) for candidate in model_parameters}
    if any(id(value) not in model_parameter_ids for value in parameters):
        raise ValueError("CoordinationPolicy invented a Parameter transition")
    if len({id(value) for value in connections}) != len(connections):
        raise ValueError("CoordinationPolicy duplicated a Connection transition")
    if len({id(value) for value in parameters}) != len(parameters):
        raise ValueError("CoordinationPolicy duplicated a Parameter transition")
    if len({id(value) for value in forwarding}) != len(forwarding):
        raise ValueError("CoordinationPolicy duplicated a Forwarding transition")
    return connections, parameters, forwarding, decisions


def _advance_parameter_state(
    differences: tuple[TopologyDifference, ...],
    connection_transitions: tuple[ConnectionTransition, ...],
    transitions: tuple[ParameterTransition, ...],
    observed_before: Mapping[_ParameterKey, DirectionalConnectionParameters],
    planned_before: Mapping[_ParameterKey, DirectionalConnectionParameters],
    pending_before: Mapping[_ParameterKey, tuple[int, int]],
    drift_started_before: Mapping[_ParameterFieldKey, datetime],
    persistence_started_before: Mapping[_ParameterFieldKey, datetime],
    parameter_model: ParameterPlanningModel,
    decided_by: str,
) -> tuple[
    dict[_ParameterKey, DirectionalConnectionParameters],
    dict[_ParameterKey, DirectionalConnectionParameters],
    dict[_ParameterKey, tuple[int, int]],
    dict[_ParameterFieldKey, datetime],
    dict[_ParameterFieldKey, datetime],
    tuple[PlanningDecision, ...],
]:
    observed = dict(observed_before)
    planned = dict(planned_before)
    pending = dict(pending_before)
    drift_started = dict(drift_started_before)
    persistence_started = dict(persistence_started_before)
    difference_ids = {difference.current_slice_id for difference in differences}
    resolver = _DifferenceTimeResolver(differences)
    establishments = {
        (
            resolver.difference_for_time(transition.nominal_time).current_slice_id,
            transition.connection_id,
        )
        for transition in connection_transitions
        if transition.operation is ConnectionTransitionOperation.ESTABLISH
    }
    emitted = {
        (
            source.current_slice_id,
            transition.connection_id,
            transition.direction,
        ): transition
        for transition in transitions
        if (
            source := resolver.difference_for_time(transition.nominal_time)
        ).current_slice_id
        in difference_ids
    }
    decisions = []
    for difference in differences:
        for connection in difference.added_connections:
            if (
                difference.current_slice_id,
                connection.connection_id,
            ) in establishments:
                _initialize_parameter_state(
                    observed,
                    planned,
                    pending,
                    drift_started,
                    persistence_started,
                    connection,
                )
        for patch in difference.connection_parameter_patches:
            key = (patch.connection_id, patch.direction)
            if key not in observed:
                continue
            previous_observed = observed[key]
            observed[key] = _apply_parameter_patch(previous_observed, patch)
            first, _ = pending.get(
                key,
                (difference.current_slice_id, difference.current_slice_id),
            )
            pending[key] = (first, difference.current_slice_id)
            for field_name in _PARAMETER_FIELDS:
                _update_parameter_temporal_field(
                    parameter_model,
                    (*key, field_name),
                    getattr(previous_observed, field_name),
                    getattr(observed[key], field_name),
                    getattr(planned[key], field_name),
                    difference.current_timestamp,
                    drift_started,
                    persistence_started,
                )

        for key in tuple(observed):
            transition = emitted.get(
                (difference.current_slice_id, key[0], key[1])
            )
            if transition is None:
                continue
            planned[key] = _apply_parameter_transition(
                planned[key], transition
            )
            for field_name in _PARAMETER_FIELDS:
                _update_parameter_temporal_field(
                    parameter_model,
                    (*key, field_name),
                    getattr(observed[key], field_name),
                    getattr(observed[key], field_name),
                    getattr(planned[key], field_name),
                    difference.current_timestamp,
                    drift_started,
                    persistence_started,
                )
            source_range = pending.pop(
                key,
                (
                    difference.current_slice_id,
                    difference.current_slice_id,
                ),
            )
            if source_range[0] != difference.current_slice_id:
                decisions.append(
                    PlanningDecision(
                        source_difference_ids=(
                            source_range[0],
                            difference.current_slice_id,
                        ),
                        decision="merged",
                        decided_by=decided_by,
                        reason=(
                            "accumulated sampled drift reached a parameter "
                            "planning threshold across the recorded Difference "
                            "range"
                        ),
                    )
                )
        for connection_id in difference.removed_connection_ids:
            _clear_parameter_state(
                observed,
                planned,
                pending,
                drift_started,
                persistence_started,
                connection_id,
            )
    return (
        observed,
        planned,
        pending,
        drift_started,
        persistence_started,
        tuple(decisions),
    )


def _initialize_parameter_state(
    observed: dict,
    planned: dict,
    pending: dict,
    drift_started: dict,
    persistence_started: dict,
    connection: Connection,
) -> None:
    for direction, parameters in (
        (ConnectionDirection.ENDPOINT_A_TO_B, connection.endpoint_a_to_b),
        (ConnectionDirection.ENDPOINT_B_TO_A, connection.endpoint_b_to_a),
    ):
        key = (connection.connection_id, direction)
        observed[key] = parameters
        planned[key] = parameters
        pending.pop(key, None)
        for field_name in _PARAMETER_FIELDS:
            drift_started.pop((*key, field_name), None)
            persistence_started.pop((*key, field_name), None)


def _clear_parameter_state(
    observed: dict,
    planned: dict,
    pending: dict,
    drift_started: dict,
    persistence_started: dict,
    connection_id: ConnectionId,
) -> None:
    for mapping in (observed, planned, pending):
        for direction in ConnectionDirection:
            mapping.pop((connection_id, direction), None)
    for mapping in (drift_started, persistence_started):
        for direction in ConnectionDirection:
            for field_name in _PARAMETER_FIELDS:
                mapping.pop((connection_id, direction, field_name), None)


def _update_parameter_temporal_field(
    parameter_model: ParameterPlanningModel,
    key: _ParameterFieldKey,
    previous_observed: int,
    observed: int,
    planned: int,
    timestamp: datetime,
    drift_started: dict[_ParameterFieldKey, datetime],
    persistence_started: dict[_ParameterFieldKey, datetime],
) -> None:
    if observed == planned:
        drift_started.pop(key, None)
    else:
        drift_started.setdefault(key, timestamp)
    outside_method = getattr(
        parameter_model, "_field_is_outside_effective_band", None
    )
    outside = (
        outside_method(key[2], observed, planned)
        if callable(outside_method)
        else observed != planned
    )
    crossed = (
        previous_observed != planned
        and observed != planned
        and (previous_observed - planned) * (observed - planned) < 0
    )
    if not outside:
        persistence_started.pop(key, None)
    elif crossed:
        persistence_started[key] = timestamp
    else:
        persistence_started.setdefault(key, timestamp)


def _advance_forwarding_state(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    next_hops_before: Mapping[RouteKey, NodeId],
    paths_before: Mapping[RouteKey, tuple[ConnectionId, ...]],
    formal_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    formal_paths: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
    connection_values: Mapping[
        int, Mapping[ConnectionId, Connection]
    ],
    formal_route_candidate_started_before: Mapping[RouteKey, datetime],
    planned_route_divergence_started_before: Mapping[RouteKey, datetime],
    *,
    planned_paths_by_slice: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ]
    | None = None,
) -> tuple[
    dict[RouteKey, NodeId],
    dict[RouteKey, tuple[ConnectionId, ...]],
    dict[RouteKey, datetime],
    dict[RouteKey, datetime],
]:
    next_hops = dict(next_hops_before)
    paths_by_slice = (
        planned_paths_by_slice
        if planned_paths_by_slice is not None
        else _planned_paths_by_slice(
            differences,
            transitions,
            formal_next_hops,
            formal_paths,
            connection_values,
            next_hops_before,
            paths_before,
        )
    )
    for transition in sorted(
        transitions,
        key=lambda value: (value.nominal_time, repr(value.route_key)),
    ):
        if transition.operation is DifferenceOperation.REMOVED:
            next_hops.pop(transition.route_key, None)
            continue
        next_hops[transition.route_key] = transition.next_hop
    candidate_started = dict(formal_route_candidate_started_before)
    divergence_started = dict(planned_route_divergence_started_before)
    previous_planned = dict(paths_before)
    for difference in differences:
        slice_id = difference.current_slice_id
        planned = paths_by_slice[slice_id]
        formal = formal_paths.get(slice_id, {})
        previous_formal = formal_paths.get(
            difference.previous_slice_id, {}
        )
        for route_key in (
            set(previous_planned)
            | set(planned)
            | set(formal)
            | set(candidate_started)
            | set(divergence_started)
        ):
            planned_path = planned.get(route_key)
            formal_path = formal.get(route_key)
            if planned_path is None or formal_path is None:
                candidate_started.pop(route_key, None)
                divergence_started.pop(route_key, None)
                continue
            if planned_path == formal_path:
                candidate_started.pop(route_key, None)
                divergence_started.pop(route_key, None)
                continue
            if (
                route_key not in candidate_started
                or previous_formal.get(route_key) != formal_path
                or previous_formal.get(route_key)
                == previous_planned.get(route_key)
            ):
                candidate_started[route_key] = difference.current_timestamp
            divergence_started.setdefault(
                route_key, difference.current_timestamp
            )
        previous_planned = dict(planned)
    return (
        next_hops,
        dict(paths_by_slice[differences[-1].current_slice_id]),
        candidate_started,
        divergence_started,
    )


def _apply_parameter_patch(previous, patch) -> DirectionalConnectionParameters:
    return DirectionalConnectionParameters(
        bandwidth_bps=(
            patch.bandwidth_bps
            if patch.bandwidth_bps is not None
            else previous.bandwidth_bps
        ),
        delay_us=(
            patch.delay_us if patch.delay_us is not None else previous.delay_us
        ),
        packet_loss_ppm=(
            patch.packet_loss_ppm
            if patch.packet_loss_ppm is not None
            else previous.packet_loss_ppm
        ),
    )


def _apply_parameter_transition(
    previous: DirectionalConnectionParameters,
    transition: ParameterTransition,
) -> DirectionalConnectionParameters:
    return DirectionalConnectionParameters(
        bandwidth_bps=(
            transition.bandwidth_bps
            if transition.bandwidth_bps is not None
            else previous.bandwidth_bps
        ),
        delay_us=(
            transition.delay_us
            if transition.delay_us is not None
            else previous.delay_us
        ),
        packet_loss_ppm=(
            transition.packet_loss_ppm
            if transition.packet_loss_ppm is not None
            else previous.packet_loss_ppm
        ),
    )


def _parameter_transition_source_or_none(
    differences: tuple[TopologyDifference, ...],
    transition: ParameterTransition,
) -> TopologyDifference | None:
    return next(
        (
            difference
            for difference in differences
            if _time_belongs_to_difference(
                difference, transition.nominal_time
            )
            and any(
                patch.connection_id == transition.connection_id
                and patch.direction is transition.direction
                for patch in difference.connection_parameter_patches
            )
        ),
        None,
    )


def _retained_connection_decision(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ConnectionTransition, ...],
    decided_by: str,
) -> PlanningDecision | None:
    if not transitions:
        return None
    resolver = _DifferenceTimeResolver(differences)
    source_ids = tuple(
        sorted(
            {
                resolver.difference_for_time(
                    transition.nominal_time
                ).current_slice_id
                for transition in transitions
            }
        )
    )
    return PlanningDecision(
        source_difference_ids=source_ids,
        decision="retained",
        decided_by=decided_by,
        reason=(
            f"planned {len(transitions)} Connection transitions across the "
            "affected planning-window region"
        ),
    )


def _validate_plan_region(
    differences: tuple[TopologyDifference, ...],
    connection_transitions: tuple[ConnectionTransition, ...],
    parameter_transitions: tuple[ParameterTransition, ...],
    forwarding_transitions: tuple[ForwardingTransition, ...],
    route_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    route_paths: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
    connection_values_by_slice_id: Mapping[
        int, Mapping[ConnectionId, Connection]
    ],
    active_before: Mapping[ConnectionId, ConnectionTransition],
    planned_next_hops_before: Mapping[RouteKey, NodeId],
    planned_paths_before: Mapping[RouteKey, tuple[ConnectionId, ...]],
    *,
    faithful_formal_translation: bool = False,
) -> dict[str, int]:
    if not isinstance(faithful_formal_translation, bool):
        raise TypeError("faithful_formal_translation must be a bool")
    active_connections = {
        connection_id: transition.connection
        for connection_id, transition in active_before.items()
    }
    if any(connection is None for connection in active_connections.values()):
        raise ValueError("active Connection baseline lacks complete values")
    current_routes = dict(planned_next_hops_before)
    current_dependencies: Mapping[
        RouteKey, tuple[ConnectionId, ...]
    ] = dict(planned_paths_before)
    dependency_reference_counts = Counter(
        connection_id
        for dependencies in current_dependencies.values()
        for connection_id in dependencies
    )
    if not faithful_formal_translation and not all(
        set(dependencies) <= set(active_connections)
        for dependencies in current_dependencies.values()
    ):
        raise ValueError("planned forwarding baseline lacks Connection support")
    connections_by_time: dict[
        datetime, list[ConnectionTransition]
    ] = {}
    parameters_by_time: dict[datetime, list[ParameterTransition]] = {}
    forwarding_by_time: dict[datetime, list[ForwardingTransition]] = {}
    timestamps_by_slice_id: dict[int, set[datetime]] = {}
    resolver = _DifferenceTimeResolver(differences)
    for transition in connection_transitions:
        source = resolver.difference_for_time(transition.nominal_time)
        connections_by_time.setdefault(transition.nominal_time, []).append(
            transition
        )
        timestamps_by_slice_id.setdefault(source.current_slice_id, set()).add(
            transition.nominal_time
        )
    for transition in parameter_transitions:
        source = resolver.difference_for_time(transition.nominal_time)
        parameters_by_time.setdefault(transition.nominal_time, []).append(
            transition
        )
        timestamps_by_slice_id.setdefault(source.current_slice_id, set()).add(
            transition.nominal_time
        )
    if not faithful_formal_translation:
        for transition in forwarding_transitions:
            source = resolver.difference_for_time(transition.nominal_time)
            forwarding_by_time.setdefault(transition.nominal_time, []).append(
                transition
            )
            timestamps_by_slice_id.setdefault(
                source.current_slice_id, set()
            ).add(transition.nominal_time)
    previous_planned_paths = dict(planned_paths_before)
    dependencies_dirty = False
    path_reconstructions = 0
    reused_boundaries = 0
    dependency_ids_checked = 0
    for difference in differences:
        interval_timestamps = sorted(
            timestamps_by_slice_id.get(difference.current_slice_id, ())
        )
        for timestamp in interval_timestamps:
            connections = connections_by_time.get(timestamp, ())
            for transition in connections:
                if (
                    transition.operation
                    is ConnectionTransitionOperation.ESTABLISH
                ):
                    if transition.connection_id in active_connections:
                        raise ValueError(
                            "contradictory Connection establishment"
                        )
                    active_connections[transition.connection_id] = (
                        transition.connection
                    )

            forwarding_at_time = forwarding_by_time.get(timestamp, ())
            for transition in forwarding_at_time:
                if transition.operation is DifferenceOperation.REMOVED:
                    current_routes.pop(transition.route_key, None)
                else:
                    current_routes[transition.route_key] = transition.next_hop
            if forwarding_at_time:
                dependencies_dirty = True

            for transition in parameters_by_time.get(timestamp, ()):
                if transition.connection_id not in active_connections:
                    raise ValueError(
                        "parameter transition outside active Connection lifetime"
                    )

            withdrawals = tuple(
                transition
                for transition in connections
                if transition.operation
                is ConnectionTransitionOperation.WITHDRAW
            )
            if withdrawals:
                if not faithful_formal_translation and dependencies_dirty:
                    current_dependencies = _resolve_planned_paths(
                        current_routes, active_connections
                    )
                    dependency_reference_counts = Counter(
                        connection_id
                        for dependencies in current_dependencies.values()
                        for connection_id in dependencies
                    )
                    path_reconstructions += 1
                    dependencies_dirty = False

            for transition in withdrawals:
                if transition.connection_id not in active_connections:
                    raise ValueError(
                        "Connection withdrawal without active lifetime"
                    )
                if not faithful_formal_translation:
                    dependency_ids_checked += 1
                    if dependency_reference_counts.get(
                        transition.connection_id, 0
                    ):
                        raise ValueError(
                            "forwarding state depends on withdrawn Connection"
                        )
                active_connections.pop(transition.connection_id)

        slice_id = difference.current_slice_id
        formal_connection_ids = set(
            connection_values_by_slice_id.get(slice_id, {})
        )
        if (
            set(active_connections) != formal_connection_ids
            if faithful_formal_translation
            else not set(active_connections) <= formal_connection_ids
        ):
            raise ValueError(
                "planned Connection state exceeds formal boundary state"
            )
        if faithful_formal_translation:
            reused_boundaries += 1
            dependencies_dirty = False
            continue

        if dependencies_dirty:
            current_dependencies = _resolve_planned_paths(
                current_routes, active_connections
            )
            dependency_reference_counts = Counter(
                connection_id
                for dependencies in current_dependencies.values()
                for connection_id in dependencies
            )
            path_reconstructions += 1
            dependencies_dirty = False
        formal_paths_at_boundary = route_paths.get(slice_id, {})
        if set(current_dependencies) != set(formal_paths_at_boundary):
            raise ValueError(
                "planned forwarding demand differs from formal boundary"
            )
        formal_connections = connection_values_by_slice_id.get(slice_id, {})
        path_nodes_cache: dict[
            tuple[NodeId, tuple[ConnectionId, ...]],
            tuple[NodeId, ...] | None,
        ] = {}
        for route_key, path in current_dependencies.items():
            formal_path = tuple(formal_paths_at_boundary[route_key])
            dependency_ids_checked += len(path) + len(formal_path)
            if path not in (
                formal_path,
                tuple(previous_planned_paths.get(route_key, ())),
            ):
                raise ValueError(
                    "planned forwarding state is a hybrid or third Route"
                )
            planned_nodes = _cached_path_nodes(
                path_nodes_cache,
                route_key.source,
                path,
                formal_connections,
            )
            formal_nodes = _cached_path_nodes(
                path_nodes_cache,
                route_key.source,
                formal_path,
                formal_connections,
            )
            if (
                planned_nodes is None
                or formal_nodes is None
                or planned_nodes[-1] != formal_nodes[-1]
            ):
                raise ValueError(
                    "planned forwarding target terminal is incompatible"
                )
        previous_planned_paths = dict(current_dependencies)
    return {
        "validation_event_timestamps": sum(
            len(values) for values in timestamps_by_slice_id.values()
        ),
        "forwarding_path_reconstructions": path_reconstructions,
        "reused_forwarding_path_boundaries": reused_boundaries,
        "validated_forwarding_dependency_ids": dependency_ids_checked,
        "faithful_formal_translation": int(faithful_formal_translation),
    }


def _difference_for_time(
    differences: tuple[TopologyDifference, ...],
    nominal_time: datetime,
) -> TopologyDifference:
    for difference in differences:
        if _time_belongs_to_difference(difference, nominal_time):
            return difference
    raise ValueError("transition nominal time is outside planning window")


def _time_belongs_to_difference(
    difference: TopologyDifference, nominal_time: datetime
) -> bool:
    if difference.previous_timestamp is None:
        return nominal_time == difference.current_timestamp
    return (
        difference.previous_timestamp
        < nominal_time
        <= difference.current_timestamp
    )


__all__ = ["Planner"]
