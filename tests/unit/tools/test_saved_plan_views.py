"""Saved-file views preserve event boundaries and never regenerate model output."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from skyseed import Scene, save_plans
from skyseed.pc.physical import PhysicalLayer, LinkCandidate
from skyseed.pc.link import LinkPlan, LinkOperation, ConnectionCandidate, LinkConfiguration, LinkProperties
from skyseed.pc.network import RoutePlan, RouteOperation, ForwardingRule, RouteDestination
from tools.observation import plan_views


@pytest.mark.parametrize('reuse_context', [False, True], ids=['file', 'shared'])
def test_saved_views_keep_different_cuts_overlap_tail_and_unknown_observations(tmp_path, monkeypatch, reuse_context):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def time(seconds):
        return start + timedelta(seconds=seconds)
    scene = Scene()
    for name in ('a', 'b'):
        scene.create_ground_station(name, latitude_deg=0, longitude_deg=0)
    scene_file = tmp_path / 'scene.scene'
    scene.dump(scene_file)
    candidate = ConnectionCandidate(('a', 'b'), {'a': LinkCandidate('a', 'b'), 'b': LinkCandidate('b', 'a')})
    link = LinkConfiguration(candidate, LinkProperties(candidate.id, 1e6, .001))
    full = LinkPlan(start, time(10), operations=(
        LinkOperation(time(5), 'establish', link.id, link),
        LinkOperation(time(9), 'withdraw', link.id)))
    base_links = [full.slice(start, time(5)), full.slice(time(5), time(10))]
    execution_links = [full.slice(start, time(4)), full.slice(time(4), time(10))]
    rule = ForwardingRule(RouteDestination('node', 'b'), 'deliver')
    updated = ForwardingRule(rule.destination, 'deliver', data={'version': 2})
    last = ForwardingRule(RouteDestination('prefix', '192.0.2.0/24'), 'deliver')
    base_routes = [RoutePlan(start, time(5)), RoutePlan(time(5), time(10), operations=(
        RouteOperation(time(5), 'a', 'install', rule),))]
    delayed = RouteOperation(time(7), 'a', 'install', rule)
    delivery = [RoutePlan(start, time(8), operations=(delayed,)),
                RoutePlan(time(5), time(12), operations=(
                    RouteOperation(time(7), 'a', 'replace', updated),
                    RouteOperation(time(11), 'b', 'install', last)), context_operations=(delayed,))]
    histories = {'base_link': base_links, 'execution_link': execution_links,
                 'base_route': base_routes, 'delivery_route': delivery}
    paths = {name: tmp_path / f'{name}.plan' for name in histories}
    for name, plans in histories.items():
        save_plans(paths[name], plans)
    context = plan_views.save_context(
        tmp_path / 'context.json', PhysicalLayer(scene),
        link_plans=[*base_links, *execution_links], route_plans=[*base_routes, *delivery], physical_times=[start],
        position_times=[time(5).astimezone(timezone(timedelta(hours=8)))])
    sources = [scene_file, context, *paths.values()]
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    context_input = context
    if reuse_context:
        context_input = plan_views.load_context(context)
        original_context = json.dumps(context_input, sort_keys=True)
        read_text = Path.read_text

        def read_without_context(path, *args, **kwargs):
            if path == context:
                raise AssertionError('shared views must not reopen the context file')
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', read_without_context)

    def unexpected(*args, **kwargs):
        raise AssertionError('a saved-file view must not query a model')
    monkeypatch.setattr(PhysicalLayer, 'snapshot_at', unexpected)
    monkeypatch.setattr(PhysicalLayer, 'candidates', unexpected)
    plan_views.write_scene(scene_file, tmp_path / 'scene')
    plan_views.write_physical(context_input, tmp_path / 'physical')
    plan_views.write_link(scene_file, paths['base_link'], paths['execution_link'],
                          context=context_input, output=tmp_path / 'link')
    plan_views.write_network(scene_file, paths['base_route'], paths['delivery_route'], paths['base_link'],
                             context=context_input, output=tmp_path / 'network')
    link_data = json.loads((tmp_path / 'link/001/link.json').read_text())
    assert [frame['timestamp'] for frame in link_data['frames']] == [time(5).isoformat(), time(9).isoformat()]
    assert len(link_data['frames'][0]['base']) == len(link_data['frames'][0]['execution']) == 1
    assert link_data['frames'][-1]['base'] == link_data['frames'][-1]['execution'] == []
    assert all(frame['candidates'] is None for frame in link_data['frames'])
    route_data = json.loads((tmp_path / 'network/001/network.json').read_text())
    assert route_data['end'] == time(12).isoformat()
    frames = {frame['timestamp']: frame for frame in route_data['frames']}
    assert frames[time(7).isoformat()]['delivery']['a'][0]['data'] == {'version': 2}
    tail = frames[time(11).isoformat()]
    assert tail['base_held'] and tail['links_held']
    assert tail['delivery']['b'][0]['destination']['identity'] == '192.0.2.0/24'
    assert tail['assignments'] is None
    if reuse_context:
        assert json.dumps(context_input, sort_keys=True) == original_context
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
