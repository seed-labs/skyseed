"""Lower one semantic Plan into an immutable symbolic BPF realization."""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from time import perf_counter

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    Connection,
    ConnectionDirection,
    ConnectionId,
    ConnectionKind,
    ConnectionTransition,
    ConnectionTransitionOperation,
    DifferenceOperation,
    DirectionalConnectionParameters,
    ForwardingTransition,
    GroundStationRouteTarget,
    ParameterTransition,
    Plan,
    RouteKey,
    Slicer,
    TopologySlice,
    ZoneRouteTarget,
)

from .compiled_realization import (
    BpfCompiledRealization,
    _AttachmentDemand,
    _ForwardingBinding,
    _ForwardingIntent,
    _IntendedState,
    _IntentReference,
    _OrderingConstraint,
    _QdiscIntent,
    _QdiscState,
    _ResourceIntent,
    _ResourceState,
    _TopologySignature,
    _ZoneEdgeIntent,
    _ZoneEdgeState,
)


_ZONE_POOL = IPv4Network("198.18.0.0/15")


@dataclass(slots=True)
class _MutableState:
    resources: dict[ConnectionId, Connection]
    qdiscs: dict[
        tuple[ConnectionId, ConnectionDirection],
        DirectionalConnectionParameters,
    ]
    forwarding: dict[RouteKey, _ForwardingBinding]
    zone_edges: dict[str, _ZoneEdgeState]


@dataclass(frozen=True, slots=True)
class _PreparedSourceFacts:
    source_slices_by_time: Mapping[
        datetime,
        tuple[TopologySlice, TopologySlice],
    ]


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
    """Bind private evidence metadata around one public ``compile_bpf`` call."""

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
    *,
    slicer: Slicer,
    previous: BpfCompiledRealization | None = None,
) -> BpfCompiledRealization:
    """Deterministically lower one Plan using retained authoritative Slice facts.

    This function reads existing Slicer/TopologySlice values only. It does not
    advance Slicer, invoke a Model, synchronize Planner, resolve live runtime
    resources or mutate the supplied objects.
    """

    observation = _CURRENT_COMPILE_OBSERVATION.get()
    return _compile_bpf(
        plan,
        slicer=slicer,
        previous=previous,
        execution_observer=(None if observation is None else observation.observer),
        compile_index=(0 if observation is None else observation.compile_index),
        observation_binding=observation,
    )


def _compile_bpf_observed(
    plan: Plan,
    *,
    slicer: Slicer,
    previous: BpfCompiledRealization | None,
    execution_observer: _ExecutionObserver,
    compile_index: int,
) -> BpfCompiledRealization:
    """Compile once while emitting detached private execution evidence."""

    with _bind_compile_observation(
        execution_observer,
        compile_index=compile_index,
    ):
        return compile_bpf(plan, slicer=slicer, previous=previous)


