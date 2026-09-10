from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.backend.bpf import (
    BpfCompiledActivationBoundary,
    BpfCompiledRealization,
    compile_bpf,
)
from skyseed_v2.backend.bpf.compiled_realization import _ZoneEdgePhase
from skyseed_v2.backend.bpf.compiler import _compile_bpf_observed
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    PlanTransition,
    ReachableRouteOutcome,
    RouteDemand,
    RouteOutcomeTransition,
    RouteUnreachableReason,
    UnreachableRouteOutcome,
    ZoneRouteDemandTarget,
    apply_plan,
    plan_transition_fingerprint,
)
from skyseed_v2.simulator.planning import create_plan


@pytest.fixture(scope="module")
def complete_chain():
    simulator = create_complete_static_simulator(build_emulator())
    generated, _, snapshot = run_complete_static_chain(simulator)
    transitions = tuple(t for p in generated for t in p.transitions)
    plans, realizations, state = [], [], None
    # Force a predecessor boundary in this Compiler fixture, independently of
    # the Example's production batch size.
    for start in range(0, len(transitions), 3):
        batch = transitions[start:start+3]
        timestamps = {transition.timestamp for transition in batch}
        plan = create_plan(state, previous_plan_id=None if not plans else plans[-1].plan_id,
            physical_world_fingerprint=simulator.get_physical_world().semantic_fingerprint(),
            observation_interval=simulator.get_timeline().observation_interval,
            start_timestamp=transitions[start].timestamp, transitions=batch,
            semantic_event_precedences=tuple(
                precedence for published in generated
                for precedence in published.semantic_event_precedences
                if precedence.predecessor.timestamp in timestamps
                and precedence.successor.timestamp in timestamps))
        realization = compile_bpf(plan, simulator.get_physical_world(), None if not realizations else realizations[-1])
        plans.append(plan)
        realizations.append(realization)
        state = realization.output_planned_state
    plans, realizations = tuple(plans), tuple(realizations)
    return simulator, plans, realizations, snapshot


def test_compile_bpf_has_the_only_round_b_signature() -> None:
    parameters = inspect.signature(compile_bpf).parameters
    assert tuple(parameters) == (
        "plan",
        "physical_world",
        "previous_realization",
    )
    assert all(
        value.default is inspect.Parameter.empty
        for value in parameters.values()
    )


def test_observed_compile_records_real_stage_and_total_durations(
    complete_chain,
) -> None:
    simulator, plans, realizations, _ = complete_chain
    observer = _ExecutionObserver()

    actual = _compile_bpf_observed(
        plans[0],
        simulator.get_physical_world(),
        None,
        execution_observer=observer,
        compile_index=7,
    )

    assert actual == realizations[0]
    records = observer.snapshot()["records"]
    assert tuple(value["stage"] for value in records) == (
        "plan-lineage",
        "event-and-world-indexing",
        "transition-lowering",
        "constraint-folding-and-materialization",
        "backend-state-validation-and-freeze",
        "semantic-to-bpf-lowering",
        "compile-total",
    )
    assert all(value["duration_seconds"] > 0 for value in records)
    assert records[0]["work"]["replayed_transitions"] == 0
    assert records[-1]["duration_role"] == "rollup"
    assert all(value["scope"]["compile_index"] == 7 for value in records)


def test_compiler_lowers_plan_lineage_and_complete_semantic_output(
    complete_chain,
) -> None:
    simulator, plans, realizations, _ = complete_chain
    world = simulator.get_physical_world()
    assert len(plans) == len(realizations) == 2

    previous = None
    previous_state = None
    for plan, realization in zip(plans, realizations):
        assert isinstance(realization, BpfCompiledRealization)
        assert realization.source_plan_id == plan.plan_id
        assert realization.source_previous_plan_id == plan.previous_plan_id
        assert (
            realization.physical_world_fingerprint
            == world.semantic_fingerprint()
        )
        assert realization.input_state_fingerprint == plan.input_state_fingerprint
        assert realization.output_state_fingerprint == plan.output_state_fingerprint
        assert realization.observation_interval == plan.observation_interval
        assert sum(
            count for _, count in realization.semantic_precedence_folding
        ) == len(plan.semantic_event_precedences)
        assert all(
            value.reason != "plan-semantic-precedence"
            for value in realization.constraints
        )
        assert realization.output_planned_state == apply_plan(
            previous_state,
            plan,
            physical_world_fingerprint=world.semantic_fingerprint(),
        )
        assert realization.start_timestamp == plan.start_timestamp
        assert realization.end_timestamp == plan.end_timestamp
        if previous is not None:
            assert (
                realization.start_timestamp
                == previous.end_timestamp + realization.observation_interval
            )
        previous = realization
        previous_state = realization.output_planned_state


