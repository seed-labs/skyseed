"""Shared-network assembly and sealed delivery packaging before qualification."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path

from yaml import safe_load


def test_five_shells_share_one_ground_inventory_and_use_within_shell_isls(tmp_path):
    from examples.starlink_multi_shell_satellite_internet.emu import write_inputs, build_emulator
    from examples.starlink_multi_shell_satellite_internet.simu import create_simulator
    from skyseed_v2.simulator import RetainFirstInterSatelliteConnectionPlanningModel
    settings = safe_load(Path('examples/starlink_multi_shell_satellite_internet/settings.yaml').read_text())
    simulator = create_simulator(build_emulator(write_inputs(tmp_path, settings)),
        zones=tmp_path/'inputs/zones.yaml', settings=settings)
    world = simulator.get_physical_world()
    satellites = tuple(sat for autonomous in world.autonomous_systems for shell in autonomous.shells
                       for orbit in shell.orbits for sat in orbit.satellites)
    assert len(satellites) == 4408
    assert sum(len(autonomous.ground_stations) for autonomous in world.autonomous_systems) == 355
    assert len(world.get_zone_layout().zones) == 732
    shell_inputs = safe_load((tmp_path/'inputs/shells.yaml').read_text())["shells"]
    assert {sat["maximum_simultaneous_inter_satellite_links"] for shell in shell_inputs
            for orbit in shell["orbits"] for sat in orbit["satellites"]} == {4}
    owner = {sat.node_id: shell.shell_id for autonomous in world.autonomous_systems
             for shell in autonomous.shells for orbit in shell.orbits for sat in orbit.satellites}
    pairs = RetainFirstInterSatelliteConnectionPlanningModel().create_inter_satellite_candidate_pairs(world)
    assert pairs and all(owner[a] == owner[b] for a, b in pairs)
    simulator.get_planner().shutdown()


def test_shared_packaging_preserves_two_sealed_deliveries_and_tool_metadata(tmp_path, monkeypatch):
    from examples.complete_static_emulator.emu import build_emulator
    from examples.complete_static_emulator.simu import create_complete_static_simulator, create_complete_static_planner
    from examples.starlink_gen1_satellite_internet import main
    from skyseed_v2.backend.bpf._runner_artifact import _open_runner_artifact
    from tools._detached_inspection import _delivered_batch_evidence
    simulator = create_complete_static_simulator(build_emulator())
    create_complete_static_planner(simulator)
    # The fixture and Starlink both publish up to 32 frames per Plan.
    timeline = simulator.get_timeline()
    _, snapshot, _, _ = main._run_compiler_stage(simulator, tmp_path, phase_records=[],
        delivery_ends=(timeline.start_time + timedelta(seconds=10), timeline.end_time))
    paths = [Path(value['manifest']) for value in snapshot['delivered_batches']]
    timestamps = []
    for path in paths:
        with closing(_open_runner_artifact(path)) as artifact:
            timestamps.extend(value.nominal_time for value in artifact.boundaries())
    assert len(set(timestamps)) == len(timestamps) == 4
    (tmp_path/'runtime').mkdir(exist_ok=True)
    definition = tmp_path/'runtime/execution.json'
    definition.write_text(json.dumps({'runner_artifact': {'manifest': str(paths[0].relative_to(tmp_path))},
        'runner_batches': [{'manifest': str(paths[1].relative_to(tmp_path))}]}))
    rows = _delivered_batch_evidence(definition)
    assert len(rows) == 2
    assert rows[1]['delivery']['index'] == 1
    assert rows[1]['timeline']['start'] == timestamps[2].isoformat()