def _compile_bpf(
    plan: Plan,
    *,
    slicer: Slicer,
    previous: BpfCompiledRealization | None,
    execution_observer: _ExecutionObserver | None,
    compile_index: int,
    observation_binding: _CompileObservationBinding | None,
) -> BpfCompiledRealization:
    total_started = _observation_start(execution_observer)
    stage_started = _observation_start(execution_observer)
    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    if not isinstance(slicer, Slicer):
        raise TypeError("slicer must be a Slicer")
    if previous is not None and not isinstance(previous, BpfCompiledRealization):
        raise TypeError("previous must be a BpfCompiledRealization or None")
    scope = _compile_scope(plan, compile_index)
    _observe_compile_stage(
        execution_observer,
        stage="input-validation",
        started=stage_started,
        scope=scope,
        work={
            "connection_transitions": len(plan.connection_transitions),
            "parameter_transitions": len(plan.parameter_transitions),
            "forwarding_transitions": len(plan.forwarding_transitions),
            "forwarding_precedences": len(plan.forwarding_precedences),
        },
    )

    stage_started = _observation_start(execution_observer)
    slices = _coverage_slices(plan, slicer)
    _observe_compile_stage(
        execution_observer,
        stage="slice-coverage",
        started=stage_started,
        scope=scope,
        work={"coverage_slices": len(slices)},
    )
    stage_started = _observation_start(execution_observer)
    source_facts = _prepare_source_facts(plan, slices)
    _observe_compile_stage(
        execution_observer,
        stage="temporal-source-indexing",
        started=stage_started,
        scope=scope,
        work={
            "temporal_groups": len(plan.temporal_index),
            "source_boundaries_indexed": len(
                source_facts.source_slices_by_time
            ),
        },
    )
    stage_started = _observation_start(execution_observer)
    topology_signature = _topology_signature(slicer)
    zone_prefixes = _allocate_zone_prefixes(topology_signature.zone_ids)
    _observe_compile_stage(
        execution_observer,
        stage="topology-preparation",
        started=stage_started,
        scope=scope,
        work={
            "topology_nodes": len(topology_signature.node_ids),
            "zone_prefixes": len(zone_prefixes),
        },
    )
    stage_started = _observation_start(execution_observer)
    state = _initial_state(
        plan,
        slices[plan.start_slice_id],
        topology_signature,
        zone_prefixes,
        previous,
    )
    _observe_compile_stage(
        execution_observer,
        stage="baseline-initialization",
        started=stage_started,
        scope=scope,
        work=_state_cardinality(state, prefix="baseline"),
    )
    baseline_resource_ids = frozenset(state.resources)
    baseline_forwarding = dict(state.forwarding)

    stage_started = _observation_start(execution_observer)
    _validate_plan_transitions(plan, source_facts)
    _observe_compile_stage(
        execution_observer,
        stage="plan-transition-validation",
        started=stage_started,
        scope=scope,
        work={
            "connection_transitions_validated": len(
                plan.connection_transitions
            ),
            "parameter_transitions_validated": len(plan.parameter_transitions),
            "forwarding_transitions_validated": len(plan.forwarding_transitions),
        },
    )
    stage_started = _observation_start(execution_observer)
    resource_intents, qdisc_intents = _lower_resources_and_parameters(
        plan, state
    )
    _observe_compile_stage(
        execution_observer,
        stage="resource-parameter-lowering",
        started=stage_started,
        scope=scope,
        work={
            "connection_transitions_lowered": len(plan.connection_transitions),
            "parameter_transitions_lowered": len(plan.parameter_transitions),
            "resource_intents_emitted": len(resource_intents),
            "parameter_intents_emitted": len(qdisc_intents),
        },
    )
    stage_started = _observation_start(execution_observer)
    forwarding_intents = _lower_forwarding(
        plan,
        source_facts,
        zone_prefixes,
        _active_resource_ids_by_time(
            plan,
            baseline_resource_ids,
            {
                transition.nominal_time
                for transition in plan.forwarding_transitions
            },
        ),
        state,
    )
    _observe_compile_stage(
        execution_observer,
        stage="forwarding-lowering",
        started=stage_started,
        scope=scope,
        work={
            "forwarding_transitions_lowered": len(plan.forwarding_transitions),
            "forwarding_intents_emitted": len(forwarding_intents),
        },
    )
    stage_started = _observation_start(execution_observer)
    zone_intents = _lower_zone_edges(
        plan,
        slices,
        zone_prefixes,
        baseline_forwarding,
        forwarding_intents,
        state,
    )
    _observe_compile_stage(
        execution_observer,
        stage="zone-edge-lowering",
        started=stage_started,
        scope=scope,
        work={
            "slice_intervals_processed": max(0, len(slices) - 1),
            "zones_considered_per_interval": len(zone_prefixes),
            "zone_edge_intents_emitted": len(zone_intents),
        },
    )
    stage_started = _observation_start(execution_observer)
    constraints = _compile_constraints(
        plan,
        resource_intents,
        qdisc_intents,
        forwarding_intents,
        zone_intents,
        baseline_forwarding,
        source_facts,
    )
    _observe_compile_stage(
        execution_observer,
        stage="constraint-compilation",
        started=stage_started,
        scope=scope,
        work={
            "forwarding_precedences_lowered": len(plan.forwarding_precedences),
            "resource_intents_considered": len(resource_intents),
            "parameter_intents_considered": len(qdisc_intents),
            "forwarding_intents_considered": len(forwarding_intents),
            "zone_edge_intents_considered": len(zone_intents),
            "constraints_emitted": len(constraints),
        },
    )

    stage_started = _observation_start(execution_observer)
    _validate_end_state(slices[plan.end_slice_id], state)
    _observe_compile_stage(
        execution_observer,
        stage="end-state-validation",
        started=stage_started,
        scope=scope,
        work=_state_cardinality(state, prefix="end_state"),
    )
    stage_started = _observation_start(execution_observer)
    intended_state = _freeze_state(state, zone_prefixes)
    result = BpfCompiledRealization._create(
        start_slice_id=plan.start_slice_id,
        start_timestamp=plan.start_timestamp,
        end_slice_id=plan.end_slice_id,
        end_timestamp=plan.end_timestamp,
        resource_realizations=resource_intents,
        parameter_realizations=qdisc_intents,
        forwarding_realizations=forwarding_intents,
        zone_edge_realizations=zone_intents,
        constraints=constraints,
        intended_state=intended_state,
        topology_signature=topology_signature,
    )
    output_work = {
        "resource_intents": len(resource_intents),
        "parameter_intents": len(qdisc_intents),
        "forwarding_intents": len(forwarding_intents),
        "zone_edge_intents": len(zone_intents),
        "constraints": len(constraints),
        "baseline_resources": len(intended_state.resources),
        "baseline_parameters": len(intended_state.qdiscs),
        "baseline_forwarding": len(intended_state.forwarding),
        "baseline_zone_edges": len(intended_state.zone_edges),
        "baseline_zone_prefixes": len(intended_state.zone_prefixes),
    }
    _observe_compile_stage(
        execution_observer,
        stage="realization-freeze",
        started=stage_started,
        scope=scope,
        work=output_work,
    )
    total_duration = _observe_compile_stage(
        execution_observer,
        stage="total-compile",
        started=total_started,
        scope=scope,
        work={
            "plan_delta_transitions": (
                len(plan.connection_transitions)
                + len(plan.parameter_transitions)
                + len(plan.forwarding_transitions)
            ),
            "plan_forwarding_precedences": len(plan.forwarding_precedences),
            "interval_intents": (
                len(resource_intents)
                + len(qdisc_intents)
                + len(forwarding_intents)
                + len(zone_intents)
            ),
            "realization_constraints": len(constraints),
            "baseline_records": (
                len(intended_state.resources)
                + len(intended_state.qdiscs)
                + len(intended_state.forwarding)
                + len(intended_state.zone_edges)
            ),
        },
        duration_role="rollup",
    )
    if observation_binding is not None:
        assert total_duration is not None
        observation_binding._set_total_duration(total_duration)
    return result


