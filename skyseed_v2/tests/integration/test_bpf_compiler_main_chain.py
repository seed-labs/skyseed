from __future__ import annotations

from pathlib import Path

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from tests.fixtures import small_network
from tests._support import drain_simulator
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.backend.bpf.execution_materials.actions import _ZoneEdgePhase
from skyseed_v2.simulator.planner import plan_from_data, plan_to_data


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


def test_small_chain_splits_zone_handover_around_route_publication() -> None:
    simulator = small_network.create_simulator(small_network.build_emulator())
    plans, realizations, snapshot = drain_simulator(simulator)
    zone_intents = tuple(
        value
        for realization in realizations
        for value in realization.zone_edge_realizations
    )
    assert len(plans) == len(realizations) == 2
    assert snapshot["formal_frame_count"] == 64
    assert any(value.phase is _ZoneEdgePhase.READINESS for value in zone_intents)
    assert any(value.phase is _ZoneEdgePhase.RETIREMENT for value in zone_intents)
