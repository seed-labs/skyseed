"""Exercise the real local algorithms, frozen construction and normal handoff."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import pytest
from examples.Sky01_s5_custom_models import s5
from examples.Sky01_s5_custom_models.model import CurrentGeometryModel, GreedyInterSatelliteModel, GreedyGroundStationModel
from templates.models.position import StaticPositionModel
from skyseed import simulator, adapters, configuration
from skyseed.simulator import planner, physical
from skyseed.simulator.geometry import geodetic_to_ecef
from skyseed.simulator.input import SimulatorStaticInput
from skyseed.backend.bpf import compile_bpf
from tests.unit.test_network_planner_semantic_core import _satellite, _station, EPOCH
from tests._support import selection_state, drain_simulator


def test_frozen_s5_construction_and_zones():
    network = s5.build_emulator()
    system, = s5.emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    static = adapters.create_simulator_static_input((system,))
    satellites = static.list_satellites()
    assert system.getAsn() == 65040
    assert len(satellites) == 172 and len(static.list_ground_stations()) == 10
    assert sorted(Counter(s.orbit_id for s in satellites).values()) == [43] * 4
    assert sorted({s.right_ascension_deg for s in satellites}) == [75.7, 87.7, 99.7, 117.7]
    assert {(s.altitude_km, s.inclination_deg, s.epoch_utc) for s in satellites} == {
        (560.0, 97.6, datetime(2015, 1, 1, tzinfo=timezone.utc))}
    source = next(s for s in configuration.emulator.load_shell_configs(s5.SHELLS_FILE) if s.shell_id == 'S5')
    assert {(s.satellite_id, s.mean_anomaly_deg, s.orbit_index) for s in satellites} == {
        (s.satellite_id, s.mean_anomaly_deg, s.index_in_orbit) for o in source.explicit_orbits for s in o.satellites}
    zones = configuration.simulator.load_zone_layout_definition(s5.ZONE_FILE)
    assert len(zones.zones) == 184
    assert zones == configuration.simulator.build_global_zone_layout(
        latitude_bands=12, equatorial_cells=24, alignment='antimeridian-centered')
    stations = static.list_ground_stations()
    assert sum(-48 <= s.latitude_deg <= -34 and 165 <= s.longitude_deg <= 179 for s in stations) == 6
    assert sum(-55 <= s.latitude_deg <= -35 and -76 <= s.longitude_deg <= -68 for s in stations) == 4
    assert {s.max_simultaneous_ground_station_accesses for s in satellites} == {2}
    assert {s.maximum_simultaneous_inter_satellite_links for s in satellites} == {4}


def test_local_models_nonempty_preparation_capacity_loss_and_compiler():
    sats = tuple(replace(_satellite(i, capability=1), max_simultaneous_ground_station_accesses=1) for i in range(4))
    gs = (replace(_station(0), max_simultaneous_satellite_accesses=None),
          replace(_station(1), max_simultaneous_satellite_accesses=1))
    ids = [s.node_id for s in sats]
    points = {n: geodetic_to_ecef(0, lon, 560_000) for n, lon in zip(ids, (0, 0.5, 2, 180))}
    points.update({g.node_id: geodetic_to_ecef(g.latitude_deg, g.longitude_deg, 0) for g in gs})
    position = StaticPositionModel(points)
    definition = simulator.SimulationDefinition(SimulatorStaticInput(sats, gs),
        simulator.SimulationTimeline(EPOCH, EPOCH + timedelta(seconds=8), timedelta(seconds=1)))
    demands = tuple(planner.RouteDemand(n, planner.GroundStationRouteDemandTarget(gs[0].node_id)) for n in (ids[0], ids[3]))
    sim = simulator.create_default_simulator(definition,
        configuration=simulator.DefaultSimulatorConfiguration(zone_layout_definition=None),
        planner_configuration=planner.PlannerConfiguration(frames_per_batch=1),
        position_model=position, physical_link_model=CurrentGeometryModel(),
        inter_satellite_model=GreedyInterSatelliteModel(), ground_station_model=GreedyGroundStationModel(),
        route_demands=demands)
    world, plan, compiled = sim.get_physical_world(), sim.get_planner(), None
    try:
        request = plan.create_physical_link_request()
        assert len(request.inter_satellite_pairs) == 6
        assert request.inter_satellite_minimum_contact_duration == timedelta(0)
        assert request.ground_station_minimum_contact_duration == timedelta(0)
        snapshots = []
        for seconds in range(9):
            # A genuine geometric outage cancels preparation; restoration starts it again.
            position.positions = ({**points, ids[1]: geodetic_to_ecef(0, 100, 560_000),
                                   ids[2]: geodetic_to_ecef(0, -100, 560_000)} if seconds == 2 else points)
            frame = world.frame_at(EPOCH + timedelta(seconds=seconds))
            facts = sim.get_physical_link_model().compute_observation(world, frame, request)
            assert facts.work_counts is None
            assert bool(facts.inter_satellite_candidates) == (seconds != 2)
            assert facts.ground_station_access_candidates
            assert all(c.contact_window is None and c.same_plane is None for c in facts.inter_satellite_candidates)
            generated, = plan.observe(frame, facts)
            compiled = compile_bpf(generated, world, compiled)
            evidence = plan.get_published_evidence(generated.plan_id)
            snapshots.append(evidence.frames[0])
            assert evidence.inspections[0].underfilled_isl_slots is None
            if seconds == 0:
                assert not plan.get_state().operational_connections
                assert all(isinstance(o, planner.UnreachableRouteOutcome) for o in plan.get_state().route_outcomes)
            assert isinstance(plan.get_state().route_outcomes[-1], planner.UnreachableRouteOutcome)
            assert compiled.output_planned_state == plan.get_state()
            plan.release_published_through(generated.plan_id)
        assert plan.get_state().operational_connections
        assert any(isinstance(o, planner.ReachableRouteOutcome) for o in plan.get_state().route_outcomes)
        # Direct selections include pending reservations and never exceed either endpoint.
        pending = selection_state([(ids[0], ids[1])], timestamp=EPOCH, acquisition_duration=timedelta(seconds=5), operational=False)
        facts = sim.get_physical_link_model().compute_observation(world, world.frame_at(EPOCH), request)
        chosen = GreedyInterSatelliteModel().select_inter_satellite_links(world, facts.inter_satellite_candidates, pending)
        degree = Counter(n for c in chosen.selected_candidates for n in (c.endpoint_a, c.endpoint_b))
        assert max(degree.values()) == 1
        assert any({c.endpoint_a, c.endpoint_b} == {ids[0], ids[1]} for c in chosen.selected_candidates)
        selected = GreedyGroundStationModel().select_ground_station_accesses(world, facts.ground_station_access_candidates, pending)
        counts = Counter(n for c in selected for n in (c.ground_station_id, c.satellite_id))
        assert counts[gs[0].node_id] <= 4 and counts[gs[1].node_id] <= 1
        assert all(counts[n] <= 1 for n in ids)
        key = planner.OperationalConnectionId(planner.OperationalConnectionKind.INTER_SATELLITE, ids[0], ids[1])
        starts = [v.selected_since for v in snapshots[-1].selected_inter_satellite_links if {v.candidate.endpoint_a, v.candidate.endpoint_b} == {key.endpoint_a, key.endpoint_b}]
        assert starts == [EPOCH + timedelta(seconds=3)]
    finally:
        sim.shutdown()


def test_entry_injects_three_local_instances_and_default_traffic(tmp_path, monkeypatch):
    calls = []
    class Controller:
        @staticmethod
        def add_arguments(parser, **options):
            return original.add_arguments(parser, **options)
        def __init__(self, network, simulation, output, **options):
            self.simulation = simulation
            assert options['traffic_enabled'] is True
            assert options['input_files'] == (s5.SHELLS_FILE, s5.GROUND_STATIONS_FILE, s5.ZONE_FILE, s5.EXAMPLE_ROOT/'model.py')
            assert isinstance(simulation.get_physical_link_model(), CurrentGeometryModel)
            assert isinstance(simulation.get_planner().get_inter_satellite_model(), GreedyInterSatelliteModel)
            assert isinstance(simulation.get_planner().get_ground_station_model(), GreedyGroundStationModel)
            assert isinstance(simulation.get_planner().get_route_model(), planner.GatewayHopCountRoutePlanningModel)
            assert simulation.get_timeline().end_time - simulation.get_timeline().start_time == timedelta(seconds=600)
            assert simulation.get_timeline().observation_interval == timedelta(seconds=5)
        def generate(self):
            calls.append('generate')
            self.simulation.shutdown()
            return tmp_path
        def serve(self, **options):
            calls.append('run' if options['run'] else 'serve')
            options['on_completed'](tmp_path, {})
            self.simulation.shutdown()
            return tmp_path
    original = s5.experiment.ExperimentController
    monkeypatch.setattr(s5.experiment, 'ExperimentController', Controller)
    monkeypatch.setattr(s5.tools, 'write_experiment_tools', lambda *a, **k: calls.append('tools'))
    monkeypatch.setattr(s5.experiment, 'run_generated_experiment', lambda *a, **k: calls.append('execute'))
    assert s5.main(['--output', str(tmp_path)]) == 0
    assert calls == ['serve', 'tools']
    calls.clear()
    assert s5.main(['--output', str(tmp_path), '--execute']) == 0
    assert calls == ['generate', 'tools', 'execute']
    calls.clear()
    assert s5.main(['--output', str(tmp_path), '--operation', 'run']) == 0
    assert calls == ['run', 'tools']


@pytest.mark.parametrize('declared_limit,expected', [(None, 4), (1, 1), (7, 5)])
def test_local_gs_fallback_only_applies_when_device_limit_unspecified(declared_limit, expected):
    sats = tuple(_satellite(i) for i in range(5))
    station = replace(_station(), max_simultaneous_satellite_accesses=declared_limit)
    definition = simulator.SimulationDefinition(SimulatorStaticInput(sats, (station,)),
        simulator.SimulationTimeline(EPOCH, EPOCH + timedelta(seconds=5), timedelta(seconds=5)))
    sim = simulator.create_default_simulator(definition,
        configuration=simulator.DefaultSimulatorConfiguration(zone_layout_definition=None))
    try:
        candidates = tuple(physical.GroundStationAccessCandidate(station.node_id, s.node_id,
            elevation_deg=90-i, propagation_delay_us=2000) for i, s in enumerate(sats))
        # Retain the lowest-elevation pending reservation even with higher alternatives.
        pending = selection_state([(station.node_id, sats[-1].node_id)], timestamp=EPOCH,
                                  acquisition_duration=timedelta(seconds=5), operational=False)
        selected = GreedyGroundStationModel().select_ground_station_accesses(sim.get_physical_world(), candidates, pending)
        assert len(selected) == expected
        assert candidates[-1] in selected
    finally:
        sim.shutdown()