def _observation_start(observer: _ExecutionObserver | None) -> float:
    return perf_counter() if observer is not None else 0.0


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
    )
    return duration_seconds


def _compile_scope(plan: Plan, compile_index: int) -> dict[str, object]:
    return {
        "compile_index": compile_index,
        "realization_index": compile_index,
        "plan_index": compile_index,
        "publication_index": compile_index,
        "publication_start_slice_id": plan.start_slice_id,
        "publication_end_slice_id": plan.end_slice_id,
        "start_slice_id": plan.start_slice_id,
        "end_slice_id": plan.end_slice_id,
        "start_timestamp": (
            None if plan.start_timestamp is None else plan.start_timestamp.isoformat()
        ),
        "end_timestamp": plan.end_timestamp.isoformat(),
    }


def _state_cardinality(
    state: _MutableState,
    *,
    prefix: str,
) -> dict[str, int]:
    return {
        f"{prefix}_resources": len(state.resources),
        f"{prefix}_parameters": len(state.qdiscs),
        f"{prefix}_forwarding": len(state.forwarding),
        f"{prefix}_zone_edges": len(state.zone_edges),
    }


def _coverage_slices(plan: Plan, slicer: Slicer) -> dict[int, TopologySlice]:
    slices = {
        slice_id: slicer.get_slice(slice_id)
        for slice_id in range(plan.start_slice_id, plan.end_slice_id + 1)
    }
    start = slices[plan.start_slice_id]
    end = slices[plan.end_slice_id]
    if start.timestamp != plan.start_timestamp:
        raise ValueError("Plan start does not match retained Slice facts")
    if end.timestamp != plan.end_timestamp:
        raise ValueError("Plan end does not match retained Slice facts")
    return slices


def _prepare_source_facts(
    plan: Plan,
    slices: Mapping[int, TopologySlice],
) -> _PreparedSourceFacts:
    return _PreparedSourceFacts(
        source_slices_by_time={
            nominal_time: _source_slices(nominal_time, slices)
            for nominal_time in plan.temporal_index
        }
    )


def _topology_signature(slicer: Slicer) -> _TopologySignature:
    topology = slicer.get_topology()
    node_ids = tuple(
        sorted(
            (
                node.node_id
                for autonomous_system in topology.autonomous_systems
                for node in (
                    *(
                        satellite
                        for shell in autonomous_system.shells
                        for orbit in shell.orbits
                        for satellite in orbit.satellites
                    ),
                    *autonomous_system.ground_stations,
                )
            ),
            key=_node_key,
        )
    )
    try:
        zone_ids = tuple(
            zone.zone_id for zone in topology.get_zone_layout().zones
        )
    except RuntimeError:
        zone_ids = ()
    return _TopologySignature(node_ids=node_ids, zone_ids=zone_ids)


def _allocate_zone_prefixes(zone_ids: tuple[str, ...]) -> dict[str, str]:
    if len(zone_ids) > _ZONE_POOL.num_addresses:
        raise ValueError("BPF Zone identity pool is exhausted")
    base = int(_ZONE_POOL.network_address)
    return {
        zone_id: f"{IPv4Address(base + index)}/32"
        for index, zone_id in enumerate(sorted(zone_ids))
    }


def _initial_state(
    plan: Plan,
    start_slice: TopologySlice,
    topology_signature: _TopologySignature,
    zone_prefixes: Mapping[str, str],
    previous: BpfCompiledRealization | None,
) -> _MutableState:
    if plan.start_slice_id == 0:
        if previous is not None:
            raise ValueError("bootstrap compilation must not have previous state")
        state = _MutableState({}, {}, {}, {})
    else:
        if previous is None:
            raise ValueError("non-bootstrap compilation requires previous state")
        if (
            previous.end_slice_id != plan.start_slice_id
            or previous.end_timestamp != plan.start_timestamp
        ):
            raise ValueError("previous compiled realization is not continuous")
        if previous._topology_signature != topology_signature:
            raise ValueError("previous compiled realization has different topology")
        if dict(previous.zone_prefixes) != dict(zone_prefixes):
            raise ValueError("previous Zone packet identities do not match")
        state = _MutableState(
            resources={
                value.connection.connection_id: value.connection
                for value in previous.intended_resources
            },
            qdiscs={
                (value.connection_id, value.direction): value.parameters
                for value in previous.intended_parameters
            },
            forwarding={
                value.route_key: value
                for value in previous.intended_forwarding
            },
            zone_edges={
                value.zone_id: value for value in previous.intended_zone_edges
            },
        )
    _validate_baseline(start_slice, state)
    return state


