"""The single-result S3 entry retains normal construction and a fixed Area layout."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from examples.Sky03_s3_ospf import s3_ospf as example
from examples.Sky03_s3_ospf.ospf_model import area_interfaces, OspfRoutePlanningModel
from skyseed import adapters, simulator


def test_s3_fixed_interfaces_cover_default_potential_links_and_cyclic_boundary():
    network = example.build_emulator()
    systems = example.emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    static = adapters.create_simulator_static_input(systems)
    satellites = static.list_satellites()
    assert len(satellites) == 720 and len(static.list_ground_stations()) == 355
    orbits = sorted({(s.right_ascension_deg, s.orbit_id) for s in satellites})
    assert len(orbits) == 36
    rank = {orbit: i for i, (_, orbit) in enumerate(orbits)}
    backbone = {s.node_id for s in satellites if rank[s.orbit_id] % 6 == 5 or 0 <= s.mean_anomaly_deg < 72}
    assert len(backbone) == 240
    interfaces = area_interfaces(static)
    assert len(interfaces) == len({c.connection_id for c in interfaces}) == 15120
    members = defaultdict(set)
    for interface in interfaces:
        a, b = interface.connection_id.endpoint_a, interface.connection_id.endpoint_b
        assert interface.area_id == (0 if a in backbone and b in backbone else
            rank[static.get_satellite(b if a in backbone else a).orbit_id] // 6 + 1)
        members[a].add(interface.area_id); members[b].add(interface.area_id)
    assert Counter(next(iter(areas)) for n, areas in members.items() if n not in backbone) == {i:80 for i in range(1,7)}
    first = next(s for s in satellites if s.orbit_id == orbits[0][1] and s.mean_anomaly_deg == 90)
    last = next(s for s in satellites if s.orbit_id == orbits[-1][1] and s.mean_anomaly_deg >= 72)
    assert next(c.area_id for c in interfaces if {c.connection_id.endpoint_a,c.connection_id.endpoint_b} == {first.node_id,last.node_id}) == 1
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    sim = simulator.create_default_simulator(simulator.SimulationDefinition(static,
        simulator.SimulationTimeline(start,start+example.DEFAULT_DURATION,example.CADENCE)))
    try:
        possible = sim.get_planner()._inter_satellite_model.create_inter_satellite_candidate_pairs(sim.get_physical_world())
        assert {frozenset(pair) for pair in possible} == {frozenset((c.connection_id.endpoint_a,c.connection_id.endpoint_b)) for c in interfaces}
        assert len(sim.get_physical_world().get_zone_layout().zones) == 732
    finally:
        sim.shutdown()


def test_single_s3_entry_generates_once_then_ordinary_tools(tmp_path, monkeypatch):
    calls = []
    original = example.experiment.ExperimentController
    class Workflow:
        add_arguments = staticmethod(original.add_arguments)
        def __init__(self, network, simulation, output, **options):
            self.simulation = simulation
            assert len(simulation.get_definition().static_input.list_satellites()) == 720
            assert isinstance(simulation.get_planner()._route_model, OspfRoutePlanningModel)
            assert options['entry_path'].name == 's3_ospf.py'
            assert len(options['scenario_parameters']['node_labels']) == 355
            assert simulation.get_timeline().end_time-simulation.get_timeline().start_time == example.DEFAULT_DURATION
            assert simulation.get_timeline().observation_interval == example.CADENCE
        def generate(self):
            calls.append('generate'); self.simulation.shutdown(); return tmp_path
    monkeypatch.setattr(example.experiment, 'ExperimentController', Workflow)
    monkeypatch.setattr(example.tools, 'write_experiment_tools',
        lambda output, **kwargs: calls.append('tools') or {'planner':{'status':'PASS'}})
    monkeypatch.setattr(example.experiment, 'run_generated_experiment', lambda *a, **k: (_ for _ in ()).throw(AssertionError('live execution')))
    assert example.main(['--output',str(tmp_path), '--operation', 'generate']) == 0
    assert calls == ['generate','tools']


def test_comparison_product_removed():
    import tools
    import tools.observation.planner
    root = Path(__file__).resolve().parents[2]
    assert not list((root/'examples/route_model_comparison').glob('*.py'))
    assert not (root/'tools/observation/planner/comparison.py').exists()
    assert not (root/'tools/observation/planner/_comparison_renderer.py').exists()
    assert not hasattr(tools,'write_planner_comparison')
    assert not hasattr(tools.observation.planner,'write_planner_comparison')
