"""Real Planner/Compiler/Traffic integration without Docker or invented paths."""
from contextlib import closing
import json
from pathlib import Path
import pytest
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from tests.fixtures.native_ospf import A, C, G, H, START
from tests.fixtures.native_zone_business import generate
from tools.generation.traffic.probe_plan import read_traffic_probe_plan


@pytest.mark.parametrize('daemon', ['frr', 'bird'])
def test_zone_business_edges_and_unknown_transit(tmp_path, daemon):
    output = generate(tmp_path / daemon, daemon)
    plan = read_traffic_probe_plan(output / 'results/traffic/probe-plan.json')
    assert len(plan.episodes) == 4
    from tools.generation.traffic.probe_plan import TrafficProbePlan
    forged = plan.artifact(); forged['forwarding_backend'] = 'bpf'
    with pytest.raises(ValueError, match='BPF Traffic requires planned reachability'):
        TrafficProbePlan.from_artifact(forged)
    assert {i.ground_station_id for i in plan.probe_identities} == {G.value, H.value}
    assert {i.path_stability for i in plan.probe_identities} == {'unknown'}
    assert {i.path_length_class for i in plan.probe_identities} == {'unknown'}
    assert {i.geographic_separation_class for i in plan.probe_identities} == {'near', 'far'}
    assert all(i.geographic_separation_m > 0 for i in plan.probe_identities)
    assert all(e.estimated_telemetry_events == 0 for e in plan.episodes)
    for episode in plan.episodes:
        assert any(s.expected_reachable is None for s in episode.state_intervals)
        assert any(s.expected_reachable is False for s in episode.state_intervals)
        assert all(not s.path_node_ids and s.path_length_hops is None for s in episode.state_intervals)
        assert {s.service_satellite_id for s in episode.state_intervals} == {A.value, C.value, None}
    reverse = {i.probe_id for i in plan.probe_identities if i.direction == 'zone-to-ground-station'}
    assert any('zone-source-migration' in s.boundary_reasons for e in plan.episodes
               if e.probe_id in reverse for s in e.state_intervals)
    with closing(_open_runner_artifact(output / 'results/compiler/runner-artifact/manifest.json')) as artifact:
        address, = artifact.dataplane['zone_addresses']
        actions = {int((b.nominal_time - START).total_seconds()): b.executable['request']['actions'] for b in artifact.boundaries()}
        changes = [(t, a['op'], a['key'], a['address']) for t, batch in actions.items()
                   for a in batch if a['op'] in ('address_add', 'address_delete')]
        assert (120, 'address_delete', C.value, address) in changes
        assert (120, 'address_add', A.value, address) in changes
        assert (240, 'address_delete', A.value, address) in changes
        assert (300, 'address_add', C.value, address) in changes
        # H's uplink is unchanged when the Zone moves C→A. G moves its access here.
        assert not any(a['op'] == 'route' and (a.get('current') or a.get('previous'))['key'] == H.value for a in actions[120])
        assert any(a['op'] == 'route' and a.get('previous', {}).get('key') == G.value for a in actions[90])
    page = (output / 'visualizations/traffic-generation/index.html').read_text()
    assert 'Transit paths: unknown' in page and 'telemetry: unavailable' in page
    assert 'Geographic separation' in page and '222.4 km (near)' in page and '333.6 km (far)' in page
    assert read_traffic_probe_plan(output / 'results/traffic/probe-plan.json').artifact() == plan.artifact()