def _validate_baseline(
    topology_slice: TopologySlice,
    state: _MutableState,
) -> None:
    expected_connections = {
        connection.connection_id: connection
        for connection in topology_slice.connection_slice.connections
    }
    if not set(state.resources) <= set(expected_connections):
        raise ValueError("compiled resource baseline exceeds start Slice")
    expected_qdisc_keys = {
        (connection_id, direction)
        for connection_id in state.resources
        for direction in ConnectionDirection
    }
    if set(state.qdiscs) != expected_qdisc_keys:
        raise ValueError("compiled qdisc baseline does not match active resources")


def _validate_plan_transitions(
    plan: Plan,
    source_facts: _PreparedSourceFacts,
) -> None:
    for transition in plan.connection_transitions:
        previous_slice, current_slice = (
            source_facts.source_slices_by_time[transition.nominal_time]
        )
        previous_connection = _connection_or_none(
            previous_slice, transition.connection_id
        )
        current_connection = _connection_or_none(
            current_slice, transition.connection_id
        )
        if transition.operation is ConnectionTransitionOperation.ESTABLISH:
            if (
                previous_connection is not None
                or current_connection != transition.connection
            ):
                raise ValueError("Connection transition contradicts Slice facts")
        elif (
            previous_connection is None
            or current_connection is not None
        ):
            raise ValueError("Connection transition contradicts Slice facts")

    for transition in plan.parameter_transitions:
        _, current_slice = source_facts.source_slices_by_time[
            transition.nominal_time
        ]
        connection = current_slice.connection_slice.get_connection(
            transition.connection_id
        )
        formal = (
            connection.endpoint_a_to_b
            if transition.direction is ConnectionDirection.ENDPOINT_A_TO_B
            else connection.endpoint_b_to_a
        )
        for field_name in ("bandwidth_bps", "delay_us", "packet_loss_ppm"):
            target = getattr(transition, field_name)
            if target is not None and target != getattr(formal, field_name):
                raise ValueError("Parameter transition contradicts Slice facts")

    for transition in plan.forwarding_transitions:
        previous_slice, current_slice = (
            source_facts.source_slices_by_time[transition.nominal_time]
        )
        old = _route_next_hop(previous_slice, transition.route_key)
        new = _route_next_hop(current_slice, transition.route_key)
        if transition.operation is DifferenceOperation.ADDED:
            valid = old is None and new == transition.next_hop
        elif transition.operation is DifferenceOperation.CHANGED:
            valid = old is not None and new == transition.next_hop
        else:
            valid = old is not None and new is None and transition.next_hop is None
        if not valid:
            raise ValueError("Forwarding transition contradicts Slice facts")


