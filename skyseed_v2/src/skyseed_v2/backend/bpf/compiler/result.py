"""Immutable compilation results and retained computation indexes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from skyseed_v2.simulator import PlannedOperationalState, planned_operational_state_fingerprint
from skyseed_v2.backend.bpf.execution_materials.actions import (
    _ResourceIntent,
    _QdiscIntent,
    _ForwardingBinding,
    _ForwardingIntent,
    _ZoneEdgeState,
    _ZoneEdgeIntent,
    _OrderingConstraint,
    _ResourceState,
    _QdiscState,
    _IntendedState,
    _PhysicalWorldProjection,
)


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
class BpfCompilationResult:
    """Carry one immutable Plan lowering and its semantic provenance.

    Layer: public immutable BPF Compiler output. It is public because the producer,
    sealing writer, Traffic and detached Tools consume its Plan lineage, intended state,
    activation schedule and backend intentions. Runner consumes only the
    resulting sealed execution materials. It is not Planner state ownership, a hidden
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
    _incremental_indexes: tuple = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("BpfCompilationResult is created by compile_bpf")

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
        incremental_indexes: tuple,
    ) -> BpfCompilationResult:
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
        object.__setattr__(instance, "_incremental_indexes", incremental_indexes)
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
    def zone_prefixes(self) -> tuple[tuple[tuple[str, str], str], ...]:
        return self._intended_state.zone_prefixes