def test_every_formal_transition_is_an_explicit_activation_boundary(
    complete_chain,
) -> None:
    _, plans, realizations, _ = complete_chain
    for plan, realization in zip(plans, realizations):
        assert realization.activation_timestamps == tuple(
            value.timestamp for value in plan.transitions
        )
        assert tuple(
            value.transition_fingerprint
            for value in realization.compiled_activation_boundaries
        ) == tuple(
            plan_transition_fingerprint(value) for value in plan.transitions
        )


def test_empty_transition_remains_a_boundary_without_backend_work(
    complete_chain,
) -> None:
    simulator, _, realizations, _ = complete_chain
    world = simulator.get_physical_world()
    previous = realizations[-1]
    timestamp = previous.end_timestamp + previous.observation_interval
    transition = PlanTransition(timestamp)
    plan = create_plan(
        previous.output_planned_state,
        previous_plan_id=previous.source_plan_id,
        physical_world_fingerprint=world.semantic_fingerprint(),
        observation_interval=simulator.get_timeline().observation_interval,
        start_timestamp=timestamp,
        transitions=(transition,),
    )

    realization = compile_bpf(plan, world, previous)

    assert realization.activation_timestamps == (timestamp,)
    assert realization.compiled_activation_boundaries[0].transition_fingerprint == (
        plan_transition_fingerprint(transition)
    )
    assert realization.resource_realizations == ()
    assert realization.parameter_realizations == ()
    assert realization.forwarding_realizations == ()
    assert realization.zone_edge_realizations == ()


def test_bootstrap_plan_must_explicitly_cover_every_zone(complete_chain) -> None:
    simulator, _, _, _ = complete_chain
    world = simulator.get_physical_world()
    timestamp = datetime(2027, 1, 1, tzinfo=timezone.utc)
    plan = create_plan(
        None,
        previous_plan_id=None,
        physical_world_fingerprint=world.semantic_fingerprint(),
        observation_interval=simulator.get_timeline().observation_interval,
        start_timestamp=timestamp,
        transitions=(PlanTransition(timestamp),),
    )

    with pytest.raises(ValueError, match="explicitly assign every"):
        compile_bpf(plan, world, None)


def test_unreachable_route_demand_must_belong_to_physical_world(
    complete_chain,
) -> None:
    simulator, _, realizations, _ = complete_chain
    world = simulator.get_physical_world()
    previous = realizations[-1]
    timestamp = previous.end_timestamp + previous.observation_interval
    demand = RouteDemand(
        NodeId(99999, "satellite", "foreign-source"),
        ZoneRouteDemandTarget("foreign-zone"),
    )
    outcome = UnreachableRouteOutcome(
        demand,
        RouteUnreachableReason.NO_OPERATIONAL_PATH,
    )
    transition = PlanTransition(
        timestamp,
        route_outcome_transitions=(
            RouteOutcomeTransition(demand, None, outcome),
        ),
    )
    plan = create_plan(
        previous.output_planned_state,
        previous_plan_id=previous.source_plan_id,
        physical_world_fingerprint=world.semantic_fingerprint(),
        observation_interval=previous.observation_interval,
        start_timestamp=timestamp,
        transitions=(transition,),
    )

    with pytest.raises(ValueError, match="outside the PhysicalWorld"):
        compile_bpf(plan, world, previous)