def _lower_resources_and_parameters(
    plan: Plan,
    state: _MutableState,
) -> tuple[tuple[_ResourceIntent, ...], tuple[_QdiscIntent, ...]]:
    resource_intents: list[_ResourceIntent] = []
    qdisc_intents: list[_QdiscIntent] = []
    events: list[tuple[datetime, int, int, object]] = []
    for index, transition in enumerate(plan.connection_transitions):
        phase = (
            0
            if transition.operation is ConnectionTransitionOperation.ESTABLISH
            else 2
        )
        events.append((transition.nominal_time, phase, index, transition))
    for index, transition in enumerate(plan.parameter_transitions):
        events.append((transition.nominal_time, 1, index, transition))

    for _, _, _, transition in sorted(events, key=lambda value: value[:3]):
        if isinstance(transition, ConnectionTransition):
            if transition.operation is ConnectionTransitionOperation.ESTABLISH:
                assert transition.connection is not None
                if transition.connection_id in state.resources:
                    raise ValueError("Connection is already active in baseline")
                connection = transition.connection
                state.resources[transition.connection_id] = connection
                attachments = _attachment_demands(connection)
                resource_intents.append(
                    _ResourceIntent(
                        transition.nominal_time,
                        transition.operation,
                        transition.connection_id,
                        attachments,
                    )
                )
                for direction, parameters in (
                    (
                        ConnectionDirection.ENDPOINT_A_TO_B,
                        connection.endpoint_a_to_b,
                    ),
                    (
                        ConnectionDirection.ENDPOINT_B_TO_A,
                        connection.endpoint_b_to_a,
                    ),
                ):
                    state.qdiscs[(transition.connection_id, direction)] = parameters
                    source, target = _direction_endpoints(
                        connection.connection_id, direction
                    )
                    qdisc_intents.append(
                        _QdiscIntent(
                            transition.nominal_time,
                            transition.connection_id,
                            direction,
                            source,
                            target,
                            parameters.bandwidth_bps,
                            parameters.delay_us,
                            parameters.packet_loss_ppm,
                            "connection-establish",
                        )
                    )
            else:
                try:
                    connection = state.resources.pop(transition.connection_id)
                except KeyError as error:
                    raise ValueError(
                        "Connection withdrawal has no active resource"
                    ) from error
                resource_intents.append(
                    _ResourceIntent(
                        transition.nominal_time,
                        transition.operation,
                        transition.connection_id,
                        _attachment_demands(connection),
                    )
                )
                for direction in ConnectionDirection:
                    state.qdiscs.pop((transition.connection_id, direction), None)
        elif isinstance(transition, ParameterTransition):
            key = (transition.connection_id, transition.direction)
            try:
                previous = state.qdiscs[key]
                connection = state.resources[transition.connection_id]
            except KeyError as error:
                raise ValueError(
                    "Parameter transition has no active symbolic resource"
                ) from error
            current = DirectionalConnectionParameters(
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
            state.qdiscs[key] = current
            source, target = _direction_endpoints(
                connection.connection_id, transition.direction
            )
            qdisc_intents.append(
                _QdiscIntent(
                    transition.nominal_time,
                    transition.connection_id,
                    transition.direction,
                    source,
                    target,
                    transition.bandwidth_bps,
                    transition.delay_us,
                    transition.packet_loss_ppm,
                    "parameter-transition",
                )
            )
        else:  # pragma: no cover - constructed only above
            raise TypeError("unsupported lowering event")
    return tuple(resource_intents), tuple(qdisc_intents)


def _lower_forwarding(
    plan: Plan,
    source_facts: _PreparedSourceFacts,
    zone_prefixes: Mapping[str, str],
    active_resource_ids_by_time: Mapping[
        datetime,
        frozenset[ConnectionId],
    ],
    state: _MutableState,
) -> tuple[_ForwardingIntent, ...]:
    result = []
    for transition in plan.forwarding_transitions:
        previous = state.forwarding.get(transition.route_key)
        if transition.operation is DifferenceOperation.ADDED:
            if previous is not None:
                raise ValueError("Forwarding addition already exists in baseline")
            current = _forwarding_binding(
                transition,
                source_facts.source_slices_by_time[
                    transition.nominal_time
                ][1],
                zone_prefixes,
            )
            _require_planned_connection(
                current,
                transition.nominal_time,
                active_resource_ids_by_time,
            )
            state.forwarding[transition.route_key] = current
        elif transition.operation is DifferenceOperation.CHANGED:
            if previous is None:
                raise ValueError("Forwarding change has no previous realization")
            current = _forwarding_binding(
                transition,
                source_facts.source_slices_by_time[
                    transition.nominal_time
                ][1],
                zone_prefixes,
            )
            _require_planned_connection(
                current,
                transition.nominal_time,
                active_resource_ids_by_time,
            )
            state.forwarding[transition.route_key] = current
        else:
            if previous is None:
                raise ValueError("Forwarding removal has no previous realization")
            current = None
            state.forwarding.pop(transition.route_key)
        result.append(
            _ForwardingIntent(
                transition.nominal_time,
                transition.operation,
                transition.route_key,
                previous,
                current,
            )
        )
    return tuple(result)


def _lower_zone_edges(
    plan: Plan,
    slices: Mapping[int, TopologySlice],
    zone_prefixes: Mapping[str, str],
    baseline_forwarding: Mapping[RouteKey, _ForwardingBinding],
    forwarding_intents: tuple[_ForwardingIntent, ...],
    state: _MutableState,
) -> tuple[_ZoneEdgeIntent, ...]:
    result = []
    projected_forwarding_by_zone: dict[
        str,
        dict[RouteKey, _ForwardingBinding],
    ] = defaultdict(dict)
    for route_key, binding in baseline_forwarding.items():
        target = route_key.target
        if isinstance(target, ZoneRouteTarget):
            projected_forwarding_by_zone[target.zone_id][route_key] = binding
    forwarding_index = 0
    for slice_id in range(plan.start_slice_id + 1, plan.end_slice_id + 1):
        nominal_time = slices[slice_id].timestamp
        assert nominal_time is not None
        touched_zones = set()
        while (
            forwarding_index < len(forwarding_intents)
            and forwarding_intents[forwarding_index].nominal_time
            <= nominal_time
        ):
            intent = forwarding_intents[forwarding_index]
            target = intent.route_key.target
            if isinstance(target, ZoneRouteTarget):
                touched_zones.add(target.zone_id)
                zone_forwarding = projected_forwarding_by_zone[target.zone_id]
                if intent.current is None:
                    zone_forwarding.pop(intent.route_key, None)
                else:
                    zone_forwarding[intent.route_key] = intent.current
            forwarding_index += 1

        raw_assignments = (
            slices[slice_id].connection_slice.zone_service_assignments
        )
        for zone_id in sorted(zone_prefixes):
            previous_edge = state.zone_edges.get(zone_id)
            old = (
                previous_edge.service_satellite_id
                if previous_edge is not None
                else None
            )
            new = _planned_zone_service_satellite(
                zone_id,
                projected_forwarding_by_zone.get(zone_id, {}),
                raw_assignments.get(zone_id),
                old,
                zone_id in touched_zones,
            )
            if old == new:
                continue
            if old is None:
                operation = DifferenceOperation.ADDED
            elif new is None:
                operation = DifferenceOperation.REMOVED
            else:
                operation = DifferenceOperation.CHANGED
            prefix = zone_prefixes[zone_id]
            result.append(
                _ZoneEdgeIntent(
                    nominal_time,
                    operation,
                    zone_id,
                    prefix,
                    old,
                    new,
                )
            )
            if new is None:
                state.zone_edges.pop(zone_id, None)
            else:
                state.zone_edges[zone_id] = _ZoneEdgeState(
                    zone_id, prefix, new
                )
    return tuple(result)


def _planned_zone_service_satellite(
    zone_id: str,
    forwarding: Mapping[RouteKey, _ForwardingBinding],
    raw_service_satellite: NodeId | None,
    previous_service_satellite: NodeId | None,
    forwarding_changed: bool,
) -> NodeId | None:
    next_hops = {
        route_key.source: binding.next_hop
        for route_key, binding in forwarding.items()
        if isinstance(route_key.target, ZoneRouteTarget)
        and route_key.target.zone_id == zone_id
    }
    if next_hops:
        terminals = set()
        for source in next_hops:
            current = source
            seen = set()
            while current in next_hops:
                if current in seen:
                    raise ValueError(
                        "planned Zone forwarding contains a cycle"
                    )
                seen.add(current)
                current = next_hops[current]
            terminals.add(current)
        if len(terminals) != 1:
            raise ValueError(
                "planned Zone forwarding has multiple service terminals"
            )
        terminal = next(iter(terminals))
        if terminal.entity_kind != "satellite":
            raise ValueError(
                "planned Zone forwarding terminal is not a Satellite"
            )
        return terminal
    if raw_service_satellite is None:
        return None
    if previous_service_satellite is None or forwarding_changed:
        return raw_service_satellite
    return previous_service_satellite


def _compile_constraints(
    plan: Plan,
    resources: tuple[_ResourceIntent, ...],
    qdiscs: tuple[_QdiscIntent, ...],
    forwarding: tuple[_ForwardingIntent, ...],
    zone_edges: tuple[_ZoneEdgeIntent, ...],
    baseline_forwarding: Mapping[RouteKey, _ForwardingBinding],
    source_facts: _PreparedSourceFacts,
) -> tuple[_OrderingConstraint, ...]:
    constraints: set[_OrderingConstraint] = set()
    forwarding_by_transition_id = {
        id(transition): index
        for index, transition in enumerate(plan.forwarding_transitions)
    }
    for precedence in plan.forwarding_precedences:
        constraints.add(
            _OrderingConstraint(
                _IntentReference(
                    "forwarding",
                    forwarding_by_transition_id[id(precedence.predecessor)],
                ),
                _IntentReference(
                    "forwarding",
                    forwarding_by_transition_id[id(precedence.successor)],
                ),
                "plan-forwarding-precedence",
            )
        )

    establishment_qdiscs: dict[
        tuple[ConnectionId, datetime],
        list[int],
    ] = defaultdict(list)
    resource_establishments: dict[
        ConnectionId,
        list[tuple[datetime, int]],
    ] = defaultdict(list)
    parameter_updates: dict[
        tuple[ConnectionId, ConnectionDirection],
        list[tuple[datetime, int]],
    ] = defaultdict(list)
    for qdisc_index, qdisc in enumerate(qdiscs):
        parameter_updates[(qdisc.connection_id, qdisc.direction)].append(
            (qdisc.nominal_time, qdisc_index)
        )
        if qdisc.reason == "connection-establish":
            establishment_qdiscs[
                (qdisc.connection_id, qdisc.nominal_time)
            ].append(qdisc_index)
    for resource_index, resource in enumerate(resources):
        if resource.operation is ConnectionTransitionOperation.ESTABLISH:
            resource_establishments[resource.connection_id].append(
                (resource.nominal_time, resource_index)
            )
            for qdisc_index in establishment_qdiscs.get(
                (resource.connection_id, resource.nominal_time),
                (),
            ):
                constraints.add(
                    _OrderingConstraint(
                        _IntentReference("resource", resource_index),
                        _IntentReference("parameter", qdisc_index),
                        "resource-ready-before-qdisc",
                    )
                )

    for forwarding_index, intent in enumerate(forwarding):
        if intent.current is None:
            continue
        connection_id = intent.current.connection_id
        direction = intent.current.direction
        resource_index = _latest_index_at_or_before(
            resource_establishments.get(connection_id, ()),
            intent.nominal_time,
        )
        if resource_index is not None:
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("resource", resource_index),
                    _IntentReference("forwarding", forwarding_index),
                    "resource-ready-before-forwarding",
                )
            )
        parameter_index = _latest_index_at_or_before(
            parameter_updates.get((connection_id, direction), ()),
            intent.nominal_time,
        )
        if parameter_index is not None:
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("parameter", parameter_index),
                    _IntentReference("forwarding", forwarding_index),
                    "qdisc-ready-before-forwarding",
                )
            )

    stops_by_connection: dict[
        ConnectionId,
        list[tuple[datetime, int]],
    ] = defaultdict(list)
    for forwarding_index, intent in enumerate(forwarding):
        if intent.previous is not None and (
            intent.current is None
            or intent.current.connection_id != intent.previous.connection_id
        ):
            stops_by_connection[intent.previous.connection_id].append(
                (intent.nominal_time, forwarding_index)
            )

    projected = dict(baseline_forwarding)
    forwarding_use_counts = Counter(
        binding.connection_id for binding in projected.values()
    )
    projected_forwarding_index = 0
    for resource_index, resource in enumerate(resources):
        if resource.operation is not ConnectionTransitionOperation.WITHDRAW:
            continue
        while (
            projected_forwarding_index < len(forwarding)
            and forwarding[projected_forwarding_index].nominal_time
            <= resource.nominal_time
        ):
            intent = forwarding[projected_forwarding_index]
            previous = projected.get(intent.route_key)
            if previous is not None:
                forwarding_use_counts[previous.connection_id] -= 1
                if forwarding_use_counts[previous.connection_id] == 0:
                    del forwarding_use_counts[previous.connection_id]
            if intent.current is None:
                projected.pop(intent.route_key, None)
            else:
                projected[intent.route_key] = intent.current
                forwarding_use_counts[intent.current.connection_id] += 1
            projected_forwarding_index += 1
        if resource.connection_id in forwarding_use_counts:
            raise ValueError(
                "Connection retirement precedes forwarding stop-use"
            )
        stops = stops_by_connection.get(resource.connection_id, ())
        stop_count = bisect_right(
            stops,
            (resource.nominal_time, len(forwarding)),
        )
        for _, forwarding_index in stops[:stop_count]:
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("forwarding", forwarding_index),
                    _IntentReference("resource", resource_index),
                    "stop-use-before-resource-retirement",
                )
            )

    forwarding_by_zone_boundary: dict[
        tuple[str, datetime],
        list[int],
    ] = defaultdict(list)
    for forwarding_index, intent in enumerate(forwarding):
        target = intent.route_key.target
        if isinstance(target, ZoneRouteTarget):
            current_slice = source_facts.source_slices_by_time[
                intent.nominal_time
            ][1]
            assert current_slice.timestamp is not None
            forwarding_by_zone_boundary[
                (target.zone_id, current_slice.timestamp)
            ].append(forwarding_index)
    for zone_index, zone_edge in enumerate(zone_edges):
        for forwarding_index in forwarding_by_zone_boundary.get(
            (zone_edge.zone_id, zone_edge.nominal_time),
            (),
        ):
            reason = (
                "forwarding-stopped-before-zone-retirement"
                if zone_edge.operation is DifferenceOperation.REMOVED
                else "forwarding-ready-before-zone-activation"
            )
            constraints.add(
                _OrderingConstraint(
                    _IntentReference("forwarding", forwarding_index),
                    _IntentReference("zone-edge", zone_index),
                    reason,
                )
            )

    return tuple(sorted(constraints, key=_constraint_key))


