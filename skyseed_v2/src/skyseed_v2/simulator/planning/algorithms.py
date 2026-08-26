"""Define replaceable planning-window-aware Planner algorithms."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from skyseed_v2.shared import NodeId

from ..slicing import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    DifferenceOperation,
    DirectionalConnectionParameters,
    RouteKey,
    RouteTarget,
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
from ._difference_time import _DifferenceTimeResolver


_PARAMETER_FIELDS = (
    "bandwidth_bps",
    "delay_us",
    "packet_loss_ppm",
)
_ParameterKey = tuple[ConnectionId, ConnectionDirection]
_ParameterFieldKey = tuple[ConnectionId, ConnectionDirection, str]
_EMPTY_MAPPING: Mapping = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ConnectionEpisodeConfiguration:
    """Configure per-kind Connection episode omission candidacy.

    Layer: immutable Simulator Planner algorithm configuration. It is public
    from ``skyseed_v2.simulator`` and is not a physical setup-cost model.
    """

    omission_candidate_max_duration_by_kind: Mapping[
        ConnectionKind, timedelta
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(
            self.omission_candidate_max_duration_by_kind, Mapping
        ):
            raise TypeError(
                "omission_candidate_max_duration_by_kind must be a Mapping"
            )
        values = dict(self.omission_candidate_max_duration_by_kind)
        if any(
            not isinstance(kind, ConnectionKind)
            or not isinstance(duration, timedelta)
            or duration <= timedelta(0)
            for kind, duration in values.items()
        ):
            raise ValueError(
                "episode candidate durations must map ConnectionKind values "
                "to positive timedeltas"
            )
        object.__setattr__(
            self,
            "omission_candidate_max_duration_by_kind",
            MappingProxyType(values),
        )


@dataclass(frozen=True, slots=True)
class ParameterFieldConfiguration:
    """Configure one parameter field's deadband and temporal triggers.

    Layer: immutable Simulator Planner algorithm configuration. It is public
    from ``skyseed_v2.simulator`` and is not a backend traffic-control value.
    """

    absolute_deadband: int | None = None
    relative_deadband: float | None = None
    persistence: timedelta | None = None
    max_staleness: timedelta | None = None

    def __post_init__(self) -> None:
        if (
            self.absolute_deadband is not None
            and (
                isinstance(self.absolute_deadband, bool)
                or not isinstance(self.absolute_deadband, int)
                or self.absolute_deadband < 0
            )
        ):
            raise ValueError(
                "absolute_deadband must be a non-negative integer or None"
            )
        if self.relative_deadband is not None and (
            isinstance(self.relative_deadband, bool)
            or not isinstance(self.relative_deadband, (int, float))
            or not isfinite(float(self.relative_deadband))
            or self.relative_deadband < 0
        ):
            raise ValueError(
                "relative_deadband must be a finite non-negative number or None"
            )
        object.__setattr__(
            self,
            "relative_deadband",
            (
                float(self.relative_deadband)
                if self.relative_deadband is not None
                else None
            ),
        )
        for name in ("persistence", "max_staleness"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, timedelta) or value < timedelta(0)
            ):
                raise ValueError(
                    f"{name} must be a non-negative timedelta or None"
                )


@dataclass(frozen=True, slots=True)
class ParameterPlanningConfiguration:
    """Configure field-specific parameter stability behavior.

    Layer: immutable Simulator Planner algorithm configuration. It is public
    from ``skyseed_v2.simulator`` and is not planned network state.
    """

    bandwidth_bps: ParameterFieldConfiguration = field(
        default_factory=ParameterFieldConfiguration
    )
    delay_us: ParameterFieldConfiguration = field(
        default_factory=ParameterFieldConfiguration
    )
    packet_loss_ppm: ParameterFieldConfiguration = field(
        default_factory=ParameterFieldConfiguration
    )
    bundle_drifted_fields: bool = False

    def __post_init__(self) -> None:
        for name in _PARAMETER_FIELDS:
            if not isinstance(
                getattr(self, name), ParameterFieldConfiguration
            ):
                raise TypeError(
                    f"{name} must be a ParameterFieldConfiguration"
                )
        if not isinstance(self.bundle_drifted_fields, bool):
            raise TypeError("bundle_drifted_fields must be a bool")


@dataclass(frozen=True, slots=True)
class ForwardingStabilityConfiguration:
    """Configure interpretable stable-forwarding FOLLOW thresholds.

    Layer: immutable Simulator Planner algorithm configuration. It is public
    from ``skyseed_v2.simulator`` and is not a Route computation model.
    """

    minimum_hop_gain: int | None = None
    minimum_delay_gain_us: int | None = None
    persistence: timedelta | None = None
    immediate_hop_gain: int | None = None
    immediate_delay_gain_us: int | None = None
    maximum_hold: timedelta | None = None

    def __post_init__(self) -> None:
        for name in (
            "minimum_hop_gain",
            "minimum_delay_gain_us",
            "immediate_hop_gain",
            "immediate_delay_gain_us",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"{name} must be a non-negative integer or None"
                )
        for name in ("persistence", "maximum_hold"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, timedelta) or value < timedelta(0)
            ):
                raise ValueError(
                    f"{name} must be a non-negative timedelta or None"
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


class TimeHorizonPlanningWindowPolicy:
    """Select exact time-bounded planning and publication prefixes.

    Layer: replaceable Simulator Planner window algorithm. It is public from
    ``skyseed_v2.simulator`` and is not a lifecycle or scheduling owner.

    ``planning_lookahead=None`` preserves global/offline inspection while
    still allowing bounded publication. A finite lookahead provides rolling
    continuous operation. Horizons include a Difference whose current
    timestamp is exactly on the boundary. Because Differences are
    indivisible and publication must progress, the first Difference is always
    selected even when its single interval exceeds a configured horizon.
    """

    __slots__ = ("_planning_lookahead", "_publication_horizon")

    def __init__(
        self,
        *,
        planning_lookahead: timedelta | None,
        publication_horizon: timedelta,
    ) -> None:
        self._planning_lookahead = _positive_timedelta_or_none(
            planning_lookahead, "planning_lookahead"
        )
        self._publication_horizon = _positive_timedelta(
            publication_horizon, "publication_horizon"
        )
        if (
            self._planning_lookahead is not None
            and self._publication_horizon > self._planning_lookahead
        ):
            raise ValueError(
                "publication_horizon must not exceed planning_lookahead"
            )

    def select_planning_differences(
        self,
        available_differences: tuple[TopologyDifference, ...],
    ) -> tuple[TopologyDifference, ...]:
        values = tuple(available_differences)
        if self._planning_lookahead is None:
            return values
        return _difference_time_prefix(values, self._planning_lookahead)

    def select_publication_differences(
        self,
        planning_differences: tuple[TopologyDifference, ...],
    ) -> tuple[TopologyDifference, ...]:
        return _difference_time_prefix(
            tuple(planning_differences), self._publication_horizon
        )


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

    __slots__ = ("_episode_configuration",)

    def __init__(
        self,
        *,
        episode_configuration: ConnectionEpisodeConfiguration | None = None,
        minimum_dwell: timedelta | None = None,
    ) -> None:
        self._episode_configuration = _connection_episode_configuration(
            episode_configuration, minimum_dwell
        )

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
        return _is_episode_candidate(
            establish, withdraw, self._episode_configuration
        )


class MidpointConnectionPlanningModel:
    """Estimate affected real-sample changes at interval midpoints.

    Layer: reference Simulator Planner Connection algorithm. It is public
    from ``skyseed_v2.simulator`` and estimates semantic time. It does not
    recompute physical visibility.
    """

    __slots__ = ("_episode_configuration",)

    def __init__(
        self,
        *,
        episode_configuration: ConnectionEpisodeConfiguration | None = None,
        minimum_dwell: timedelta | None = None,
    ) -> None:
        self._episode_configuration = _connection_episode_configuration(
            episode_configuration, minimum_dwell
        )

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
        return _is_episode_candidate(
            establish, withdraw, self._episode_configuration
        )


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
        parameter_drift_started_at: Mapping[
            _ParameterFieldKey, datetime
        ] = _EMPTY_MAPPING,
        parameter_persistence_started_at: Mapping[
            _ParameterFieldKey, datetime
        ] = _EMPTY_MAPPING,
    ) -> tuple[ParameterTransition, ...]:
        """Return the complete planning-window Parameter result."""


class SampleBoundaryParameterPlanningModel:
    """Emit every formal parameter patch at its sampled boundary.

    Layer: reference Simulator Planner Parameter algorithm. It is public from
    ``skyseed_v2.simulator`` and is not a backend traffic-control writer.
    """

    __slots__ = ()

    def plan_parameter_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ParameterTransition, ...],
        observed_parameters: Mapping[
            _ParameterKey, DirectionalConnectionParameters
        ],
        planned_parameters: Mapping[
            _ParameterKey, DirectionalConnectionParameters
        ],
        parameter_drift_started_at: Mapping[
            _ParameterFieldKey, datetime
        ] = _EMPTY_MAPPING,
        parameter_persistence_started_at: Mapping[
            _ParameterFieldKey, datetime
        ] = _EMPTY_MAPPING,
    ) -> tuple[ParameterTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        _parameter_mapping(observed_parameters, "observed_parameters")
        _parameter_mapping(planned_parameters, "planned_parameters")
        _parameter_time_mapping(
            parameter_drift_started_at, "parameter_drift_started_at"
        )
        _parameter_time_mapping(
            parameter_persistence_started_at,
            "parameter_persistence_started_at",
        )
        return (
            *previous_transitions,
            *(
                ParameterTransition(
                    nominal_time=difference.current_timestamp,
                    connection_id=patch.connection_id,
                    direction=patch.direction,
                    bandwidth_bps=patch.bandwidth_bps,
                    delay_us=patch.delay_us,
                    packet_loss_ppm=patch.packet_loss_ppm,
                )
                for difference in affected_differences
                for patch in difference.connection_parameter_patches
            ),
        )


class AccumulatedDeadbandParameterPlanningModel:
    """Emit accumulated targets after deadband and temporal triggers.

    Layer: reference Simulator Planner Parameter algorithm. It is public from
    ``skyseed_v2.simulator`` and emits semantic sparse targets, not backend
    traffic-control actions.
    """

    __slots__ = ("_configuration",)

    def __init__(
        self,
        *,
        configuration: ParameterPlanningConfiguration | None = None,
        bandwidth_bps_threshold: int = 0,
        delay_us_threshold: int = 0,
        packet_loss_ppm_threshold: int = 0,
        include_accumulated_fields: bool = False,
    ) -> None:
        if configuration is not None:
            if not isinstance(configuration, ParameterPlanningConfiguration):
                raise TypeError(
                    "configuration must be a ParameterPlanningConfiguration"
                )
            if (
                bandwidth_bps_threshold
                or delay_us_threshold
                or packet_loss_ppm_threshold
                or include_accumulated_fields
            ):
                raise ValueError(
                    "configuration cannot be combined with legacy threshold "
                    "arguments"
                )
            self._configuration = configuration
            return
        for value, name in (
            (bandwidth_bps_threshold, "bandwidth_bps_threshold"),
            (delay_us_threshold, "delay_us_threshold"),
            (packet_loss_ppm_threshold, "packet_loss_ppm_threshold"),
        ):
            _validate_threshold(value, name)
        if not isinstance(include_accumulated_fields, bool):
            raise TypeError("include_accumulated_fields must be a bool")
        self._configuration = ParameterPlanningConfiguration(
            bandwidth_bps=ParameterFieldConfiguration(
                absolute_deadband=bandwidth_bps_threshold
            ),
            delay_us=ParameterFieldConfiguration(
                absolute_deadband=delay_us_threshold
            ),
            packet_loss_ppm=ParameterFieldConfiguration(
                absolute_deadband=packet_loss_ppm_threshold
            ),
            bundle_drifted_fields=include_accumulated_fields,
        )

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
        parameter_drift_started_at: Mapping[
            _ParameterFieldKey, datetime
        ] = _EMPTY_MAPPING,
        parameter_persistence_started_at: Mapping[
            _ParameterFieldKey, datetime
        ] = _EMPTY_MAPPING,
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

        drift_started = _parameter_time_mapping(
            parameter_drift_started_at, "parameter_drift_started_at"
        )
        persistence_started = _parameter_time_mapping(
            parameter_persistence_started_at,
            "parameter_persistence_started_at",
        )
        result = list(previous_transitions)
        for difference in affected_differences:
            for connection in difference.added_connections:
                _initialize_parameter_maps(observed, planned, connection)
                _clear_parameter_times(
                    drift_started,
                    persistence_started,
                    connection.connection_id,
                )
            for patch in difference.connection_parameter_patches:
                key = (patch.connection_id, patch.direction)
                try:
                    previous_observed = observed[key]
                    current = _apply_parameter_patch(observed[key], patch)
                    last_planned = planned[key]
                except KeyError as error:
                    raise ValueError(
                        "parameter patch is outside an active Connection"
                    ) from error
                observed[key] = current
                for field_name in _PARAMETER_FIELDS:
                    self._update_temporal_field_state(
                        patch.connection_id,
                        patch.direction,
                        field_name,
                        getattr(previous_observed, field_name),
                        getattr(current, field_name),
                        getattr(last_planned, field_name),
                        difference.current_timestamp,
                        drift_started,
                        persistence_started,
                    )

            # A sampled removal is already outside formal Connection state at
            # this boundary. Discard its private drift state before evaluating
            # boundary-triggered persistence/staleness emissions.
            for connection_id in difference.removed_connection_ids:
                _clear_parameter_maps(observed, planned, connection_id)
                _clear_parameter_times(
                    drift_started, persistence_started, connection_id
                )

            due_by_key: dict[_ParameterKey, set[str]] = {}
            for key, current in observed.items():
                last_planned = planned[key]
                for field_name in _PARAMETER_FIELDS:
                    field_key = (*key, field_name)
                    field_configuration = getattr(
                        self._configuration, field_name
                    )
                    if self._field_triggers(
                        field_configuration,
                        difference.current_timestamp,
                        field_key,
                        getattr(current, field_name),
                        getattr(last_planned, field_name),
                        drift_started,
                        persistence_started,
                    ):
                        due_by_key.setdefault(key, set()).add(field_name)

            for key, triggered in due_by_key.items():
                current = observed[key]
                last_planned = planned[key]
                selected = triggered
                if self._configuration.bundle_drifted_fields:
                    selected = {
                        field_name
                        for field_name in _PARAMETER_FIELDS
                        if getattr(current, field_name)
                        != getattr(last_planned, field_name)
                    }
                transition = ParameterTransition(
                    nominal_time=difference.current_timestamp,
                    connection_id=key[0],
                    direction=key[1],
                    **{
                        field_name: (
                            getattr(current, field_name)
                            if field_name in selected
                            else None
                        )
                        for field_name in _PARAMETER_FIELDS
                    },
                )
                result.append(transition)
                planned[key] = _apply_parameter_transition(
                    last_planned, transition
                )
                for field_name in selected:
                    field_key = (*key, field_name)
                    drift_started.pop(field_key, None)
                    persistence_started.pop(field_key, None)
        return tuple(result)

    def _field_is_outside_effective_band(
        self,
        field_name: str,
        observed_value: int,
        planned_value: int,
    ) -> bool:
        configuration = getattr(self._configuration, field_name)
        absolute = configuration.absolute_deadband or 0
        relative = configuration.relative_deadband or 0.0
        threshold = max(absolute, relative * abs(planned_value))
        return (
            observed_value != planned_value
            and abs(observed_value - planned_value) >= threshold
        )

    def _update_temporal_field_state(
        self,
        connection_id: ConnectionId,
        direction: ConnectionDirection,
        field_name: str,
        previous_observed: int,
        observed: int,
        planned: int,
        timestamp: datetime,
        drift_started: dict[_ParameterFieldKey, datetime],
        persistence_started: dict[_ParameterFieldKey, datetime],
    ) -> None:
        key = (connection_id, direction, field_name)
        if observed == planned:
            drift_started.pop(key, None)
        else:
            drift_started.setdefault(key, timestamp)

        outside = self._field_is_outside_effective_band(
            field_name, observed, planned
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

    @staticmethod
    def _field_triggers(
        configuration: ParameterFieldConfiguration,
        timestamp: datetime,
        key: _ParameterFieldKey,
        observed: int,
        planned: int,
        drift_started: Mapping[_ParameterFieldKey, datetime],
        persistence_started: Mapping[_ParameterFieldKey, datetime],
    ) -> bool:
        if observed == planned:
            return False
        persistence_triggered = False
        persistence_start = persistence_started.get(key)
        if persistence_start is not None:
            persistence = configuration.persistence or timedelta(0)
            persistence_triggered = timestamp - persistence_start >= persistence
        staleness_triggered = False
        drift_start = drift_started.get(key)
        if (
            drift_start is not None
            and configuration.max_staleness is not None
        ):
            staleness_triggered = (
                timestamp - drift_start >= configuration.max_staleness
            )
        return persistence_triggered or staleness_triggered


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
        formal_route_candidate_started_at: Mapping[RouteKey, datetime],
        planned_route_divergence_started_at: Mapping[RouteKey, datetime],
    ) -> tuple[ForwardingTransition, ...]:
        """Return the complete planning-window Forwarding result."""

    def plan_forwarding_precedences(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
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
        route_next_hops: Mapping[
            int, Mapping[RouteKey, NodeId]
        ] = _EMPTY_MAPPING,
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
        connection_values_by_slice_id: Mapping[
            int, Mapping[ConnectionId, Connection]
        ] = _EMPTY_MAPPING,
        published_route_next_hops: Mapping[
            RouteKey, NodeId
        ] = _EMPTY_MAPPING,
        published_route_path_connection_ids: Mapping[
            RouteKey, tuple[ConnectionId, ...]
        ] = _EMPTY_MAPPING,
        formal_route_candidate_started_at: Mapping[
            RouteKey, datetime
        ] = _EMPTY_MAPPING,
        planned_route_divergence_started_at: Mapping[
            RouteKey, datetime
        ] = _EMPTY_MAPPING,
    ) -> tuple[ForwardingTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        time_resolver = _DifferenceTimeResolver(differences)
        transitions = _typed_tuple(
            connection_transitions,
            ConnectionTransition,
            "connection_transitions",
        )
        _next_hop_mapping(route_next_hops)
        paths = _path_mapping(route_path_connection_ids)
        _connection_value_mapping(connection_values_by_slice_id)
        _route_next_hop_state(
            published_route_next_hops, "published_route_next_hops"
        )
        _route_path_state(
            published_route_path_connection_ids,
            "published_route_path_connection_ids",
        )
        _route_time_mapping(
            formal_route_candidate_started_at,
            "formal_route_candidate_started_at",
        )
        _route_time_mapping(
            planned_route_divergence_started_at,
            "planned_route_divergence_started_at",
        )
        withdrawal_times = {}
        for transition in transitions:
            if (
                transition.operation
                is not ConnectionTransitionOperation.WITHDRAW
            ):
                continue
            source = _connection_difference(
                differences,
                transition,
                time_resolver=time_resolver,
            )
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
        route_next_hops: Mapping[
            int, Mapping[RouteKey, NodeId]
        ] = _EMPTY_MAPPING,
        route_path_connection_ids: Mapping[
            int, Mapping[RouteKey, tuple[ConnectionId, ...]]
        ],
        connection_values_by_slice_id: Mapping[
            int, Mapping[ConnectionId, Connection]
        ] = _EMPTY_MAPPING,
        published_route_next_hops: Mapping[
            RouteKey, NodeId
        ] = _EMPTY_MAPPING,
        published_route_path_connection_ids: Mapping[
            RouteKey, tuple[ConnectionId, ...]
        ] = _EMPTY_MAPPING,
    ) -> tuple[ForwardingTransitionPrecedence, ...]:
        selected = _difference_tuple(differences, "differences")
        transitions = _typed_tuple(
            forwarding_transitions,
            ForwardingTransition,
            "forwarding_transitions",
        )
        paths = _path_mapping(route_path_connection_ids)
        _next_hop_mapping(route_next_hops)
        _connection_value_mapping(connection_values_by_slice_id)
        _route_next_hop_state(
            published_route_next_hops, "published_route_next_hops"
        )
        _route_path_state(
            published_route_path_connection_ids,
            "published_route_path_connection_ids",
        )
        time_resolver = _DifferenceTimeResolver(selected)
        path_states = {
            id(transition): _forwarding_transition_paths(
                selected,
                transition,
                paths,
                time_resolver=time_resolver,
            )
            for transition in transitions
        }
        candidates_by_key: dict[
            tuple[datetime, RouteTarget],
            list[ForwardingTransition],
        ] = {}
        for transition in transitions:
            candidates_by_key.setdefault(
                (transition.nominal_time, transition.route_key.target), []
            ).append(transition)
        result = []
        for predecessor in transitions:
            predecessor_old_path, predecessor_new_path = path_states[
                id(predecessor)
            ]
            for successor in candidates_by_key[
                (predecessor.nominal_time, predecessor.route_key.target)
            ]:
                if predecessor is successor:
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


class StableForwardingPlanningModel:
    """Choose a coherent formal FOLLOW or supported planned HOLD path.

    Layer: optimized Simulator Planner Forwarding algorithm. It is public
    from ``skyseed_v2.simulator`` and is not a third Route computation model.
    """

    __slots__ = ("_configuration",)

    def __init__(
        self, *, configuration: ForwardingStabilityConfiguration
    ) -> None:
        if not isinstance(configuration, ForwardingStabilityConfiguration):
            raise TypeError(
                "configuration must be a ForwardingStabilityConfiguration"
            )
        self._configuration = configuration

    def plan_forwarding_transitions(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        affected_differences: tuple[TopologyDifference, ...],
        previous_transitions: tuple[ForwardingTransition, ...],
        connection_transitions: tuple[ConnectionTransition, ...],
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
        formal_route_candidate_started_at: Mapping[RouteKey, datetime],
        planned_route_divergence_started_at: Mapping[RouteKey, datetime],
    ) -> tuple[ForwardingTransition, ...]:
        _require_planning_region(
            differences, affected_differences, previous_transitions
        )
        next_hops = _next_hop_mapping(route_next_hops)
        formal_paths = _path_mapping(route_path_connection_ids)
        connections = _connection_value_mapping(
            connection_values_by_slice_id
        )
        planned_next_hops = _route_next_hop_state(
            published_route_next_hops, "published_route_next_hops"
        )
        planned_paths = _route_path_state(
            published_route_path_connection_ids,
            "published_route_path_connection_ids",
        )
        candidate_started = _route_time_mapping(
            formal_route_candidate_started_at,
            "formal_route_candidate_started_at",
        )
        divergence_started = _route_time_mapping(
            planned_route_divergence_started_at,
            "planned_route_divergence_started_at",
        )
        transitions = _typed_tuple(
            connection_transitions,
            ConnectionTransition,
            "connection_transitions",
        )
        resolver = _DifferenceTimeResolver(differences)
        withdrawal_times: dict[tuple[int, ConnectionId], datetime] = {}
        for transition in transitions:
            if transition.operation is ConnectionTransitionOperation.WITHDRAW:
                source = _connection_difference(
                    differences, transition, time_resolver=resolver
                )
                withdrawal_times[
                    (source.current_slice_id, transition.connection_id)
                ] = transition.nominal_time

        result: list[ForwardingTransition] = []
        for difference in differences:
            slice_id = difference.current_slice_id
            current_formal_paths = dict(formal_paths.get(slice_id, {}))
            previous_formal_paths = formal_paths.get(
                difference.previous_slice_id, {}
            )
            current_connections = connections.get(slice_id, {})
            timestamp = difference.current_timestamp
            route_keys = set(planned_paths) | set(current_formal_paths)
            follow: dict[RouteKey, bool] = {}
            path_nodes_cache: dict[
                tuple[NodeId, tuple[ConnectionId, ...]],
                tuple[NodeId, ...] | None,
            ] = {}
            path_delay_cache: dict[
                tuple[NodeId, tuple[ConnectionId, ...]], int
            ] = {}

            for route_key in route_keys:
                planned_path = planned_paths.get(route_key)
                formal_path = current_formal_paths.get(route_key)
                if planned_path is None or formal_path is None:
                    follow[route_key] = True
                    continue
                if planned_path == formal_path:
                    candidate_started.pop(route_key, None)
                    divergence_started.pop(route_key, None)
                    follow[route_key] = False
                    continue

                previous_formal = previous_formal_paths.get(route_key)
                if (
                    route_key not in candidate_started
                    or previous_formal != formal_path
                    or previous_formal == planned_path
                ):
                    candidate_started[route_key] = timestamp
                divergence_started.setdefault(route_key, timestamp)

                planned_nodes = _cached_path_nodes(
                    path_nodes_cache,
                    route_key.source,
                    planned_path,
                    current_connections,
                )
                formal_nodes = _cached_path_nodes(
                    path_nodes_cache,
                    route_key.source,
                    formal_path,
                    current_connections,
                )
                if (
                    planned_nodes is None
                    or formal_nodes is None
                    or planned_nodes[-1] != formal_nodes[-1]
                ):
                    follow[route_key] = True
                    continue

                hop_gain = len(planned_path) - len(formal_path)
                delay_gain = _cached_path_delay_us(
                    path_delay_cache,
                    planned_nodes,
                    planned_path,
                    current_connections,
                ) - _cached_path_delay_us(
                    path_delay_cache,
                    formal_nodes,
                    formal_path,
                    current_connections,
                )
                immediate = _meets_forwarding_gain(
                    hop_gain,
                    delay_gain,
                    self._configuration.immediate_hop_gain,
                    self._configuration.immediate_delay_gain_us,
                    default=False,
                )
                normal = _meets_forwarding_gain(
                    hop_gain,
                    delay_gain,
                    self._configuration.minimum_hop_gain,
                    self._configuration.minimum_delay_gain_us,
                    default=True,
                )
                persistence = self._configuration.persistence or timedelta(0)
                persistent = (
                    timestamp - candidate_started[route_key] >= persistence
                )
                maximum_hold = self._configuration.maximum_hold
                hold_expired = (
                    maximum_hold is not None
                    and timestamp - divergence_started[route_key]
                    >= maximum_hold
                )
                follow[route_key] = (
                    immediate or hold_expired or (normal and persistent)
                )

            _promote_dependency_consistent_follow(
                follow,
                planned_paths,
                current_formal_paths,
                current_connections,
                path_nodes_cache=path_nodes_cache,
            )

            selected_paths = {
                route_key: (
                    current_formal_paths[route_key]
                    if follow[route_key]
                    else planned_paths[route_key]
                )
                for route_key in route_keys
                if (
                    route_key in current_formal_paths
                    if follow[route_key]
                    else route_key in planned_paths
                )
            }
            selected_next_hops: dict[RouteKey, NodeId] = {}
            for route_key, path in selected_paths.items():
                nodes = _cached_path_nodes(
                    path_nodes_cache,
                    route_key.source,
                    path,
                    current_connections,
                )
                if nodes is None or len(nodes) < 2:
                    raise ValueError(
                        "selected forwarding path lacks current Connection support"
                    )
                selected_next_hops[route_key] = nodes[1]

            changed: list[ForwardingTransition] = []
            for route_key in sorted(route_keys, key=repr):
                old_next_hop = planned_next_hops.get(route_key)
                new_next_hop = selected_next_hops.get(route_key)
                if old_next_hop == new_next_hop:
                    continue
                if new_next_hop is None:
                    operation = DifferenceOperation.REMOVED
                elif old_next_hop is None:
                    operation = DifferenceOperation.ADDED
                else:
                    operation = DifferenceOperation.CHANGED
                nominal_time = timestamp
                old_path = planned_paths.get(route_key, ())
                relevant_withdrawals = tuple(
                    withdrawal_times[(slice_id, connection_id)]
                    for connection_id in old_path
                    if (slice_id, connection_id) in withdrawal_times
                )
                if relevant_withdrawals:
                    nominal_time = min(nominal_time, *relevant_withdrawals)
                changed.append(
                    ForwardingTransition(
                        nominal_time=nominal_time,
                        route_key=route_key,
                        operation=operation,
                        next_hop=new_next_hop,
                    )
                )
            result.extend(
                sorted(
                    changed,
                    key=lambda transition: _forwarding_order_key(
                        differences,
                        transition,
                        formal_paths,
                        time_resolver=resolver,
                    ),
                )
            )
            planned_next_hops = selected_next_hops
            planned_paths = selected_paths
            for route_key in tuple(candidate_started):
                if route_key not in planned_paths:
                    candidate_started.pop(route_key, None)
                    divergence_started.pop(route_key, None)
                elif planned_paths.get(route_key) == current_formal_paths.get(
                    route_key
                ):
                    candidate_started.pop(route_key, None)
                    divergence_started.pop(route_key, None)
        return tuple(result)

    def plan_forwarding_precedences(
        self,
        *,
        differences: tuple[TopologyDifference, ...],
        forwarding_transitions: tuple[ForwardingTransition, ...],
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
    ) -> tuple[ForwardingTransitionPrecedence, ...]:
        return _planned_forwarding_precedences(
            differences,
            forwarding_transitions,
            route_next_hops,
            route_path_connection_ids,
            connection_values_by_slice_id,
            published_route_next_hops,
            published_route_path_connection_ids,
        )


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
        connection_values_by_slice_id: Mapping[
            int, Mapping[ConnectionId, Connection]
        ],
        published_route_next_hops: Mapping[RouteKey, NodeId],
        published_route_path_connection_ids: Mapping[
            RouteKey, tuple[ConnectionId, ...]
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
        connection_values_by_slice_id: Mapping[
            int, Mapping[ConnectionId, Connection]
        ],
        published_route_next_hops: Mapping[RouteKey, NodeId],
        published_route_path_connection_ids: Mapping[
            RouteKey, tuple[ConnectionId, ...]
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
        formal_next_hops = _next_hop_mapping(route_next_hops)
        formal_paths = _path_mapping(route_path_connection_ids)
        connection_values = _connection_value_mapping(
            connection_values_by_slice_id
        )
        published_next_hops = _route_next_hop_state(
            published_route_next_hops, "published_route_next_hops"
        )
        published_paths = _route_path_state(
            published_route_path_connection_ids,
            "published_route_path_connection_ids",
        )
        planned_paths_by_slice = _planned_paths_by_slice(
            selected,
            forwarding,
            formal_next_hops,
            formal_paths,
            connection_values,
            published_next_hops,
            published_paths,
        )
        resolver = _DifferenceTimeResolver(selected)
        active_connection_transition_ids = {
            id(transition) for transition in connections
        }
        excluded_parameter_ids: set[int] = set()
        decisions: list[PlanningDecision] = []

        for establish, withdraw in candidates:
            _require_lifetime(establish, withdraw)
            if (
                id(establish) not in active_connection_transition_ids
                or id(withdraw) not in active_connection_transition_ids
            ):
                continue
            establish_difference = _connection_difference(
                selected, establish, time_resolver=resolver
            )
            withdraw_difference = _connection_difference(
                selected, withdraw, time_resolver=resolver
            )
            start_id = establish_difference.current_slice_id
            end_id = withdraw_difference.current_slice_id
            connection_id = establish.connection_id
            if any(
                connection_id in path
                for slice_id in range(start_id, end_id + 1)
                for path in planned_paths_by_slice.get(slice_id, {}).values()
            ):
                continue

            active_connection_transition_ids.remove(id(establish))
            active_connection_transition_ids.remove(id(withdraw))
            removed_parameter_count = 0
            for transition in parameters:
                if transition.connection_id != connection_id:
                    continue
                source = resolver.difference_for_time(
                    transition.nominal_time
                )
                if start_id <= source.current_slice_id <= end_id:
                    excluded_parameter_ids.add(id(transition))
                    removed_parameter_count += 1
            decisions.append(
                PlanningDecision(
                    source_difference_ids=tuple(
                        range(start_id, end_id + 1)
                    ),
                    decision="suppressed",
                    decided_by=type(self).__name__,
                    reason=(
                        f"omitted candidate Connection episode for "
                        f"{connection_id!r} because no planned forwarding "
                        f"path depended on it; atomically removed "
                        f"{removed_parameter_count} episode-only Parameter "
                        "transitions"
                    ),
                )
            )

        return (
            tuple(
                transition
                for transition in connections
                if id(transition) in active_connection_transition_ids
            ),
            tuple(
                transition
                for transition in parameters
                if id(transition) not in excluded_parameter_ids
            ),
            forwarding,
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


def _is_episode_candidate(
    establish: ConnectionTransition,
    withdraw: ConnectionTransition,
    configuration: ConnectionEpisodeConfiguration,
) -> bool:
    _require_lifetime(establish, withdraw)
    maximum = configuration.omission_candidate_max_duration_by_kind.get(
        establish.connection_id.kind
    )
    return (
        maximum is not None
        and withdraw.nominal_time - establish.nominal_time <= maximum
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


def _parameter_time_mapping(
    values: Mapping[_ParameterFieldKey, datetime],
    field_name: str,
) -> dict[_ParameterFieldKey, datetime]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a Mapping")
    result = dict(values)
    for key, value in result.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 3
            or not isinstance(key[0], ConnectionId)
            or not isinstance(key[1], ConnectionDirection)
            or key[2] not in _PARAMETER_FIELDS
            or not isinstance(value, datetime)
            or value.tzinfo is None
        ):
            raise TypeError(f"{field_name} contains invalid temporal state")
    return result


def _clear_parameter_times(
    drift_started: dict[_ParameterFieldKey, datetime],
    persistence_started: dict[_ParameterFieldKey, datetime],
    connection_id: ConnectionId,
) -> None:
    for mapping in (drift_started, persistence_started):
        for key in tuple(mapping):
            if key[0] == connection_id:
                mapping.pop(key, None)


def _route_time_mapping(
    values: Mapping[RouteKey, datetime], field_name: str
) -> dict[RouteKey, datetime]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a Mapping")
    result = dict(values)
    if any(
        not isinstance(key, RouteKey)
        or not isinstance(value, datetime)
        or value.tzinfo is None
        for key, value in result.items()
    ):
        raise TypeError(f"{field_name} contains invalid temporal state")
    return result


def _route_next_hop_state(
    values: Mapping[RouteKey, NodeId], field_name: str
) -> dict[RouteKey, NodeId]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a Mapping")
    result = dict(values)
    if any(
        not isinstance(key, RouteKey) or not isinstance(value, NodeId)
        for key, value in result.items()
    ):
        raise TypeError(f"{field_name} contains invalid route state")
    return result


def _route_path_state(
    values: Mapping[RouteKey, tuple[ConnectionId, ...]], field_name: str
) -> dict[RouteKey, tuple[ConnectionId, ...]]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a Mapping")
    result = dict(values)
    if any(
        not isinstance(key, RouteKey)
        or not isinstance(path, tuple)
        or any(not isinstance(value, ConnectionId) for value in path)
        for key, path in result.items()
    ):
        raise TypeError(f"{field_name} contains invalid path state")
    return result


def _connection_value_mapping(
    values: Mapping[int, Mapping[ConnectionId, Connection]],
) -> Mapping[int, Mapping[ConnectionId, Connection]]:
    if not isinstance(values, Mapping):
        raise TypeError("connection_values_by_slice_id must be a Mapping")
    if any(
        isinstance(slice_id, bool)
        or not isinstance(slice_id, int)
        or not isinstance(connections, Mapping)
        or any(
            not isinstance(connection_id, ConnectionId)
            or not isinstance(connection, Connection)
            or connection.connection_id != connection_id
            for connection_id, connection in connections.items()
        )
        for slice_id, connections in values.items()
    ):
        raise TypeError(
            "connection_values_by_slice_id contains invalid formal state"
        )
    return values


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


def _path_mapping_with_reverse(
    values: Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
) -> tuple[
    Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
    dict[int, dict[ConnectionId, set[RouteKey]]],
]:
    if not isinstance(values, Mapping):
        raise TypeError("route_path_connection_ids must be a Mapping")
    reverse: dict[int, dict[ConnectionId, set[RouteKey]]] = {}
    for slice_id, routes in values.items():
        if (
            isinstance(slice_id, bool)
            or not isinstance(slice_id, int)
            or not isinstance(routes, Mapping)
        ):
            raise TypeError(
                "route_path_connection_ids contains invalid formal path state"
            )
        by_connection: dict[ConnectionId, set[RouteKey]] = {}
        for route_key, connection_ids in routes.items():
            if (
                not isinstance(route_key, RouteKey)
                or not isinstance(connection_ids, tuple)
                or any(
                    not isinstance(connection_id, ConnectionId)
                    for connection_id in connection_ids
                )
            ):
                raise TypeError(
                    "route_path_connection_ids contains invalid formal path state"
                )
            for connection_id in connection_ids:
                by_connection.setdefault(connection_id, set()).add(route_key)
        reverse[slice_id] = by_connection
    return values, reverse


def _path_nodes(
    source: NodeId,
    path: tuple[ConnectionId, ...],
    connections: Mapping[ConnectionId, Connection],
) -> tuple[NodeId, ...] | None:
    current = source
    nodes = [source]
    for connection_id in path:
        connection = connections.get(connection_id)
        if connection is None:
            return None
        if current == connection_id.endpoint_a:
            current = connection_id.endpoint_b
        elif current == connection_id.endpoint_b:
            current = connection_id.endpoint_a
        else:
            return None
        nodes.append(current)
    return tuple(nodes)


def _cached_path_nodes(
    cache: dict[
        tuple[NodeId, tuple[ConnectionId, ...]], tuple[NodeId, ...] | None
    ],
    source: NodeId,
    path: tuple[ConnectionId, ...],
    connections: Mapping[ConnectionId, Connection],
) -> tuple[NodeId, ...] | None:
    key = (source, path)
    if key not in cache:
        cache[key] = _path_nodes(source, path, connections)
    return cache[key]


def _path_delay_us(
    nodes: tuple[NodeId, ...],
    path: tuple[ConnectionId, ...],
    connections: Mapping[ConnectionId, Connection],
) -> int:
    return sum(
        connections[connection_id].parameters_from(source).delay_us
        for source, connection_id in zip(nodes[:-1], path, strict=True)
    )


def _cached_path_delay_us(
    cache: dict[tuple[NodeId, tuple[ConnectionId, ...]], int],
    nodes: tuple[NodeId, ...],
    path: tuple[ConnectionId, ...],
    connections: Mapping[ConnectionId, Connection],
) -> int:
    key = (nodes[0], path)
    cached = cache.get(key)
    if cached is not None:
        return cached
    if not path:
        cache[key] = 0
        return 0
    result = connections[path[0]].parameters_from(nodes[0]).delay_us
    result += _cached_path_delay_us(
        cache,
        nodes[1:],
        path[1:],
        connections,
    )
    cache[key] = result
    return result


def _meets_forwarding_gain(
    hop_gain: int,
    delay_gain_us: int,
    hop_threshold: int | None,
    delay_threshold_us: int | None,
    *,
    default: bool,
) -> bool:
    configured = []
    if hop_threshold is not None:
        configured.append(hop_gain >= hop_threshold)
    if delay_threshold_us is not None:
        configured.append(delay_gain_us >= delay_threshold_us)
    return any(configured) if configured else default


def _promote_dependency_consistent_follow(
    follow: dict[RouteKey, bool],
    planned_paths: Mapping[RouteKey, tuple[ConnectionId, ...]],
    formal_paths: Mapping[RouteKey, tuple[ConnectionId, ...]],
    connections: Mapping[ConnectionId, Connection],
    *,
    path_nodes_cache: dict[
        tuple[NodeId, tuple[ConnectionId, ...]],
        tuple[NodeId, ...] | None,
    ]
    | None = None,
) -> None:
    if path_nodes_cache is None:
        path_nodes_cache = {}

    def selected_path(route_key: RouteKey):
        use_formal = follow.get(route_key)
        if use_formal is None:
            return None
        return (
            formal_paths.get(route_key)
            if use_formal
            else planned_paths.get(route_key)
        )

    # A promotion can only invalidate routes whose selected first hop points
    # at the promoted RouteKey. Index both possible first hops once so the
    # monotonic HOLD -> FOLLOW closure does not rescan the whole route table
    # after every promotion wave.
    upstream_by_downstream: dict[RouteKey, set[RouteKey]] = {}
    for route_key in follow:
        for path in {
            planned_paths.get(route_key),
            formal_paths.get(route_key),
        } - {None}:
            if len(path) <= 1:
                continue
            nodes = _cached_path_nodes(
                path_nodes_cache, route_key.source, path, connections
            )
            if nodes is not None:
                downstream = RouteKey(nodes[1], route_key.target)
                upstream_by_downstream.setdefault(downstream, set()).add(
                    route_key
                )

    pending = deque(follow)
    queued = set(follow)

    def enqueue(route_keys) -> None:
        for route_key in route_keys:
            if route_key in follow and route_key not in queued:
                pending.append(route_key)
                queued.add(route_key)

    def promote(route_key: RouteKey) -> None:
        if follow[route_key]:
            return
        follow[route_key] = True
        enqueue((route_key,))
        enqueue(upstream_by_downstream.get(route_key, ()))

    while pending:
        route_key = pending.popleft()
        queued.remove(route_key)
        path = selected_path(route_key)
        if path is None or len(path) <= 1:
            continue
        nodes = _cached_path_nodes(
            path_nodes_cache, route_key.source, path, connections
        )
        if nodes is None:
            if not follow[route_key]:
                promote(route_key)
            continue
        downstream = RouteKey(nodes[1], route_key.target)
        if selected_path(downstream) == path[1:]:
            continue
        if not follow[route_key]:
            promote(route_key)
        elif downstream in follow and not follow[downstream]:
            promote(downstream)

    # Keep the original fail-closed oracle after the monotonic closure. Error
    # precedence remains unsupported formal Connection state before an
    # inconsistent formal suffix.
    unsupported_connection = False
    inconsistent_suffix = False
    for route_key in follow:
        path = selected_path(route_key)
        if path is None or len(path) <= 1:
            continue
        nodes = _cached_path_nodes(
            path_nodes_cache, route_key.source, path, connections
        )
        if nodes is None:
            unsupported_connection = True
            continue
        downstream = RouteKey(nodes[1], route_key.target)
        if selected_path(downstream) != path[1:]:
            inconsistent_suffix = True
    if unsupported_connection:
        raise ValueError("formal forwarding path lacks Connection support")
    if inconsistent_suffix:
        raise ValueError(
            "formal forwarding state lacks a dependency-consistent suffix"
        )


def _connection_id_for_hop(
    first: NodeId,
    second: NodeId,
    connections: Mapping[ConnectionId, Connection],
    connection_ids_by_hop: Mapping[
        frozenset[NodeId], ConnectionId | None
    ]
    | None = None,
) -> ConnectionId:
    if connection_ids_by_hop is not None:
        connection_id = connection_ids_by_hop.get(frozenset((first, second)))
        if connection_id is None:
            raise ValueError(
                "planned forwarding hop must map to exactly one current "
                "Connection"
            )
        return connection_id
    matches = tuple(
        connection_id
        for connection_id in connections
        if {connection_id.endpoint_a, connection_id.endpoint_b}
        == {first, second}
    )
    if len(matches) != 1:
        raise ValueError(
            "planned forwarding hop must map to exactly one current Connection"
        )
    return matches[0]


def _resolve_planned_path(
    route_key: RouteKey,
    next_hops: Mapping[RouteKey, NodeId],
    connections: Mapping[ConnectionId, Connection],
    connection_ids_by_hop: Mapping[
        frozenset[NodeId], ConnectionId | None
    ]
    | None = None,
) -> tuple[ConnectionId, ...]:
    path = []
    current_key = route_key
    visited = set()
    while current_key in next_hops:
        if current_key in visited:
            raise ValueError("planned forwarding state contains a loop")
        visited.add(current_key)
        next_hop = next_hops[current_key]
        path.append(
            _connection_id_for_hop(
                current_key.source,
                next_hop,
                connections,
                connection_ids_by_hop,
            )
        )
        downstream = RouteKey(next_hop, route_key.target)
        if downstream not in next_hops:
            break
        current_key = downstream
    return tuple(path)


def _resolve_planned_paths(
    next_hops: Mapping[RouteKey, NodeId],
    connections: Mapping[ConnectionId, Connection],
) -> dict[RouteKey, tuple[ConnectionId, ...]]:
    connection_ids_by_hop: dict[
        frozenset[NodeId], ConnectionId | None
    ] = {}
    for connection_id in connections:
        key = frozenset(
            (connection_id.endpoint_a, connection_id.endpoint_b)
        )
        connection_ids_by_hop[key] = (
            None if key in connection_ids_by_hop else connection_id
        )
    resolved: dict[RouteKey, tuple[ConnectionId, ...]] = {}

    def resolve(route_key: RouteKey) -> tuple[ConnectionId, ...]:
        cached = resolved.get(route_key)
        if cached is not None:
            return cached

        chain: list[tuple[RouteKey, ConnectionId]] = []
        chain_positions: dict[RouteKey, int] = {}
        current = route_key
        while current not in resolved:
            if current in chain_positions:
                raise ValueError("planned forwarding state contains a loop")
            chain_positions[current] = len(chain)
            next_hop = next_hops[current]
            connection_id = _connection_id_for_hop(
                current.source,
                next_hop,
                connections,
                connection_ids_by_hop,
            )
            chain.append((current, connection_id))
            downstream = RouteKey(next_hop, route_key.target)
            if downstream not in next_hops:
                suffix: tuple[ConnectionId, ...] = ()
                break
            current = downstream
        else:
            suffix = resolved[current]

        for key, connection_id in reversed(chain):
            suffix = (connection_id, *suffix)
            resolved[key] = suffix
        return resolved[route_key]

    # Preserve the original outer mapping traversal even though shared
    # downstream suffixes are resolved only once.
    return {route_key: resolve(route_key) for route_key in next_hops}


def _planned_paths_by_slice(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    route_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    route_paths: Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
    connection_values: Mapping[int, Mapping[ConnectionId, Connection]],
    published_next_hops: Mapping[RouteKey, NodeId],
    published_paths: Mapping[RouteKey, tuple[ConnectionId, ...]],
) -> dict[int, dict[RouteKey, tuple[ConnectionId, ...]]]:
    _next_hop_mapping(route_next_hops)
    _path_mapping(route_paths)
    _connection_value_mapping(connection_values)
    next_hops = _route_next_hop_state(
        published_next_hops, "published_route_next_hops"
    )
    paths = _route_path_state(
        published_paths, "published_route_path_connection_ids"
    )
    resolver = _DifferenceTimeResolver(differences)
    by_slice: dict[int, list[ForwardingTransition]] = {}
    for transition in transitions:
        source = resolver.difference_for_time(transition.nominal_time)
        by_slice.setdefault(source.current_slice_id, []).append(transition)
    result = {differences[0].previous_slice_id: dict(paths)}
    for difference in differences:
        for transition in sorted(
            by_slice.get(difference.current_slice_id, ()),
            key=lambda value: (value.nominal_time, repr(value.route_key)),
        ):
            if transition.operation is DifferenceOperation.REMOVED:
                next_hops.pop(transition.route_key, None)
            else:
                next_hops[transition.route_key] = transition.next_hop
        paths = _resolve_planned_paths(
            next_hops,
            connection_values.get(difference.current_slice_id, {}),
        )
        result[difference.current_slice_id] = paths
    return result


def _planned_forwarding_precedences(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    route_next_hops: Mapping[int, Mapping[RouteKey, NodeId]],
    route_paths: Mapping[int, Mapping[RouteKey, tuple[ConnectionId, ...]]],
    connection_values: Mapping[int, Mapping[ConnectionId, Connection]],
    published_next_hops: Mapping[RouteKey, NodeId],
    published_paths: Mapping[RouteKey, tuple[ConnectionId, ...]],
) -> tuple[ForwardingTransitionPrecedence, ...]:
    selected = _difference_tuple(differences, "differences")
    values = _typed_tuple(
        transitions, ForwardingTransition, "forwarding_transitions"
    )
    resolver = _DifferenceTimeResolver(selected)
    paths_by_slice = _planned_paths_by_slice(
        selected,
        values,
        route_next_hops,
        route_paths,
        connection_values,
        published_next_hops,
        published_paths,
    )
    return _planned_forwarding_precedences_from_paths(
        selected,
        values,
        paths_by_slice,
        time_resolver=resolver,
    )


def _planned_forwarding_precedences_from_paths(
    differences: tuple[TopologyDifference, ...],
    transitions: tuple[ForwardingTransition, ...],
    paths_by_slice: Mapping[
        int, Mapping[RouteKey, tuple[ConnectionId, ...]]
    ],
    *,
    time_resolver: _DifferenceTimeResolver | None = None,
) -> tuple[ForwardingTransitionPrecedence, ...]:
    selected = _difference_tuple(differences, "differences")
    values = _typed_tuple(
        transitions, ForwardingTransition, "forwarding_transitions"
    )
    resolver = time_resolver or _DifferenceTimeResolver(selected)
    path_states = {}
    for transition in values:
        source = resolver.difference_for_time(transition.nominal_time)
        path_states[id(transition)] = (
            paths_by_slice.get(source.previous_slice_id, {}).get(
                transition.route_key, ()
            ),
            paths_by_slice.get(source.current_slice_id, {}).get(
                transition.route_key, ()
            ),
        )
    candidates_by_key: dict[
        tuple[datetime, RouteTarget], list[ForwardingTransition]
    ] = {}
    for transition in values:
        candidates_by_key.setdefault(
            (transition.nominal_time, transition.route_key.target), []
        ).append(transition)
    result = []
    for predecessor in values:
        predecessor_old_path, predecessor_new_path = path_states[
            id(predecessor)
        ]
        for successor in candidates_by_key[
            (predecessor.nominal_time, predecessor.route_key.target)
        ]:
            if predecessor is successor:
                continue
            successor_old_path, successor_new_path = path_states[id(successor)]
            if (
                predecessor.operation is not DifferenceOperation.REMOVED
                and successor.operation is not DifferenceOperation.REMOVED
                and _is_direct_upstream_path(
                    successor_new_path, predecessor_new_path
                )
            ) or (
                predecessor.operation is not DifferenceOperation.ADDED
                and successor.operation is DifferenceOperation.REMOVED
                and _is_direct_upstream_path(
                    predecessor_old_path, successor_old_path
                )
            ):
                result.append(
                    ForwardingTransitionPrecedence(predecessor, successor)
                )
    return tuple(result)


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
    *,
    time_resolver: _DifferenceTimeResolver | None = None,
) -> TopologyDifference:
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    source = resolver.difference_for_time(transition.nominal_time)
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
    *,
    time_resolver: _DifferenceTimeResolver | None = None,
) -> tuple[datetime, int, int, str]:
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    source = resolver.difference_for_time(transition.nominal_time)
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
    *,
    time_resolver: _DifferenceTimeResolver | None = None,
) -> tuple[tuple[ConnectionId, ...], tuple[ConnectionId, ...]]:
    resolver = time_resolver or _DifferenceTimeResolver(differences)
    source = resolver.difference_for_time(transition.nominal_time)
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


def _connection_episode_configuration(
    configuration: ConnectionEpisodeConfiguration | None,
    legacy_minimum_dwell: timedelta | None,
) -> ConnectionEpisodeConfiguration:
    if configuration is not None:
        if not isinstance(configuration, ConnectionEpisodeConfiguration):
            raise TypeError(
                "episode_configuration must be a "
                "ConnectionEpisodeConfiguration"
            )
        if legacy_minimum_dwell is not None:
            raise ValueError(
                "episode_configuration cannot be combined with minimum_dwell"
            )
        return configuration
    if legacy_minimum_dwell is None:
        return ConnectionEpisodeConfiguration()
    maximum = _positive_timedelta(legacy_minimum_dwell, "minimum_dwell")
    return ConnectionEpisodeConfiguration(
        {kind: maximum for kind in ConnectionKind}
    )


def _positive_timedelta(value: timedelta, field_name: str) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta")
    if value <= timedelta(0):
        raise ValueError(f"{field_name} must be positive")
    return value


def _positive_timedelta_or_none(
    value: timedelta | None, field_name: str
) -> timedelta | None:
    if value is None:
        return None
    return _positive_timedelta(value, field_name)


def _difference_time_prefix(
    values: tuple[TopologyDifference, ...], horizon: timedelta
) -> tuple[TopologyDifference, ...]:
    if not values:
        return ()
    if any(not isinstance(value, TopologyDifference) for value in values):
        raise TypeError("Difference horizon input contains invalid values")
    anchor = values[0].previous_timestamp or values[0].current_timestamp
    boundary = anchor + horizon
    result = [values[0]]
    for value in values[1:]:
        if value.current_timestamp > boundary:
            break
        result.append(value)
    return tuple(result)


def _validate_threshold(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


__all__ = [
    "AccumulatedDeadbandParameterPlanningModel",
    "AllAvailablePlanningWindowPolicy",
    "ConnectionEpisodeConfiguration",
    "ConnectionPlanningModel",
    "CoordinationPolicy",
    "DefaultCoordinationPolicy",
    "DependencyAwareForwardingPlanningModel",
    "ForwardingPlanningModel",
    "ForwardingStabilityConfiguration",
    "MidpointConnectionPlanningModel",
    "ParameterPlanningModel",
    "ParameterFieldConfiguration",
    "ParameterPlanningConfiguration",
    "PlanningWindowPolicy",
    "SampleBoundaryConnectionPlanningModel",
    "SampleBoundaryParameterPlanningModel",
    "StableForwardingPlanningModel",
    "TimeHorizonPlanningWindowPolicy",
]
