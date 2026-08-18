"""Incrementally maintain and publish one semantic Planner snapshot."""

from __future__ import annotations

from collections import ChainMap, Counter
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

from skyseed_v2.shared import NodeId

from ..slicing import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    DifferenceOperation,
    DirectionalConnectionParameters,
    RouteKey,
    Slicer,
    TopologyDifference,
)
from .algorithms import (
    AllAvailablePlanningHorizonPolicy,
    ConnectionPlanningModel,
    CoordinationPolicy,
    ForwardingPlanningModel,
    ParameterPlanningModel,
    PlanningHorizonPolicy,
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


_ParameterKey = tuple[ConnectionId, ConnectionDirection]


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
        "_horizon_policy",
        "_connection_model",
        "_parameter_model",
        "_forwarding_model",
        "_coordination_policy",
        "_processed_through_slice_id",
        "_published_through_slice_id",
        "_differences",
        "_selected_differences",
        "_connection_transitions",
        "_parameter_transitions",
        "_forwarding_transitions",
        "_forwarding_precedences",
        "_observed_parameters",
        "_planned_parameters",
        "_pending_parameter_ranges",
        "_published_observed_parameters",
        "_published_planned_parameters",
        "_published_pending_parameter_ranges",
        "_route_next_hops",
        "_route_path_connection_ids",
        "_active_establishments",
        "_published_active_establishments",
        "_planned_route_next_hops",
        "_planned_route_path_connection_ids",
        "_published_route_next_hops",
        "_published_route_path_connection_ids",
        "_decisions",
        "_plan",
        "_publication_count",
    )

    def __init__(self) -> None:
        raise TypeError("Planner is created explicitly by Simulator")

    @classmethod
    def _create(
        cls,
        *,
        slicer: Slicer,
        horizon_policy: PlanningHorizonPolicy | None,
        connection_model: ConnectionPlanningModel,
        parameter_model: ParameterPlanningModel,
        forwarding_model: ForwardingPlanningModel,
        coordination_policy: CoordinationPolicy,
    ) -> "Planner":
        if not isinstance(slicer, Slicer):
            raise TypeError("slicer must be a Slicer")
        if horizon_policy is None:
            horizon_policy = AllAvailablePlanningHorizonPolicy()
        if not isinstance(horizon_policy, PlanningHorizonPolicy):
            raise TypeError(
                "horizon_policy must implement PlanningHorizonPolicy"
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
        instance._horizon_policy = horizon_policy
        instance._connection_model = connection_model
        instance._parameter_model = parameter_model
        instance._forwarding_model = forwarding_model
        instance._coordination_policy = coordination_policy
        instance._processed_through_slice_id = 0
        instance._published_through_slice_id = 0
        instance._differences: list[TopologyDifference] = []
        instance._selected_differences: tuple[TopologyDifference, ...] = ()
        instance._connection_transitions: tuple[ConnectionTransition, ...] = ()
        instance._parameter_transitions: tuple[ParameterTransition, ...] = ()
        instance._forwarding_transitions: tuple[ForwardingTransition, ...] = ()
        instance._forwarding_precedences: tuple[
            ForwardingTransitionPrecedence, ...
        ] = ()
        instance._observed_parameters: dict[
            _ParameterKey, DirectionalConnectionParameters
        ] = {}
        instance._planned_parameters: dict[
            _ParameterKey, DirectionalConnectionParameters
        ] = {}
        instance._pending_parameter_ranges: dict[
            _ParameterKey, tuple[int, int]
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
        instance._route_next_hops: dict[
            int, Mapping[RouteKey, NodeId]
        ] = {}
        instance._route_path_connection_ids: dict[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
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
        instance._decisions: list[PlanningDecision] = []
        instance._plan: Plan | None = None
        instance._publication_count = 0
        instance._synchronize_available()
        return instance

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

        return tuple(self._differences)

    def get_planning_decisions(self) -> tuple[PlanningDecision, ...]:
        """Return compact merge/suppression evidence outside Plan payload."""

        return tuple(self._decisions)

    def _synchronize_available(self) -> None:
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
        if not available:
            return

        selected = tuple(self._horizon_policy.select_differences(available))
        _validate_selected_horizon(available, selected)
        if not _identity_prefix(self._selected_differences, selected):
            raise ValueError(
                "PlanningHorizonPolicy must preserve already-planned visible input"
            )
        publication = tuple(
            self._horizon_policy.select_publication_differences(selected)
        )
        _validate_publication_horizon(selected, publication)

        affected = selected[len(self._selected_differences) :]
        previous_connections = self._connection_transitions
        previous_parameters = self._parameter_transitions
        previous_forwarding = self._forwarding_transitions
        observed_before = self._observed_parameters
        planned_before = self._planned_parameters
        active_before = self._active_establishments

        new_route_next_hops, new_route_paths = _collect_route_context(
            self._slicer,
            selected,
            self._route_next_hops,
            self._route_path_connection_ids,
        )
        route_next_hops = MappingProxyType(
            ChainMap(new_route_next_hops, self._route_next_hops)
        )
        route_paths = MappingProxyType(
            ChainMap(new_route_paths, self._route_path_connection_ids)
        )

        if affected:
            model_connections = tuple(
                self._connection_model.plan_connection_transitions(
                    differences=selected,
                    affected_differences=affected,
                    previous_transitions=previous_connections,
                )
            )
            connection_validation = _incremental_validation_region(
                previous_connections,
                model_connections,
                affected,
                selected,
            )
            _validate_connection_model_output(*connection_validation)

            model_parameters = tuple(
                self._parameter_model.plan_parameter_transitions(
                    differences=selected,
                    affected_differences=affected,
                    previous_transitions=previous_parameters,
                    observed_parameters=MappingProxyType(observed_before),
                    planned_parameters=MappingProxyType(planned_before),
                )
            )
            parameter_validation = _incremental_validation_region(
                previous_parameters,
                model_parameters,
                affected,
                selected,
            )
            _validate_parameter_model_output(
                self._slicer, *parameter_validation
            )

            model_forwarding = tuple(
                self._forwarding_model.plan_forwarding_transitions(
                    differences=selected,
                    affected_differences=affected,
                    previous_transitions=previous_forwarding,
                    connection_transitions=model_connections,
                    route_path_connection_ids=route_paths,
                )
            )
            forwarding_validation = _incremental_validation_region(
                previous_forwarding,
                model_forwarding,
                affected,
                selected,
            )
            _validate_forwarding_model_output(*forwarding_validation)

            connection_model_reuse = _identity_prefix(
                self._connection_transitions, model_connections
            )
            candidate_differences = (
                affected if connection_model_reuse else selected
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
            coordinated = self._coordination_policy.coordinate_transitions(
                differences=selected,
                suppression_candidates=candidates,
                connection_transitions=model_connections,
                parameter_transitions=model_parameters,
                forwarding_transitions=model_forwarding,
                route_next_hops=route_next_hops,
                route_path_connection_ids=route_paths,
            )
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
            forwarding_precedences = tuple(
                self._forwarding_model.plan_forwarding_precedences(
                    differences=selected,
                    forwarding_transitions=forwarding_transitions,
                    route_path_connection_ids=route_paths,
                )
            )
            _validate_forwarding_precedence_output(
                forwarding_transitions, forwarding_precedences
            )
        else:
            connection_transitions = previous_connections
            parameter_transitions = previous_parameters
            forwarding_transitions = previous_forwarding
            forwarding_precedences = self._forwarding_precedences
            coordination_decisions = ()

        candidate_active_establishments = _advance_active_establishments(
            self._published_active_establishments,
            connection_transitions,
        )
        (
            candidate_observed,
            candidate_planned,
            candidate_pending,
            _,
        ) = _advance_parameter_state(
            selected,
            connection_transitions,
            parameter_transitions,
            self._published_observed_parameters,
            self._published_planned_parameters,
            self._published_pending_parameter_ranges,
            type(self._parameter_model).__name__,
        )
        (
            candidate_planned_routes,
            candidate_planned_paths,
        ) = _advance_forwarding_state(
            selected,
            forwarding_transitions,
            self._published_route_next_hops,
            self._published_route_path_connection_ids,
            route_next_hops,
            route_paths,
        )
        _validate_plan_region(
            selected,
            connection_transitions,
            parameter_transitions,
            forwarding_transitions,
            route_next_hops,
            route_paths,
            self._published_active_establishments,
            self._published_route_next_hops,
            self._published_route_path_connection_ids,
            {
                connection.connection_id
                for connection in self._slicer.get_slice(
                    selected[-1].current_slice_id
                ).connection_slice.connections
            },
        )

        first = publication[0]
        last = publication[-1]
        publication_connections = _transitions_through(
            connection_transitions, last.current_timestamp
        )
        publication_parameters = _transitions_through(
            parameter_transitions, last.current_timestamp
        )
        publication_forwarding = _transitions_through(
            forwarding_transitions, last.current_timestamp
        )
        publication_forwarding_ids = {
            id(value) for value in publication_forwarding
        }
        publication_precedences = tuple(
            precedence
            for precedence in forwarding_precedences
            if id(precedence.predecessor) in publication_forwarding_ids
            and id(precedence.successor) in publication_forwarding_ids
        )
        candidate_plan = Plan(
            start_slice_id=first.previous_slice_id,
            start_timestamp=first.previous_timestamp,
            end_slice_id=last.current_slice_id,
            end_timestamp=last.current_timestamp,
            connection_transitions=publication_connections,
            parameter_transitions=publication_parameters,
            forwarding_transitions=publication_forwarding,
            forwarding_precedences=publication_precedences,
        )

        published_active = _advance_active_establishments(
            self._published_active_establishments,
            candidate_plan.connection_transitions,
        )
        (
            published_observed,
            published_planned,
            published_pending,
            parameter_decisions,
        ) = _advance_parameter_state(
            publication,
            candidate_plan.connection_transitions,
            candidate_plan.parameter_transitions,
            self._published_observed_parameters,
            self._published_planned_parameters,
            self._published_pending_parameter_ranges,
            type(self._parameter_model).__name__,
        )
        published_routes, published_paths = _advance_forwarding_state(
            publication,
            candidate_plan.forwarding_transitions,
            self._published_route_next_hops,
            self._published_route_path_connection_ids,
            route_next_hops,
            route_paths,
        )
        retained_decision = _retained_connection_decision(
            publication,
            candidate_plan.connection_transitions,
            type(self._connection_model).__name__,
        )
        new_decisions = (
            *((retained_decision,) if retained_decision is not None else ()),
            *parameter_decisions,
            *coordination_decisions,
        )

        retained_selected = selected[len(publication) :]
        retained_connections = _transitions_after(
            connection_transitions, last.current_timestamp
        )
        retained_parameters = _transitions_after(
            parameter_transitions, last.current_timestamp
        )
        retained_forwarding = _transitions_after(
            forwarding_transitions, last.current_timestamp
        )
        retained_forwarding_ids = {id(value) for value in retained_forwarding}
        retained_precedences = tuple(
            precedence
            for precedence in forwarding_precedences
            if id(precedence.predecessor) in retained_forwarding_ids
            and id(precedence.successor) in retained_forwarding_ids
        )

        self._route_next_hops.update(new_route_next_hops)
        self._route_path_connection_ids.update(new_route_paths)
        self._differences.extend(new_differences)
        self._selected_differences = retained_selected
        self._connection_transitions = retained_connections
        self._parameter_transitions = retained_parameters
        self._forwarding_transitions = retained_forwarding
        self._forwarding_precedences = retained_precedences
        self._observed_parameters = candidate_observed
        self._planned_parameters = candidate_planned
        self._pending_parameter_ranges = candidate_pending
        self._active_establishments = candidate_active_establishments
        self._planned_route_next_hops = candidate_planned_routes
        self._planned_route_path_connection_ids = candidate_planned_paths
        self._published_observed_parameters = published_observed
        self._published_planned_parameters = published_planned
        self._published_pending_parameter_ranges = published_pending
        self._published_active_establishments = published_active
        self._published_route_next_hops = published_routes
        self._published_route_path_connection_ids = published_paths
        for decision in new_decisions:
            if decision not in self._decisions:
                self._decisions.append(decision)
        self._processed_through_slice_id = latest_slice_id
        self._published_through_slice_id = last.current_slice_id
        self._plan = candidate_plan
        self._publication_count += 1


def _validate_selected_horizon(
    available: tuple[TopologyDifference, ...],
    selected: tuple[TopologyDifference, ...],
) -> None:
    if not selected:
        raise ValueError("PlanningHorizonPolicy must select a non-empty range")
    if any(not isinstance(value, TopologyDifference) for value in selected):
        raise TypeError(
            "PlanningHorizonPolicy must return TopologyDifference values"
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
    for value in selected:
        identity = id(value)
        match = available_positions.get(identity)
        if (
            match is None
            or identity in duplicate_identities
            or match[0] is not value
        ):
            raise ValueError(
                "PlanningHorizonPolicy must select exact consumed Differences"
            )
        positions.append(match[1])
    if positions != list(range(len(positions))):
        raise ValueError(
            "PlanningHorizonPolicy must select a visible Difference prefix"
        )


def _validate_publication_horizon(
    visible: tuple[TopologyDifference, ...],
    publication: tuple[TopologyDifference, ...],
) -> None:
    if not publication:
        raise ValueError(
            "PlanningHorizonPolicy must select a non-empty publication prefix"
        )
    if not _identity_prefix(publication, visible):
        raise ValueError(
            "PlanningHorizonPolicy publication must be an exact visible prefix"
        )


def _identity_prefix(previous: tuple, current: tuple) -> bool:
    return len(previous) <= len(current) and all(
        old is new for old, new in zip(previous, current)
    )


def _incremental_validation_region(
    previous: tuple,
    current: tuple,
    affected: tuple[TopologyDifference, ...],
    selected: tuple[TopologyDifference, ...],
) -> tuple[tuple[TopologyDifference, ...], tuple]:
    if _identity_prefix(previous, current):
        return affected, current[len(previous) :]
    return selected, current


def _collect_route_context(
    slicer: Slicer,
    differences: tuple[TopologyDifference, ...],
    retained_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    retained_paths: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
) -> tuple[
    dict[int, Mapping[RouteKey, NodeId]],
    dict[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
]:
    slice_ids = {
        differences[0].previous_slice_id,
        *(difference.current_slice_id for difference in differences),
    }
    retained_slice_ids = set(retained_next_hops) | set(retained_paths)
    new_next_hops: dict[int, Mapping[RouteKey, NodeId]] = {}
    new_paths: dict[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ] = {}
    for slice_id in sorted(slice_ids - retained_slice_ids):
        next_hops, paths = _slice_route_state(slicer, slice_id)
        new_next_hops[slice_id] = MappingProxyType(next_hops)
        new_paths[slice_id] = MappingProxyType(paths)
    return new_next_hops, new_paths


def _slice_route_state(
    slicer: Slicer,
    slice_id: int,
) -> tuple[
    dict[RouteKey, NodeId],
    dict[RouteKey, tuple[ConnectionId, ...]],
]:
    topology_slice = slicer.get_slice(slice_id)
    next_hops = {
        RouteKey(source, target): next_hop
        for source, targets in topology_slice.route_slice.routes.items()
        for target, next_hop in targets.items()
    }
    paths = {
        route_key: _path_connection_ids(
            slicer,
            slice_id,
            slicer.resolve_path(
                slice_id, route_key.source, route_key.target
            ),
        )
        for route_key in next_hops
    }
    return next_hops, paths


def _path_connection_ids(
    slicer: Slicer,
    slice_id: int,
    path: tuple[NodeId, ...],
) -> tuple[ConnectionId, ...]:
    if len(path) < 2:
        return ()
    connection_slice = slicer.get_slice(slice_id).connection_slice
    result = []
    for first, second in zip(path, path[1:]):
        matches = tuple(
            connection
            for connection in connection_slice.get_connections_for_node(first)
            if second
            in (
                connection.connection_id.endpoint_a,
                connection.connection_id.endpoint_b,
            )
        )
        if len(matches) != 1:
            raise ValueError(
                "formal path edge must map to exactly one Connection"
            )
        result.append(matches[0].connection_id)
    return tuple(result)


def _validate_connection_model_output(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ConnectionTransition, ...],
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
    for transition in transitions:
        source = _difference_for_time(differences, transition.nominal_time)
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
            "ConnectionPlanningModel must preserve exact selected-horizon "
            "Difference evolution"
        )


def _validate_parameter_model_output(
    slicer: Slicer,
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ParameterTransition, ...],
) -> None:
    if any(not isinstance(value, ParameterTransition) for value in transitions):
        raise TypeError(
            "ParameterPlanningModel must return ParameterTransition values"
        )
    identities = set()
    for transition in transitions:
        source = _difference_for_time(differences, transition.nominal_time)
        matching = tuple(
            patch
            for patch in source.connection_parameter_patches
            if patch.connection_id == transition.connection_id
            and patch.direction is transition.direction
        )
        if len(matching) != 1:
            raise ValueError(
                "ParameterPlanningModel changed parameter patch identity"
            )
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
        connection = slicer.get_slice(
            source.current_slice_id
        ).connection_slice.get_connection(transition.connection_id)
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
) -> None:
    if any(not isinstance(value, ForwardingTransition) for value in transitions):
        raise TypeError(
            "ForwardingPlanningModel must return ForwardingTransition values"
        )
    expected = Counter(
        (
            difference.current_slice_id,
            patch.route_key,
            patch.operation,
            patch.next_hop,
        )
        for difference in differences
        for patch in difference.route_patches
    )
    actual = Counter()
    for transition in transitions:
        source = _difference_for_time(differences, transition.nominal_time)
        actual[
            (
                source.current_slice_id,
                transition.route_key,
                transition.operation,
                transition.next_hop,
            )
        ] += 1
    if actual != expected:
        raise ValueError(
            "ForwardingPlanningModel must preserve exact selected-horizon "
            "RoutePatch states"
        )


def _validate_forwarding_precedence_output(
    transitions: tuple[ForwardingTransition, ...],
    precedences: tuple[ForwardingTransitionPrecedence, ...],
) -> None:
    transition_ids = {id(value) for value in transitions}
    seen = set()
    for precedence in precedences:
        if not isinstance(precedence, ForwardingTransitionPrecedence):
            raise TypeError(
                "ForwardingPlanningModel must return "
                "ForwardingTransitionPrecedence values"
            )
        key = (id(precedence.predecessor), id(precedence.successor))
        if key[0] not in transition_ids or key[1] not in transition_ids:
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


def _transitions_through(transitions: tuple, timestamp: datetime) -> tuple:
    return tuple(
        transition
        for transition in transitions
        if transition.nominal_time <= timestamp
    )


def _transitions_after(transitions: tuple, timestamp: datetime) -> tuple:
    return tuple(
        transition
        for transition in transitions
        if transition.nominal_time > timestamp
    )


def _suppression_candidates(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ConnectionTransition, ...],
    active_before: Mapping[ConnectionId, ConnectionTransition],
    model: ConnectionPlanningModel,
) -> tuple[tuple[ConnectionTransition, ConnectionTransition], ...]:
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
        _difference_for_time(differences, transition.nominal_time)
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
    if any(
        not any(value is candidate for candidate in model_connections)
        for value in connections
    ):
        raise ValueError("CoordinationPolicy invented a Connection transition")
    if any(
        not any(value is candidate for candidate in model_parameters)
        for value in parameters
    ):
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
    decided_by: str,
) -> tuple[
    dict[_ParameterKey, DirectionalConnectionParameters],
    dict[_ParameterKey, DirectionalConnectionParameters],
    dict[_ParameterKey, tuple[int, int]],
    tuple[PlanningDecision, ...],
]:
    observed = dict(observed_before)
    planned = dict(planned_before)
    pending = dict(pending_before)
    difference_ids = {difference.current_slice_id for difference in differences}
    establishments = {
        (
            _difference_for_time(differences, transition.nominal_time)
            .current_slice_id,
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
            source := _parameter_transition_source_or_none(
                differences, transition
            )
        )
        is not None
        and source.current_slice_id in difference_ids
    }
    decisions = []
    for difference in differences:
        for connection in difference.added_connections:
            if (
                difference.current_slice_id,
                connection.connection_id,
            ) in establishments:
                _initialize_parameter_state(
                    observed, planned, pending, connection
                )
        for patch in difference.connection_parameter_patches:
            key = (patch.connection_id, patch.direction)
            if key not in observed:
                continue
            observed[key] = _apply_parameter_patch(observed[key], patch)
            first, _ = pending.get(
                key,
                (difference.current_slice_id, difference.current_slice_id),
            )
            pending[key] = (first, difference.current_slice_id)
            transition = emitted.get(
                (
                    difference.current_slice_id,
                    patch.connection_id,
                    patch.direction,
                )
            )
            if transition is None:
                continue
            planned[key] = _apply_parameter_transition(
                planned[key], transition
            )
            source_range = pending.pop(key)
            if source_range[0] != source_range[1]:
                decisions.append(
                    PlanningDecision(
                        source_difference_ids=source_range,
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
                observed, planned, pending, connection_id
            )
    return observed, planned, pending, tuple(decisions)


def _initialize_parameter_state(
    observed: dict,
    planned: dict,
    pending: dict,
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


def _clear_parameter_state(
    observed: dict,
    planned: dict,
    pending: dict,
    connection_id: ConnectionId,
) -> None:
    for mapping in (observed, planned, pending):
        for key in tuple(mapping):
            if key[0] == connection_id:
                mapping.pop(key)


def _advance_forwarding_state(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    next_hops_before: Mapping[RouteKey, NodeId],
    paths_before: Mapping[RouteKey, tuple[ConnectionId, ...]],
    formal_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    formal_paths: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
) -> tuple[
    dict[RouteKey, NodeId],
    dict[RouteKey, tuple[ConnectionId, ...]],
]:
    next_hops = dict(next_hops_before)
    paths = dict(paths_before)
    for transition in sorted(
        transitions,
        key=lambda value: (value.nominal_time, repr(value.route_key)),
    ):
        if transition.operation is DifferenceOperation.REMOVED:
            next_hops.pop(transition.route_key, None)
            paths.pop(transition.route_key, None)
            continue
        source = _difference_for_time(differences, transition.nominal_time)
        next_hops[transition.route_key] = transition.next_hop
        paths[transition.route_key] = tuple(
            formal_paths.get(source.current_slice_id, {}).get(
                transition.route_key, ()
            )
        )
    _refresh_formal_path_dependencies(
        next_hops,
        paths,
        formal_next_hops,
        formal_paths,
        differences[-1].current_slice_id,
    )
    return next_hops, paths


def _refresh_formal_path_dependencies(
    current_routes: Mapping[RouteKey, NodeId],
    current_dependencies: dict[RouteKey, tuple[ConnectionId, ...]],
    formal_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    formal_paths: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
    slice_id: int,
) -> None:
    next_hops = formal_next_hops.get(slice_id, {})
    paths = formal_paths.get(slice_id, {})
    for route_key, current_next_hop in current_routes.items():
        if next_hops.get(route_key) == current_next_hop:
            current_dependencies[route_key] = tuple(paths.get(route_key, ()))


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
    source_ids = tuple(
        sorted(
            {
                _difference_for_time(
                    differences, transition.nominal_time
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
            "affected selected-horizon region"
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
    active_before: Mapping[ConnectionId, ConnectionTransition],
    planned_next_hops_before: Mapping[RouteKey, NodeId],
    planned_paths_before: Mapping[RouteKey, tuple[ConnectionId, ...]],
    formal_end_connection_ids: set[ConnectionId],
) -> None:
    active_connections = set(active_before)
    current_routes = dict(planned_next_hops_before)
    current_dependencies = dict(planned_paths_before)
    if not all(
        set(dependencies) <= active_connections
        for dependencies in current_dependencies.values()
    ):
        raise ValueError("planned forwarding baseline lacks Connection support")
    connections_by_time: dict[
        datetime, list[ConnectionTransition]
    ] = {}
    parameters_by_time: dict[datetime, list[ParameterTransition]] = {}
    forwarding_by_time: dict[datetime, list[ForwardingTransition]] = {}
    for transition in connection_transitions:
        connections_by_time.setdefault(transition.nominal_time, []).append(
            transition
        )
    for transition in parameter_transitions:
        parameters_by_time.setdefault(transition.nominal_time, []).append(
            transition
        )
    for transition in forwarding_transitions:
        forwarding_by_time.setdefault(transition.nominal_time, []).append(
            transition
        )
    timestamps = sorted(
        connections_by_time.keys()
        | parameters_by_time.keys()
        | forwarding_by_time.keys()
    )
    for timestamp in timestamps:
        connections = connections_by_time.get(timestamp, ())
        for transition in connections:
            if transition.operation is ConnectionTransitionOperation.ESTABLISH:
                if transition.connection_id in active_connections:
                    raise ValueError("contradictory Connection establishment")
                active_connections.add(transition.connection_id)

        for transition in forwarding_by_time.get(timestamp, ()):
            source = _difference_for_time(differences, transition.nominal_time)
            formal_next_hop = route_next_hops.get(
                source.current_slice_id, {}
            ).get(transition.route_key)
            if transition.operation is DifferenceOperation.REMOVED:
                if formal_next_hop is not None:
                    raise ValueError(
                        "coordinated forwarding removal lacks formal state"
                    )
                current_routes.pop(transition.route_key, None)
                current_dependencies.pop(transition.route_key, None)
                continue
            if formal_next_hop != transition.next_hop:
                raise ValueError(
                    "coordinated forwarding transition invented a next hop"
                )
            dependencies = route_paths.get(
                source.current_slice_id, {}
            ).get(transition.route_key, ())
            if not set(dependencies) <= active_connections:
                raise ValueError(
                    "forwarding transition lacks planned Connection support"
                )
            current_routes[transition.route_key] = transition.next_hop
            current_dependencies[transition.route_key] = dependencies

        for slice_id in {
            _difference_for_time(differences, transition.nominal_time)
            .current_slice_id
            for transition in connections
            if transition.operation
            is ConnectionTransitionOperation.WITHDRAW
        }:
            _refresh_formal_path_dependencies(
                current_routes,
                current_dependencies,
                route_next_hops,
                route_paths,
                slice_id,
            )

        for transition in parameters_by_time.get(timestamp, ()):
            if transition.connection_id not in active_connections:
                raise ValueError(
                    "parameter transition outside active Connection lifetime"
                )

        for transition in connections:
            if transition.operation is ConnectionTransitionOperation.WITHDRAW:
                if transition.connection_id not in active_connections:
                    raise ValueError(
                        "Connection withdrawal without active lifetime"
                    )
                if any(
                    transition.connection_id in dependencies
                    for dependencies in current_dependencies.values()
                ):
                    raise ValueError(
                        "forwarding state depends on withdrawn Connection"
                    )
                active_connections.remove(transition.connection_id)

    if not active_connections <= formal_end_connection_ids:
        raise ValueError(
            "planned Connection state exceeds formal coverage-end state"
        )
    formal_route_states = {
        (route_key, next_hop)
        for routes in route_next_hops.values()
        for route_key, next_hop in routes.items()
    }
    if any(
        (route_key, next_hop) not in formal_route_states
        for route_key, next_hop in current_routes.items()
    ):
        raise ValueError(
            "planned forwarding state was not provided by formal Route state"
        )
    if not all(
        set(dependencies) <= active_connections
        for dependencies in current_dependencies.values()
    ):
        raise ValueError("planned forwarding state lacks Connection support")


def _difference_for_time(
    differences: tuple[TopologyDifference, ...],
    nominal_time: datetime,
) -> TopologyDifference:
    for difference in differences:
        if _time_belongs_to_difference(difference, nominal_time):
            return difference
    raise ValueError("transition nominal time is outside selected horizon")


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
