"""Lower one self-contained semantic Plan into a BPF realization."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from time import perf_counter
from types import MappingProxyType

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    EffectiveConnectionParameters,
    GroundStationRouteDemandTarget,
    OperationalConnection,
    OperationalConnectionId,
    OperationalConnectionKind,
    PhysicalWorld,
    Plan,
    PlanTransition,
    PlannedOperationalState,
    ReachableRouteOutcome,
    RouteDemand,
    RouteUnreachableReason,
    SemanticEventKind,
    SemanticEventReference,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    apply_plan,
    apply_plan_transition,
    plan_transition_fingerprint,
    planned_operational_state_fingerprint,
    semantic_event_references,
)

from .compiled_realization import (
    BpfCompiledActivationBoundary,
    BpfCompiledRealization,
    _AttachmentDemand,
    _ForwardingBinding,
    _ForwardingIntent,
    _ForwardingKey,
    _IntendedState,
    _IntentReference,
    _MutationOperation,
    _OrderingConstraint,
    _PhysicalWorldProjection,
    _QdiscIntent,
    _QdiscState,
    _ResourceIntent,
    _ResourceOperation,
    _ResourceState,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
    _ZoneEdgeState,
)


_ZONE_POOL = IPv4Network("198.18.0.0/15")


@dataclass(slots=True)
class _MutableState:
    resources: dict[OperationalConnectionId, OperationalConnection]
    qdiscs: dict[
        tuple[OperationalConnectionId, NodeId, NodeId],
        EffectiveConnectionParameters,
    ]
    qdisc_keys_by_connection: dict[
        OperationalConnectionId,
        set[tuple[OperationalConnectionId, NodeId, NodeId]],
    ]
    forwarding: dict[_ForwardingKey, _ForwardingBinding]
    forwarding_keys_by_connection: dict[
        OperationalConnectionId, set[_ForwardingKey]
    ]
    zone_edges: dict[str, _ZoneEdgeState]
    route_bindings: dict[
        RouteDemand, dict[_ForwardingKey, _ForwardingBinding]
    ]
    forwarding_owners: dict[
        _ForwardingKey, dict[RouteDemand, _ForwardingBinding]
    ]


@dataclass(frozen=True, slots=True)
class _LoweredPlan:
    """Hold one deterministic lowering result for production and validation."""

    resource_intents: tuple[_ResourceIntent, ...]
    qdisc_intents: tuple[_QdiscIntent, ...]
    forwarding_intents: tuple[_ForwardingIntent, ...]
    zone_edge_intents: tuple[_ZoneEdgeIntent, ...]
    constraints: tuple[_OrderingConstraint, ...]
    semantic_precedence_folding: tuple[tuple[str, int], ...]
    intended_state: _IntendedState
    output_state: PlannedOperationalState
    incremental_indexes: tuple


@dataclass(frozen=True, slots=True)
class _TransitionEventIndex:
    """Index canonical semantic events once for one formal boundary."""

    by_connection: Mapping[
        tuple[SemanticEventKind, OperationalConnectionId | None],
        SemanticEventReference,
    ]
    by_route_demand: Mapping[RouteDemand, tuple[SemanticEventReference, ...]]
    by_zone: Mapping[tuple[SemanticEventKind, str], SemanticEventReference]


@dataclass(slots=True)
class _CompileObservationBinding:
    observer: _ExecutionObserver
    compile_index: int
    _total_duration_seconds: float | None = None

    @property
    def total_duration_seconds(self) -> float:
        duration = self._total_duration_seconds
        if duration is None:
            raise RuntimeError("bound public compilation did not complete")
        return duration

    def _set_total_duration(self, duration_seconds: float) -> None:
        if self._total_duration_seconds is not None:
            raise RuntimeError("bound public compilation recorded twice")
        self._total_duration_seconds = duration_seconds


_CURRENT_COMPILE_OBSERVATION: ContextVar[_CompileObservationBinding | None] = (
    ContextVar("skyseed_bpf_compile_observation", default=None)
)


@contextmanager
def _bind_compile_observation(
    execution_observer: _ExecutionObserver,
    *,
    compile_index: int,
) -> Iterator[_CompileObservationBinding]:
    """Bind detached execution-cost observation around one compilation."""

    if not isinstance(execution_observer, _ExecutionObserver):
        raise TypeError("execution_observer must be an _ExecutionObserver")
    if (
        isinstance(compile_index, bool)
        or not isinstance(compile_index, int)
        or compile_index < 0
    ):
        raise ValueError("compile_index must be a non-negative integer")
    if _CURRENT_COMPILE_OBSERVATION.get() is not None:
        raise RuntimeError("Compiler execution observation is already bound")
    binding = _CompileObservationBinding(execution_observer, compile_index)
    token = _CURRENT_COMPILE_OBSERVATION.set(binding)
    try:
        yield binding
    finally:
        _CURRENT_COMPILE_OBSERVATION.reset(token)


def compile_bpf(
    plan: Plan,
    physical_world: PhysicalWorld,
    previous_realization: BpfCompiledRealization | None,
) -> BpfCompiledRealization:
    """Deterministically lower one Plan without consulting Planner history.

    The only source facts are the self-contained Plan, static PhysicalWorld
    identity/provenance, and the preceding compiled realization. The Compiler
    does not run physical, Connection-selection, acquisition, or Route Models.
    """

    observation = _CURRENT_COMPILE_OBSERVATION.get()
    return _compile_bpf(
        plan,
        physical_world,
        previous_realization,
        execution_observer=(None if observation is None else observation.observer),
        compile_index=(0 if observation is None else observation.compile_index),
        observation_binding=observation,
    )


def _compile_bpf_observed(
    plan: Plan,
    physical_world: PhysicalWorld,
    previous_realization: BpfCompiledRealization | None,
    *,
    execution_observer: _ExecutionObserver,
    compile_index: int,
) -> BpfCompiledRealization:
    """Compile once while recording detached private cost evidence."""

    with _bind_compile_observation(
        execution_observer,
        compile_index=compile_index,
    ):
        observation = _CURRENT_COMPILE_OBSERVATION.get()
        assert observation is not None
        return _compile_bpf(
            plan,
            physical_world,
            previous_realization,
            execution_observer=observation.observer,
            compile_index=observation.compile_index,
            observation_binding=observation,
        )


def _compile_bpf(
    plan: Plan,
    physical_world: PhysicalWorld,
    previous_realization: BpfCompiledRealization | None,
    *,
    execution_observer: _ExecutionObserver | None,
    compile_index: int,
    observation_binding: _CompileObservationBinding | None,
) -> BpfCompiledRealization:
    total_started = perf_counter()
    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    if not isinstance(physical_world, PhysicalWorld):
        raise TypeError("physical_world must be a PhysicalWorld")
    if previous_realization is not None and not isinstance(
        previous_realization, BpfCompiledRealization
    ):
        raise TypeError(
            "previous_realization must be a BpfCompiledRealization or None"
        )

    world_fingerprint = physical_world.semantic_fingerprint()
    projection = (_physical_world_projection(physical_world) if previous_realization is None
                  else previous_realization._physical_world_projection)
    _validate_lineage(
        plan,
        world_fingerprint=world_fingerprint,
        projection=projection,
        previous=previous_realization,
    )
    previous_state = (
        None
        if previous_realization is None
        else previous_realization.output_planned_state
    )
    if previous_state is not None and planned_operational_state_fingerprint(previous_state) != plan.input_state_fingerprint:
        raise ValueError('Plan input state fingerprint mismatch')
    scope = _compile_scope(plan, compile_index)
    _observe_compile_stage(
        execution_observer,
        stage="plan-lineage",
        started=total_started,
        scope=scope,
        work={
            "plan_transitions": len(plan.transitions),
            "semantic_precedences": len(plan.semantic_event_precedences),
            "replayed_transitions": 0,
        },
    )

    lowering_started = perf_counter()
    lowered = _lower_plan(
        plan,
        projection=projection,
        previous=previous_realization,
        previous_state=previous_state,
        output_state=None,
        execution_observer=execution_observer,
        scope=scope,
    )
    output_state = lowered.output_state
    result = BpfCompiledRealization._create(
        source_plan_id=plan.plan_id,
        source_previous_plan_id=plan.previous_plan_id,
        physical_world_fingerprint=world_fingerprint,
        input_state_fingerprint=plan.input_state_fingerprint,
        output_state_fingerprint=plan.output_state_fingerprint,
        observation_interval=plan.observation_interval,
        start_timestamp=plan.start_timestamp,
        end_timestamp=plan.end_timestamp,
        output_planned_state=output_state,
        compiled_activation_boundaries=tuple(
            BpfCompiledActivationBoundary(
                transition.timestamp,
                plan_transition_fingerprint(transition),
            )
            for transition in plan.transitions
        ),
        resource_realizations=lowered.resource_intents,
        parameter_realizations=lowered.qdisc_intents,
        forwarding_realizations=lowered.forwarding_intents,
        zone_edge_realizations=lowered.zone_edge_intents,
        constraints=lowered.constraints,
        semantic_precedence_folding=lowered.semantic_precedence_folding,
        intended_state=lowered.intended_state,
        physical_world_projection=projection,
    )
    object.__setattr__(result, "_incremental_indexes", lowered.incremental_indexes)
    _observe_compile_stage(
        execution_observer,
        stage="semantic-to-bpf-lowering",
        started=lowering_started,
        scope=scope,
        work={
            "activation_boundaries": len(result.activation_timestamps),
            "resource_intents": len(lowered.resource_intents),
            "parameter_intents": len(lowered.qdisc_intents),
            "forwarding_intents": len(lowered.forwarding_intents),
            "zone_edge_intents": len(lowered.zone_edge_intents),
            "constraints": len(lowered.constraints),
            "semantic_precedences_folded": sum(
                count
                for name, count in lowered.semantic_precedence_folding
                if name != "explicit-fallback"
            ),
        },
    )
    total_duration = _observe_compile_stage(
        execution_observer,
        stage="compile-total",
        started=total_started,
        scope=scope,
        work={
            "activation_boundaries": len(result.activation_timestamps),
            "output_connections": len(output_state.operational_connections),
            "output_route_outcomes": len(output_state.route_outcomes),
        },
        duration_role="rollup",
    )
    if observation_binding is not None:
        assert total_duration is not None
        observation_binding._set_total_duration(total_duration)
    return result


def _lower_plan(
    plan: Plan,
    *,
    projection: _PhysicalWorldProjection,
    previous: BpfCompiledRealization | None,
    previous_state: PlannedOperationalState | None,
    output_state: PlannedOperationalState | None,
    execution_observer: _ExecutionObserver | None = None,
    scope: Mapping[str, object] | None = None,
) -> _LoweredPlan:
    """Deterministically derive every backend fact from one semantic Plan."""

    stage_scope = {} if scope is None else scope
    indexing_started = perf_counter()
    zone_prefixes = _allocate_zone_prefixes(projection.zone_ids)
    node_ids = frozenset(projection.node_ids)
    zone_ids = frozenset(projection.zone_ids)
    state = _initial_state(
        previous,
        previous_state,
        projection=projection,
        zone_prefixes=zone_prefixes,
    )
    resource_intents: list[_ResourceIntent] = []
    qdisc_intents: list[_QdiscIntent] = []
    forwarding_intents: list[_ForwardingIntent] = []
    zone_edge_intents: list[_ZoneEdgeIntent] = []
    semantic_intents: dict[
        SemanticEventReference, set[_IntentReference]
    ] = {}
    event_indexes: dict[datetime, _TransitionEventIndex] = {}
    for transition in plan.transitions:
        references = semantic_event_references(transition)
        event_indexes[transition.timestamp] = _index_transition_events(references)
        for reference in references:
            semantic_intents[reference] = set()
    _observe_compile_stage(
        execution_observer,
        stage="event-and-world-indexing",
        started=indexing_started,
        scope=stage_scope,
        work={
            "semantic_events": len(semantic_intents),
            "world_nodes": len(node_ids),
            "world_zones": len(zone_ids),
        },
    )

    transitions_started = perf_counter()
    semantic_state = previous_state
    for transition in plan.transitions:
        next_semantic_state = apply_plan_transition(semantic_state, transition)
        _validate_zone_assignments_for_projection(
            next_semantic_state,
            node_ids=node_ids,
            zone_ids=zone_ids,
        )
        _lower_transition(
            transition,
            semantic_state,
            next_semantic_state,
            state=state,
            projection=projection,
            node_ids=node_ids,
            zone_ids=zone_ids,
            zone_prefixes=zone_prefixes,
            resource_intents=resource_intents,
            qdisc_intents=qdisc_intents,
            forwarding_intents=forwarding_intents,
            zone_edge_intents=zone_edge_intents,
            event_index=event_indexes[transition.timestamp],
            semantic_intents=semantic_intents,
        )
        semantic_state = next_semantic_state
    if output_state is not None and semantic_state != output_state:
        raise ValueError("Compiler transition replay differs from Plan replay")
    output_state = semantic_state
    if planned_operational_state_fingerprint(output_state) != plan.output_state_fingerprint:
        raise ValueError('Plan output state fingerprint mismatch')
    _observe_compile_stage(
        execution_observer,
        stage="transition-lowering",
        started=transitions_started,
        scope=stage_scope,
        work={
            "transitions": len(plan.transitions),
            "resource_intents": len(resource_intents),
            "parameter_intents": len(qdisc_intents),
            "forwarding_intents": len(forwarding_intents),
            "zone_edge_intents": len(zone_edge_intents),
        },
    )

    constraints_started = perf_counter()
    constraints, semantic_precedence_folding = _compile_constraints(
        plan,
        resource_intents=tuple(resource_intents),
        qdisc_intents=tuple(qdisc_intents),
        forwarding_intents=tuple(forwarding_intents),
        zone_edge_intents=tuple(zone_edge_intents),
        semantic_intents=semantic_intents,
    )
    _observe_compile_stage(
        execution_observer,
        stage="constraint-folding-and-materialization",
        started=constraints_started,
        scope=stage_scope,
        work={
            "semantic_precedences": len(plan.semantic_event_precedences),
            "explicit_constraints": len(constraints),
            **{
                f"fold_{name.replace('-', '_')}": count
                for name, count in semantic_precedence_folding
            },
        },
    )
    validation_started = perf_counter()
    _validate_backend_state(
        state,
        output_state,
        projection=projection,
        zone_prefixes=zone_prefixes,
    )
    intended_state = _freeze_state(state, zone_prefixes)
    _observe_compile_stage(
        execution_observer,
        stage="backend-state-validation-and-freeze",
        started=validation_started,
        scope=stage_scope,
        work={
            "intended_resources": len(intended_state.resources),
            "intended_parameters": len(intended_state.qdiscs),
            "intended_forwarding": len(intended_state.forwarding),
            "intended_zone_edges": len(intended_state.zone_edges),
        },
    )
    return _LoweredPlan(
        tuple(resource_intents),
        tuple(qdisc_intents),
        tuple(forwarding_intents),
        tuple(zone_edge_intents),
        constraints,
        semantic_precedence_folding,
        intended_state,
        output_state,
        _freeze_incremental_indexes(state),
    )


def _validate_compiled_realization_lowering(
    plan: Plan,
    value: BpfCompiledRealization,
    previous: BpfCompiledRealization | None,
) -> None:
    """Fail closed unless persisted backend facts equal canonical lowering.

    This validator deliberately shares the Compiler's one lowering function.
    It does not invoke PhysicalWorld, Planner, physical, selection, acquisition,
    or Route Models. The reconstructed source Plan and the preceding validated
    intended state are its only changing source facts.
    """

    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    if not isinstance(value, BpfCompiledRealization):
        raise TypeError("value must be a BpfCompiledRealization")
    if previous is not None and not isinstance(previous, BpfCompiledRealization):
        raise TypeError("previous must be a BpfCompiledRealization or None")
    boundaries = value.compiled_activation_boundaries
    if tuple(item.timestamp for item in boundaries) != tuple(
        transition.timestamp for transition in plan.transitions
    ) or tuple(item.transition_fingerprint for item in boundaries) != tuple(
        plan_transition_fingerprint(transition)
        for transition in plan.transitions
    ):
        raise ValueError(
            "compiled activation boundaries differ from the source Plan"
        )
    expected_metadata = (
        plan.plan_id,
        plan.previous_plan_id,
        plan.physical_world_fingerprint,
        plan.input_state_fingerprint,
        plan.output_state_fingerprint,
        plan.observation_interval,
        plan.start_timestamp,
        plan.end_timestamp,
    )
    actual_metadata = (
        value.source_plan_id,
        value.source_previous_plan_id,
        value.physical_world_fingerprint,
        value.input_state_fingerprint,
        value.output_state_fingerprint,
        value.observation_interval,
        value.start_timestamp,
        value.end_timestamp,
    )
    if actual_metadata != expected_metadata:
        raise ValueError("compiled realization differs from source Plan lineage")
    projection = value._physical_world_projection
    if previous is not None and previous._physical_world_projection != projection:
        raise ValueError("compiled realization PhysicalWorld projection changed")
    previous_state = None if previous is None else previous.output_planned_state
    output_state = apply_plan(
        previous_state,
        plan,
        physical_world_fingerprint=value.physical_world_fingerprint,
    )
    if value.output_planned_state != output_state:
        raise ValueError(
            "compiled realization output state differs from source Plan replay"
        )
    expected = _lower_plan(
        plan,
        projection=projection,
        previous=previous,
        previous_state=previous_state,
        output_state=output_state,
    )
    families = (
        (
            "resource",
            value.resource_realizations,
            expected.resource_intents,
        ),
        (
            "parameter",
            value.parameter_realizations,
            expected.qdisc_intents,
        ),
        (
            "forwarding",
            value.forwarding_realizations,
            expected.forwarding_intents,
        ),
        (
            "zone-edge",
            value.zone_edge_realizations,
            expected.zone_edge_intents,
        ),
    )
    for timestamp in value.activation_timestamps:
        for name, actual_values, expected_values in families:
            actual_at_boundary = tuple(
                item for item in actual_values if item.nominal_time == timestamp
            )
            expected_at_boundary = tuple(
                item for item in expected_values if item.nominal_time == timestamp
            )
            if actual_at_boundary != expected_at_boundary:
                raise ValueError(
                    f"persisted {name} intents differ at activation boundary"
                )
    for name, actual_values, expected_values in families:
        if actual_values != expected_values:
            raise ValueError(f"persisted {name} intents differ from source Plan")
    if value.constraints != expected.constraints:
        raise ValueError(
            "persisted ordering constraints differ from source Plan lowering"
        )
    if (
        value.semantic_precedence_folding
        != expected.semantic_precedence_folding
    ):
        raise ValueError(
            "persisted semantic precedence folding differs from source Plan"
        )
    if value._intended_state != expected.intended_state:
        raise ValueError(
            "persisted final intended state differs from source Plan lowering"
        )


def _validate_lineage(
    plan: Plan,
    *,
    world_fingerprint: str,
    projection: _PhysicalWorldProjection,
    previous: BpfCompiledRealization | None,
) -> None:
    if plan.physical_world_fingerprint != world_fingerprint:
        raise ValueError("Plan belongs to a different PhysicalWorld")
    if plan.previous_plan_id is None:
        if plan.input_state_fingerprint is not None:
            raise ValueError("bootstrap Plan cannot have an input fingerprint")
        if previous is not None:
            raise ValueError("bootstrap Plan cannot have a previous realization")
        return
    if previous is None:
        raise ValueError("non-bootstrap Plan requires a previous realization")
    if plan.previous_plan_id != previous.source_plan_id:
        raise ValueError("Plan previous_plan_id does not match prior realization")
    if plan.input_state_fingerprint != previous.output_state_fingerprint:
        raise ValueError("Plan input state fingerprint does not match prior output")
    if previous.physical_world_fingerprint != world_fingerprint:
        raise ValueError("previous realization belongs to a different PhysicalWorld")
    if previous._physical_world_projection != projection:
        raise ValueError("previous realization PhysicalWorld projection differs")
    if plan.observation_interval != previous.observation_interval:
        raise ValueError(
            "Plan observation_interval differs from previous realization"
        )
    if (
        plan.start_timestamp
        != previous.end_timestamp + plan.observation_interval
    ):
        raise ValueError(
            "Plan must start one observation_interval after previous realization"
        )


def _freeze_incremental_indexes(state):
    # Immutable handoff indexes belong to compilation, never to artifacts or
    # Planner. A subsequent compile copies mutable working containers once.
    return (
        MappingProxyType(dict(state.resources)), MappingProxyType(dict(state.qdiscs)),
        MappingProxyType({k: frozenset(v) for k, v in state.qdisc_keys_by_connection.items()}),
        MappingProxyType(dict(state.forwarding)),
        MappingProxyType({k: frozenset(v) for k, v in state.forwarding_keys_by_connection.items()}),
        MappingProxyType(dict(state.zone_edges)),
        MappingProxyType({k: MappingProxyType(dict(v)) for k, v in state.route_bindings.items()}),
        MappingProxyType({k: MappingProxyType(dict(v)) for k, v in state.forwarding_owners.items()}),
    )


def _initial_state(
    previous: BpfCompiledRealization | None,
    previous_state: PlannedOperationalState | None,
    *,
    projection: _PhysicalWorldProjection,
    zone_prefixes: Mapping[str, str],
) -> _MutableState:
    if previous is None:
        return _MutableState({}, {}, {}, {}, {}, {}, {}, {})
    if previous._incremental_indexes is not None:
        values = previous._incremental_indexes
        return _MutableState(
            dict(values[0]), dict(values[1]),
            {k: set(v) for k, v in values[2].items()},
            dict(values[3]), {k: set(v) for k, v in values[4].items()},
            dict(values[5]), dict(values[6]),
            {k: dict(v) for k, v in values[7].items()})
    resources = {
        value.connection.connection_id: value.connection
        for value in previous.intended_resources
    }
    qdiscs = {
        (value.connection_id, value.source, value.target): value.parameters
        for value in previous.intended_parameters
    }
    qdisc_keys_by_connection: dict[
        OperationalConnectionId,
        set[tuple[OperationalConnectionId, NodeId, NodeId]],
    ] = defaultdict(set)
    for key in qdiscs:
        qdisc_keys_by_connection[key[0]].add(key)
    forwarding = {
        value.route_key: value for value in previous.intended_forwarding
    }
    forwarding_keys_by_connection: dict[
        OperationalConnectionId, set[_ForwardingKey]
    ] = defaultdict(set)
    for key, binding in forwarding.items():
        forwarding_keys_by_connection[binding.connection_id].add(key)
    zone_edges = {
        value.zone_id: value for value in previous.intended_zone_edges
    }
    state = _MutableState(
        resources,
        qdiscs,
        dict(qdisc_keys_by_connection),
        forwarding,
        dict(forwarding_keys_by_connection),
        zone_edges,
        {},
        {},
    )
    assert previous_state is not None
    _initialize_route_owners(
        state,
        previous_state,
        projection=projection,
        zone_prefixes=zone_prefixes,
    )
    _validate_backend_state(
        state,
        previous_state,
        projection=projection,
        zone_prefixes=zone_prefixes,
    )
    return state


def _initialize_route_owners(
    state: _MutableState,
    semantic_state: PlannedOperationalState,
    *,
    projection: _PhysicalWorldProjection,
    zone_prefixes: Mapping[str, str],
) -> None:
    node_ids = frozenset(projection.node_ids)
    zone_ids = frozenset(projection.zone_ids)
    connections = {
        value.connection_id: value
        for value in semantic_state.operational_connections
    }
    zone_assignments = {
        value.zone_id: value.satellite_id
        for value in semantic_state.zone_service_assignments
    }
    for outcome in semantic_state.route_outcomes:
        bindings = _bindings_for_outcome(
            outcome,
            connections,
            semantic_state,
            projection=projection,
            zone_prefixes=zone_prefixes,
            node_ids=node_ids,
            zone_ids=zone_ids,
            zone_assignments=zone_assignments,
        )
        state.route_bindings[outcome.demand] = bindings
        for key, binding in bindings.items():
            state.forwarding_owners.setdefault(key, {})[outcome.demand] = binding
    expected = _aggregate_forwarding(state.forwarding_owners)
    if expected != state.forwarding:
        raise ValueError(
            "previous realization forwarding differs from planned Route truth"
        )


def _lower_transition(
    transition: PlanTransition,
    previous_semantic_state: PlannedOperationalState | None,
    next_semantic_state: PlannedOperationalState,
    *,
    state: _MutableState,
    projection: _PhysicalWorldProjection,
    node_ids: frozenset[NodeId],
    zone_ids: frozenset[str],
    zone_prefixes: Mapping[str, str],
    resource_intents: list[_ResourceIntent],
    qdisc_intents: list[_QdiscIntent],
    forwarding_intents: list[_ForwardingIntent],
    zone_edge_intents: list[_ZoneEdgeIntent],
    event_index: _TransitionEventIndex,
    semantic_intents: dict[SemanticEventReference, set[_IntentReference]],
) -> None:
    timestamp = transition.timestamp
    previous_connections = {c.connection_id: state.resources.get(c.connection_id)
                            for c in transition.parameter_updated_connections}
    for connection in transition.established_connections:
        identifier = connection.connection_id
        if identifier in state.resources:
            raise ValueError("Compiler establishes an existing BPF resource")
        _require_connection_world(connection, node_ids=node_ids)
        state.resources[identifier] = connection
        resource_index = len(resource_intents)
        resource_intents.append(
            _ResourceIntent(
                timestamp,
                _ResourceOperation.ESTABLISH,
                identifier,
                _attachment_demands(identifier),
            )
        )
        _bind_subject_event(
            event_index,
            semantic_intents,
            kind=SemanticEventKind.CONNECTION_ESTABLISH,
            connection_id=identifier,
            intent=_IntentReference("resource", resource_index),
        )
        parameter_refs = []
        for source, target, parameters in _directional_parameters(connection):
            key = identifier, source, target
            state.qdiscs[key] = parameters
            state.qdisc_keys_by_connection.setdefault(identifier, set()).add(key)
            qdisc_index = len(qdisc_intents)
            qdisc_intents.append(
                _QdiscIntent(
                    timestamp,
                    identifier,
                    source,
                    target,
                    parameters.bandwidth_bps,
                    parameters.delay_us,
                    parameters.packet_loss_ppm,
                    "connection-establish",
                )
            )
            parameter_refs.append(_IntentReference("parameter", qdisc_index))
        for intent in parameter_refs:
            _bind_subject_event(
                event_index,
                semantic_intents,
                kind=SemanticEventKind.PARAMETER_READY,
                connection_id=identifier,
                intent=intent,
            )

    for connection in transition.parameter_updated_connections:
        identifier = connection.connection_id
        previous = previous_connections.get(identifier)
        if previous is None or state.resources.get(identifier) != previous:
            raise ValueError("parameter update has no matching compiled resource")
        state.resources[identifier] = connection
        emitted = 0
        old_parameters = {
            (source, target): value
            for source, target, value in _directional_parameters(previous)
        }
        for source, target, parameters in _directional_parameters(connection):
            if old_parameters[(source, target)] == parameters:
                continue
            key = identifier, source, target
            if state.qdiscs.get(key) != old_parameters[(source, target)]:
                raise ValueError("compiled qdisc baseline is stale")
            state.qdiscs[key] = parameters
            state.qdisc_keys_by_connection.setdefault(identifier, set()).add(key)
            qdisc_index = len(qdisc_intents)
            qdisc_intents.append(
                _QdiscIntent(
                    timestamp,
                    identifier,
                    source,
                    target,
                    parameters.bandwidth_bps,
                    parameters.delay_us,
                    parameters.packet_loss_ppm,
                    "parameter-update",
                )
            )
            _bind_subject_event(
                event_index,
                semantic_intents,
                kind=SemanticEventKind.PARAMETER_UPDATE,
                connection_id=identifier,
                intent=_IntentReference("parameter", qdisc_index),
            )
            emitted += 1
        if not emitted:
            raise ValueError("parameter update has no directional backend change")

    for change in transition.zone_service_transitions:
        if change.zone_id not in zone_prefixes:
            raise ValueError("Plan references a Zone foreign to PhysicalWorld")
        previous_edge = state.zone_edges.get(change.zone_id)
        previous_satellite = (
            None
            if previous_edge is None
            else previous_edge.service_satellite_id
        )
        if previous_satellite != change.previous_satellite_id:
            raise ValueError("compiled Zone edge baseline is stale")
        if change.current_satellite_id is None:
            if previous_edge is None:
                continue
            operation = _MutationOperation.REMOVE
            state.zone_edges.pop(change.zone_id)
        else:
            if change.current_satellite_id not in node_ids:
                raise ValueError("Zone service Satellite is foreign to PhysicalWorld")
            operation = (
                _MutationOperation.ADD
                if previous_edge is None
                else _MutationOperation.CHANGE
            )
            state.zone_edges[change.zone_id] = _ZoneEdgeState(
                change.zone_id,
                zone_prefixes[change.zone_id],
                change.current_satellite_id,
            )
        if change.current_satellite_id is not None:
            readiness_index = len(zone_edge_intents)
            zone_edge_intents.append(
                _ZoneEdgeIntent(
                    timestamp,
                    operation,
                    _ZoneEdgePhase.READINESS,
                    change.zone_id,
                    zone_prefixes[change.zone_id],
                    previous_satellite,
                    change.current_satellite_id,
                )
            )
            readiness_kind = (
                SemanticEventKind.ZONE_SERVICE_ASSIGN
                if change.previous_satellite_id is None
                else SemanticEventKind.ZONE_SERVICE_CHANGE
            )
            reference = event_index.by_zone.get(
                (readiness_kind, change.zone_id)
            )
            if reference is None:
                raise ValueError(
                    "canonical Zone readiness event is missing"
                )
            semantic_intents[reference].add(
                _IntentReference("zone-edge", readiness_index)
            )
        if previous_satellite is not None:
            retirement_index = len(zone_edge_intents)
            zone_edge_intents.append(
                _ZoneEdgeIntent(
                    timestamp,
                    operation,
                    _ZoneEdgePhase.RETIREMENT,
                    change.zone_id,
                    zone_prefixes[change.zone_id],
                    previous_satellite,
                    change.current_satellite_id,
                )
            )
            reference = event_index.by_zone.get(
                (SemanticEventKind.ZONE_SERVICE_UNASSIGN, change.zone_id)
            )
            if reference is None:
                raise ValueError(
                    "canonical Zone retirement event is missing"
                )
            semantic_intents[reference].add(
                _IntentReference("zone-edge", retirement_index)
            )

    withdrawn = frozenset(transition.withdrawn_connection_ids)
    next_connections = {k: v for k, v in state.resources.items() if k not in withdrawn} if withdrawn else state.resources
    _lower_route_outcomes(
        transition,
        next_semantic_state,
        state=state,
        next_connections=next_connections,
        projection=projection,
        node_ids=node_ids,
        zone_ids=zone_ids,
        zone_prefixes=zone_prefixes,
        forwarding_intents=forwarding_intents,
        event_index=event_index,
        semantic_intents=semantic_intents,
    )

    for identifier in transition.withdrawn_connection_ids:
        connection = state.resources.get(identifier)
        if connection is None:
            raise ValueError("Compiler withdraws an inactive BPF resource")
        if state.forwarding_keys_by_connection.get(identifier):
            raise ValueError(
                "Connection retirement precedes planned forwarding stop-use"
            )
        resource_index = len(resource_intents)
        resource_intents.append(
            _ResourceIntent(
                timestamp,
                _ResourceOperation.WITHDRAW,
                identifier,
                _attachment_demands(identifier),
            )
        )
        _bind_subject_event(
            event_index,
            semantic_intents,
            kind=SemanticEventKind.CONNECTION_WITHDRAW,
            connection_id=identifier,
            intent=_IntentReference("resource", resource_index),
        )
        del state.resources[identifier]
        for key in state.qdisc_keys_by_connection.pop(identifier, ()):
            del state.qdiscs[key]



def _lower_route_outcomes(
    transition: PlanTransition,
    next_semantic_state: PlannedOperationalState,
    *,
    state: _MutableState,
    next_connections: Mapping[OperationalConnectionId, OperationalConnection],
    projection: _PhysicalWorldProjection,
    node_ids: frozenset[NodeId],
    zone_ids: frozenset[str],
    zone_prefixes: Mapping[str, str],
    forwarding_intents: list[_ForwardingIntent],
    event_index: _TransitionEventIndex,
    semantic_intents: dict[SemanticEventReference, set[_IntentReference]],
) -> None:
    connection_ids_by_pair = {
        pair: identifier for identifier in next_connections
        for pair in ((identifier.endpoint_a, identifier.endpoint_b),
                     (identifier.endpoint_b, identifier.endpoint_a))
    }
    touched: dict[_ForwardingKey, set[RouteDemand]] = defaultdict(set)
    zone_assignments = {
        value.zone_id: value.satellite_id
        for value in next_semantic_state.zone_service_assignments
    }
    for change in transition.route_outcome_transitions:
        prior_bindings = state.route_bindings.pop(change.demand, {})
        for key in prior_bindings:
            owners = state.forwarding_owners.get(key)
            if owners is None or owners.pop(change.demand, None) is None:
                raise ValueError("compiled Route ownership baseline is stale")
            if not owners:
                del state.forwarding_owners[key]
            touched[key].add(change.demand)
        current_bindings = _bindings_for_outcome(
            change.current,
            next_connections,
            next_semantic_state,
            projection=projection,
            zone_prefixes=zone_prefixes,
            node_ids=node_ids,
            zone_ids=zone_ids,
            zone_assignments=zone_assignments,
            connection_ids_by_pair=connection_ids_by_pair,
        )
        if change.current is not None:
            state.route_bindings[change.demand] = current_bindings
        for key, binding in current_bindings.items():
            state.forwarding_owners.setdefault(key, {})[
                change.demand
            ] = binding
            touched[key].add(change.demand)

    for key in sorted(touched, key=_forwarding_key_sort):
        previous = state.forwarding.get(key)
        owners = state.forwarding_owners.get(key, {})
        current = _one_owned_binding(key, owners)
        if previous == current:
            continue
        if previous is None:
            operation = _MutationOperation.ADD
        elif current is None:
            operation = _MutationOperation.REMOVE
        else:
            operation = _MutationOperation.CHANGE
        forwarding_index = len(forwarding_intents)
        forwarding_intents.append(
            _ForwardingIntent(
                transition.timestamp,
                operation,
                key,
                previous,
                current,
            )
        )
        if previous is not None:
            previous_keys = state.forwarding_keys_by_connection[
                previous.connection_id
            ]
            previous_keys.remove(key)
            if not previous_keys:
                del state.forwarding_keys_by_connection[
                    previous.connection_id
                ]
        if current is None:
            state.forwarding.pop(key, None)
        else:
            state.forwarding[key] = current
            state.forwarding_keys_by_connection.setdefault(
                current.connection_id, set()
            ).add(key)
        for demand in touched[key]:
            for reference in event_index.by_route_demand.get(demand, ()):
                semantic_intents[reference].add(
                    _IntentReference("forwarding", forwarding_index)
                )


def _bindings_for_outcome(
    outcome: object,
    connections: Mapping[OperationalConnectionId, OperationalConnection],
    semantic_state: PlannedOperationalState,
    *,
    projection: _PhysicalWorldProjection,
    zone_prefixes: Mapping[str, str],
    node_ids: frozenset[NodeId] | None = None,
    zone_ids: frozenset[str] | None = None,
    zone_assignments: Mapping[str, NodeId | None] | None = None,
    connection_ids_by_pair=None,
) -> dict[_ForwardingKey, _ForwardingBinding]:
    if outcome is None:
        return {}
    exact_node_ids = (
        frozenset(projection.node_ids) if node_ids is None else node_ids
    )
    exact_zone_ids = (
        frozenset(projection.zone_ids) if zone_ids is None else zone_ids
    )
    if isinstance(outcome, (ReachableRouteOutcome, UnreachableRouteOutcome)):
        _validate_route_demand_for_projection(
            outcome.demand,
            node_ids=exact_node_ids,
            zone_ids=exact_zone_ids,
        )
    if isinstance(outcome, UnreachableRouteOutcome):
        _validate_unreachable_reason(
            outcome,
            semantic_state,
            zone_assignments=zone_assignments,
        )
        return {}
    if not isinstance(outcome, ReachableRouteOutcome):
        raise TypeError("Route outcome has an unsupported type")
    demand = outcome.demand
    path = outcome.path
    if any(value not in exact_node_ids for value in path):
        raise ValueError("planned Route path contains a foreign PhysicalWorld node")
    target = demand.target
    if isinstance(target, GroundStationRouteDemandTarget):
        if path[-1] != target.ground_station_id:
            raise ValueError("planned GroundStation Route terminates elsewhere")
        packet_prefix = None
    elif isinstance(target, ZoneRouteDemandTarget):
        assignments = (
            {
                value.zone_id: value.satellite_id
                for value in semantic_state.zone_service_assignments
            }
            if zone_assignments is None
            else zone_assignments
        )
        if target.zone_id not in zone_prefixes:
            raise ValueError("planned Route targets a foreign Zone")
        if assignments.get(target.zone_id) != path[-1]:
            raise ValueError("planned Zone Route does not end at its service Satellite")
        packet_prefix = zone_prefixes[target.zone_id]
    else:  # pragma: no cover - RouteDemand validates its target
        raise TypeError("unsupported Route demand target")
    result: dict[_ForwardingKey, _ForwardingBinding] = {}
    for source, next_hop in zip(path, path[1:]):
        identifier = (_connection_id_for_pair(source, next_hop)
                      if connection_ids_by_pair is None
                      else connection_ids_by_pair.get((source, next_hop)))
        if identifier not in connections:
            raise ValueError(
                "planned reachable Route uses a non-operational Connection"
            )
        key = _ForwardingKey(source, target)
        binding = _ForwardingBinding(
            key,
            next_hop,
            identifier,
            packet_prefix,
        )
        if key in result:
            raise ValueError("planned Route path repeats a forwarding source")
        result[key] = binding
    return result


def _aggregate_forwarding(
    owners: Mapping[
        _ForwardingKey, Mapping[RouteDemand, _ForwardingBinding]
    ],
) -> dict[_ForwardingKey, _ForwardingBinding]:
    return {
        key: _one_owned_binding(key, values)
        for key, values in owners.items()
    }


def _one_owned_binding(
    key: _ForwardingKey,
    owners: Mapping[RouteDemand, _ForwardingBinding],
) -> _ForwardingBinding | None:
    if not owners:
        return None
    values = set(owners.values())
    if len(values) != 1:
        raise ValueError(
            "planned Routes require conflicting forwarding for one target/source"
        )
    value = next(iter(values))
    if value.route_key != key:
        raise ValueError("compiled forwarding ownership key differs")
    return value


def _compile_constraints(
    plan: Plan,
    *,
    resource_intents: tuple[_ResourceIntent, ...],
    qdisc_intents: tuple[_QdiscIntent, ...],
    forwarding_intents: tuple[_ForwardingIntent, ...],
    zone_edge_intents: tuple[_ZoneEdgeIntent, ...],
    semantic_intents: Mapping[
        SemanticEventReference, set[_IntentReference]
    ],
) -> tuple[tuple[_OrderingConstraint, ...], tuple[tuple[str, int], ...]]:
    constraints: set[_OrderingConstraint] = set()
    intent_families: dict[str, Sequence[object]] = {
        "resource": resource_intents,
        "parameter": qdisc_intents,
        "forwarding": forwarding_intents,
        "zone-edge": zone_edge_intents,
    }
    fold_counts: dict[str, int] = defaultdict(int)
    for precedence in plan.semantic_event_precedences:
        predecessors = semantic_intents[precedence.predecessor]
        successors = semantic_intents[precedence.successor]
        if not predecessors or not successors:
            fold_counts["no-backend-effect"] += 1
            continue
        if precedence.predecessor.timestamp < precedence.successor.timestamp:
            fold_counts["activation-frontier"] += 1
            continue
        if precedence.predecessor.timestamp > precedence.successor.timestamp:
            raise ValueError("semantic precedence reverses formal time")
        if (
            len(predecessors) == 1
            and predecessors == successors
        ):
            fold_counts["same-backend-effect"] += 1
            continue
        predecessor_phases = {
            _intent_phase(intent_families[value.domain][value.index])
            for value in predecessors
        }
        successor_phases = {
            _intent_phase(intent_families[value.domain][value.index])
            for value in successors
        }
        if max(predecessor_phases) < min(successor_phases):
            fold_counts["runner-phase"] += 1
            continue
        if predecessor_phases == successor_phases == {3}:
            fold_counts["atomic-forwarding-generation"] += 1
            continue

        fallback: list[_OrderingConstraint] = []
        for predecessor in predecessors:
            predecessor_intent = intent_families[predecessor.domain][
                predecessor.index
            ]
            for successor in successors:
                if predecessor == successor:
                    continue
                successor_intent = intent_families[successor.domain][
                    successor.index
                ]
                if _intent_phase(predecessor_intent) > _intent_phase(
                    successor_intent
                ):
                    raise ValueError(
                        "semantic precedence cannot be represented by BPF "
                        "activation phases"
                    )
                fallback.append(
                    _OrderingConstraint(
                        predecessor,
                        successor,
                        "plan-semantic-precedence-fallback",
                    )
                )
        constraints.update(fallback)
        fold_counts[
            "explicit-fallback" if fallback else "same-backend-effect"
        ] += 1

    establishments: dict[
        tuple[OperationalConnectionId, datetime], int
    ] = {}
    qdiscs_by_direction: dict[
        tuple[OperationalConnectionId, NodeId, NodeId],
        list[tuple[datetime, int]],
    ] = defaultdict(list)
    for index, intent in enumerate(resource_intents):
        if intent.operation is _ResourceOperation.ESTABLISH:
            establishments[(intent.connection_id, intent.nominal_time)] = index
    for index, intent in enumerate(qdisc_intents):
        qdiscs_by_direction[
            (intent.connection_id, intent.source, intent.target)
        ].append((intent.nominal_time, index))
        resource_index = establishments.get(
            (intent.connection_id, intent.nominal_time)
        )
        if resource_index is not None:
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("resource", resource_index),
                    _IntentReference("parameter", index),
                    "resource-ready-before-qdisc",
                )
            )

    for index, intent in enumerate(forwarding_intents):
        if intent.current is None:
            continue
        binding = intent.current
        resource_index = establishments.get(
            (binding.connection_id, intent.nominal_time)
        )
        if resource_index is not None:
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("resource", resource_index),
                    _IntentReference("forwarding", index),
                    "resource-ready-before-forwarding",
                )
            )
        parameter_index = _latest_intent_at_or_before(
            qdiscs_by_direction.get(
                (
                    binding.connection_id,
                    binding.route_key.source,
                    binding.next_hop,
                ),
                (),
            ),
            intent.nominal_time,
        )
        if parameter_index is not None:
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("parameter", parameter_index),
                    _IntentReference("forwarding", index),
                    "qdisc-ready-before-forwarding",
                )
            )

    forwarding_stop_users: dict[
        OperationalConnectionId, list[tuple[datetime, int]]
    ] = defaultdict(list)
    for forwarding_index, forwarding in enumerate(forwarding_intents):
        if (
            forwarding.previous is not None
            and (
                forwarding.current is None
                or forwarding.current.connection_id
                != forwarding.previous.connection_id
            )
        ):
            forwarding_stop_users[
                forwarding.previous.connection_id
            ].append((forwarding.nominal_time, forwarding_index))
    for resource_index, resource in enumerate(resource_intents):
        if resource.operation is not _ResourceOperation.WITHDRAW:
            continue
        for nominal_time, forwarding_index in forwarding_stop_users.get(
            resource.connection_id, ()
        ):
            if nominal_time <= resource.nominal_time:
                constraints.add(
                    _OrderingConstraint(
                        _IntentReference("forwarding", forwarding_index),
                        _IntentReference("resource", resource_index),
                        "stop-use-before-resource-retirement",
                    )
                )

    forwarding_by_zone_time: dict[tuple[str, datetime], list[int]] = defaultdict(list)
    for index, intent in enumerate(forwarding_intents):
        target = intent.route_key.target
        if isinstance(target, ZoneRouteDemandTarget):
            forwarding_by_zone_time[(target.zone_id, intent.nominal_time)].append(
                index
            )
    for zone_index, zone in enumerate(zone_edge_intents):
        for forwarding_index in forwarding_by_zone_time.get(
            (zone.zone_id, zone.nominal_time), ()
        ):
            zone_reference = _IntentReference("zone-edge", zone_index)
            forwarding_reference = _IntentReference(
                "forwarding", forwarding_index
            )
            if zone.phase is _ZoneEdgePhase.READINESS:
                predecessor = zone_reference
                successor = forwarding_reference
                reason = "zone-ready-before-forwarding"
            else:
                predecessor = forwarding_reference
                successor = zone_reference
                reason = "forwarding-stopped-before-zone-retirement"
            constraints.add(
                _OrderingConstraint(predecessor, successor, reason)
            )

    inventory: dict[_IntentReference, object] = {}
    for domain, values in (
        ("resource", resource_intents),
        ("parameter", qdisc_intents),
        ("forwarding", forwarding_intents),
        ("zone-edge", zone_edge_intents),
    ):
        for index, value in enumerate(values):
            inventory[_IntentReference(domain, index)] = value
    for constraint in constraints:
        predecessor = inventory[constraint.predecessor]
        successor = inventory[constraint.successor]
        if getattr(predecessor, "nominal_time") > getattr(successor, "nominal_time"):
            raise ValueError("compiled constraint reverses formal time")
        if (
            getattr(predecessor, "nominal_time")
            == getattr(successor, "nominal_time")
            and _intent_phase(predecessor) > _intent_phase(successor)
        ):
            raise ValueError(
                "semantic precedence cannot be lowered by BPF activation phases: "
                f"{constraint.reason}: {predecessor!r} -> {successor!r}"
            )
    return (
        tuple(sorted(constraints, key=_constraint_sort_key)),
        tuple(sorted(fold_counts.items())),
    )


def _validate_backend_state(
    state: _MutableState,
    semantic_state: PlannedOperationalState,
    *,
    projection: _PhysicalWorldProjection,
    zone_prefixes: Mapping[str, str],
) -> None:
    assignment_zone_ids = {
        value.zone_id for value in semantic_state.zone_service_assignments
    }
    if assignment_zone_ids != set(projection.zone_ids):
        raise ValueError(
            "planned state must explicitly assign every PhysicalWorld Zone"
        )
    expected_resources = {
        value.connection_id: value
        for value in semantic_state.operational_connections
    }
    if state.resources != expected_resources:
        raise ValueError("compiled resources differ from planned Connections")
    expected_qdiscs = {
        (connection.connection_id, source, target): parameters
        for connection in semantic_state.operational_connections
        for source, target, parameters in _directional_parameters(connection)
    }
    if state.qdiscs != expected_qdiscs:
        raise ValueError("compiled qdiscs differ from planned parameters")
    expected_qdisc_keys: dict[
        OperationalConnectionId,
        set[tuple[OperationalConnectionId, NodeId, NodeId]],
    ] = defaultdict(set)
    for key in expected_qdiscs:
        expected_qdisc_keys[key[0]].add(key)
    if state.qdisc_keys_by_connection != dict(expected_qdisc_keys):
        raise ValueError("compiled qdisc Connection index differs")
    expected_zone_edges = {
        value.zone_id: _ZoneEdgeState(
            value.zone_id,
            zone_prefixes[value.zone_id],
            value.satellite_id,
        )
        for value in semantic_state.zone_service_assignments
        if value.satellite_id is not None
    }
    if state.zone_edges != expected_zone_edges:
        raise ValueError("compiled Zone edges differ from planned assignments")
    expected_forwarding = _aggregate_forwarding(state.forwarding_owners)
    if state.forwarding != expected_forwarding:
        raise ValueError("compiled forwarding differs from planned Route outcomes")
    expected_forwarding_keys: dict[
        OperationalConnectionId, set[_ForwardingKey]
    ] = defaultdict(set)
    for key, binding in expected_forwarding.items():
        expected_forwarding_keys[binding.connection_id].add(key)
    if state.forwarding_keys_by_connection != dict(expected_forwarding_keys):
        raise ValueError("compiled forwarding Connection index differs")
    node_ids = frozenset(projection.node_ids)
    for connection in semantic_state.operational_connections:
        _require_connection_world(connection, node_ids=node_ids)


def _validate_zone_assignments_for_projection(
    semantic_state: PlannedOperationalState,
    *,
    node_ids: frozenset[NodeId],
    zone_ids: frozenset[str],
) -> None:
    assignments = semantic_state.zone_service_assignments
    if {value.zone_id for value in assignments} != zone_ids:
        raise ValueError(
            "planned state must explicitly assign every PhysicalWorld Zone"
        )
    for assignment in assignments:
        if (
            assignment.satellite_id is not None
            and assignment.satellite_id not in node_ids
        ):
            raise ValueError(
                "planned Zone assignment contains a foreign PhysicalWorld node"
            )
def _validate_route_demand_for_projection(
    demand: RouteDemand,
    *,
    node_ids: frozenset[NodeId],
    zone_ids: frozenset[str],
) -> None:
    if demand.source not in node_ids:
        raise ValueError("planned Route source is outside the PhysicalWorld")
    target = demand.target
    if isinstance(target, GroundStationRouteDemandTarget):
        if target.ground_station_id not in node_ids:
            raise ValueError(
                "planned Route targets a foreign PhysicalWorld GroundStation"
            )
    elif isinstance(target, ZoneRouteDemandTarget):
        if target.zone_id not in zone_ids:
            raise ValueError("planned Route targets a foreign Zone")
    else:  # pragma: no cover - RouteDemand validates the public union
        raise TypeError("unsupported Route demand target")


def _validate_unreachable_reason(
    outcome: UnreachableRouteOutcome,
    semantic_state: PlannedOperationalState,
    *,
    zone_assignments: Mapping[str, NodeId | None] | None = None,
) -> None:
    target = outcome.demand.target
    expected = RouteUnreachableReason.NO_OPERATIONAL_PATH
    if isinstance(target, ZoneRouteDemandTarget):
        assignments = (
            {
                value.zone_id: value.satellite_id
                for value in semantic_state.zone_service_assignments
            }
            if zone_assignments is None
            else zone_assignments
        )
        if assignments[target.zone_id] is None:
            expected = RouteUnreachableReason.ZONE_UNSERVED
    if outcome.reason is not expected:
        raise ValueError(
            "planned unreachable Route reason contradicts Zone service state"
        )


def _freeze_state(
    state: _MutableState,
    zone_prefixes: Mapping[str, str],
) -> _IntendedState:
    return _IntendedState(
        resources=tuple(
            _ResourceState(state.resources[key])
            for key in sorted(state.resources, key=_connection_key_sort)
        ),
        qdiscs=tuple(
            _QdiscState(connection_id, source, target, state.qdiscs[key])
            for key in sorted(state.qdiscs, key=_qdisc_key_sort)
            for connection_id, source, target in (key,)
        ),
        forwarding=tuple(
            state.forwarding[key]
            for key in sorted(state.forwarding, key=_forwarding_key_sort)
        ),
        zone_edges=tuple(
            state.zone_edges[key] for key in sorted(state.zone_edges)
        ),
        zone_prefixes=tuple(sorted(zone_prefixes.items())),
    )


def _physical_world_projection(
    physical_world: PhysicalWorld,
) -> _PhysicalWorldProjection:
    nodes = []
    for autonomous_system in physical_world.autonomous_systems:
        nodes.extend(
            station.node_id for station in autonomous_system.ground_stations
        )
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                nodes.extend(satellite.node_id for satellite in orbit.satellites)
    ordered_nodes = tuple(sorted(nodes, key=_node_key))
    if len(ordered_nodes) != len(set(ordered_nodes)):
        raise ValueError("PhysicalWorld contains duplicate Node identities")
    try:
        zones = physical_world.get_zone_layout().zones
    except RuntimeError:
        zones = ()
    zone_ids = tuple(sorted(zone.zone_id for zone in zones))
    return _PhysicalWorldProjection(ordered_nodes, zone_ids)


def _allocate_zone_prefixes(zone_ids: Sequence[str]) -> dict[str, str]:
    if len(zone_ids) > _ZONE_POOL.num_addresses - 2:
        raise ValueError("PhysicalWorld has too many Zones for BPF identity space")
    return {
        zone_id: f"{IPv4Address(int(_ZONE_POOL.network_address) + index)}/32"
        for index, zone_id in enumerate(zone_ids, start=1)
    }


def _require_connection_world(
    connection: OperationalConnection,
    *,
    node_ids: frozenset[NodeId],
) -> None:
    identifier = connection.connection_id
    if (
        identifier.endpoint_a not in node_ids
        or identifier.endpoint_b not in node_ids
    ):
        raise ValueError("planned Connection contains a foreign PhysicalWorld node")


def _connection_id_for_pair(
    first: NodeId,
    second: NodeId,
) -> OperationalConnectionId:
    kinds = {first.entity_kind, second.entity_kind}
    if kinds == {"satellite"}:
        kind = OperationalConnectionKind.INTER_SATELLITE
    elif kinds == {"satellite", "ground-station"}:
        kind = OperationalConnectionKind.GROUND_STATION_ACCESS
    else:
        raise ValueError("planned Route hop has no supported Connection kind")
    return OperationalConnectionId(kind, first, second)


def _attachment_demands(
    identifier: OperationalConnectionId,
) -> tuple[_AttachmentDemand, _AttachmentDemand]:
    return (
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


def _directional_parameters(
    connection: OperationalConnection,
) -> tuple[
    tuple[NodeId, NodeId, EffectiveConnectionParameters],
    tuple[NodeId, NodeId, EffectiveConnectionParameters],
]:
    identifier = connection.connection_id
    return (
        (
            identifier.endpoint_a,
            identifier.endpoint_b,
            connection.endpoint_a_to_b,
        ),
        (
            identifier.endpoint_b,
            identifier.endpoint_a,
            connection.endpoint_b_to_a,
        ),
    )


def _bind_subject_event(
    event_index: _TransitionEventIndex,
    semantic_intents: dict[SemanticEventReference, set[_IntentReference]],
    *,
    kind: SemanticEventKind,
    intent: _IntentReference,
    connection_id: OperationalConnectionId | None = None,
) -> None:
    reference = event_index.by_connection.get((kind, connection_id))
    if reference is None:
        raise ValueError("canonical semantic event reference is missing or ambiguous")
    semantic_intents[reference].add(intent)


def _index_transition_events(
    references: tuple[SemanticEventReference, ...],
) -> _TransitionEventIndex:
    by_connection: dict[
        tuple[SemanticEventKind, OperationalConnectionId | None],
        SemanticEventReference,
    ] = {}
    by_route_demand: dict[RouteDemand, list[SemanticEventReference]] = (
        defaultdict(list)
    )
    by_zone: dict[
        tuple[SemanticEventKind, str], SemanticEventReference
    ] = {}
    for reference in references:
        if reference.connection_id is not None:
            key = reference.kind, reference.connection_id
            if key in by_connection:
                raise ValueError("canonical Connection event is ambiguous")
            by_connection[key] = reference
        if reference.route_demand is not None:
            by_route_demand[reference.route_demand].append(reference)
        if reference.zone_id is not None:
            zone_key = reference.kind, reference.zone_id
            if zone_key in by_zone:
                raise ValueError("canonical Zone event is ambiguous")
            by_zone[zone_key] = reference
    return _TransitionEventIndex(
        by_connection,
        {
            demand: tuple(values)
            for demand, values in by_route_demand.items()
        },
        by_zone,
    )


def _latest_intent_at_or_before(
    values: Sequence[tuple[datetime, int]],
    timestamp: datetime,
) -> int | None:
    position = bisect_right(values, (timestamp, 2**63 - 1)) - 1
    return None if position < 0 else values[position][1]


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


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _connection_key_sort(
    identifier: OperationalConnectionId,
) -> tuple[object, ...]:
    return (
        identifier.kind.value,
        _node_key(identifier.endpoint_a),
        _node_key(identifier.endpoint_b),
    )


def _qdisc_key_sort(
    value: tuple[OperationalConnectionId, NodeId, NodeId],
) -> tuple[object, ...]:
    identifier, source, target = value
    return _connection_key_sort(identifier), _node_key(source), _node_key(target)


def _route_target_sort(target: object) -> tuple[object, ...]:
    if isinstance(target, GroundStationRouteDemandTarget):
        return "ground-station", _node_key(target.ground_station_id)
    if isinstance(target, ZoneRouteDemandTarget):
        return "zone", target.zone_id
    raise TypeError("unsupported Route demand target")


def _forwarding_key_sort(value: _ForwardingKey) -> tuple[object, ...]:
    return _node_key(value.source), _route_target_sort(value.target)


def _constraint_sort_key(value: _OrderingConstraint) -> tuple[object, ...]:
    return (
        value.predecessor.domain,
        value.predecessor.index,
        value.successor.domain,
        value.successor.index,
        value.reason,
    )


def _compile_scope(plan: Plan, compile_index: int) -> dict[str, object]:
    return {
        "compile_index": compile_index,
        "realization_index": compile_index,
        "plan_id": plan.plan_id,
        "previous_plan_id": plan.previous_plan_id,
        "start_timestamp": plan.start_timestamp.isoformat(),
        "end_timestamp": plan.end_timestamp.isoformat(),
    }


def _observe_compile_stage(
    observer: _ExecutionObserver | None,
    *,
    stage: str,
    started: float,
    scope: Mapping[str, object],
    work: Mapping[str, int | float],
    duration_role: str = "stage",
) -> float | None:
    if observer is None:
        return None
    duration_seconds = perf_counter() - started
    observer.record(
        component="compiler",
        stage=stage,
        phase="one-shot",
        duration_seconds=duration_seconds,
        duration_role=duration_role,
        scope=scope,
        work=work,
        started_monotonic=started,
    )
    return duration_seconds


__all__ = ["compile_bpf"]
