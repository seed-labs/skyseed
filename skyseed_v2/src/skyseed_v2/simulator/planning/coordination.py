"""Canonical event derivation and public precedence policy."""

from __future__ import annotations

from .values import (
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    PlanTransition,
    ReachableRouteOutcome,
    RouteDemand,
    SemanticEventReference,
    SemanticEventKind,
    SemanticEventPrecedence,
    ZoneRouteDemandTarget,
    semantic_event_references,
)


class DefaultCoordinationPolicy:
    """Return required semantic precedence without owning event payloads.

    Layer: public Simulator planning coordination implementation. It is public
    because composition roots need a supplied default for the replaceable
    CoordinationPolicy contract. It is not a Plan-transition author, event
    owner, backend scheduler, or second semantic truth.
    """

    __slots__ = ()

    def coordinate(
        self,
        transition: PlanTransition,
        canonical_events: tuple[SemanticEventReference, ...],
        required_precedences: tuple[SemanticEventPrecedence, ...],
    ) -> tuple[SemanticEventPrecedence, ...]:
        del transition, canonical_events
        return required_precedences


def _validate_coordinated_precedences(
    transition: PlanTransition,
    canonical_events: tuple[SemanticEventReference, ...],
    required_precedences: tuple[SemanticEventPrecedence, ...],
    proposed_precedences: object,
) -> tuple[SemanticEventPrecedence, ...]:
    if not isinstance(proposed_precedences, tuple):
        raise TypeError(
            "CoordinationPolicy must return a tuple of SemanticEventPrecedence"
        )
    if any(
        not isinstance(value, SemanticEventPrecedence)
        for value in proposed_precedences
    ):
        raise TypeError(
            "CoordinationPolicy must return only SemanticEventPrecedence values"
        )
    expected_events = semantic_event_references(transition)
    if canonical_events != expected_events:
        raise ValueError(
            "canonical_events must equal transition-derived references"
        )
    event_set = set(expected_events)
    if any(
        value.predecessor not in event_set or value.successor not in event_set
        for value in proposed_precedences
    ):
        raise ValueError(
            "precedence endpoints must be canonical event references"
        )
    edge_keys = tuple(
        (value.predecessor, value.successor)
        for value in proposed_precedences
    )
    if len(edge_keys) != len(set(edge_keys)):
        raise ValueError("CoordinationPolicy returned a duplicate precedence")
    _validate_required_ordering(
        canonical_events, proposed_precedences, required_precedences
    )
    _validate_acyclic(canonical_events, proposed_precedences)
    return tuple(sorted(proposed_precedences, key=_precedence_key))


