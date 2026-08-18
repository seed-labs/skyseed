from __future__ import annotations

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.simulator import (
    ConnectionTransitionOperation,
    DifferenceOperation,
)


def test_canonical_emulator_simulator_planner_compiles_consecutive_bpf_chain():
    emulator = build_mini_satellite_internet_emulator()
    simulator = create_mini_satellite_internet_simulator(emulator)
    slicer = simulator.get_slicer()
    _, plans, _ = prepare_mini_satellite_internet_planning(simulator)

    compiled = []
    previous = None
    for plan in plans:
        result = compile_bpf(plan, slicer=slicer, previous=previous)
        compiled.append(result)
        previous = result

    assert [(value.start_slice_id, value.end_slice_id) for value in plans] == [
        (0, 4),
        (4, 8),
        (8, 12),
        (12, 16),
        (16, 20),
    ]
    assert {
        value.operation
        for result in compiled
        for value in result.resource_realizations
    } == set(ConnectionTransitionOperation)
    assert {
        value.operation
        for result in compiled
        for value in result.forwarding_realizations
    } == set(DifferenceOperation)
    assert any(result.parameter_realizations for result in compiled)
    assert any(result.zone_edge_realizations for result in compiled)

    prefix_sets = {result.zone_prefixes for result in compiled}
    assert len(prefix_sets) == 1
    prefixes = dict(compiled[-1].zone_prefixes)
    assert len(prefixes) == 5
    assert len(set(prefixes.values())) == len(prefixes)
    assert all(
        value.startswith("198.18.") and value.endswith("/32")
        for value in prefixes.values()
    )
    end_slice = slicer.get_slice(plans[-1].end_slice_id)
    assert {
        value.zone_id: value.service_satellite_id
        for value in compiled[-1].intended_zone_edges
    } == dict(end_slice.connection_slice.zone_service_assignments)

    plan_precedence_count = sum(
        len(plan.forwarding_precedences) for plan in plans
    )
    compiled_precedence_count = sum(
        constraint.reason == "plan-forwarding-precedence"
        for result in compiled
        for constraint in result.constraints
    )
    assert plan_precedence_count > 0
    assert compiled_precedence_count == plan_precedence_count
    assert any(
        constraint.reason == "forwarding-ready-before-zone-activation"
        for result in compiled
        for constraint in result.constraints
    )
