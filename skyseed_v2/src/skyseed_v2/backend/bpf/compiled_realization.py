"""Define the public BPF compilation result and backend-private values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    EffectiveConnectionParameters,
    OperationalConnection,
    OperationalConnectionId,
    PlannedOperationalState,
    RouteDemandTarget,
    planned_operational_state_fingerprint,
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


@dataclass(frozen=True, slots=True)
class _ZoneEdgeIntent:
    nominal_time: datetime
    operation: _MutationOperation
    phase: _ZoneEdgePhase
    zone_id: str
    packet_prefix: str
    previous_service_satellite_id: NodeId | None
    service_satellite_id: NodeId | None

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
    zone_prefixes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _PhysicalWorldProjection:
    """Retain the static identities required by the detached BPF runtime."""

    node_ids: tuple[NodeId, ...]
    zone_ids: tuple[str, ...]

    def __post_init__(self) -> None:
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


@dataclass(frozen=True, slots=True)
class BpfCompiledActivationBoundary:
    """Bind one formal Plan transition identity to an activation timestamp.

    Layer: public immutable BPF Compiler-to-Runner schedule value. It is public
    because persisted handoff, Runner, and detached evidence must preserve
    every formal timestamp, including empty transitions. It is not a second
    semantic delta authority, a backend mutation, or proof of runtime
    activation.
    """

    timestamp: datetime
    transition_fingerprint: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("timestamp must be timezone-aware")
        if (
            not isinstance(self.transition_fingerprint, str)
            or not self.transition_fingerprint
        ):
            raise ValueError("transition_fingerprint must be a non-empty string")


@dataclass(frozen=True, slots=True, init=False)
class BpfCompiledRealization:
    """Carry one immutable Plan lowering and its semantic provenance.

    Layer: public immutable BPF Compiler output and Runner handoff. It is public
    because the common producer, persisted handoff, Runner, Traffic, and
    detached Tools share its Plan lineage, intended state, activation schedule,
    and backend intentions. It is not Planner state ownership, a hidden
    compilation context, or live/realized kernel state.
    """

    source_plan_id: str
    source_previous_plan_id: str | None
    physical_world_fingerprint: str
    input_state_fingerprint: str | None
    output_state_fingerprint: str
    observation_interval: timedelta
    start_timestamp: datetime
    end_timestamp: datetime
    output_planned_state: PlannedOperationalState
    compiled_activation_boundaries: tuple[BpfCompiledActivationBoundary, ...]
    resource_realizations: tuple[_ResourceIntent, ...]
    parameter_realizations: tuple[_QdiscIntent, ...]
    forwarding_realizations: tuple[_ForwardingIntent, ...]
    zone_edge_realizations: tuple[_ZoneEdgeIntent, ...]
    constraints: tuple[_OrderingConstraint, ...]
    semantic_precedence_folding: tuple[tuple[str, int], ...]
    _intended_state: _IntendedState
    _physical_world_projection: _PhysicalWorldProjection
    _incremental_indexes: tuple | None = field(default=None, repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("BpfCompiledRealization is created by compile_bpf")

    @classmethod
    def _create(
        cls,
        *,
        source_plan_id: str,
        source_previous_plan_id: str | None,
        physical_world_fingerprint: str,
        input_state_fingerprint: str | None,
        output_state_fingerprint: str,
        observation_interval: timedelta,
        start_timestamp: datetime,
        end_timestamp: datetime,
        output_planned_state: PlannedOperationalState,
        compiled_activation_boundaries: tuple[
            BpfCompiledActivationBoundary, ...
        ],
        resource_realizations: tuple[_ResourceIntent, ...],
        parameter_realizations: tuple[_QdiscIntent, ...],
        forwarding_realizations: tuple[_ForwardingIntent, ...],
        zone_edge_realizations: tuple[_ZoneEdgeIntent, ...],
        constraints: tuple[_OrderingConstraint, ...],
        semantic_precedence_folding: tuple[tuple[str, int], ...],
        intended_state: _IntendedState,
        physical_world_projection: _PhysicalWorldProjection,
    ) -> BpfCompiledRealization:
        for value, name in (
            (source_plan_id, "source_plan_id"),
            (physical_world_fingerprint, "physical_world_fingerprint"),
            (output_state_fingerprint, "output_state_fingerprint"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if (source_previous_plan_id is None) != (
            input_state_fingerprint is None
        ):
            raise ValueError(
                "previous Plan identity and input fingerprint are all-or-none"
            )
        for value, name in (
            (source_previous_plan_id, "source_previous_plan_id"),
            (input_state_fingerprint, "input_state_fingerprint"),
        ):
            if value is not None and (
                not isinstance(value, str) or not value
            ):
                raise ValueError(f"{name} must be None or a non-empty string")
        boundaries = tuple(compiled_activation_boundaries)
        if not boundaries or any(
            not isinstance(value, BpfCompiledActivationBoundary)
            for value in boundaries
        ):
            raise ValueError(
                "compiled_activation_boundaries must contain formal boundaries"
            )
        if (
            not isinstance(observation_interval, timedelta)
            or observation_interval <= timedelta(0)
        ):
            raise ValueError("observation_interval must be positive")
        if not isinstance(start_timestamp, datetime) or not isinstance(
            end_timestamp, datetime
        ):
            raise TypeError("realization timestamps must be datetimes")
        if end_timestamp < start_timestamp:
            raise ValueError("realization end cannot precede start")
        if not isinstance(output_planned_state, PlannedOperationalState):
            raise TypeError(
                "output_planned_state must be a PlannedOperationalState"
            )
        if output_planned_state.timestamp != end_timestamp:
            raise ValueError(
                "output planned state must use the realization end timestamp"
            )
        if (
            planned_operational_state_fingerprint(output_planned_state)
            != output_state_fingerprint
        ):
            raise ValueError(
                "output_state_fingerprint differs from output planned state"
            )
        activation_timestamps = tuple(value.timestamp for value in boundaries)
        if activation_timestamps != tuple(sorted(set(activation_timestamps))):
            raise ValueError(
                "compiled activation timestamps must be unique and ordered"
            )
        if any(
            current - previous != observation_interval
            for previous, current in zip(
                activation_timestamps, activation_timestamps[1:]
            )
        ):
            raise ValueError(
                "compiled activation timestamps differ from observation interval"
            )
        if (
            activation_timestamps[0] != start_timestamp
            or activation_timestamps[-1] != end_timestamp
        ):
            raise ValueError(
                "compiled activation timestamps differ from realization interval"
            )
        operation_values = (
            tuple(resource_realizations),
            tuple(parameter_realizations),
            tuple(forwarding_realizations),
            tuple(zone_edge_realizations),
        )
        operation_types = (
            _ResourceIntent,
            _QdiscIntent,
            _ForwardingIntent,
            _ZoneEdgeIntent,
        )
        if any(
            not isinstance(value, expected_type)
            for values, expected_type in zip(operation_values, operation_types)
            for value in values
        ):
            raise TypeError("compiled realization contains an invalid intent type")
        activation_set = set(activation_timestamps)
        if any(
            value.nominal_time not in activation_set
            for values in operation_values
            for value in values
        ):
            raise ValueError(
                "compiled intent has no declared formal activation boundary"
            )
        constraint_values = tuple(constraints)
        if any(
            not isinstance(value, _OrderingConstraint)
            for value in constraint_values
        ):
            raise TypeError("constraints must contain _OrderingConstraint values")
        folding = tuple(semantic_precedence_folding)
        allowed_folds = {
            "activation-frontier",
            "runner-phase",
            "atomic-forwarding-generation",
            "no-backend-effect",
            "same-backend-effect",
            "explicit-fallback",
        }
        if (
            any(
                not isinstance(item, tuple)
                or len(item) != 2
                or item[0] not in allowed_folds
                or isinstance(item[1], bool)
                or not isinstance(item[1], int)
                or item[1] < 0
                for item in folding
            )
            or tuple(name for name, _ in folding)
            != tuple(sorted(name for name, _ in folding))
            or len(folding) != len({name for name, _ in folding})
        ):
            raise ValueError("semantic_precedence_folding is not canonical")
        if not isinstance(intended_state, _IntendedState):
            raise TypeError("intended_state must be an _IntendedState")
        if not isinstance(physical_world_projection, _PhysicalWorldProjection):
            raise TypeError(
                "physical_world_projection must be a _PhysicalWorldProjection"
            )
        instance = object.__new__(cls)
        object.__setattr__(instance, "source_plan_id", source_plan_id)
        object.__setattr__(
            instance, "source_previous_plan_id", source_previous_plan_id
        )
        object.__setattr__(
            instance,
            "physical_world_fingerprint",
            physical_world_fingerprint,
        )
        object.__setattr__(
            instance,
            "input_state_fingerprint",
            input_state_fingerprint,
        )
        object.__setattr__(
            instance,
            "output_state_fingerprint",
            output_state_fingerprint,
        )
        object.__setattr__(
            instance, "observation_interval", observation_interval
        )
        object.__setattr__(
            instance, "start_timestamp", start_timestamp
        )
        object.__setattr__(instance, "end_timestamp", end_timestamp)
        object.__setattr__(instance, "output_planned_state", output_planned_state)
        object.__setattr__(
            instance,
            "compiled_activation_boundaries",
            boundaries,
        )
        object.__setattr__(
            instance, "resource_realizations", tuple(resource_realizations)
        )
        object.__setattr__(
            instance, "parameter_realizations", tuple(parameter_realizations)
        )
        object.__setattr__(
            instance, "forwarding_realizations", tuple(forwarding_realizations)
        )
        object.__setattr__(
            instance, "zone_edge_realizations", tuple(zone_edge_realizations)
        )
        object.__setattr__(instance, "constraints", constraint_values)
        object.__setattr__(instance, "semantic_precedence_folding", folding)
        object.__setattr__(instance, "_intended_state", intended_state)
        object.__setattr__(
            instance, "_physical_world_projection", physical_world_projection
        )
        object.__setattr__(instance, "_incremental_indexes", None)
        return instance

    @property
    def activation_timestamps(self) -> tuple[datetime, ...]:
        """Return every formal activation timestamp, including no-op ones."""

        return tuple(
            boundary.timestamp for boundary in self.compiled_activation_boundaries
        )

    @property
    def intended_resources(self) -> tuple[_ResourceState, ...]:
        return self._intended_state.resources

    @property
    def intended_parameters(self) -> tuple[_QdiscState, ...]:
        return self._intended_state.qdiscs

    @property
    def intended_forwarding(self) -> tuple[_ForwardingBinding, ...]:
        return self._intended_state.forwarding

    @property
    def intended_zone_edges(self) -> tuple[_ZoneEdgeState, ...]:
        return self._intended_state.zone_edges

    @property
    def zone_prefixes(self) -> tuple[tuple[str, str], ...]:
        return self._intended_state.zone_prefixes


__all__ = ["BpfCompiledActivationBoundary", "BpfCompiledRealization"]