def _required_semantic_precedences(
    transition: PlanTransition,
    canonical_events: tuple[SemanticEventReference, ...],
) -> tuple[SemanticEventPrecedence, ...]:
    if not isinstance(transition, PlanTransition):
        raise TypeError("transition must be a PlanTransition")
    values = tuple(canonical_events)
    if any(not isinstance(value, SemanticEventReference) for value in values):
        raise TypeError(
            "canonical_events must contain SemanticEventReference values"
        )
    if any(value.timestamp != transition.timestamp for value in values):
        raise ValueError("canonical event timestamp must match transition")
    event_keys = tuple(_event_semantic_key(value) for value in values)
    if len(event_keys) != len(set(event_keys)):
        raise ValueError("canonical_events contains a duplicate semantic key/kind")

    establishment_by_connection = {}
    parameter_ready_by_connection = {}
    parameter_readiness_by_edge = {}
    withdrawal_by_edge = {}
    zone_readiness = {}
    zone_unassignments = {}
    route_readiness = {}
    route_withdrawals = {}
    route_unreachable = {}
    for event in values:
        if event.kind is SemanticEventKind.CONNECTION_ESTABLISH:
            establishment_by_connection[event.connection_id] = event
        elif event.kind is SemanticEventKind.PARAMETER_READY:
            parameter_ready_by_connection[event.connection_id] = event
            parameter_readiness_by_edge[
                _connection_edge(event.connection_id)
            ] = event
        elif event.kind is SemanticEventKind.PARAMETER_UPDATE:
            parameter_readiness_by_edge[
                _connection_edge(event.connection_id)
            ] = event
        elif event.kind is SemanticEventKind.CONNECTION_WITHDRAW:
            withdrawal_by_edge[_connection_edge(event.connection_id)] = event
        elif event.kind in {
            SemanticEventKind.ZONE_SERVICE_ASSIGN,
            SemanticEventKind.ZONE_SERVICE_CHANGE,
        }:
            zone_readiness[event.service_key] = event
        elif event.kind is SemanticEventKind.ZONE_SERVICE_UNASSIGN:
            zone_unassignments[event.service_key] = event
        elif event.kind in {
            SemanticEventKind.ROUTE_REACHABLE,
            SemanticEventKind.ROUTE_CHANGE,
        }:
            route_readiness[event.route_demand] = event
        elif event.kind is SemanticEventKind.ROUTE_WITHDRAW:
            route_withdrawals[event.route_demand] = event
        elif event.kind is SemanticEventKind.ROUTE_UNREACHABLE:
            route_unreachable[event.route_demand] = event

    precedences: list[SemanticEventPrecedence] = []
    for identifier, establish in establishment_by_connection.items():
        precedences.append(
            SemanticEventPrecedence(
                establish, parameter_ready_by_connection[identifier]
            )
        )
    changes_by_demand = {
        value.demand: value for value in transition.route_outcome_transitions
    }
    for demand, readiness in route_readiness.items():
        route_change = changes_by_demand[demand]
        assert isinstance(route_change.current, ReachableRouteOutcome)
        for edge in _path_edges(route_change.current.path):
            parameter_readiness = parameter_readiness_by_edge.get(edge)
            if parameter_readiness is not None:
                precedences.append(
                    SemanticEventPrecedence(parameter_readiness, readiness)
                )
        if isinstance(demand.target, ZoneRouteDemandTarget):
            zone_ready = zone_readiness.get(demand.target.service_key)
            if zone_ready is not None:
                precedences.append(
                    SemanticEventPrecedence(zone_ready, readiness)
                )
        old_withdrawal = route_withdrawals.get(demand)
        if old_withdrawal is not None:
            precedences.append(
                SemanticEventPrecedence(readiness, old_withdrawal)
            )
    for demand, withdrawal in route_withdrawals.items():
        route_change = changes_by_demand[demand]
        assert isinstance(route_change.previous, ReachableRouteOutcome)
        for edge in _path_edges(route_change.previous.path):
            connection_withdrawal = withdrawal_by_edge.get(edge)
            if connection_withdrawal is not None:
                precedences.append(
                    SemanticEventPrecedence(withdrawal, connection_withdrawal)
                )
        unreachable = route_unreachable.get(demand)
        if unreachable is not None:
            precedences.append(
                SemanticEventPrecedence(withdrawal, unreachable)
            )
        if isinstance(demand.target, ZoneRouteDemandTarget):
            unassignment = zone_unassignments.get(demand.target.service_key)
            if unassignment is not None:
                precedences.append(
                    SemanticEventPrecedence(withdrawal, unassignment)
                )
    for zone_id, unassignment in zone_unassignments.items():
        readiness = zone_readiness.get(zone_id)
        if readiness is not None:
            precedences.append(
                SemanticEventPrecedence(readiness, unassignment)
            )
    return tuple(precedences)


def _validate_required_ordering(
    events: tuple[SemanticEventReference, ...],
    actual: tuple[SemanticEventPrecedence, ...],
    required: tuple[SemanticEventPrecedence, ...],
) -> None:
    successors = {value: set() for value in events}
    for precedence in actual:
        successors[precedence.predecessor].add(precedence.successor)
    # The built-in policy returns these exact edges. Only missing direct edges
    # require reachability, grouped by predecessor for custom policies.
    missing_by_source = {}
    for precedence in required:
        if precedence.successor not in successors[precedence.predecessor]:
            missing_by_source.setdefault(precedence.predecessor, set()).add(precedence.successor)
    for source, targets in missing_by_source.items():
        pending = [source]
        visited = set()
        while pending and not targets <= visited:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(successors[current] - visited)
        if not targets <= visited:
            raise ValueError('CoordinationPolicy omitted required semantic event ordering')