@pytest.mark.parametrize("case", ("unserved-as-path", "served-as-unserved"))
def test_unreachable_reason_must_match_explicit_zone_service(
    complete_chain, case
) -> None:
    simulator, _, realizations, _ = complete_chain
    world = simulator.get_physical_world()
    previous = realizations[-1]
    assignments = {
        value.zone_id: value.satellite_id
        for value in previous.output_planned_state.zone_service_assignments
    }
    if case == "unserved-as-path":
        prior = next(
            value
            for value in previous.output_planned_state.route_outcomes
            if isinstance(value, UnreachableRouteOutcome)
            and isinstance(value.demand.target, ZoneRouteDemandTarget)
            and assignments[value.demand.target.zone_id] is None
        )
        reason = RouteUnreachableReason.NO_OPERATIONAL_PATH
    else:
        prior = next(
            value
            for value in previous.output_planned_state.route_outcomes
            if isinstance(value, ReachableRouteOutcome)
            and isinstance(value.demand.target, ZoneRouteDemandTarget)
            and assignments[value.demand.target.zone_id] is not None
        )
        reason = RouteUnreachableReason.ZONE_UNSERVED
    current = UnreachableRouteOutcome(prior.demand, reason)
    timestamp = previous.end_timestamp + previous.observation_interval
    transition = PlanTransition(
        timestamp,
        route_outcome_transitions=(
            RouteOutcomeTransition(prior.demand, prior, current),
        ),
    )
    plan = create_plan(
        previous.output_planned_state,
        previous_plan_id=previous.source_plan_id,
        physical_world_fingerprint=world.semantic_fingerprint(),
        observation_interval=previous.observation_interval,
        start_timestamp=timestamp,
        transitions=(transition,),
    )

    with pytest.raises(ValueError, match="reason contradicts"):
        compile_bpf(plan, world, previous)


def test_activation_boundary_requires_timestamp_and_provenance_fingerprint(
    complete_chain,
) -> None:
    _, plans, _, _ = complete_chain
    with pytest.raises(ValueError, match="timezone-aware"):
        BpfCompiledActivationBoundary(
            plans[0].transitions[0].timestamp.replace(tzinfo=None), "corrupt"
        )
    with pytest.raises(ValueError, match="non-empty"):
        BpfCompiledActivationBoundary(
            plans[0].transitions[0].timestamp, ""
        )


def test_zone_readiness_precedes_forwarding(
    complete_chain,
) -> None:
    _, _, realizations, _ = complete_chain
    first = realizations[0]
    readiness_indexes = {
        index
        for index, value in enumerate(first.zone_edge_realizations)
        if value.phase is _ZoneEdgePhase.READINESS
    }
    assert readiness_indexes
    assert any(
        constraint.predecessor.domain == "zone-edge"
        and constraint.predecessor.index in readiness_indexes
        and constraint.successor.domain == "forwarding"
        for constraint in first.constraints
    )


def test_compiler_is_deterministic_and_checks_previous_lineage(
    complete_chain,
) -> None:
    simulator, plans, realizations, _ = complete_chain
    world = simulator.get_physical_world()
    assert compile_bpf(plans[0], world, None) == realizations[0]
    assert compile_bpf(plans[1], world, realizations[0]) == realizations[1]
    with pytest.raises(ValueError, match="previous"):
        compile_bpf(plans[1], world, None)
    with pytest.raises(ValueError, match="bootstrap"):
        compile_bpf(plans[0], world, realizations[0])


def test_compiler_rejects_changed_observation_cadence(complete_chain) -> None:
    simulator, plans, realizations, _ = complete_chain
    previous = realizations[0]
    interval = plans[1].observation_interval + timedelta(seconds=1)
    timestamp = previous.end_timestamp + interval
    plan = create_plan(
        previous.output_planned_state,
        previous_plan_id=previous.source_plan_id,
        physical_world_fingerprint=previous.physical_world_fingerprint,
        observation_interval=interval,
        start_timestamp=timestamp,
        transitions=(PlanTransition(timestamp),),
    )

    with pytest.raises(ValueError, match="observation_interval"):
        compile_bpf(plan, simulator.get_physical_world(), previous)
