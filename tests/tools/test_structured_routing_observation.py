"""New local facts remain available without inventing path/reachability evidence."""
import json
from pathlib import Path

from skyseed.simulator import LocalRouteOutcome, RouteDisposition, RoutePlanningResult


def test_local_results_survive_observation_and_network_export(tmp_path, monkeypatch):
    from examples.Sky00_tiny_network import tiny_network as tiny
    from skyseed.experiment import ExperimentController
    from tools.observation.planner import collect_planner_data, read_planner_history, write_planner_tool
    from tools.observation.physical_world import write_physical_world_tool
    from tools.observation.planner.export import write_network_export

    class DropRoutes(tiny.sky.simulator.planner.GatewayHopCountRoutePlanningModel):
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            return RoutePlanningResult(tuple(LocalRouteOutcome(d, RouteDisposition.DROP) for d in demands))
    monkeypatch.setattr(tiny.sky.simulator.planner, 'GatewayHopCountRoutePlanningModel', DropRoutes)
    network = tiny.build_emulator()
    simulator = tiny.create_simulator(network)
    generated = tmp_path / 'generated'
    ExperimentController(network, simulator, generated, entry_path=Path(tiny.__file__),
                         through='planner', traffic_enabled=False).generate()
    evidence = read_planner_history(generated / 'results/planner/planning.jsonl.gz')
    data = collect_planner_data(evidence)
    summary = data['frames'][0]['state_summary']
    assert summary['local_route_count'] > 0
    assert summary['reachable_route_count'] == summary['unreachable_route_count'] == 0
    write_planner_tool(evidence, tmp_path / 'planner-view')
    write_physical_world_tool(simulator.get_physical_world(), generated / 'visualizations/physical-world',
        physical_frames_path=generated / 'results/physical-world/physical-frames.jsonl')
    write_network_export(generated, tmp_path / 'export')
    index = json.loads((tmp_path / 'export/network/index.json').read_text())
    for frame in index['frames']:
        rows = json.loads((tmp_path / 'export/network' / frame['file']).read_text())['route_outcomes']
        assert rows and all(row[1:3] == [None, None] and row[3]['kind'] == 'local' for row in rows)

    # Traffic selection remains meaningful even when all authored routes DROP.
    from tools.generation.traffic.workload import select_workload
    workload = select_workload(simulator.get_physical_world())
    assert workload.communications
    assert all(c.direction in {'ground-to-zone', 'zone-to-ground'} for c in workload.communications)