def _latest_index_at_or_before(
    values: Sequence[tuple[datetime, int]],
    nominal_time: datetime,
) -> int | None:
    position = bisect_right(values, (nominal_time, 2**63 - 1)) - 1
    return None if position < 0 else values[position][1]


def _validate_end_state(
    topology_slice: TopologySlice,
    state: _MutableState,
) -> None:
    _validate_baseline(topology_slice, state)
    active = set(state.resources)
    for binding in state.forwarding.values():
        if binding.connection_id not in active:
            raise ValueError("intended forwarding uses a retired resource")


def _require_planned_connection(
    binding: _ForwardingBinding,
    nominal_time: datetime,
    active_resource_ids_by_time: Mapping[
        datetime,
        frozenset[ConnectionId],
    ],
) -> None:
    if binding.connection_id not in active_resource_ids_by_time[nominal_time]:
        raise ValueError(
            "forwarding realization requires an inactive planned Connection"
        )


def _active_resource_ids_by_time(
    plan: Plan,
    baseline_resource_ids: frozenset[ConnectionId],
    nominal_times: set[datetime],
) -> dict[datetime, frozenset[ConnectionId]]:
    active = set(baseline_resource_ids)
    result = {}
    transition_index = 0
    transitions = plan.connection_transitions
    for nominal_time in sorted(nominal_times):
        while (
            transition_index < len(transitions)
            and transitions[transition_index].nominal_time <= nominal_time
        ):
            transition = transitions[transition_index]
            if transition.operation is ConnectionTransitionOperation.ESTABLISH:
                active.add(transition.connection_id)
            else:
                active.discard(transition.connection_id)
            transition_index += 1
        result[nominal_time] = frozenset(active)
    return result


