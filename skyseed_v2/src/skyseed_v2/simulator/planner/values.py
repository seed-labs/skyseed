"""Define the authoritative public planning, Plan, and evidence values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
import json

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.shared.time import normalize_utc_datetime


class OperationalConnectionKind(Enum):
    """Classify one planned operational Connection.

    Layer: public immutable Simulator planning identity component. It is
    public because Models, Plans, and Compiler consumers share the canonical
    Connection classification. It is not a physical-candidate category,
    terminal identity, or backend link type.
    """

    INTER_SATELLITE = "inter-satellite"
    GROUND_STATION_ACCESS = "ground-station-access"


@dataclass(frozen=True, slots=True)
class OperationalConnectionId:
    """Identify one planned undirected operational Connection.

    Layer: public immutable Simulator planning identity. It is public because
    operational values, Plan transitions, and downstream compilation need one
    stable Connection identity. It is not a physical candidate, realized
    resource identity, or timestamp.
    """

    kind: OperationalConnectionKind
    endpoint_a: NodeId
    endpoint_b: NodeId

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OperationalConnectionKind):
            raise TypeError("kind must be an OperationalConnectionKind")
        _require_node_id(self.endpoint_a, "endpoint_a")
        _require_node_id(self.endpoint_b, "endpoint_b")
        if self.endpoint_a == self.endpoint_b:
            raise ValueError("Connection endpoints must be distinct")
        kinds = {self.endpoint_a.entity_kind, self.endpoint_b.entity_kind}
        if self.kind is OperationalConnectionKind.INTER_SATELLITE:
            if kinds != {"satellite"}:
                raise ValueError("an ISL must connect two Satellites")
        elif kinds != {"satellite", "ground-station"}:
            raise ValueError(
                "a GroundStation access must connect one GroundStation and "
                "one Satellite"
            )
        if _node_key(self.endpoint_b) < _node_key(self.endpoint_a):
            first, second = self.endpoint_b, self.endpoint_a
            object.__setattr__(self, "endpoint_a", first)
            object.__setattr__(self, "endpoint_b", second)


@dataclass(frozen=True, slots=True)
class EffectiveConnectionParameters:
    """Hold one direction's complete planned effective parameters.

    Layer: public immutable Simulator planning value. It is public because
    Parameter Models, OperationalConnections, Plans, and Compiler consumers
    share the complete effective decision. It is not physical feasibility,
    allocation evidence, or a backend qdisc command.
    """

    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.bandwidth_bps, bool)
            or not isinstance(self.bandwidth_bps, int)
            or self.bandwidth_bps <= 0
        ):
            raise ValueError("bandwidth_bps must be a positive integer")
        if (
            isinstance(self.delay_us, bool)
            or not isinstance(self.delay_us, int)
            or self.delay_us < 0
        ):
            raise ValueError("delay_us must be a non-negative integer")
        if (
            isinstance(self.packet_loss_ppm, bool)
            or not isinstance(self.packet_loss_ppm, int)
            or not 0 <= self.packet_loss_ppm <= 1_000_000
        ):
            raise ValueError("packet_loss_ppm must be between 0 and 1000000")


@dataclass(frozen=True, slots=True)
class OperationalConnection:
    """Represent one established Connection with complete effective state.

    Layer: public immutable Simulator planned-operational value. It is public
    because Planner output, Plan transitions, and Compiler replay share its
    complete effective state. It is not a physical candidate, acquiring
    selection, or realized resource.
    """

    connection_id: OperationalConnectionId
    endpoint_a_to_b: EffectiveConnectionParameters
    endpoint_b_to_a: EffectiveConnectionParameters

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        if not isinstance(
            self.endpoint_a_to_b, EffectiveConnectionParameters
        ):
            raise TypeError(
                "endpoint_a_to_b must be EffectiveConnectionParameters"
            )
        if not isinstance(
            self.endpoint_b_to_a, EffectiveConnectionParameters
        ):
            raise TypeError(
                "endpoint_b_to_a must be EffectiveConnectionParameters"
            )


@dataclass(frozen=True, slots=True)
class ZoneServiceAssignment:
    """Represent one Zone as explicitly served or explicitly unserved.

    Layer: public immutable Simulator planned-operational value. It is public
    because Zone Models, PlannedOperationalState, and Route planning share the
    explicit served/unserved decision. It is not physical Zone visibility, a
    candidate, Route, or backend address.
    """

    zone_id: str
    satellite_id: NodeId | None
    network_scope: str = ""

    def __post_init__(self) -> None:
        _validate_network_scope(self.network_scope)
        object.__setattr__(
            self, "zone_id", validate_identifier(self.zone_id, "zone_id")
        )
        if self.satellite_id is not None:
            _require_node_kind(
                self.satellite_id, "satellite", "satellite_id"
            )

    @property
    def is_served(self) -> bool:
        return self.satellite_id is not None

    @property
    def service_key(self) -> tuple[str, str]:
        return self.network_scope, self.zone_id


@dataclass(frozen=True, slots=True)
class GroundStationRouteDemandTarget:
    """Identify one planned GroundStation Route demand destination.

    Layer: public immutable Simulator Route-planning value. It is public
    because callers and Route Models need a stable typed demand target. It is
    not a service assignment, selected path, or backend label.
    """

    ground_station_id: NodeId
    network_scope: str = ""

    def __post_init__(self) -> None:
        _validate_network_scope(self.network_scope)
        _require_node_kind(
            self.ground_station_id,
            "ground-station",
            "ground_station_id",
        )


@dataclass(frozen=True, slots=True)
class SatelliteRouteDemandTarget:
    """Identify a Satellite's stable node identity as a Route destination.

    Layer: public immutable Simulator Route-planning value. This names a
    Satellite, not a Zone service, next hop, packet prefix or backend resource.
    """

    satellite_id: NodeId
    network_scope: str = ""

    def __post_init__(self) -> None:
        _validate_network_scope(self.network_scope)
        _require_node_kind(self.satellite_id, "satellite", "satellite_id")


@dataclass(frozen=True, slots=True)
class ZoneRouteDemandTarget:
    """Identify one planned Zone Route demand destination.

    Layer: public immutable Simulator Route-planning value. It is public
    because callers and Route Models need a stable typed Zone demand target.
    It is not a serving Satellite, physical candidate, path, or backend label.
    """

    zone_id: str
    network_scope: str = ""

    def __post_init__(self) -> None:
        _validate_network_scope(self.network_scope)
        object.__setattr__(
            self, "zone_id", validate_identifier(self.zone_id, "zone_id")
        )
    @property
    def service_key(self) -> tuple[str, str]:
        return self.network_scope, self.zone_id


RouteDemandTarget = GroundStationRouteDemandTarget | SatelliteRouteDemandTarget | ZoneRouteDemandTarget


@dataclass(frozen=True, slots=True)
class RouteDemand:
    """Identify one active source-to-target planning demand.

    Layer: public immutable Simulator Route-planning value. It is public
    because composition roots, Route Models, outcomes, and Plan transitions
    share the bounded demand identity. It is not a complete demand matrix,
    selected path, or backend forwarding key.
    """

    source: NodeId
    target: RouteDemandTarget

    def __post_init__(self) -> None:
        _require_node_id(self.source, "source")
        if not isinstance(
            self.target,
            (GroundStationRouteDemandTarget, SatelliteRouteDemandTarget, ZoneRouteDemandTarget),
        ):
            raise TypeError("target must be a supported Route demand target")


class RouteUnreachableReason(Enum):
    """Explain one explicitly planned unreachable Route demand.

    Layer: public immutable Simulator Route-planning classification. It is
    public because Planner, Plan, Compiler, and Traffic must preserve explicit
    planned unreachability. It is not an exception, missing-map inference,
    backend failure, or physical visibility fact.
    """

    NO_ELIGIBLE_PATH = "NO_ELIGIBLE_PATH"
    ZONE_UNSERVED = "ZONE_UNSERVED"


@dataclass(frozen=True, slots=True)
class ReachableRouteOutcome:
    """Carry a reachable demand's deterministic next hop and backing path.

    Layer: public immutable Simulator planned-operational value. It is public
    because Planner state, Plans, Compiler, and Traffic share the explicit
    reachable outcome. It is not a candidate alternate, traffic allocation,
    or backend forwarding command.
    """

    demand: RouteDemand
    next_hop: NodeId
    path: tuple[NodeId, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.demand, RouteDemand):
            raise TypeError("demand must be a RouteDemand")
        _require_node_id(self.next_hop, "next_hop")
        path = tuple(self.path)
        if len(path) < 2 or path[0] != self.demand.source:
            raise ValueError("reachable path must begin at its source")
        if path[1] != self.next_hop:
            raise ValueError("next_hop must be the second path node")
        if len(set(path)) != len(path):
            raise ValueError("reachable path must be loop-free")
        if any(not isinstance(value, NodeId) for value in path):
            raise TypeError("path must contain NodeId values")
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class UnreachableRouteOutcome:
    """Carry one active demand's explicit planned unreachability.

    Layer: public immutable Simulator planned-operational value. It is public
    because Planner state, Plans, Compiler, and Traffic share explicit planned
    unreachability. It is not map omission, an exception, physical visibility,
    or a realized backend failure.
    """

    demand: RouteDemand
    reason: RouteUnreachableReason

    def __post_init__(self) -> None:
        if not isinstance(self.demand, RouteDemand):
            raise TypeError("demand must be a RouteDemand")
        if not isinstance(self.reason, RouteUnreachableReason):
            raise TypeError("reason must be a RouteUnreachableReason")


@dataclass(frozen=True, slots=True)
class LogicalExit:
    """A directed operational connection endpoint, independent of any backend."""
    connection: OperationalConnectionId
    source: NodeId

    def __post_init__(self):
        if not isinstance(self.connection, OperationalConnectionId):
            raise TypeError("logical exit requires an OperationalConnectionId")
        if self.source not in (self.connection.endpoint_a, self.connection.endpoint_b):
            raise ValueError("logical exit source must be a connection endpoint")

    @property
    def peer(self):
        return (self.connection.endpoint_b if self.source == self.connection.endpoint_a
                else self.connection.endpoint_a)


@dataclass(frozen=True, slots=True, init=False)
class PolicyRecord:
    """Immutable named scalar/reference/record values; layout is declared separately."""
    fields: tuple

    def __init__(self, values=(), **fields):
        from collections.abc import Mapping
        if isinstance(values, PolicyRecord):
            values = values.fields
        pairs = tuple(values.items()) if isinstance(values, Mapping) else tuple(values)
        values = dict(pairs)
        if len(values) != len(pairs) or values.keys() & fields.keys():
            raise ValueError("duplicate policy field")
        values.update(fields)
        def freeze(value):
            if isinstance(value, Mapping):
                return PolicyRecord(value)
            if type(value) in (int, bool) or isinstance(value, (NodeId, LogicalExit, PolicyRecord)):
                return value
            raise TypeError("policy values support integers, booleans, logical references and records")
        if any(not isinstance(k, str) or not k.isidentifier() for k in values):
            raise ValueError("policy field names must be identifiers")
        object.__setattr__(self, "fields", tuple(sorted((k, freeze(v)) for k, v in values.items())))

    def __getitem__(self, name):
        return dict(self.fields)[name]


class RouteDisposition(Enum):
    FORWARD = "forward"
    DROP = "drop"
    PASS = "pass"


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    exit: LogicalExit
    data: PolicyRecord = field(default_factory=PolicyRecord)

    def __post_init__(self):
        if not isinstance(self.exit, LogicalExit):
            raise TypeError("candidate exit must be a LogicalExit")
        object.__setattr__(self, "data", PolicyRecord(self.data))


@dataclass(frozen=True, slots=True)
class LocalRouteOutcome:
    """One explicit node/target arrangement; no end-to-end reachability claim.

    Empty forwarding candidates are allowed. PASS requests Linux processing,
    not confirmed delivery. Returning a result replaces all previous outcomes.
    """
    demand: RouteDemand
    disposition: RouteDisposition = RouteDisposition.FORWARD
    candidates: tuple[RouteCandidate, ...] = ()
    data: PolicyRecord = field(default_factory=PolicyRecord)

    def __post_init__(self):
        if not isinstance(self.demand, RouteDemand) or not isinstance(self.disposition, RouteDisposition):
            raise TypeError("local arrangement requires a demand and RouteDisposition")
        candidates = tuple(self.candidates)
        if any(not isinstance(c, RouteCandidate) for c in candidates):
            raise TypeError("candidates must contain RouteCandidate values")
        if any(c.exit.source != self.demand.source for c in candidates):
            raise ValueError("candidate exits must belong to the arrangement source")
        if self.disposition is not RouteDisposition.FORWARD and candidates:
            raise ValueError("only forwarding arrangements have candidates")
        if len({c.exit for c in candidates}) != len(candidates):
            raise ValueError("duplicate candidate exit")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "data", PolicyRecord(self.data))


def _policy_material(value):
    if isinstance(value, PolicyRecord):
        return {"record": {k: _policy_material(v) for k, v in value.fields}}
    if isinstance(value, NodeId):
        return {"node": _node_material(value)}
    if isinstance(value, LogicalExit):
        return {"exit": {"connection": _connection_id_material(value.connection), "source": _node_material(value.source)}}
    return value


def _policy_references(value):
    if isinstance(value, PolicyRecord):
        for _, member in value.fields:
            yield from _policy_references(member)
    elif isinstance(value, (NodeId, LogicalExit)):
        yield value


def _outcome_edges(outcome):
    if isinstance(outcome, ReachableRouteOutcome):
        return _path_edges(outcome.path)
    if isinstance(outcome, LocalRouteOutcome):
        exits = [c.exit for c in outcome.candidates]
        exits.extend(v for record in (outcome.data, *(c.data for c in outcome.candidates))
                     for v in _policy_references(record) if isinstance(v, LogicalExit))
        return tuple(dict.fromkeys(_connection_edge(v.connection) for v in exits))
    return ()


RouteOutcome = ReachableRouteOutcome | UnreachableRouteOutcome | LocalRouteOutcome



@dataclass(frozen=True, slots=True)
class PlannedOperationalState:
    """One immutable public-shaped semantic state for an exact timestamp.

    Layer: public immutable Simulator semantic state. It is public because
    Planner inspection, Plan replay, Compiler provenance, and qualification
    share one authoritative operational-state contract. It is not a Plan,
    PhysicalPositionSnapshot, candidate collection, acquisition store, Model cache,
    publication buffer, Tool schema, or realized runtime state.

    It contains only complete operational outputs. PhysicalPositionSnapshot, physical
    candidates, selected/acquiring records, caches, buffers and evidence are
    intentionally absent.
    """

    timestamp: datetime
    operational_connections: tuple[OperationalConnection, ...] = ()
    zone_service_assignments: tuple[ZoneServiceAssignment, ...] = ()
    route_outcomes: tuple[RouteOutcome, ...] = ()

    _fingerprint: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timestamp",
            normalize_utc_datetime(self.timestamp, "timestamp"),
        )
        _set_state_collections(self, None, self.operational_connections,
                               self.zone_service_assignments, self.route_outcomes)


@dataclass(frozen=True, slots=True)
class ZoneServiceTransition:
    """Carry one replay-complete Zone service assignment change.

    Layer: public immutable Simulator Plan-transition value. It is public
    because Planner, Plan replay, and Compiler lowering share the exact
    previous/current assignment. It is not a physical Zone candidate, complete
    Zone state, or backend edge mutation.
    """

    zone_id: str
    previous_satellite_id: NodeId | None
    current_satellite_id: NodeId | None
    network_scope: str = ""

    def __post_init__(self) -> None:
        _validate_network_scope(self.network_scope)
        object.__setattr__(
            self, "zone_id", validate_identifier(self.zone_id, "zone_id")
        )
        for field_name in (
            "previous_satellite_id",
            "current_satellite_id",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, NodeId)
                or value.entity_kind != "satellite"
            ):
                raise TypeError(
                    f"{field_name} must identify a Satellite or be None"
                )

    @property
    def service_key(self) -> tuple[str, str]:
        return self.network_scope, self.zone_id


@dataclass(frozen=True, slots=True)
class RouteOutcomeTransition:
    """Carry one replay-complete Route outcome change.

    Layer: public immutable Simulator Plan-transition value. It is public
    because Planner, Plan replay, Compiler, and Traffic share the exact
    previous/current outcome. It is not a Route recomputation request,
    forwarding command, or complete Route table.
    """

    demand: RouteDemand
    previous: RouteOutcome | None
    current: RouteOutcome | None

    def __post_init__(self) -> None:
        if not isinstance(self.demand, RouteDemand):
            raise TypeError("demand must be a RouteDemand")
        for field_name in ("previous", "current"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(
                value, (ReachableRouteOutcome, UnreachableRouteOutcome, LocalRouteOutcome)
            ):
                raise TypeError(
                    f"{field_name} must be a Route outcome or None"
                )
            if value is not None and value.demand != self.demand:
                raise ValueError(f"{field_name} must belong to demand")
        if self.previous is None and self.current is None:
            raise ValueError(
                "Route transition must have a previous or current outcome"
            )


@dataclass(frozen=True, slots=True)
class PlanTransition:
    """Represent exactly one formal replayable simulation boundary.

    Layer: public immutable Simulator Plan payload. It is public because
    Planner publication, replay, Compiler, Runner scheduling, and detached
    evidence share the same timestamped semantic delta. It is not a complete
    state snapshot, physical frame, backend operation list, or synthetic ID.
    """

    timestamp: datetime
    established_connections: tuple[OperationalConnection, ...] = ()
    withdrawn_connection_ids: tuple[OperationalConnectionId, ...] = ()
    parameter_updated_connections: tuple[OperationalConnection, ...] = ()
    zone_service_transitions: tuple[ZoneServiceTransition, ...] = ()
    route_outcome_transitions: tuple[RouteOutcomeTransition, ...] = ()

    semantic_event_precedences: tuple[SemanticEventPrecedence, ...] | None = None

    _fingerprint: str | None = field(default=None, init=False, repr=False, compare=False)

    _events: tuple | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._initialize()

    @classmethod
    def _from_indexed_precedences(
        cls,
        *,
        timestamp: datetime,
        precedence_indexes: tuple[tuple[int, int], ...],
        established_connections: tuple[OperationalConnection, ...] = (),
        withdrawn_connection_ids: tuple[OperationalConnectionId, ...] = (),
        parameter_updated_connections: tuple[OperationalConnection, ...] = (),
        zone_service_transitions: tuple[ZoneServiceTransition, ...] = (),
        route_outcome_transitions: tuple[RouteOutcomeTransition, ...] = (),
    ) -> PlanTransition:
        """Decode changes and local references into one fully validated value.

        The codec parses index pairs. Shared initialization canonicalizes changes,
        constructs events once, resolves indexes to those exact objects, and
        validates dependencies before this value is returned.
        """
        value = object.__new__(cls)
        for name, content in (
            ("timestamp", timestamp),
            ("established_connections", established_connections),
            ("withdrawn_connection_ids", withdrawn_connection_ids),
            ("parameter_updated_connections", parameter_updated_connections),
            ("zone_service_transitions", zone_service_transitions),
            ("route_outcome_transitions", route_outcome_transitions),
            ("semantic_event_precedences", None),
            ("_events", None),
            ("_fingerprint", None),
        ):
            object.__setattr__(value, name, content)
        value._initialize(precedence_indexes)
        return value

    def _initialize(self, precedence_indexes=None) -> None:
        object.__setattr__(
            self,
            "timestamp",
            normalize_utc_datetime(self.timestamp, "timestamp"),
        )
        established = _ordered_unique(
            self.established_connections,
            OperationalConnection,
            lambda value: _connection_key(value.connection_id),
            "established_connections",
        )
        withdrawn = _ordered_unique(
            self.withdrawn_connection_ids,
            OperationalConnectionId,
            _connection_key,
            "withdrawn_connection_ids",
        )
        updated = _ordered_unique(
            self.parameter_updated_connections,
            OperationalConnection,
            lambda value: _connection_key(value.connection_id),
            "parameter_updated_connections",
        )
        zones = _ordered_unique(
            self.zone_service_transitions,
            ZoneServiceTransition,
            lambda value: value.service_key,
            "zone_service_transitions",
        )
        routes = _ordered_unique(
            self.route_outcome_transitions,
            RouteOutcomeTransition,
            lambda value: _route_demand_key(value.demand),
            "route_outcome_transitions",
        )
        established_ids = {value.connection_id for value in established}
        withdrawn_ids = set(withdrawn)
        updated_ids = {value.connection_id for value in updated}
        if (
            established_ids & withdrawn_ids
            or established_ids & updated_ids
            or withdrawn_ids & updated_ids
        ):
            raise ValueError(
                "a Connection identity may occur in only one transition "
                "category"
            )
        object.__setattr__(self, "established_connections", established)
        object.__setattr__(self, "withdrawn_connection_ids", withdrawn)
        object.__setattr__(self, "parameter_updated_connections", updated)
        object.__setattr__(self, "zone_service_transitions", zones)
        object.__setattr__(self, "route_outcome_transitions", routes)
        events = semantic_event_references(self)
        required = _required_semantic_precedences(self, events)
        proposed = self.semantic_event_precedences
        if precedence_indexes is not None:
            proposed = tuple(_indexed_precedence(events, first, second)
                             for first, second in precedence_indexes)
        elif proposed is None:
            proposed = required
        object.__setattr__(self, "semantic_event_precedences",
            _validate_semantic_precedences(self, events, required, proposed))


    @property
    def route_relevant(self) -> bool:
        """Report topology/service triggers, independent of Model parameter advice."""
        return bool(
            self.established_connections
            or self.withdrawn_connection_ids
            or self.zone_service_transitions
        )


class SemanticEventKind(Enum):
    """Classify a canonical event derived from one PlanTransition.

    Layer: public immutable Simulator planning coordination value. It is public
    because Plan precedence, Compiler, and detached
    inspection share stable event kinds. It is not an independently editable
    event payload, backend phase, or runtime event class.
    """

    CONNECTION_ESTABLISH = "connection-establish"
    PARAMETER_READY = "parameter-ready"
    PARAMETER_UPDATE = "parameter-update"
    ZONE_SERVICE_ASSIGN = "zone-service-assign"
    ZONE_SERVICE_CHANGE = "zone-service-change"
    ZONE_SERVICE_UNASSIGN = "zone-service-unassign"
    ROUTE_LOCAL = "route-local"
    ROUTE_REACHABLE = "route-reachable"
    ROUTE_CHANGE = "route-change"
    ROUTE_WITHDRAW = "route-withdraw"
    ROUTE_UNREACHABLE = "route-unreachable"
    CONNECTION_WITHDRAW = "connection-withdraw"


@dataclass(frozen=True, slots=True)
class SemanticEventReference:
    """Identify one transition-derived event by immutable semantic value.

    Layer: public immutable Simulator planning coordination value. It is public
    because Plan serialization and Compiler lowering need
    references that survive process boundaries. It is not Python object
    identity, an independent event payload, or a backend-intent reference.
    """

    timestamp: datetime
    kind: SemanticEventKind
    connection_id: OperationalConnectionId | None = None
    zone_id: str | None = None
    route_demand: RouteDemand | None = None
    network_scope: str = ""

    def __post_init__(self) -> None:
        _validate_network_scope(self.network_scope)
        object.__setattr__(
            self,
            "timestamp",
            normalize_utc_datetime(self.timestamp, "timestamp"),
        )
        if not isinstance(self.kind, SemanticEventKind):
            raise TypeError("kind must be a SemanticEventKind")
        if self.connection_id is not None and not isinstance(
            self.connection_id, OperationalConnectionId
        ):
            raise TypeError(
                "connection_id must be an OperationalConnectionId or None"
            )
        if self.zone_id is not None:
            object.__setattr__(
                self, "zone_id", validate_identifier(self.zone_id, "zone_id")
            )
        if self.network_scope and self.zone_id is None:
            raise ValueError("event network_scope belongs only to a Zone subject")
        if self.route_demand is not None and not isinstance(
            self.route_demand, RouteDemand
        ):
            raise TypeError("route_demand must be a RouteDemand or None")
        connection_kinds = {
            SemanticEventKind.CONNECTION_ESTABLISH,
            SemanticEventKind.PARAMETER_READY,
            SemanticEventKind.PARAMETER_UPDATE,
            SemanticEventKind.CONNECTION_WITHDRAW,
        }
        zone_kinds = {
            SemanticEventKind.ZONE_SERVICE_ASSIGN,
            SemanticEventKind.ZONE_SERVICE_CHANGE,
            SemanticEventKind.ZONE_SERVICE_UNASSIGN,
        }
        route_kinds = {
            SemanticEventKind.ROUTE_LOCAL,
            SemanticEventKind.ROUTE_REACHABLE,
            SemanticEventKind.ROUTE_CHANGE,
            SemanticEventKind.ROUTE_WITHDRAW,
            SemanticEventKind.ROUTE_UNREACHABLE,
        }
        subjects = (
            self.connection_id is not None,
            self.zone_id is not None,
            self.route_demand is not None,
        )
        if sum(subjects) != 1:
            raise ValueError("an event reference must carry exactly one subject")
        if self.kind in connection_kinds and self.connection_id is None:
            raise ValueError("Connection event requires connection_id")
        if self.kind in zone_kinds and self.zone_id is None:
            raise ValueError("Zone event requires zone_id")
        if self.kind in route_kinds and self.route_demand is None:
            raise ValueError("Route event requires route_demand")

    @property
    def service_key(self) -> tuple[str, str]:
        return self.network_scope, self.zone_id


@dataclass(frozen=True, slots=True)
class SemanticEventPrecedence:
    """Order two canonical semantic event references at one timestamp.

    Layer: public immutable Simulator Plan-coordination value. It is public
    because Transitions own it and Plan/Compiler consumers validate
    the same ordering contract. It is not an event payload, backend-only
    constraint, or cross-timestamp scheduler edge.
    """

    predecessor: SemanticEventReference
    successor: SemanticEventReference

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, SemanticEventReference):
            raise TypeError("predecessor must be a SemanticEventReference")
        if not isinstance(self.successor, SemanticEventReference):
            raise TypeError("successor must be a SemanticEventReference")
        if self.predecessor == self.successor:
            raise ValueError("precedence endpoints must be distinct")
        if self.predecessor.timestamp != self.successor.timestamp:
            raise ValueError("precedence endpoints must have equal timestamps")


def _indexed_precedence(events, predecessor: int, successor: int) -> SemanticEventPrecedence:
    if not (0 <= predecessor < len(events) and 0 <= successor < len(events)):
        raise ValueError("semantic precedence index is outside canonical transition events")
    return SemanticEventPrecedence(events[predecessor], events[successor])


def semantic_event_references(
    transition: PlanTransition,
) -> tuple[SemanticEventReference, ...]:
    """Derive the sole canonical event-reference set for one transition."""

    if not isinstance(transition, PlanTransition):
        raise TypeError("transition must be a PlanTransition")
    if transition._events is not None:
        return transition._events
    timestamp = transition.timestamp
    references: list[SemanticEventReference] = []
    for connection in transition.established_connections:
        identifier = connection.connection_id
        references.extend(
            (
                SemanticEventReference(
                    timestamp,
                    SemanticEventKind.CONNECTION_ESTABLISH,
                    connection_id=identifier,
                ),
                SemanticEventReference(
                    timestamp,
                    SemanticEventKind.PARAMETER_READY,
                    connection_id=identifier,
                ),
            )
        )
    for connection in transition.parameter_updated_connections:
        references.append(
            SemanticEventReference(
                timestamp,
                SemanticEventKind.PARAMETER_UPDATE,
                connection_id=connection.connection_id,
            )
        )
    for change in transition.zone_service_transitions:
        if change.current_satellite_id is not None:
            references.append(
                SemanticEventReference(
                    timestamp,
                    (
                        SemanticEventKind.ZONE_SERVICE_ASSIGN
                        if change.previous_satellite_id is None
                        else SemanticEventKind.ZONE_SERVICE_CHANGE
                    ),
                    zone_id=change.zone_id,
                    network_scope=change.network_scope,
                )
            )
        if change.previous_satellite_id is not None:
            references.append(
                SemanticEventReference(
                    timestamp,
                    SemanticEventKind.ZONE_SERVICE_UNASSIGN,
                    zone_id=change.zone_id,
                    network_scope=change.network_scope,
                )
            )
    for change in transition.route_outcome_transitions:
        old_reachable = isinstance(change.previous, (ReachableRouteOutcome, LocalRouteOutcome))
        new_reachable = isinstance(change.current, (ReachableRouteOutcome, LocalRouteOutcome))
        if new_reachable:
            references.append(
                SemanticEventReference(
                    timestamp,
                    (
                        SemanticEventKind.ROUTE_CHANGE
                        if old_reachable
                        else (SemanticEventKind.ROUTE_LOCAL if isinstance(change.current, LocalRouteOutcome) else SemanticEventKind.ROUTE_REACHABLE)
                    ),
                    route_demand=change.demand,
                )
            )
        if old_reachable:
            references.append(
                SemanticEventReference(
                    timestamp,
                    SemanticEventKind.ROUTE_WITHDRAW,
                    route_demand=change.demand,
                )
            )
        if isinstance(change.current, UnreachableRouteOutcome):
            references.append(
                SemanticEventReference(
                    timestamp,
                    SemanticEventKind.ROUTE_UNREACHABLE,
                    route_demand=change.demand,
                )
            )
    for identifier in transition.withdrawn_connection_ids:
        references.append(
            SemanticEventReference(
                timestamp,
                SemanticEventKind.CONNECTION_WITHDRAW,
                connection_id=identifier,
            )
        )
    ordered = tuple(sorted(references, key=_semantic_event_order_key))
    keys = tuple(_semantic_event_key(value) for value in ordered)
    if len(keys) != len(set(keys)):
        raise ValueError(
            "canonical semantic event references contain a duplicate key/kind"
        )
    object.__setattr__(transition, "_events", ordered)
    return ordered


@dataclass(frozen=True, slots=True)
class Plan:
    """Carry one self-contained semantic delta over contiguous timestamps.

    Layer: public immutable Simulator-to-Compiler handoff. It is public because
    Planner publishes it and Compiler, evidence, Tools, and qualification replay
    consume it without Planner access. It is not a state snapshot, physical
    frame collection, backend operation list, or hidden compilation context.
    """

    plan_id: str
    previous_plan_id: str | None
    physical_world_fingerprint: str
    input_state_fingerprint: str | None
    output_state_fingerprint: str
    observation_interval: timedelta
    start_timestamp: datetime
    end_timestamp: datetime
    transitions: tuple[PlanTransition, ...]

    def __post_init__(self) -> None:
        _require_fingerprint(self.plan_id, "plan_id")
        _require_fingerprint(
            self.physical_world_fingerprint,
            "physical_world_fingerprint",
        )
        if self.input_state_fingerprint is not None:
            _require_fingerprint(
                self.input_state_fingerprint,
                "input_state_fingerprint",
            )
        _require_fingerprint(
            self.output_state_fingerprint,
            "output_state_fingerprint",
        )
        interval, start, end, transitions = (
            _canonical_plan_parts(
                previous_plan_id=self.previous_plan_id,
                input_state_fingerprint=self.input_state_fingerprint,
                observation_interval=self.observation_interval,
                start_timestamp=self.start_timestamp,
                end_timestamp=self.end_timestamp,
                transitions=self.transitions,
            )
        )
        object.__setattr__(self, "start_timestamp", start)
        object.__setattr__(self, "end_timestamp", end)
        object.__setattr__(self, "observation_interval", interval)
        object.__setattr__(self, "transitions", transitions)
        expected_plan_id = _plan_identifier(
            previous_plan_id=self.previous_plan_id,
            physical_world_fingerprint=self.physical_world_fingerprint,
            input_state_fingerprint=self.input_state_fingerprint,
            output_state_fingerprint=self.output_state_fingerprint,
            observation_interval=interval,
            start_timestamp=start,
            end_timestamp=end,
            transitions=transitions,
        )
        if self.plan_id != expected_plan_id:
            raise ValueError("plan_id does not match canonical Plan content")

    @classmethod
    def _create_validated(
        cls,
        *,
        plan_id: str,
        previous_plan_id: str | None,
        physical_world_fingerprint: str,
        input_state_fingerprint: str | None,
        output_state_fingerprint: str,
        observation_interval: timedelta,
        start_timestamp: datetime,
        end_timestamp: datetime,
        transitions: tuple[PlanTransition, ...],
    ) -> Plan:
        """Create from parts already validated by the Planner publication path."""

        instance = object.__new__(cls)
        for name, value in (
            ("plan_id", plan_id),
            ("previous_plan_id", previous_plan_id),
            ("physical_world_fingerprint", physical_world_fingerprint),
            ("input_state_fingerprint", input_state_fingerprint),
            ("output_state_fingerprint", output_state_fingerprint),
            ("observation_interval", observation_interval),
            ("start_timestamp", start_timestamp),
            ("end_timestamp", end_timestamp),
            ("transitions", transitions),
        ):
            object.__setattr__(instance, name, value)
        return instance


def _canonical_plan_parts(
    *,
    previous_plan_id: str | None,
    input_state_fingerprint: str | None,
    observation_interval: timedelta,
    start_timestamp: datetime,
    end_timestamp: datetime,
    transitions: tuple[PlanTransition, ...],
) -> tuple[
    timedelta,
    datetime,
    datetime,
    tuple[PlanTransition, ...],
]:
    if previous_plan_id is not None:
        _require_fingerprint(previous_plan_id, "previous_plan_id")
    interval = _require_observation_interval(observation_interval)
    start = normalize_utc_datetime(start_timestamp, "start_timestamp")
    end = normalize_utc_datetime(end_timestamp, "end_timestamp")
    if end < start:
        raise ValueError("end_timestamp cannot precede start_timestamp")
    transition_values = tuple(transitions)
    if not transition_values:
        raise ValueError("transitions must not be empty")
    if any(
        not isinstance(value, PlanTransition) for value in transition_values
    ):
        raise TypeError("transitions must contain PlanTransition values")
    timestamps = tuple(value.timestamp for value in transition_values)
    if any(
        current - previous != interval
        for previous, current in zip(timestamps, timestamps[1:])
    ):
        raise ValueError(
            "adjacent Plan transitions must match observation_interval"
        )
    if timestamps[0] != start:
        raise ValueError("Plan start must equal its first transition timestamp")
    if previous_plan_id is None:
        if input_state_fingerprint is not None:
            raise ValueError("bootstrap Plan cannot have an input fingerprint")
    elif input_state_fingerprint is None:
        raise ValueError("non-bootstrap Plan requires input fingerprint")
    if timestamps[-1] != end:
        raise ValueError("Plan end must equal its last transition timestamp")
    return interval, start, end, transition_values


def create_plan(
    previous_state: PlannedOperationalState | None,
    *,
    previous_plan_id: str | None,
    physical_world_fingerprint: str,
    observation_interval: timedelta,
    start_timestamp: datetime,
    transitions: tuple[PlanTransition, ...],
) -> Plan:
    """Construct and fully replay-validate one canonical Plan."""

    plan, _ = _create_plan_with_output_state(
        previous_state,
        previous_plan_id=previous_plan_id,
        physical_world_fingerprint=physical_world_fingerprint,
        observation_interval=observation_interval,
        start_timestamp=start_timestamp,
        transitions=transitions,
    )
    return plan


def _create_plan_with_output_state(
    previous_state: PlannedOperationalState | None,
    *,
    previous_plan_id: str | None,
    physical_world_fingerprint: str,
    observation_interval: timedelta,
    start_timestamp: datetime,
    transitions: tuple[PlanTransition, ...],
    _constructed_state: PlannedOperationalState | None = None,
) -> tuple[Plan, PlannedOperationalState]:
    """Create one canonical Plan and return its already replayed output state."""

    if previous_state is not None and not isinstance(
        previous_state, PlannedOperationalState
    ):
        raise TypeError(
            "previous_state must be PlannedOperationalState or None"
        )
    if previous_plan_id is None and previous_state is not None:
        raise ValueError("bootstrap Plan cannot have previous_state")
    if previous_plan_id is not None and previous_state is None:
        raise ValueError("non-bootstrap Plan requires previous_state")
    _require_fingerprint(
        physical_world_fingerprint, "physical_world_fingerprint"
    )
    interval = _require_observation_interval(observation_interval)
    start = normalize_utc_datetime(start_timestamp, "start_timestamp")
    if (
        previous_state is not None
        and start != previous_state.timestamp + interval
    ):
        raise ValueError(
            "non-bootstrap Plan must start one observation_interval "
            "after previous state"
        )
    transition_values = tuple(transitions)
    state = _constructed_state
    if state is None:
        state = previous_state
        for transition in transition_values:
            state = apply_plan_transition(state, transition)
    elif not transition_values or state.timestamp != transition_values[-1].timestamp:
        raise ValueError('constructed state must end at the final transition')
    if state is None:  # pragma: no cover - empty tuples fail in Plan validation
        raise ValueError("transitions must not be empty")
    input_fingerprint = (
        None
        if previous_state is None
        else planned_operational_state_fingerprint(previous_state)
    )
    output_fingerprint = planned_operational_state_fingerprint(state)
    end = state.timestamp
    (
        interval,
        start,
        end,
        transition_values,
    ) = _canonical_plan_parts(
        previous_plan_id=previous_plan_id,
        input_state_fingerprint=input_fingerprint,
        observation_interval=interval,
        start_timestamp=start,
        end_timestamp=end,
        transitions=transition_values,
    )
    plan_id = _plan_identifier(
        previous_plan_id=previous_plan_id,
        physical_world_fingerprint=physical_world_fingerprint,
        input_state_fingerprint=input_fingerprint,
        output_state_fingerprint=output_fingerprint,
        observation_interval=interval,
        start_timestamp=start,
        end_timestamp=end,
        transitions=transition_values,
    )
    plan = Plan._create_validated(
        plan_id=plan_id,
        previous_plan_id=previous_plan_id,
        physical_world_fingerprint=physical_world_fingerprint,
        input_state_fingerprint=input_fingerprint,
        output_state_fingerprint=output_fingerprint,
        observation_interval=interval,
        start_timestamp=start,
        end_timestamp=end,
        transitions=transition_values,
    )
    return plan, state


def _set_state_collections(state, previous, connections, assignments, outcomes):
    for name, values, value_type, key in (
        ("operational_connections", connections, OperationalConnection, lambda v: _connection_key(v.connection_id)),
        ("zone_service_assignments", assignments, ZoneServiceAssignment, lambda v: v.service_key),
        ("route_outcomes", outcomes, (ReachableRouteOutcome, UnreachableRouteOutcome, LocalRouteOutcome), lambda v: _route_demand_key(v.demand)),
    ):
        collection = (() if previous is None else getattr(previous, name)) if values is None else _ordered_unique(values, value_type, key, name)
        object.__setattr__(state, name, collection)


def _updated_operational_state(previous, timestamp, connections=None, assignments=None, outcomes=None):
    """Build the next immutable state, validating only collections being replaced."""
    state = object.__new__(PlannedOperationalState)
    object.__setattr__(state, "timestamp", normalize_utc_datetime(timestamp, "timestamp"))
    _set_state_collections(state, previous, connections, assignments, outcomes)
    object.__setattr__(state, "_fingerprint", None)
    return state


def apply_plan_transition(
    previous_state: PlannedOperationalState | None,
    transition: PlanTransition,
) -> PlannedOperationalState:
    """Purely replay one validated transition into its complete next state."""

    if previous_state is not None and not isinstance(
        previous_state, PlannedOperationalState
    ):
        raise TypeError("previous_state must be PlannedOperationalState or None")
    if not isinstance(transition, PlanTransition):
        raise TypeError("transition must be a PlanTransition")
    if (
        previous_state is not None
        and transition.timestamp <= previous_state.timestamp
    ):
        raise ValueError("transition timestamp must follow previous state")

    connections = assignments = outcomes = None
    if transition.established_connections or transition.parameter_updated_connections or transition.withdrawn_connection_ids:
        connections = {
            value.connection_id: value
            for value in (
                ()
                if previous_state is None
                else previous_state.operational_connections
            )
        }
        for connection in transition.established_connections:
            identifier = connection.connection_id
            if identifier in connections:
                raise ValueError("transition establishes an existing Connection")
            connections[identifier] = connection
        for connection in transition.parameter_updated_connections:
            identifier = connection.connection_id
            previous = connections.get(identifier)
            if previous is None:
                raise ValueError("transition updates a missing Connection")
            if previous == connection:
                raise ValueError("transition contains a no-op parameter update")
            connections[identifier] = connection
        for identifier in transition.withdrawn_connection_ids:
            if identifier not in connections:
                raise ValueError("transition withdraws a missing Connection")
            del connections[identifier]

    if transition.zone_service_transitions:
        assignments = {
            value.service_key: value
            for value in (
                ()
                if previous_state is None
                else previous_state.zone_service_assignments
            )
        }
        for change in transition.zone_service_transitions:
            prior = assignments.get(change.service_key)
            if prior is None:
                if change.previous_satellite_id is not None:
                    raise ValueError(
                        "new Zone assignment has a foreign previous value"
                    )
            else:
                if prior.satellite_id != change.previous_satellite_id:
                    raise ValueError(
                        "Zone transition previous value is inconsistent"
                    )
                if prior.satellite_id == change.current_satellite_id:
                    raise ValueError("transition contains a no-op Zone change")
            assignments[change.service_key] = ZoneServiceAssignment(
                change.zone_id, change.current_satellite_id, change.network_scope
            )

    if transition.route_outcome_transitions:
        outcomes = {
            value.demand: value
            for value in (
                () if previous_state is None else previous_state.route_outcomes
            )
        }
        for change in transition.route_outcome_transitions:
            previous = outcomes.get(change.demand)
            if previous != change.previous:
                raise ValueError(
                    "Route transition previous outcome is inconsistent"
                )
            if previous == change.current:
                raise ValueError("transition contains a no-op Route change")
            if change.current is None:
                if previous is None:
                    raise ValueError(
                        "transition withdraws a missing Route outcome"
                    )
                del outcomes[change.demand]
            else:
                outcomes[change.demand] = change.current

    return _updated_operational_state(
        previous_state, transition.timestamp,
        None if connections is None else connections.values(),
        None if assignments is None else assignments.values(),
        None if outcomes is None else outcomes.values(),
    )


def apply_plan(
    previous_state: PlannedOperationalState | None,
    plan: Plan,
    *,
    physical_world_fingerprint: str,
) -> PlannedOperationalState:
    """Replay and validate one Plan against state and PhysicalWorld lineage."""

    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    _require_fingerprint(
        physical_world_fingerprint, "physical_world_fingerprint"
    )
    if plan.physical_world_fingerprint != physical_world_fingerprint:
        raise ValueError("Plan belongs to a different PhysicalWorld")
    if previous_state is None:
        if plan.previous_plan_id is not None:
            raise ValueError("non-bootstrap Plan requires previous state")
        if plan.input_state_fingerprint is not None:
            raise ValueError("bootstrap Plan cannot have input fingerprint")
    else:
        if not isinstance(previous_state, PlannedOperationalState):
            raise TypeError(
                "previous_state must be PlannedOperationalState or None"
            )
        if (
            plan.start_timestamp
            != previous_state.timestamp + plan.observation_interval
        ):
            raise ValueError(
                "Plan start must be one observation_interval after "
                "previous state"
            )
        if (
            planned_operational_state_fingerprint(previous_state)
            != plan.input_state_fingerprint
        ):
            raise ValueError("Plan input state fingerprint mismatch")
    state = previous_state
    for transition in plan.transitions:
        state = apply_plan_transition(state, transition)
    assert state is not None
    if planned_operational_state_fingerprint(state) != plan.output_state_fingerprint:
        raise ValueError("Plan output state fingerprint mismatch")
    return state


def planned_operational_state_fingerprint(
    state: PlannedOperationalState,
) -> str:
    """Return the versioned deterministic semantic-state fingerprint."""

    if not isinstance(state, PlannedOperationalState):
        raise TypeError("state must be a PlannedOperationalState")
    if state._fingerprint is None:
        object.__setattr__(state, '_fingerprint',
            _json_fingerprint(_planned_operational_state_material(state)))
    return state._fingerprint


def plan_transition_fingerprint(transition: PlanTransition) -> str:
    """Return the versioned deterministic transition fingerprint."""

    if not isinstance(transition, PlanTransition):
        raise TypeError("transition must be a PlanTransition")
    if transition._fingerprint is None:
        object.__setattr__(transition, '_fingerprint',
            _json_fingerprint(_plan_transition_material(transition)))
    return transition._fingerprint


def _plan_identifier(
    *,
    previous_plan_id: str | None,
    physical_world_fingerprint: str,
    input_state_fingerprint: str | None,
    output_state_fingerprint: str,
    observation_interval: timedelta,
    start_timestamp: datetime,
    end_timestamp: datetime,
    transitions: tuple[PlanTransition, ...],
) -> str:
    return _json_fingerprint(
        {
            "schema": "skyseed-v2-plan-v6",
            "previous_plan_id": previous_plan_id,
            "physical_world_fingerprint": physical_world_fingerprint,
            "input_state_fingerprint": input_state_fingerprint,
            "output_state_fingerprint": output_state_fingerprint,
            "observation_interval": _timedelta_material(
                observation_interval
            ),
            "start_timestamp": start_timestamp.isoformat(),
            "end_timestamp": end_timestamp.isoformat(),
            "transition_fingerprints": [plan_transition_fingerprint(value) for value in transitions],
        }
    )


def _planned_operational_state_material(
    state: PlannedOperationalState,
) -> dict[str, object]:
    return {
        "schema": "skyseed-v2-planned-operational-state-v4",
        "timestamp": state.timestamp.isoformat(),
        "operational_connections": [
            _connection_material(value) for value in state.operational_connections
        ],
        "zone_service_assignments": [
            {
                "zone_id": value.zone_id,
                "network_scope": value.network_scope,
                "satellite_id": _optional_node_material(value.satellite_id),
            }
            for value in state.zone_service_assignments
        ],
        "route_outcomes": [
            _route_outcome_material(value) for value in state.route_outcomes
        ],
    }


def _plan_transition_material(transition: PlanTransition) -> dict[str, object]:
    # The canonical event tuple is already cached on the transition. The same
    # compact material is used for identity and storage; no event payload list.
    event_indexes = (
        {event: index for index, event in enumerate(semantic_event_references(transition))}
        if transition.semantic_event_precedences else {}
    )
    return {
        "schema": "skyseed-v2-plan-transition-v6",
        "timestamp": transition.timestamp.isoformat(),
        "established_connections": [
            _connection_material(value)
            for value in transition.established_connections
        ],
        "withdrawn_connection_ids": [
            _connection_id_material(value)
            for value in transition.withdrawn_connection_ids
        ],
        "parameter_updated_connections": [
            _connection_material(value)
            for value in transition.parameter_updated_connections
        ],
        "zone_service_transitions": [
            {
                "zone_id": value.zone_id,
                "network_scope": value.network_scope,
                "previous_satellite_id": _optional_node_material(
                    value.previous_satellite_id
                ),
                "current_satellite_id": _optional_node_material(
                    value.current_satellite_id
                ),
            }
            for value in transition.zone_service_transitions
        ],
        "route_outcome_transitions": [
            {
                "demand": _route_demand_material(value.demand),
                "previous": _optional_route_outcome_material(value.previous, include_demand=False),
                "current": _optional_route_outcome_material(value.current, include_demand=False),
            }
            for value in transition.route_outcome_transitions
        ],
        "semantic_event_precedences": [
            [event_indexes[value.predecessor], event_indexes[value.successor]]
            for value in transition.semantic_event_precedences
        ],
    }


def _connection_material(
    connection: OperationalConnection,
) -> dict[str, object]:
    return {
        "connection_id": _connection_id_material(connection.connection_id),
        "endpoint_a_to_b": _parameter_material(connection.endpoint_a_to_b),
        "endpoint_b_to_a": _parameter_material(connection.endpoint_b_to_a),
    }


def _connection_id_material(
    identifier: OperationalConnectionId,
) -> dict[str, object]:
    return {
        "kind": identifier.kind.value,
        "endpoint_a": _node_material(identifier.endpoint_a),
        "endpoint_b": _node_material(identifier.endpoint_b),
    }


def _parameter_material(
    value: EffectiveConnectionParameters,
) -> dict[str, int]:
    return {
        "bandwidth_bps": value.bandwidth_bps,
        "delay_us": value.delay_us,
        "packet_loss_ppm": value.packet_loss_ppm,
    }


def _route_demand_material(demand: RouteDemand) -> dict[str, object]:
    target = demand.target
    return {
        "source": _node_material(demand.source),
        "target": (
            {
                "kind": "ground-station",
                "network_scope": target.network_scope,
                "ground_station_id": _node_material(
                    target.ground_station_id
                ),
            }
            if isinstance(target, GroundStationRouteDemandTarget)
            else {"kind": "satellite", "satellite_id": _node_material(target.satellite_id),
                  "network_scope": target.network_scope}
            if isinstance(target, SatelliteRouteDemandTarget)
            else {"kind": "zone", "zone_id": target.zone_id, "network_scope": target.network_scope}
        ),
    }


def _route_outcome_material(outcome: RouteOutcome, *, include_demand=True) -> dict[str, object]:
    common = {"demand": _route_demand_material(outcome.demand)} if include_demand else {}
    if isinstance(outcome, LocalRouteOutcome):
        return {**common, "kind": "local", "disposition": outcome.disposition.value,
                "data": _policy_material(outcome.data),
                "candidates": [{"exit": _policy_material(c.exit), "data": _policy_material(c.data)} for c in outcome.candidates]}
    if isinstance(outcome, ReachableRouteOutcome):
        return {
            **common,
            "kind": "reachable",
            "next_hop": _node_material(outcome.next_hop),
            "path": [_node_material(value) for value in outcome.path],
        }
    return {
        **common,
        "kind": "unreachable",
        "reason": outcome.reason.value,
    }


def _optional_route_outcome_material(
    outcome: RouteOutcome | None, *, include_demand=True,
) -> dict[str, object] | None:
    return None if outcome is None else _route_outcome_material(outcome, include_demand=include_demand)


def _optional_node_material(node_id: NodeId | None) -> dict[str, object] | None:
    return None if node_id is None else _node_material(node_id)


def _node_material(node_id: NodeId) -> dict[str, object]:
    return {
        "asn": node_id.asn,
        "entity_kind": node_id.entity_kind,
        "entity_id": node_id.entity_id,
    }


def _json_fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _require_fingerprint(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 value"
        )
    return value


def _ordered_unique(values, expected_type, key, field_name: str) -> tuple:
    try:
        copied = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in copied):
        raise TypeError(f"{field_name} contains an invalid value")
    keyed = sorted(((key(value), value) for value in copied), key=lambda item: item[0])
    keys = tuple(identity for identity, _ in keyed)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} contains a duplicate identity")
    return tuple(value for _, value in keyed)


def _route_demand_key(demand: RouteDemand) -> tuple:
    source = _node_key(demand.source)
    if isinstance(demand.target, GroundStationRouteDemandTarget):
        return source, demand.target.network_scope, "G", _node_key(demand.target.ground_station_id)
    if isinstance(demand.target, SatelliteRouteDemandTarget):
        return source, demand.target.network_scope, "S", _node_key(demand.target.satellite_id)
    return source, demand.target.network_scope, "Z", demand.target.zone_id


def _semantic_event_key(event: SemanticEventReference) -> tuple:
    if event.connection_id is not None:
        identity = ("C", _connection_key(event.connection_id))
    elif event.zone_id is not None:
        identity = ("Z", event.service_key)
    else:
        identity = ("R", _route_demand_key(event.route_demand))
    return event.kind.value, identity


_SEMANTIC_EVENT_ORDER = {kind: index for index, kind in enumerate(SemanticEventKind)}


def _semantic_event_order_key(event: SemanticEventReference) -> tuple:
    return event.timestamp, _SEMANTIC_EVENT_ORDER[event.kind], _semantic_event_key(event)[1]


def _precedence_key(value: SemanticEventPrecedence) -> tuple:
    return (
        _semantic_event_order_key(value.predecessor),
        _semantic_event_order_key(value.successor),
    )


def _connection_key(identifier: OperationalConnectionId) -> tuple:
    return (
        identifier.kind.value,
        _node_key(identifier.endpoint_a),
        _node_key(identifier.endpoint_b),
    )


def _require_node_id(node_id: NodeId, field_name: str) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError(f"{field_name} must be a NodeId")


def _require_observation_interval(value: timedelta) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("observation_interval must be a timedelta")
    if value <= timedelta(0):
        raise ValueError("observation_interval must be positive")
    return value


def _timedelta_material(value: timedelta) -> dict[str, int]:
    interval = _require_observation_interval(value)
    return {
        "days": interval.days,
        "seconds": interval.seconds,
        "microseconds": interval.microseconds,
    }


def _require_node_kind(
    node_id: NodeId, expected: str, field_name: str
) -> None:
    _require_node_id(node_id, field_name)
    if node_id.entity_kind != expected:
        raise ValueError(f"{field_name} must identify a {expected}")


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _validate_network_scope(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("network_scope must be a string")
    if value:
        validate_identifier(value, "network_scope")


def _validate_semantic_precedences(transition, canonical_events, required_precedences, proposed_precedences):
    if not isinstance(proposed_precedences, tuple):
        raise TypeError("semantic_event_precedences must be a tuple of SemanticEventPrecedence")
    if any(not isinstance(value, SemanticEventPrecedence) for value in proposed_precedences):
        raise TypeError("semantic_event_precedences must contain only SemanticEventPrecedence values")
    successors = {event: set() for event in canonical_events}
    indegree = {event: 0 for event in canonical_events}
    for value in proposed_precedences:
        first, second = value.predecessor, value.successor
        if first not in successors or second not in successors:
            raise ValueError("precedence endpoints must be canonical event references")
        if second in successors[first]:
            raise ValueError("duplicate semantic precedence")
        successors[first].add(second)
        indegree[second] += 1
    pending = [event for event, degree in indegree.items() if degree == 0]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                pending.append(successor)
    if visited != len(canonical_events):
        raise ValueError("semantic event precedence must be acyclic")
    missing = {}
    for value in required_precedences:
        if value.successor not in successors[value.predecessor]:
            missing.setdefault(value.predecessor, set()).add(value.successor)
    for source, targets in missing.items():
        reached, pending = set(), [source]
        while pending and not targets <= reached:
            current = pending.pop()
            if current not in reached:
                reached.add(current)
                pending.extend(successors[current] - reached)
        if not targets <= reached:
            raise ValueError("dependencies omitted required semantic event ordering")
    return tuple(sorted(proposed_precedences, key=_precedence_key))


def _required_semantic_precedences(
    transition: PlanTransition,
    canonical_events: tuple[SemanticEventReference, ...],
) -> tuple[SemanticEventPrecedence, ...]:
    values = canonical_events

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
            SemanticEventKind.ROUTE_LOCAL,
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
        for edge in _outcome_edges(route_change.current):
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
        for edge in _outcome_edges(route_change.previous):
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
