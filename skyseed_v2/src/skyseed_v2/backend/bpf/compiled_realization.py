"""Define the public BPF compilation result and private realization values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    RouteKey,
)


@dataclass(frozen=True, slots=True)
class _AttachmentDemand:
    connection_id: ConnectionId
    direction: ConnectionDirection
    source: NodeId
    target: NodeId


@dataclass(frozen=True, slots=True)
class _ResourceIntent:
    nominal_time: datetime
    operation: ConnectionTransitionOperation
    connection_id: ConnectionId
    attachments: tuple[_AttachmentDemand, _AttachmentDemand]


@dataclass(frozen=True, slots=True)
class _QdiscIntent:
    nominal_time: datetime
    connection_id: ConnectionId
    direction: ConnectionDirection
    source: NodeId
    target: NodeId
    bandwidth_bps: int | None
    delay_us: int | None
    packet_loss_ppm: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class _ForwardingBinding:
    route_key: RouteKey
    next_hop: NodeId
    connection_id: ConnectionId
    direction: ConnectionDirection
    packet_prefix: str | None


@dataclass(frozen=True, slots=True)
class _ForwardingIntent:
    nominal_time: datetime
    operation: DifferenceOperation
    route_key: RouteKey
    previous: _ForwardingBinding | None
    current: _ForwardingBinding | None


@dataclass(frozen=True, slots=True)
class _ZoneEdgeState:
    zone_id: str
    packet_prefix: str
    service_satellite_id: NodeId


@dataclass(frozen=True, slots=True)
class _ZoneEdgeIntent:
    nominal_time: datetime
    operation: DifferenceOperation
    zone_id: str
    packet_prefix: str
    previous_service_satellite_id: NodeId | None
    service_satellite_id: NodeId | None


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
    connection: Connection


@dataclass(frozen=True, slots=True)
class _QdiscState:
    connection_id: ConnectionId
    direction: ConnectionDirection
    parameters: DirectionalConnectionParameters


@dataclass(frozen=True, slots=True)
class _IntendedState:
    resources: tuple[_ResourceState, ...]
    qdiscs: tuple[_QdiscState, ...]
    forwarding: tuple[_ForwardingBinding, ...]
    zone_edges: tuple[_ZoneEdgeState, ...]
    zone_prefixes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _TopologySignature:
    node_ids: tuple[NodeId, ...]
    zone_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class BpfCompiledRealization:
    """Carry one immutable interval of intended symbolic BPF realization.

    Layer: public BPF-backend handoff value. ``compile_bpf`` creates it, future
    preparation/runtime consumers may read it, and the next compilation may
    consume it as the explicit previous intended baseline. It is not live
    kernel state, a runtime authority, a generation, a command bundle or a
    long-lived state service. It is public from ``skyseed_v2.backend.bpf``
    because compilation continuity and downstream handoff cross a call boundary.
    """

    start_slice_id: int
    start_timestamp: datetime | None
    end_slice_id: int
    end_timestamp: datetime
    resource_realizations: tuple[_ResourceIntent, ...]
    parameter_realizations: tuple[_QdiscIntent, ...]
    forwarding_realizations: tuple[_ForwardingIntent, ...]
    zone_edge_realizations: tuple[_ZoneEdgeIntent, ...]
    constraints: tuple[_OrderingConstraint, ...]
    _intended_state: _IntendedState
    _topology_signature: _TopologySignature

    def __init__(self) -> None:
        raise TypeError("BpfCompiledRealization is created by compile_bpf")

    @classmethod
    def _create(
        cls,
        *,
        start_slice_id: int,
        start_timestamp: datetime | None,
        end_slice_id: int,
        end_timestamp: datetime,
        resource_realizations: tuple[_ResourceIntent, ...],
        parameter_realizations: tuple[_QdiscIntent, ...],
        forwarding_realizations: tuple[_ForwardingIntent, ...],
        zone_edge_realizations: tuple[_ZoneEdgeIntent, ...],
        constraints: tuple[_OrderingConstraint, ...],
        intended_state: _IntendedState,
        topology_signature: _TopologySignature,
    ) -> "BpfCompiledRealization":
        instance = object.__new__(cls)
        object.__setattr__(instance, "start_slice_id", start_slice_id)
        object.__setattr__(instance, "start_timestamp", start_timestamp)
        object.__setattr__(instance, "end_slice_id", end_slice_id)
        object.__setattr__(instance, "end_timestamp", end_timestamp)
        object.__setattr__(
            instance, "resource_realizations", resource_realizations
        )
        object.__setattr__(
            instance, "parameter_realizations", parameter_realizations
        )
        object.__setattr__(
            instance, "forwarding_realizations", forwarding_realizations
        )
        object.__setattr__(
            instance, "zone_edge_realizations", zone_edge_realizations
        )
        object.__setattr__(instance, "constraints", constraints)
        object.__setattr__(instance, "_intended_state", intended_state)
        object.__setattr__(instance, "_topology_signature", topology_signature)
        return instance

    @property
    def intended_resources(self) -> tuple[_ResourceState, ...]:
        """Return the interval-end symbolic resource state."""

        return self._intended_state.resources

    @property
    def intended_parameters(self) -> tuple[_QdiscState, ...]:
        """Return the interval-end directional qdisc targets."""

        return self._intended_state.qdiscs

    @property
    def intended_forwarding(self) -> tuple[_ForwardingBinding, ...]:
        """Return the interval-end symbolic local forwarding state."""

        return self._intended_state.forwarding

    @property
    def intended_zone_edges(self) -> tuple[_ZoneEdgeState, ...]:
        """Return the interval-end Zone service-edge projection."""

        return self._intended_state.zone_edges

    @property
    def zone_prefixes(self) -> tuple[tuple[str, str], ...]:
        """Return the stable backend-local Zone packet identities."""

        return self._intended_state.zone_prefixes


__all__ = ["BpfCompiledRealization"]