def _freeze_state(
    state: _MutableState,
    zone_prefixes: Mapping[str, str],
) -> _IntendedState:
    return _IntendedState(
        resources=tuple(
            _ResourceState(connection)
            for _, connection in sorted(
                state.resources.items(), key=lambda value: _connection_key(value[0])
            )
        ),
        qdiscs=tuple(
            _QdiscState(connection_id, direction, parameters)
            for (connection_id, direction), parameters in sorted(
                state.qdiscs.items(),
                key=lambda value: (
                    _connection_key(value[0][0]),
                    value[0][1].value,
                ),
            )
        ),
        forwarding=tuple(
            binding
            for _, binding in sorted(
                state.forwarding.items(), key=lambda value: _route_key(value[0])
            )
        ),
        zone_edges=tuple(
            state.zone_edges[zone_id] for zone_id in sorted(state.zone_edges)
        ),
        zone_prefixes=tuple(sorted(zone_prefixes.items())),
    )


def _forwarding_binding(
    transition: ForwardingTransition,
    current_slice: TopologySlice,
    zone_prefixes: Mapping[str, str],
) -> _ForwardingBinding:
    assert transition.next_hop is not None
    source = transition.route_key.source
    kind = _connection_kind(source, transition.next_hop)
    connection = current_slice.connection_slice.get_connection_for_endpoints(
        kind, source, transition.next_hop
    )
    direction = _direction_from_source(connection.connection_id, source)
    target = transition.route_key.target
    if isinstance(target, ZoneRouteTarget):
        packet_prefix = zone_prefixes[target.zone_id]
    elif isinstance(target, GroundStationRouteTarget):
        packet_prefix = None
    else:  # pragma: no cover - RouteKey validates this
        raise TypeError("unsupported RouteTarget")
    return _ForwardingBinding(
        transition.route_key,
        transition.next_hop,
        connection.connection_id,
        direction,
        packet_prefix,
    )


