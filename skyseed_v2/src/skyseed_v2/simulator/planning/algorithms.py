"""Define replaceable planning-window-aware Planner algorithms."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from skyseed_v2.shared import NodeId

from ..slicing import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    DifferenceOperation,
    DirectionalConnectionParameters,
    RouteKey,
    TopologyDifference,
)
from .values import (
    ConnectionTransition,
    ConnectionTransitionOperation,
    ForwardingTransition,
    ForwardingTransitionPrecedence,
    ParameterTransition,
    PlanningDecision,
)


@runtime_checkable
class PlanningWindowPolicy(Protocol):
    """Select the planning window and the next publication window.

    Layer: replaceable Simulator Planner window algorithm. Planner invokes it
    with immutable not-yet-published Differences. Models and Coordination see
    ``select_planning_differences`` output;
    ``select_publication_differences`` chooses a non-empty prefix of that
    planning window for the next Plan. It is not Slice
    generation, retention ownership or runtime scheduling and is public from
    ``skyseed_v2.simulator``.
    """

    def select_planning_differences(
        self,
        available_differences: tuple[TopologyDifference, ...],
    ) -> tuple[TopologyDifference, ...]:
        """Return a non-empty planning-window prefix when input is available."""

    def select_publication_differences(
        self,
        planning_differences: tuple[TopologyDifference, ...],
    ) -> tuple[TopologyDifference, ...]:
        """Return the non-empty planning-window prefix owned by the next Plan."""


class AllAvailablePlanningWindowPolicy:
    """Select every currently available Difference for planning.

    Layer: default Simulator Planner window algorithm. It is a replaceable
    policy choice, not hard-coded Planner behavior, Slicer retention policy or
    fixed-duration scheduler and is public from ``skyseed_v2.simulator``.
    """

    __slots__ = ()

    def select_planning_differences(
        self,
        available_differences: tuple[TopologyDifference, ...],
    ) -> tuple[TopologyDifference, ...]:
        return tuple(available_differences)

    def select_publication_differences(
        self,
        planning_differences: tuple[TopologyDifference, ...],
    ) -> tuple[TopologyDifference, ...]:
        return tuple(planning_differences)


@runtime_checkable
class ConnectionPlanningModel(Protocol):
    """Plan logical Connection transitions over a planning window.

    Layer: replaceable Simulator Planner Connection algorithm. It is public
    from ``skyseed_v2.simulator`` and does not own formal Connection state,
    backend operations, or the published Plan.

    ``differences`` is the complete Difference sequence in the planning
    window. ``affected_differences`` is the newly added or otherwise affected
    suffix that requires incremental
    algorithm work; it is call information, not a new network domain object.
    ``previous_transitions`` is previously planned, still-unpublished work that
    may be reused or locally adjusted; it is not a second Plan or owner.
    Implementations return the complete planning-window result.
    """

    def plan_connection_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ConnectionTransition, ...],
    ) -> tuple[ConnectionTransition, ...]:
        """Return the complete planning-window Connection result."""

    def is_suppression_candidate(
        self,
        establish: ConnectionTransition,
        withdraw: ConnectionTransition,
    ) -> bool:
        """Return whether one matched planned lifetime is a candidate."""


class SampleBoundaryConnectionPlanningModel:
    """Place affected Connection changes at sampled current timestamps.

    Layer: reference Simulator Planner Connection algorithm. It is public
    from ``skyseed_v2.simulator`` and does not refine physical visibility or
    produce backend operations.
    """

    __slots__ = ("_minimum_dwell",)

    def __init__(self, *, minimum_dwell: timedelta | None = None) -> None:
        self._minimum_dwell = _validate_minimum_dwell(minimum_dwell)

    def plan_connection_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ConnectionTransition, ...],
    ) -> tuple[ConnectionTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        planned = tuple(
            transition
            for difference in affected_differences
            for transition in _connection_transitions_at(
                difference, difference.current_timestamp
            )
        )
        return (*previous_transitions, *planned)

    def is_suppression_candidate(
        self,
        establish: ConnectionTransition,
        withdraw: ConnectionTransition,
    ) -> bool:
        return _is_short_lifetime(establish, withdraw, self._minimum_dwell)


class MidpointConnectionPlanningModel:
    """Estimate affected real-sample changes at interval midpoints.

    Layer: reference Simulator Planner Connection algorithm. It is public
    from ``skyseed_v2.simulator`` and estimates semantic time. It does not
    recompute physical visibility.
    """

    __slots__ = ("_minimum_dwell",)

    def __init__(self, *, minimum_dwell: timedelta | None = None) -> None:
        self._minimum_dwell = _validate_minimum_dwell(minimum_dwell)

    def plan_connection_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ConnectionTransition, ...],
    ) -> tuple[ConnectionTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        planned = []
        for difference in affected_differences:
            if difference.previous_timestamp is None:
                nominal_time = difference.current_timestamp
            else:
                nominal_time = difference.previous_timestamp + (
                    difference.current_timestamp - difference.previous_timestamp
                ) / 2
            planned.extend(_connection_transitions_at(difference, nominal_time))
        return (*previous_transitions, *planned)

    def is_suppression_candidate(
        self,
        establish: ConnectionTransition,
        withdraw: ConnectionTransition,
    ) -> bool:
        return _is_short_lifetime(establish, withdraw, self._minimum_dwell)


@runtime_checkable
class ParameterPlanningModel(Protocol):
    """Plan sparse parameter targets over a planning window.

    Layer: replaceable Simulator Planner Parameter algorithm. It is public
    from ``skyseed_v2.simulator`` and does not own formal parameters, backend
    traffic control, or the published Plan.

    ``differences``, ``affected_differences`` and ``previous_transitions`` have
    the same planning-window, incremental-suffix and reusable-unpublished
    meanings as the Connection Model inputs. The two immutable mappings
    describe state immediately before ``affected_differences``: observed means
    the latest formal Simulator/Slicer parameters, while planned means the
    latest target Planner actually decided should be realized. They use
    existing formal Connection values rather than a public planning-context
    DTO, and the distinction survives Plan publication boundaries.
    """

    def plan_parameter_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ParameterTransition, ...],
        observed_parameters: Mapping[
            tuple[ConnectionId, ConnectionDirection],
            DirectionalConnectionParameters,
        ],
        planned_parameters: Mapping[
            tuple[ConnectionId, ConnectionDirection],
            DirectionalConnectionParameters,
        ],
    ) -> tuple[ParameterTransition, ...]:
        """Return the complete planning-window Parameter result."""


class AccumulatedDeadbandParameterPlanningModel:
    """Emit sparse targets after accumulated absolute thresholds are met.

    Layer: reference Simulator Planner Parameter algorithm. It is public from
    ``skyseed_v2.simulator`` and emits semantic sparse targets, not backend
    traffic-control actions.
    """

    __slots__ = (
        "_bandwidth_bps_threshold",
        "_delay_us_threshold",
        "_packet_loss_ppm_threshold",
        "_include_accumulated_fields",
    )

    def __init__(
        self,
        *,
        bandwidth_bps_threshold: int = 0,
        delay_us_threshold: int = 0,
        packet_loss_ppm_threshold: int = 0,
        include_accumulated_fields: bool = False,
    ) -> None:
        self._bandwidth_bps_threshold = _validate_threshold(
            bandwidth_bps_threshold, "bandwidth_bps_threshold"
        )
        self._delay_us_threshold = _validate_threshold(
            delay_us_threshold, "delay_us_threshold"
        )
        self._packet_loss_ppm_threshold = _validate_threshold(
            packet_loss_ppm_threshold, "packet_loss_ppm_threshold"
        )
        if not isinstance(include_accumulated_fields, bool):
            raise TypeError("include_accumulated_fields must be a bool")
        self._include_accumulated_fields = include_accumulated_fields

    def plan_parameter_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ParameterTransition, ...],
        observed_parameters: Mapping[
            tuple[ConnectionId, ConnectionDirection],
            DirectionalConnectionParameters,
        ],
        planned_parameters: Mapping[
            tuple[ConnectionId, ConnectionDirection],
            DirectionalConnectionParameters,
        ],
    ) -> tuple[ParameterTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        observed = _parameter_mapping(observed_parameters, "observed_parameters")
        planned = _parameter_mapping(planned_parameters, "planned_parameters")
        if observed.keys() != planned.keys():
            raise ValueError(
                "observed_parameters and planned_parameters must have equal keys"
            )

        result = list(previous_transitions)
        thresholds = {
            "bandwidth_bps": self._bandwidth_bps_threshold,
            "delay_us": self._delay_us_threshold,
            "packet_loss_ppm": self._packet_loss_ppm_threshold,
        }
        for difference in affected_differences:
            for connection in difference.added_connections:
                _initialize_parameter_maps(observed, planned, connection)
            for patch in difference.connection_parameter_patches:
                key = (patch.connection_id, patch.direction)
                try:
                    current = _apply_parameter_patch(observed[key], patch)
                    last_planned = planned[key]
                except KeyError as error:
                    raise ValueError(
                        "parameter patch is outside an active Connection"
                    ) from error
                observed[key] = current
                crossed = {
                    field_name
                    for field_name, threshold in thresholds.items()
                    if getattr(patch, field_name) is not None
                    and abs(
                        getattr(current, field_name)
                        - getattr(last_planned, field_name)
                    )
                    >= threshold
                }
                if not crossed:
                    continue
                selected = crossed
                if self._include_accumulated_fields:
                    selected = {
                        field_name
                        for field_name in thresholds
                        if getattr(current, field_name)
                        != getattr(last_planned, field_name)
                    }
                transition = ParameterTransition(
                    nominal_time=difference.current_timestamp,
                    connection_id=patch.connection_id,
                    direction=patch.direction,
                    **{
                        field_name: (
                            getattr(current, field_name)
                            if field_name in selected
                            else None
                        )
                        for field_name in thresholds
                    },
                )
                result.append(transition)
                planned[key] = _apply_parameter_transition(
                    last_planned, transition
                )
            for connection_id in difference.removed_connection_ids:
                _clear_parameter_maps(observed, planned, connection_id)
        return tuple(result)


@runtime_checkable
class ForwardingPlanningModel(Protocol):
    """Plan forwarding from selected Differences and immutable path facts.

    Layer: replaceable Simulator Planner Forwarding algorithm. It is public
    from ``skyseed_v2.simulator`` and does not own Slicer, RouteModel, or
    backend routing authority.

    ``differences`` is the complete planning window,
    ``affected_differences`` is its currently affected incremental suffix, and
    ``previous_transitions`` is reusable still-unpublished planning work.
    Planner performs every Slicer query and supplies the narrow read-only
    per-Slice RouteKey-to-Connection-ID path mapping. The Model receives no
    Slicer and has no authority to create Slices or Differences, invoke
    RouteModel, or recompute Simulator state.
    """

    def plan_forwarding_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ForwardingTransition, ...],
        connection_transitions: tuple[ConnectionTransition, ...],
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
    ) -> tuple[ForwardingTransition, ...]:
        """Return the complete planning-window Forwarding result."""

    def plan_forwarding_precedences(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
    ) -> tuple[ForwardingTransitionPrecedence, ...]:
        """Return only required same-time semantic ordering relations."""


class DependencyAwareForwardingPlanningModel:
    """Plan RoutePatch transitions and precedences from formal path facts.

    Layer: reference Simulator Planner Forwarding algorithm. It is public
    from ``skyseed_v2.simulator`` and publishes only genuine direct,
    operation-aware relations: downstream readiness precedes upstream use for
    new state, while upstream disuse precedes downstream removal for old state.
    Deterministic tuple order remains non-authoritative. It does not recompute
    routes or create a runtime schedule.
    """

    __slots__ = ()

    def plan_forwarding_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ForwardingTransition, ...],
        connection_transitions: tuple[ConnectionTransition, ...],
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
    ) -> tuple[ForwardingTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        transitions = _typed_tuple(
            connection_transitions,
            ConnectionTransition,
            "connection_transitions",
        )
        if not isinstance(route_path_connection_ids, Mapping):
            raise TypeError("route_path_connection_ids must be a Mapping")
        paths = route_path_connection_ids
        withdrawal_times = {}
        for transition in transitions:
            if (
                transition.operation
                is not ConnectionTransitionOperation.WITHDRAW
            ):
                continue
            source = _connection_difference(differences, transition)
            withdrawal_times[
                (source.current_slice_id, transition.connection_id)
            ] = transition.nominal_time

        planned: list[
            tuple[tuple[datetime, int, int, str], ForwardingTransition]
        ] = []
        for difference in affected_differences:
            for patch in difference.route_patches:
                old_dependencies = tuple(paths.get(
                    difference.previous_slice_id, {}
                ).get(patch.route_key, ()))
                new_dependencies = tuple(paths.get(
                    difference.current_slice_id, {}
                ).get(patch.route_key, ()))
                if any(
                    not isinstance(value, ConnectionId)
                    for value in (*old_dependencies, *new_dependencies)
                ):
                    raise TypeError(
                        "route_path_connection_ids contains invalid path state"
                    )
                relevant_withdrawals = tuple(
                    withdrawal_times[
                        (difference.current_slice_id, connection_id)
                    ]
                    for connection_id in old_dependencies
                    if (
                        difference.current_slice_id,
                        connection_id,
                    )
                    in withdrawal_times
                )
                nominal_time = difference.current_timestamp
                if relevant_withdrawals:
                    nominal_time = min(nominal_time, *relevant_withdrawals)
                transition = ForwardingTransition(
                    nominal_time=nominal_time,
                    route_key=patch.route_key,
                    operation=patch.operation,
                    next_hop=patch.next_hop,
                )
                if patch.operation is DifferenceOperation.REMOVED:
                    phase = 1
                    depth = -len(old_dependencies)
                else:
                    phase = 0
                    depth = len(new_dependencies)
                planned.append(
                    (
                        (nominal_time, phase, depth, repr(patch.route_key)),
                        transition,
                    )
                )
        return (
            *previous_transitions,
            *(value for _, value in sorted(planned, key=lambda item: item[0])),
        )

    def plan_forwarding_precedences(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
    ) -> tuple[ForwardingTransitionPrecedence, ...]:
        selected = _difference_tuple(differences, "differences")
        transitions = _typed_tuple(
            forwarding_transitions,
            ForwardingTransition,
            "forwarding_transitions",
        )
        paths = _path_mapping(route_path_connection_ids)
        path_states = {
            id(transition): _forwarding_transition_paths(
                selected, transition, paths
            )
            for transition in transitions
        }
        result = []
        for predecessor in transitions:
            predecessor_old_path, predecessor_new_path = path_states[
                id(predecessor)
            ]
            for successor in transitions:
                if predecessor is successor:
                    continue
                if predecessor.nominal_time != successor.nominal_time:
                    continue
                if predecessor.route_key.target != successor.route_key.target:
                    continue
                successor_old_path, successor_new_path = path_states[
                    id(successor)
                ]
                prepares_direct_upstream = (
                    predecessor.operation is not DifferenceOperation.REMOVED
                    and successor.operation is not DifferenceOperation.REMOVED
                    and _is_direct_upstream_path(
                        successor_new_path, predecessor_new_path
                    )
                )
                removes_direct_downstream = (
                    predecessor.operation is not DifferenceOperation.ADDED
                    and successor.operation is DifferenceOperation.REMOVED
                    and _is_direct_upstream_path(
                        predecessor_old_path, successor_old_path
                    )
                )
                if prepares_direct_upstream or removes_direct_downstream:
                    result.append(
                        ForwardingTransitionPrecedence(
                            predecessor=predecessor,
                            successor=successor,
                        )
                    )
        return tuple(result)


@runtime_checkable
class CoordinationPolicy(Protocol):
    """Produce one coherent cross-domain result for the planning window.

    Layer: replaceable Simulator Planner cross-domain policy. It is public
    from ``skyseed_v2.simulator`` and transforms Model results. It does not
    own fixed validity, backend behavior, or the published Plan.

    Planner supplies all three Model results, matched short-lifetime
    candidates, and immutable formal route/path facts. The policy returns the
    complete coordinated transition collections plus concise decisions.
    Planner retains ownership and enforces fixed validity afterward.
    """

    def coordinate_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        suppression_candidates: tuple[
            tuple[ConnectionTransition, ConnectionTransition], ...
        ],
        connection_transitions: tuple[ConnectionTransition, ...],
        parameter_transitions: tuple[ParameterTransition, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
        route_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
    ) -> tuple[
        tuple[ConnectionTransition, ...],
        tuple[ParameterTransition, ...],
        tuple[ForwardingTransition, ...],
        tuple[PlanningDecision, ...],
    ]:
        """Return complete coordinated transition and decision collections."""


class DefaultCoordinationPolicy:
    """Jointly suppress safe short lifetimes and their transient effects.

    Layer: default Simulator Planner cross-domain policy. It is public from
    ``skyseed_v2.simulator`` and reconciles semantic transition collections,
    not backend operations or runtime execution.
    """

    __slots__ = ()

    def coordinate_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        suppression_candidates: tuple[
            tuple[ConnectionTransition, ConnectionTransition], ...
        ],
        connection_transitions: tuple[ConnectionTransition, ...],
        parameter_transitions: tuple[ParameterTransition, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
        route_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
    ) -> tuple[
        tuple[ConnectionTransition, ...],
        tuple[ParameterTransition, ...],
        tuple[ForwardingTransition, ...],
        tuple[PlanningDecision, ...],
    ]:
        candidates = tuple(suppression_candidates)
        if any(
            not isinstance(candidate, tuple)
            or len(candidate) != 2
            or any(
                not isinstance(value, ConnectionTransition)
                for value in candidate
            )
            for candidate in candidates
        ):
            raise TypeError("suppression_candidates must contain transition pairs")
        if not candidates:
            return (
                connection_transitions,
                parameter_transitions,
                forwarding_transitions,
                (),
            )
        selected = _difference_tuple(differences, "differences")
        connections = list(
            _typed_tuple(
                connection_transitions,
                ConnectionTransition,
                "connection_transitions",
            )
        )
        parameters = list(
            _typed_tuple(
                parameter_transitions,
                ParameterTransition,
                "parameter_transitions",
            )
        )
        forwarding = list(
            _typed_tuple(
                forwarding_transitions,
                ForwardingTransition,
                "forwarding_transitions",
            )
        )
        next_hops = _next_hop_mapping(route_next_hops)
        paths = _path_mapping(route_path_connection_ids)

        decisions: list[PlanningDecision] = []
        for establish, withdraw in candidates:
            _require_lifetime(establish, withdraw)
            if establish not in connections or withdraw not in connections:
                continue
            establish_difference = _connection_difference(
                selected, establish
            )
            withdraw_difference = _connection_difference(selected, withdraw)
            start_id = establish_difference.current_slice_id
            end_id = withdraw_difference.current_slice_id
            connection_id = establish.connection_id
            final_supports_suppression = not any(
                connection_id in dependencies
                for dependencies in paths.get(end_id, {}).values()
            )
            if not final_supports_suppression:
                continue

            affected_route_keys = set()
            for slice_id in range(
                establish_difference.previous_slice_id, end_id + 1
            ):
                affected_route_keys.update(
                    route_key
                    for route_key, dependencies in paths.get(
                        slice_id, {}
                    ).items()
                    if connection_id in dependencies
                )
            related_parameters = [
                transition
                for transition in parameters
                if transition.connection_id == connection_id
                and start_id
                <= _difference_for_time(
                    selected, transition.nominal_time
                ).current_slice_id
                <= end_id
            ]
            related_forwarding = [
                transition
                for transition in forwarding
                if transition.route_key in affected_route_keys
                and start_id
                <= _difference_for_time(
                    selected, transition.nominal_time
                ).current_slice_id
                <= end_id
            ]
            suppression_is_safe = all(
                next_hops.get(
                    establish_difference.previous_slice_id, {}
                ).get(route_key)
                == next_hops.get(end_id, {}).get(route_key)
                and paths.get(
                    establish_difference.previous_slice_id, {}
                ).get(route_key, ())
                == paths.get(end_id, {}).get(route_key, ())
                and any(
                    transition.route_key == route_key
                    for transition in related_forwarding
                )
                for route_key in affected_route_keys
            )
            if not suppression_is_safe:
                continue

            connections = [
                transition
                for transition in connections
                if transition is not establish and transition is not withdraw
            ]
            parameters = [
                transition
                for transition in parameters
                if transition not in related_parameters
            ]
            forwarding = [
                transition
                for transition in forwarding
                if transition not in related_forwarding
            ]
            decisions.append(
                PlanningDecision(
                    source_difference_ids=tuple(range(start_id, end_id + 1)),
                    decision="suppressed",
                    decided_by=type(self).__name__,
                    reason=(
                        f"suppressed short lifetime for {connection_id!r}; "
                        f"removed {len(related_parameters)} parameter intents "
                        f"and coalesced {len(related_forwarding)} forwarding "
                        "intents"
                    ),
                )
            )

        forwarding.sort(
            key=lambda transition: _forwarding_order_key(
                selected, transition, paths
            )
        )
        return (
            tuple(connections),
            tuple(parameters),
            tuple(forwarding),
            tuple(decisions),
        )


def _connection_transitions_at(
    difference: TopologyDifference,
    nominal_time: datetime,
) -> tuple[ConnectionTransition, ...]:
    return (
        *(
            ConnectionTransition(
                nominal_time=nominal_time,
                operation=ConnectionTransitionOperation.ESTABLISH,
                connection_id=connection.connection_id,
                connection=connection,
            )
            for connection in difference.added_connections
        ),
        *(
            ConnectionTransition(
                nominal_time=nominal_time,
                operation=ConnectionTransitionOperation.WITHDRAW,
                connection_id=connection_id,
            )
            for connection_id in difference.removed_connection_ids
        ),
    )


def _is_short_lifetime(
    establish: ConnectionTransition,
    withdraw: ConnectionTransition,
    minimum_dwell: timedelta | None,
) -> bool:
    _require_lifetime(establish, withdraw)
    return (
        minimum_dwell is not None
        and withdraw.nominal_time - establish.nominal_time < minimum_dwell
    )


def _require_lifetime(
    establish: ConnectionTransition,
    withdraw: ConnectionTransition,
) -> None:
    if not isinstance(establish, ConnectionTransition):
        raise TypeError("establish must be a ConnectionTransition")
    if not isinstance(withdraw, ConnectionTransition):
        raise TypeError("withdraw must be a ConnectionTransition")
    if establish.operation is not ConnectionTransitionOperation.ESTABLISH:
        raise ValueError("establish must use ESTABLISH")
    if withdraw.operation is not ConnectionTransitionOperation.WITHDRAW:
        raise ValueError("withdraw must use WITHDRAW")
    if establish.connection_id != withdraw.connection_id:
        raise ValueError("Connection lifetime identities must match")
    if withdraw.nominal_time <= establish.nominal_time:
        raise ValueError("Connection withdrawal must follow establishment")


def _require_planning_region(
    differences: tuple[TopologyDifference, ...],
    affected_differences: tuple[TopologyDifference, ...],
    previous_transitions: tuple[object, ...],
) -> None:
    if not isinstance(differences, tuple) or not differences:
        raise ValueError("differences must be a non-empty tuple")
    if not isinstance(affected_differences, tuple) or any(
        not isinstance(value, TopologyDifference)
        for value in affected_differences
    ):
        raise TypeError(
            "affected_differences must contain TopologyDifference values"
        )
    first_id = differences[0].current_slice_id
    for value in affected_differences:
        position = value.current_slice_id - first_id
        if (
            position < 0
            or position >= len(differences)
            or differences[position] is not value
        ):
            raise ValueError("affected_differences must belong to differences")
    tuple(previous_transitions)


def _difference_tuple(
    values: tuple[TopologyDifference, ...],
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[TopologyDifference, ...]:
    result = _typed_tuple(values, TopologyDifference, field_name)
    if not result and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _typed_tuple(values, expected_type: type, field_name: str) -> tuple:
    try:
        result = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in result):
        raise TypeError(
            f"{field_name} must contain {expected_type.__name__} values"
        )
    return result


def _parameter_mapping(
    values: Mapping[
        tuple[ConnectionId, ConnectionDirection],
        DirectionalConnectionParameters,
    ],
    field_name: str,
) -> dict[
    tuple[ConnectionId, ConnectionDirection], DirectionalConnectionParameters
]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a Mapping")
    result = dict(values)
    for key, value in result.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(key[0], ConnectionId)
            or not isinstance(key[1], ConnectionDirection)
            or not isinstance(value, DirectionalConnectionParameters)
        ):
            raise TypeError(f"{field_name} contains invalid parameter state")
    return result


def _next_hop_mapping(
    values: Mapping[int, Mapping[RouteKey, NodeId]],
) -> Mapping[int, Mapping[RouteKey, NodeId]]:
    if not isinstance(values, Mapping):
        raise TypeError("route_next_hops must be a Mapping")
    if any(
        isinstance(slice_id, bool)
        or not isinstance(slice_id, int)
        or not isinstance(routes, Mapping)
        or any(
            not isinstance(route_key, RouteKey)
            or not isinstance(next_hop, NodeId)
            for route_key, next_hop in routes.items()
        )
        for slice_id, routes in values.items()
    ):
        raise TypeError("route_next_hops contains invalid formal state")
    return values


def _path_mapping(
    values: Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
) -> Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]]:
    if not isinstance(values, Mapping):
        raise TypeError("route_path_connection_ids must be a Mapping")
    if any(
        isinstance(slice_id, bool)
        or not isinstance(slice_id, int)
        or not isinstance(routes, Mapping)
        or any(
            not isinstance(route_key, RouteKey)
            or not isinstance(connection_ids, tuple)
            or any(
                not isinstance(connection_id, ConnectionId)
                for connection_id in connection_ids
            )
            for route_key, connection_ids in routes.items()
        )
        for slice_id, routes in values.items()
    ):
        raise TypeError(
            "route_path_connection_ids contains invalid formal path state"
        )
    return values


def _initialize_parameter_maps(
    observed: dict,
    planned: dict,
    connection: Connection,
) -> None:
    for direction, parameters in (
        (ConnectionDirection.ENDPOINT_A_TO_B, connection.endpoint_a_to_b),
        (ConnectionDirection.ENDPOINT_B_TO_A, connection.endpoint_b_to_a),
    ):
        key = (connection.connection_id, direction)
        observed[key] = parameters
        planned[key] = parameters


def _clear_parameter_maps(
    observed: dict,
    planned: dict,
    connection_id: ConnectionId,
) -> None:
    for mapping in (observed, planned):
        for key in tuple(mapping):
            if key[0] == connection_id:
                mapping.pop(key)


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


def _connection_difference(
    differences: tuple[TopologyDifference, ...],
    transition: ConnectionTransition,
) -> TopologyDifference:
    source = _difference_for_time(differences, transition.nominal_time)
    if transition.operation is ConnectionTransitionOperation.ESTABLISH:
        if not any(
            connection.connection_id == transition.connection_id
            for connection in source.added_connections
        ):
            raise ValueError("Connection transition has no source Difference")
    elif transition.connection_id not in source.removed_connection_ids:
        raise ValueError("Connection transition has no source Difference")
    return source


def _difference_for_time(
    differences: tuple[TopologyDifference, ...],
    nominal_time: datetime,
) -> TopologyDifference:
    for difference in differences:
        if difference.previous_timestamp is None:
            if nominal_time == difference.current_timestamp:
                return difference
        elif (
            difference.previous_timestamp < nominal_time
            <= difference.current_timestamp
        ):
            return difference
    raise ValueError("transition nominal time is outside planning window")


def _forwarding_order_key(
    differences: tuple[TopologyDifference, ...],
    transition: ForwardingTransition,
    paths: Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
) -> tuple[datetime, int, int, str]:
    source = _difference_for_time(differences, transition.nominal_time)
    if transition.operation is DifferenceOperation.REMOVED:
        phase = 1
        depth = -len(
            paths.get(source.previous_slice_id, {}).get(
                transition.route_key, ()
            )
        )
    else:
        phase = 0
        depth = len(
            paths.get(source.current_slice_id, {}).get(
                transition.route_key, ()
            )
        )
    return transition.nominal_time, phase, depth, repr(transition.route_key)


def _forwarding_transition_paths(
    differences: tuple[TopologyDifference, ...],
    transition: ForwardingTransition,
    paths: Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
) -> tuple[tuple[ConnectionId, ...], tuple[ConnectionId, ...]]:
    source = _difference_for_time(differences, transition.nominal_time)
    return (
        tuple(
            paths.get(source.previous_slice_id, {}).get(
                transition.route_key, ()
            )
        ),
        tuple(
            paths.get(source.current_slice_id, {}).get(
                transition.route_key, ()
            )
        ),
    )


def _is_direct_upstream_path(
    upstream: tuple[ConnectionId, ...],
    downstream: tuple[ConnectionId, ...],
) -> bool:
    return len(upstream) == len(downstream) + 1 and upstream[1:] == downstream


def _validate_minimum_dwell(
    value: timedelta | None,
) -> timedelta | None:
    if value is None:
        return None
    if not isinstance(value, timedelta):
        raise TypeError("minimum_dwell must be a timedelta or None")
    if value <= timedelta(0):
        raise ValueError("minimum_dwell must be positive")
    return value


def _validate_threshold(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


__all__ = [
    "AccumulatedDeadbandParameterPlanningModel",
    "AllAvailablePlanningWindowPolicy",
    "ConnectionPlanningModel",
    "CoordinationPolicy",
    "DefaultCoordinationPolicy",
    "DependencyAwareForwardingPlanningModel",
    "ForwardingPlanningModel",
    "MidpointConnectionPlanningModel",
    "ParameterPlanningModel",
    "PlanningWindowPolicy",
    "SampleBoundaryConnectionPlanningModel",
]