def test_native_traffic_continues_across_a_handover_delivery_boundary(tmp_path):
    from datetime import timedelta
    from tools.generation.traffic.probe_plan import read_generated_plan
    output = generate(tmp_path / 'continued', 'bird', delivery_ends=(
        START + timedelta(seconds=115), START + timedelta(seconds=390)))
    first = read_traffic_probe_plan(output / 'results/traffic/probe-plan.json')
    second = read_traffic_probe_plan(output / 'results/traffic/probe-plan-001.json')
    assert first.coverage_end == second.coverage_start == START + timedelta(seconds=120)
    assert {i.probe_id for i in first.probe_identities} == {i.probe_id for i in second.probe_identities}
    assert all(e.state_intervals[-1].service_satellite_id == C.value for e in first.episodes)
    assert all(e.state_intervals[0].service_satellite_id == A.value for e in second.episodes)
    aggregate = read_generated_plan(output / 'results/traffic/probe-plan.json', published_batches=2)
    assert len(aggregate['episodes']) == 8
    assert aggregate['episodes'] == first.artifact()['episodes'] + second.artifact()['episodes']
    # Relative rank may change with a later delivery's coverage. Preview must
    # keep the earlier delivery's label rather than use the last identity row.
    from dataclasses import replace
    from tools.generation.traffic.probe_plan import write_traffic_probe_plan
    from tools.generation.traffic.preview import write_traffic_preview
    revised = replace(second, probe_identities=tuple(replace(i, geographic_separation_class='far')
        if i.ground_station_id == G.value else i for i in second.probe_identities))
    write_traffic_probe_plan(revised, output / 'results/traffic/probe-plan-001.json')
    write_traffic_preview(output, publication={'published_batches': 2})
    page = (output / 'visualizations/traffic-generation/index.html').read_text()
    assert page.count('222.4 km (near)') == page.count('222.4 km (far)') == 2


@pytest.mark.parametrize('version', [2, 3])
def test_previous_workload_formats_have_no_invented_geographic_labels(tmp_path, version):
    from tools.generation.traffic.probe_plan import TrafficProbePlan
    output = generate(tmp_path / 'legacy', 'bird', tools_enabled=False)
    artifact = read_traffic_probe_plan(output / 'results/traffic/probe-plan.json').artifact()
    artifact['format_version'] = version
    artifact['planning']['selection_strategy'] = 'bounded-native-edge-coverage'
    for identity in artifact['probe_identities']:
        identity.pop('geographic_separation_m')
        identity.pop('geographic_separation_class')
    restored = TrafficProbePlan.from_artifact(artifact)
    assert all(i.geographic_separation_m is None and i.geographic_separation_class == 'unavailable'
               for i in restored.probe_identities)


def test_traffic_enablement_and_selection_do_not_change_planning(tmp_path):
    from tools.observation.planner.reader import read_planner_history
    facts = []
    planned = []
    counts = []
    for name, enabled, maximum in (('off', False, 4), ('two', True, 2), ('four', True, 4)):
        output = generate(tmp_path / name, 'bird', tools_enabled=False,
                          traffic_enabled=enabled, maximum_probes=maximum)
        with closing(_open_runner_artifact(output / 'results/compiler/runner-artifact/manifest.json')) as artifact:
            facts.append([(b.nominal_time, b.executable['request']) for b in artifact.boundaries()])
        planned.append([e.plan for e in read_planner_history(output / 'results/planner/planning.jsonl.gz')])
        if enabled:
            counts.append(len(read_traffic_probe_plan(output / 'results/traffic/probe-plan.json').episodes))
        else:
            assert not (output / 'results/traffic/probe-plan.json').exists()
    assert facts[0] == facts[1] == facts[2]
    assert planned[0] == planned[1] == planned[2]
    assert counts == [2, 4]


def test_native_scan_reuses_static_distances_and_boundary_access_facts(tmp_path, monkeypatch):
    from collections import Counter
    from tools.generation.traffic import probe_plan as traffic
    edges, distances = Counter(), Counter()
    real_edges, real_distance = traffic._native_ground_edges, traffic._geographic_separation_m
    def counted_edges(projection):
        edges[projection.nominal_time] += 1
        return real_edges(projection)
    def counted_distance(first, second):
        distances[first, second] += 1
        return real_distance(first, second)
    monkeypatch.setattr(traffic, '_native_ground_edges', counted_edges)
    monkeypatch.setattr(traffic, '_geographic_separation_m', counted_distance)
    output = generate(tmp_path / 'reuse', 'bird', tools_enabled=False)
    plan = read_traffic_probe_plan(output / 'results/traffic/probe-plan.json')
    assert len(plan.episodes) == 4
    assert len(distances) == 2 and set(distances.values()) == {1}
    # One candidate pass, one selected-path reconstruction pass per boundary,
    # independent of selected direction/probe count. Withdrawals still survive.
    assert len(edges) == len(plan.nominal_boundaries)
    assert set(edges.values()) <= {1, 2}
    assert any(s.expected_reachable is False for e in plan.episodes for s in e.state_intervals)
