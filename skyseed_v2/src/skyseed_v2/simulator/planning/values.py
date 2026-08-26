"""Define immutable backend-independent Planner values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.time import normalize_utc_datetime

from ..slicing import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    DifferenceOperation,
    RouteKey,
)


class ConnectionTransitionOperation(Enum):
    """Classify one planned logical Connection transition.

    Layer: immutable Simulator Planner semantic operation. A
    ConnectionTransition uses it for establishment or withdrawal. It is not a
    sampled Difference operation or backend command and is public from
    ``skyseed_v2.simulator``.
    """

    ESTABLISH = "establish"
    WITHDRAW = "withdraw"


@dataclass(frozen=True, slots=True)
class ConnectionTransition:
    """Describe one planned logical Connection lifetime boundary.

    Layer: immutable Simulator Planner transition. Planner publishes it in a
    Plan and semantic consumers read it. It is not physical visibility
    evidence, a sampled ConnectionSlice value or backend operation and is
    public from ``skyseed_v2.simulator``.
    """

    nominal_time: datetime
    operation: ConnectionTransitionOperation
    connection_id: ConnectionId
    connection: Connection | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "nominal_time",
            normalize_utc_datetime(self.nominal_time, "nominal_time"),
        )
        if not isinstance(self.operation, ConnectionTransitionOperation):
            raise TypeError(
                "operation must be a ConnectionTransitionOperation"
            )
        if not isinstance(self.connection_id, ConnectionId):
            raise TypeError("connection_id must be a ConnectionId")
        if self.operation is ConnectionTransitionOperation.ESTABLISH:
            if not isinstance(self.connection, Connection):
                raise TypeError("ESTABLISH requires a complete Connection")
            if self.connection.connection_id != self.connection_id:
                raise ValueError(
                    "ESTABLISH Connection must match connection_id"
                )
        elif self.connection is not None:
            raise ValueError("WITHDRAW must not retain a Connection")


@dataclass(frozen=True, slots=True)
class ParameterTransition:
    """Carry one sparse planned directional Connection parameter target.

    Layer: immutable Simulator Planner transition. Planner publishes it only
    inside a planned active Connection lifetime. It is not an initial
    Connection parameter set, sampled patch or backend traffic-control action
    and is public from ``skyseed_v2.simulator``.
    """

    nominal_time: datetime
    connection_id: ConnectionId
    direction: ConnectionDirection
    bandwidth_bps: int | None = None
    delay_us: int | None = None
    packet_loss_ppm: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "nominal_time",
            normalize_utc_datetime(self.nominal_time, "nominal_time"),
        )
        if not isinstance(self.connection_id, ConnectionId):
            raise TypeError("connection_id must be a ConnectionId")
        if not isinstance(self.direction, ConnectionDirection):
            raise TypeError("direction must be a ConnectionDirection")
        _validate_sparse_parameters(
            self.bandwidth_bps,
            self.delay_us,
            self.packet_loss_ppm,
        )


@dataclass(frozen=True, slots=True)
class ForwardingTransition:
    """Describe one planned forwarding transition from formal Route state.

    Layer: immutable Simulator Planner transition. ForwardingPlanningModel
    derives it from a RoutePatch and Planner publishes it. It is not a route
    selection, full path, constraint graph or backend route command and is
    public from ``skyseed_v2.simulator``.
    """

    nominal_time: datetime
    route_key: RouteKey
    operation: DifferenceOperation
    next_hop: NodeId | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "nominal_time",
            normalize_utc_datetime(self.nominal_time, "nominal_time"),
        )
        if not isinstance(self.route_key, RouteKey):
            raise TypeError("route_key must be a RouteKey")
        if not isinstance(self.operation, DifferenceOperation):
            raise TypeError("operation must be a DifferenceOperation")
        if self.operation is DifferenceOperation.REMOVED:
            if self.next_hop is not None:
                raise ValueError(
                    "removed Forwarding transition must not retain a next hop"
                )
        elif not isinstance(self.next_hop, NodeId):
            raise TypeError(
                "added or changed Forwarding transition requires a NodeId"
            )


@dataclass(frozen=True, slots=True)
class ForwardingTransitionPrecedence:
    """Require one same-time Forwarding transition to precede another.

    Layer: immutable semantic ordering relation owned by Plan. A
    ForwardingPlanningModel produces it from formal path facts and future Plan
    consumers may use it as a network constraint. It is not tuple order, a
    temporal offset, execution schedule or general dependency graph and is
    public from ``skyseed_v2.simulator``.
    """

    predecessor: ForwardingTransition
    successor: ForwardingTransition

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, ForwardingTransition):
            raise TypeError("predecessor must be a ForwardingTransition")
        if not isinstance(self.successor, ForwardingTransition):
            raise TypeError("successor must be a ForwardingTransition")
        if self.predecessor is self.successor:
            raise ValueError("precedence endpoints must be distinct")
        if self.predecessor.nominal_time != self.successor.nominal_time:
            raise ValueError("precedence endpoints must have equal nominal time")


@dataclass(frozen=True, slots=True)
class PlanningDecision:
    """Preserve one concise explanation of a Planner optimization decision.

    Layer: immutable Simulator Planner inspection evidence. Planner creates
    and retains it separately from Plan payload so callers can explain merged
    or suppressed sampled evolution. It is not semantic network state,
    exhaustive tracing or a backend log and is public from
    ``skyseed_v2.simulator``.
    """

    source_difference_ids: tuple[int, ...]
    decision: str
    decided_by: str
    reason: str

    def __post_init__(self) -> None:
        ids = tuple(self.source_difference_ids)
        if not ids or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for value in ids
        ):
            raise ValueError(
                "source_difference_ids must contain positive Slice IDs"
            )
        if tuple(sorted(set(ids))) != ids:
            raise ValueError(
                "source_difference_ids must be unique and increasing"
            )
        if self.decision not in {"retained", "merged", "suppressed"}:
            raise ValueError(
                "decision must be retained, merged or suppressed"
            )
        for field_name in ("decided_by", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "source_difference_ids", ids)


@dataclass(frozen=True, slots=True, init=False)
class Plan:
    """Publish one immutable semantic plan for `(start, end]` coverage.

    Layer: immutable Simulator Planner snapshot. Planner creates it from a
    contiguous publication prefix and callers or future approved semantic
    consumers read it. Successive Plans are continuous and non-overlapping. It
    is not a Slice, complete Topology, executable schedule or backend operation
    list and is public from ``skyseed_v2.simulator``.
    """

    start_slice_id: int
    start_timestamp: datetime | None
    end_slice_id: int
    end_timestamp: datetime
    connection_transitions: tuple[ConnectionTransition, ...]
    parameter_transitions: tuple[ParameterTransition, ...]
    forwarding_transitions: tuple[ForwardingTransition, ...]
    forwarding_precedences: tuple[ForwardingTransitionPrecedence, ...]
    temporal_index: Mapping[
        datetime,
        tuple[
            ConnectionTransition | ParameterTransition | ForwardingTransition,
            ...,
        ],
    ] = field(repr=False, compare=True, hash=False)

    def __init__(
        self,
        *,
        start_slice_id: int,
        start_timestamp: datetime | None,
        end_slice_id: int,
        end_timestamp: datetime,
        connection_transitions: Iterable[ConnectionTransition] = (),
        parameter_transitions: Iterable[ParameterTransition] = (),
        forwarding_transitions: Iterable[ForwardingTransition] = (),
        forwarding_precedences: Iterable[
            ForwardingTransitionPrecedence
        ] = (),
    ) -> None:
        _validate_coverage_id(start_slice_id, "start_slice_id")
        _validate_coverage_id(end_slice_id, "end_slice_id")
        if end_slice_id <= start_slice_id:
            raise ValueError("Plan coverage must contain a Difference")
        if start_slice_id == 0:
            if start_timestamp is not None:
                raise ValueError("synthetic Slice 0 timestamp must be None")
            normalized_start = None
        else:
            if start_timestamp is None:
                raise ValueError(
                    "start real Slice timestamp must be a datetime"
                )
            normalized_start = normalize_utc_datetime(
                start_timestamp, "start_timestamp"
            )
        normalized_end = normalize_utc_datetime(
            end_timestamp, "end_timestamp"
        )
        if (
            normalized_start is not None
            and normalized_end <= normalized_start
        ):
            raise ValueError(
                "end_timestamp must be later than start_timestamp"
            )

        connections = _typed_tuple(
            connection_transitions,
            ConnectionTransition,
            "connection_transitions",
        )
        parameters = _typed_tuple(
            parameter_transitions,
            ParameterTransition,
            "parameter_transitions",
        )
        forwarding = _typed_tuple(
            forwarding_transitions,
            ForwardingTransition,
            "forwarding_transitions",
        )
        connections = _sort_if_needed(
            connections, _connection_transition_key
        )
        parameters = _sort_if_needed(parameters, _parameter_transition_key)
        forwarding = _sort_if_needed(
            forwarding, lambda value: value.nominal_time
        )
        precedences = _typed_tuple(
            forwarding_precedences,
            ForwardingTransitionPrecedence,
            "forwarding_precedences",
        )
        _validate_transition_times(
            normalized_start,
            normalized_end,
            connections,
            parameters,
            forwarding,
        )
        precedences = _validate_forwarding_precedences(
            forwarding, precedences
        )
        temporal_index = _build_temporal_index(
            connections, parameters, forwarding
        )

        object.__setattr__(self, "start_slice_id", start_slice_id)
        object.__setattr__(self, "start_timestamp", normalized_start)
        object.__setattr__(self, "end_slice_id", end_slice_id)
        object.__setattr__(self, "end_timestamp", normalized_end)
        object.__setattr__(self, "connection_transitions", connections)
        object.__setattr__(self, "parameter_transitions", parameters)
        object.__setattr__(self, "forwarding_transitions", forwarding)
        object.__setattr__(self, "forwarding_precedences", precedences)
        object.__setattr__(
            self,
            "temporal_index",
            MappingProxyType(temporal_index),
        )

    @classmethod
    def _from_validated(
        cls,
        *,
        start_slice_id: int,
        start_timestamp: datetime | None,
        end_slice_id: int,
        end_timestamp: datetime,
        connection_transitions: tuple[ConnectionTransition, ...],
        parameter_transitions: tuple[ParameterTransition, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
        forwarding_precedences: tuple[
            ForwardingTransitionPrecedence, ...
        ],
    ) -> "Plan":
        """Build Planner-owned output after its complete trust-boundary proof.

        This private path does not weaken arbitrary public ``Plan(...)``
        construction. Planner has already validated exact transition types and
        precedence endpoints/duplicates/cycles, and has already put precedence
        values in canonical transition order. This path retains a lightweight
        final coverage/time integrity check, then performs only representation
        sorting, temporal indexing, and immutable snapshots.
        """

        _validate_coverage_id(start_slice_id, "start_slice_id")
        _validate_coverage_id(end_slice_id, "end_slice_id")
        if end_slice_id <= start_slice_id:
            raise ValueError("Plan coverage must contain a Difference")
        if start_slice_id == 0:
            if start_timestamp is not None:
                raise ValueError("synthetic Slice 0 timestamp must be None")
            normalized_start = None
        else:
            if start_timestamp is None:
                raise ValueError(
                    "start real Slice timestamp must be a datetime"
                )
            normalized_start = normalize_utc_datetime(
                start_timestamp, "start_timestamp"
            )
        normalized_end = normalize_utc_datetime(
            end_timestamp, "end_timestamp"
        )
        if (
            normalized_start is not None
            and normalized_end <= normalized_start
        ):
            raise ValueError(
                "end_timestamp must be later than start_timestamp"
            )

        connections = _sort_if_needed(
            connection_transitions, _connection_transition_key
        )
        parameters = _sort_if_needed(
            parameter_transitions, _parameter_transition_key
        )
        forwarding = _sort_if_needed(
            forwarding_transitions, lambda value: value.nominal_time
        )
        _validate_transition_times(
            normalized_start,
            normalized_end,
            connections,
            parameters,
            forwarding,
        )
        temporal_index = _build_temporal_index(
            connections, parameters, forwarding
        )

        instance = object.__new__(cls)
        object.__setattr__(instance, "start_slice_id", start_slice_id)
        object.__setattr__(instance, "start_timestamp", normalized_start)
        object.__setattr__(instance, "end_slice_id", end_slice_id)
        object.__setattr__(instance, "end_timestamp", normalized_end)
        object.__setattr__(instance, "connection_transitions", connections)
        object.__setattr__(instance, "parameter_transitions", parameters)
        object.__setattr__(instance, "forwarding_transitions", forwarding)
        object.__setattr__(
            instance,
            "forwarding_precedences",
            forwarding_precedences,
        )
        object.__setattr__(
            instance,
            "temporal_index",
            MappingProxyType(temporal_index),
        )
        return instance


def _build_temporal_index(
    connections: tuple[ConnectionTransition, ...],
    parameters: tuple[ParameterTransition, ...],
    forwarding: tuple[ForwardingTransition, ...],
) -> dict[
    datetime,
    tuple[ConnectionTransition | ParameterTransition | ForwardingTransition, ...],
]:
    grouped: dict[datetime, list[object]] = {}
    for transitions in (
        connections,
        forwarding,
        parameters,
    ):
        for transition in transitions:
            grouped.setdefault(transition.nominal_time, []).append(transition)
    return {
        timestamp: tuple(grouped[timestamp]) for timestamp in sorted(grouped)
    }


def _sort_if_needed(values: tuple, key) -> tuple:
    if all(
        key(previous) <= key(current)
        for previous, current in zip(values, values[1:])
    ):
        return values
    return tuple(sorted(values, key=key))


def _connection_transition_key(
    value: ConnectionTransition,
) -> tuple[datetime, int, str]:
    return (
        value.nominal_time,
        0
        if value.operation is ConnectionTransitionOperation.ESTABLISH
        else 1,
        repr(value.connection_id),
    )


def _parameter_transition_key(
    value: ParameterTransition,
) -> tuple[datetime, str, str]:
    return value.nominal_time, repr(value.connection_id), value.direction.value


def _validate_sparse_parameters(
    bandwidth_bps: int | None,
    delay_us: int | None,
    packet_loss_ppm: int | None,
) -> None:
    if bandwidth_bps is None and delay_us is None and packet_loss_ppm is None:
        raise ValueError("ParameterTransition must contain a target value")
    if bandwidth_bps is not None and (
        isinstance(bandwidth_bps, bool)
        or not isinstance(bandwidth_bps, int)
        or bandwidth_bps <= 0
    ):
        raise ValueError("bandwidth_bps must be a positive integer")
    if delay_us is not None and (
        isinstance(delay_us, bool)
        or not isinstance(delay_us, int)
        or delay_us < 0
    ):
        raise ValueError("delay_us must be a non-negative integer")
    if packet_loss_ppm is not None and (
        isinstance(packet_loss_ppm, bool)
        or not isinstance(packet_loss_ppm, int)
        or not 0 <= packet_loss_ppm <= 1_000_000
    ):
        raise ValueError("packet_loss_ppm must be between 0 and 1000000")


def _validate_coverage_id(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _typed_tuple(
    values: Iterable[object],
    expected_type: type,
    field_name: str,
) -> tuple:
    try:
        result = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in result):
        raise TypeError(
            f"{field_name} must contain {expected_type.__name__} values"
        )
    return result


def _validate_transition_times(
    start_timestamp: datetime | None,
    end_timestamp: datetime,
    *transition_groups: tuple,
) -> None:
    for transitions in transition_groups:
        for transition in transitions:
            if transition.nominal_time > end_timestamp:
                raise ValueError("transition falls after Plan coverage")
            if (
                start_timestamp is not None
                and transition.nominal_time <= start_timestamp
            ):
                raise ValueError(
                    "transition falls before or on the Plan-owned start boundary"
                )


def _validate_forwarding_precedences(
    forwarding: tuple[ForwardingTransition, ...],
    precedences: tuple[ForwardingTransitionPrecedence, ...],
) -> tuple[ForwardingTransitionPrecedence, ...]:
    positions = {id(value): index for index, value in enumerate(forwarding)}
    seen: set[tuple[int, int]] = set()
    for precedence in precedences:
        key = (id(precedence.predecessor), id(precedence.successor))
        if key[0] not in positions or key[1] not in positions:
            raise ValueError(
                "precedence endpoints must reference transitions in this Plan"
            )
        if key in seen:
            raise ValueError("duplicate Forwarding transition precedence")
        seen.add(key)
    if _precedence_has_cycle(seen):
        raise ValueError("Forwarding transition precedence must be acyclic")
    return _order_forwarding_precedences(
        forwarding, precedences, positions=positions
    )


def _order_forwarding_precedences(
    forwarding: tuple[ForwardingTransition, ...],
    precedences: tuple[ForwardingTransitionPrecedence, ...],
    *,
    positions: dict[int, int] | None = None,
) -> tuple[ForwardingTransitionPrecedence, ...]:
    if positions is None:
        positions = {id(value): index for index, value in enumerate(forwarding)}
    keyed = [
        (
            (
                positions[id(precedence.predecessor)],
                positions[id(precedence.successor)],
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


def _precedence_has_cycle(edges) -> bool:
    successors: dict[int, set[int]] = {}
    indegrees: dict[int, int] = {}
    for predecessor, successor in edges:
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
    return visited != len(indegrees)


__all__ = [
    "ConnectionTransition",
    "ConnectionTransitionOperation",
    "ForwardingTransition",
    "ForwardingTransitionPrecedence",
    "ParameterTransition",
    "Plan",
    "PlanningDecision",
]
