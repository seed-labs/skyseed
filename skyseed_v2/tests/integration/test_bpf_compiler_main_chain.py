from __future__ import annotations

from pathlib import Path

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_producer,
    drain_mini_satellite_internet,
)
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.backend.bpf.compiled_realization import _ZoneEdgePhase
from skyseed_v2.simulator.planning import plan_from_data, plan_to_data


def test_complete_chain_compiles_detached_self_contained_plans() -> None:
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, snapshot = run_complete_static_chain(simulator)
    world = simulator.get_physical_world()

    detached_plans = tuple(plan_from_data(plan_to_data(value)) for value in plans)
    detached_realizations = []
    previous = None
    for plan in detached_plans:
        previous = compile_bpf(plan, world, previous)
        detached_realizations.append(previous)

    assert tuple(detached_realizations) == realizations
    assert snapshot["formal_frame_count"] == 4
    assert sum(len(value.transitions) for value in plans) == 4
    assert sum(len(value.activation_timestamps) for value in realizations) == 4
    assert all(
        realization.physical_world_fingerprint == world.semantic_fingerprint()
        for realization in realizations
    )


def test_mini_chain_splits_zone_handover_around_route_publication() -> None:
    simulator, producer = create_mini_satellite_internet_producer(
        Path("examples/mini_satellite_internet")
    )
    plans, realizations, snapshot = drain_mini_satellite_internet(
        simulator, producer
    )
    zone_intents = tuple(
        value
        for realization in realizations
        for value in realization.zone_edge_realizations
    )
    assert len(plans) == len(realizations) == 2
    assert snapshot["formal_frame_count"] == 64
    assert any(value.phase is _ZoneEdgePhase.READINESS for value in zone_intents)
    assert any(value.phase is _ZoneEdgePhase.RETIREMENT for value in zone_intents)
