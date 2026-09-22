"""New local facts remain available without inventing path/reachability evidence."""
import json
from pathlib import Path

from skyseed_v2.simulator import LocalRouteOutcome, RouteDisposition, RoutePlanningResult


def test_local_results_survive_observation_and_network_export(tmp_path, monkeypatch):
    from examples.Sky00_tiny_network import tiny_network as tiny
    from skyseed_v2.experiment import ExperimentController
    from tools.observation.planner import collect_planner_data, read_planner_history, write_planner_tool
    from tools.observation.physical_world import write_physical_world_tool
    from tools.observation.planner.export import write_network_export
    from tools.observation.traffic.projection import _project_transaction
    from tools.observation.traffic.renderer import _transaction_row

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

    current = next(c for c in data['frames'][0]['transition']['route_outcome_transitions']
                   if c['demand']['target']['kind'] == 'ground-station')
    source, target = current['demand']['source'], current['demand']['target']['ground_station_id']
    def node_id(n): return f"{n['asn']}/{n['entity_kind']}/{n['entity_id']}"
    route = {'demand': current['demand'], **current['current']}
    transaction = {'request': {'simulation_timestamp': data['frames'][0]['timestamp'],
                              'source_ipv4': '10.0.0.1', 'destination_ipv4': '10.0.0.2'},
                   'observed_reply': False, 'identifier': 1, 'sequence': 1,
                   'packet_observations': 1, 'pairing_status': 'request-only'}
    for disposition in ('forward', 'drop', 'pass'):
        value = _project_transaction(transaction, boundary=data['frames'][0], zone_assignments={},
            route_outcomes=[dict(route, disposition=disposition)],
            nodes_by_address={'10.0.0.1': {'node_id': node_id(source)}, '10.0.0.2': {'node_id': node_id(target)}},
            zone_prefixes={}, activation_times=())
        assert value['route_outcome'] is not None
        assert value['planned_reachability'] == 'unknown' and value['classification'] == 'unclassified'
        assert f'local {disposition}' in _transaction_row(value)
