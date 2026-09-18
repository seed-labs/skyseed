"""Shared backend action values and the single activation phase rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    EffectiveConnectionParameters,
    OperationalConnection,
    OperationalConnectionId,
    RouteDemandTarget,
)


class _ResourceOperation(Enum):
    """Classify a backend resource lifecycle intent."""

    ESTABLISH = "establish"
    WITHDRAW = "withdraw"


class _MutationOperation(Enum):
    """Classify a backend mapping mutation."""

    ADD = "add"
    CHANGE = "change"
    REMOVE = "remove"


class _ZoneEdgePhase(Enum):
    """Locate a Zone service mutation around atomic route publication."""

    READINESS = "readiness"
    RETIREMENT = "retirement"


@dataclass(frozen=True, slots=True)
class _AttachmentDemand:
    connection_id: OperationalConnectionId
    source: NodeId
    target: NodeId


@dataclass(frozen=True, slots=True)
class _ResourceIntent:
    nominal_time: datetime
    operation: _ResourceOperation
    connection_id: OperationalConnectionId
    attachments: tuple[_AttachmentDemand, _AttachmentDemand]

    def __post_init__(self) -> None:
        identifier = self.connection_id
        expected = (
            _AttachmentDemand(
                identifier,
                identifier.endpoint_a,
                identifier.endpoint_b,
            ),
            _AttachmentDemand(
                identifier,
                identifier.endpoint_b,
                identifier.endpoint_a,
            ),
        )
        if self.attachments != expected:
            raise ValueError("resource attachments differ from Connection endpoints")


@dataclass(frozen=True, slots=True)
class _QdiscIntent:
    nominal_time: datetime
    connection_id: OperationalConnectionId
    source: NodeId
    target: NodeId
    bandwidth_bps: int
    delay_us: int
    packet_loss_ppm: int
    reason: str

    def __post_init__(self) -> None:
        identifier = self.connection_id
        if (self.source, self.target) not in {
            (identifier.endpoint_a, identifier.endpoint_b),
            (identifier.endpoint_b, identifier.endpoint_a),
        }:
            raise ValueError("qdisc direction differs from Connection endpoints")
        if (
            isinstance(self.bandwidth_bps, bool)
            or not isinstance(self.bandwidth_bps, int)
            or self.bandwidth_bps <= 0
            or isinstance(self.delay_us, bool)
            or not isinstance(self.delay_us, int)
            or self.delay_us < 0
            or isinstance(self.packet_loss_ppm, bool)
            or not isinstance(self.packet_loss_ppm, int)
            or not 0 <= self.packet_loss_ppm <= 1_000_000
        ):
            raise ValueError("qdisc parameters are outside their canonical ranges")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("qdisc reason must be a non-empty string")


@dataclass(frozen=True, slots=True)
class _ForwardingKey:
    source: NodeId
    target: RouteDemandTarget


@dataclass(frozen=True, slots=True)
class _ForwardingBinding:
    route_key: _ForwardingKey
    next_hop: NodeId
    connection_id: OperationalConnectionId
    packet_prefix: str | None


@dataclass(frozen=True, slots=True)
class _ForwardingIntent:
    nominal_time: datetime
    operation: _MutationOperation
    route_key: _ForwardingKey
    previous: _ForwardingBinding | None
    current: _ForwardingBinding | None

    def __post_init__(self) -> None:
        if self.previous is not None and self.previous.route_key != self.route_key:
            raise ValueError("previous forwarding binding uses another key")
        if self.current is not None and self.current.route_key != self.route_key:
            raise ValueError("current forwarding binding uses another key")
        if self.operation is _MutationOperation.ADD:
            valid = self.previous is None and self.current is not None
        elif self.operation is _MutationOperation.CHANGE:
            valid = (
                self.previous is not None
                and self.current is not None
                and self.previous != self.current
            )
        else:
            valid = self.previous is not None and self.current is None
        if not valid:
            raise ValueError("forwarding mutation operation differs from its values")


@dataclass(frozen=True, slots=True)
class _ZoneEdgeState:
    zone_id: str
    packet_prefix: str
    service_satellite_id: NodeId
    network_scope: str = ""

    @property
    def service_key(self) -> tuple[str, str]:
        return self.network_scope, self.zone_id


@dataclass(frozen=True, slots=True)
class _ZoneEdgeIntent:
    nominal_time: datetime
    operation: _MutationOperation
    phase: _ZoneEdgePhase
    zone_id: str
    packet_prefix: str
    previous_service_satellite_id: NodeId | None
    service_satellite_id: NodeId | None
    network_scope: str = ""

    def __post_init__(self) -> None:
        previous = self.previous_service_satellite_id
        current = self.service_satellite_id
        if previous == current:
            raise ValueError("Zone edge mutation must change its service Satellite")
        if self.phase is _ZoneEdgePhase.READINESS:
            valid = current is not None and self.operation is (
                _MutationOperation.ADD
                if previous is None
                else _MutationOperation.CHANGE
            )
        else:
            valid = previous is not None and self.operation is (
                _MutationOperation.REMOVE
                if current is None
                else _MutationOperation.CHANGE
            )
        if not valid:
            raise ValueError("Zone edge phase differs from its semantic mutation")

    @property
    def service_key(self) -> tuple[str, str]:
        return self.network_scope, self.zone_id


@dataclass(frozen=True, slots=True)
class _IntentReference:
    domain: str
    index: int


@dataclass(frozen=True, slots=True)
class _OrderingConstraint:
    predecessor: _IntentReference
    successor: _IntentReference
    reason: str


@dataclass(frozen=True, slots=True)
class _ResourceState:
    connection: OperationalConnection


@dataclass(frozen=True, slots=True)
class _QdiscState:
    connection_id: OperationalConnectionId
    source: NodeId
    target: NodeId
    parameters: EffectiveConnectionParameters


@dataclass(frozen=True, slots=True)
class _IntendedState:
    resources: tuple[_ResourceState, ...]
    qdiscs: tuple[_QdiscState, ...]
    forwarding: tuple[_ForwardingBinding, ...]
    zone_edges: tuple[_ZoneEdgeState, ...]
    zone_prefixes: tuple[tuple[tuple[str, str], str], ...]


@dataclass(frozen=True, slots=True)
class _PhysicalWorldProjection:
    """Retain the static identities required by the detached BPF runtime."""

    node_ids: tuple[NodeId, ...]
    zone_ids: tuple[str, ...]
    network_scopes: tuple[str, ...] = ("",)
    @property
    def service_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((scope, zone) for scope in self.network_scopes for zone in self.zone_ids)

    def __post_init__(self) -> None:
        if not self.network_scopes or tuple(sorted(set(self.network_scopes))) != self.network_scopes:
            raise ValueError("network scopes must be nonempty, unique and canonical")
        from skyseed_v2.shared.identifier import validate_identifier
        for scope in self.network_scopes:
            if not isinstance(scope, str):
                raise TypeError("network scope must be a string")
            if scope:
                validate_identifier(scope, "network_scope")
        if len(self.network_scopes) > 1 and "" in self.network_scopes:
            raise ValueError("scoped and unscoped service cannot be mixed")
        nodes = tuple(self.node_ids)
        zones = tuple(self.zone_ids)
        if (
            not nodes
            or any(not isinstance(value, NodeId) for value in nodes)
            or len(nodes) != len(set(nodes))
        ):
            raise ValueError("PhysicalWorld projection nodes must be unique NodeIds")
        if (
            any(not isinstance(value, str) or not value for value in zones)
            or len(zones) != len(set(zones))
        ):
            raise ValueError("PhysicalWorld projection Zones must be unique identifiers")


def _intent_phase(intent: object) -> int:
    if isinstance(intent, _ResourceIntent):
        return 0 if intent.operation is _ResourceOperation.ESTABLISH else 5
    if isinstance(intent, _QdiscIntent):
        return 1
    if (
        isinstance(intent, _ZoneEdgeIntent)
        and intent.phase is _ZoneEdgePhase.READINESS
    ):
        return 2
    if isinstance(intent, _ForwardingIntent):
        return 3
    if isinstance(intent, _ZoneEdgeIntent):
        return 4
    raise TypeError("unknown compiled BPF intent")