def _source_slices(
    nominal_time: datetime,
    slices: Mapping[int, TopologySlice],
) -> tuple[TopologySlice, TopologySlice]:
    ids = sorted(slices)
    for current_id in ids[1:]:
        previous = slices[current_id - 1]
        current = slices[current_id]
        assert current.timestamp is not None
        if previous.timestamp is None:
            matches = nominal_time == current.timestamp
        else:
            matches = previous.timestamp < nominal_time <= current.timestamp
        if matches:
            return previous, current
    raise ValueError("transition has no source Difference in Plan coverage")


def _connection_or_none(
    topology_slice: TopologySlice,
    connection_id: ConnectionId,
) -> Connection | None:
    try:
        return topology_slice.connection_slice.get_connection(connection_id)
    except KeyError:
        return None


def _route_next_hop(
    topology_slice: TopologySlice,
    route_key: RouteKey,
) -> NodeId | None:
    return topology_slice.route_slice.routes.get(route_key.source, {}).get(
        route_key.target
    )


def _attachment_demands(
    connection: Connection,
) -> tuple[_AttachmentDemand, _AttachmentDemand]:
    identifier = connection.connection_id
    return (
        _AttachmentDemand(
            identifier,
            ConnectionDirection.ENDPOINT_A_TO_B,
            identifier.endpoint_a,
            identifier.endpoint_b,
        ),
        _AttachmentDemand(
            identifier,
            ConnectionDirection.ENDPOINT_B_TO_A,
            identifier.endpoint_b,
            identifier.endpoint_a,
        ),
    )


def _direction_endpoints(
    connection_id: ConnectionId,
    direction: ConnectionDirection,
) -> tuple[NodeId, NodeId]:
    if direction is ConnectionDirection.ENDPOINT_A_TO_B:
        return connection_id.endpoint_a, connection_id.endpoint_b
    return connection_id.endpoint_b, connection_id.endpoint_a


def _direction_from_source(
    connection_id: ConnectionId,
    source: NodeId,
) -> ConnectionDirection:
    if source == connection_id.endpoint_a:
        return ConnectionDirection.ENDPOINT_A_TO_B
    if source == connection_id.endpoint_b:
        return ConnectionDirection.ENDPOINT_B_TO_A
    raise ValueError("forwarding source is not a Connection endpoint")


def _connection_kind(first: NodeId, second: NodeId) -> ConnectionKind:
    kinds = {first.entity_kind, second.entity_kind}
    if kinds == {"satellite"}:
        return ConnectionKind.INTER_SATELLITE
    if kinds == {"satellite", "ground-station"}:
        return ConnectionKind.GROUND_STATION_ACCESS
    raise ValueError("forwarding node pair has no supported Connection kind")


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _connection_key(
    connection_id: ConnectionId,
) -> tuple[str, tuple[int, str, str], tuple[int, str, str]]:
    return (
        connection_id.kind.value,
        _node_key(connection_id.endpoint_a),
        _node_key(connection_id.endpoint_b),
    )


def _route_key(route_key: RouteKey) -> tuple:
    target = route_key.target
    if isinstance(target, GroundStationRouteTarget):
        target_key = ("G", _node_key(target.ground_station_id))
    else:
        target_key = ("Z", target.zone_id)
    return _node_key(route_key.source), target_key


def _constraint_key(constraint: _OrderingConstraint) -> tuple:
    return (
        constraint.predecessor.domain,
        constraint.predecessor.index,
        constraint.successor.domain,
        constraint.successor.index,
        constraint.reason,
    )


__all__ = ["compile_bpf"]