def _path_edges(path) -> tuple[tuple, ...]:
    return tuple(
        _canonical_edge(first, second)
        for first, second in zip(path, path[1:], strict=False)
    )


def _connection_edge(identifier: OperationalConnectionId | None) -> tuple:
    if not isinstance(identifier, OperationalConnectionId):
        raise TypeError("Connection event requires an OperationalConnectionId")
    return _canonical_edge(identifier.endpoint_a, identifier.endpoint_b)


def _canonical_edge(first, second) -> tuple:
    return (
        (first, second)
        if _node_key(first) < _node_key(second)
        else (second, first)
    )


def _event_semantic_key(event: SemanticEventReference) -> tuple:
    identity = (
        _connection_key(event.connection_id)
        if event.connection_id is not None
        else (
            _route_demand_key(event.route_demand)
            if event.route_demand is not None
            else (event.service_key,)
        )
    )
    return event.kind.value, identity


_EVENT_ORDER = {
    SemanticEventKind.CONNECTION_ESTABLISH: 0,
    SemanticEventKind.PARAMETER_READY: 1,
    SemanticEventKind.PARAMETER_UPDATE: 2,
    SemanticEventKind.ZONE_SERVICE_ASSIGN: 3,
    SemanticEventKind.ZONE_SERVICE_CHANGE: 3,
    SemanticEventKind.ROUTE_REACHABLE: 4,
    SemanticEventKind.ROUTE_CHANGE: 4,
    SemanticEventKind.ROUTE_WITHDRAW: 5,
    SemanticEventKind.ROUTE_UNREACHABLE: 6,
    SemanticEventKind.ZONE_SERVICE_UNASSIGN: 7,
    SemanticEventKind.CONNECTION_WITHDRAW: 8,
}


def _event_order_key(event: SemanticEventReference) -> tuple:
    return _EVENT_ORDER[event.kind], _event_semantic_key(event)[1]

def _precedence_key(value: SemanticEventPrecedence) -> tuple:
    return _event_order_key(value.predecessor), _event_order_key(value.successor)


def _validate_acyclic(
    events: tuple[SemanticEventReference, ...],
    precedences: tuple[SemanticEventPrecedence, ...],
) -> None:
    successors = {value: set() for value in events}
    indegree = {value: 0 for value in events}
    for precedence in precedences:
        predecessor = precedence.predecessor
        successor = precedence.successor
        if successor not in successors[predecessor]:
            successors[predecessor].add(successor)
            indegree[successor] += 1
    pending = [value for value, count in indegree.items() if count == 0]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                pending.append(successor)
    if visited != len(events):
        raise ValueError("semantic event precedence must be acyclic")


def _connection_key(identifier: OperationalConnectionId | None) -> tuple:
    if not isinstance(identifier, OperationalConnectionId):
        raise TypeError("Connection event requires an OperationalConnectionId")
    return (
        identifier.kind.value,
        _node_key(identifier.endpoint_a),
        _node_key(identifier.endpoint_b),
    )


def _route_demand_key(demand: RouteDemand | None) -> tuple:
    if not isinstance(demand, RouteDemand):
        raise TypeError("Route event requires a RouteDemand")
    target = demand.target
    target_key = (
        ("G", _node_key(target.ground_station_id))
        if isinstance(target, GroundStationRouteDemandTarget)
        else ("Z", target.zone_id)
    )
    return _node_key(demand.source), demand.target.network_scope, target_key


def _node_key(node_id) -> tuple:
    return node_id.asn, node_id.entity_kind, node_id.entity_id
