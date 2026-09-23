"""Native material behavior and fixed Traffic selection, without Docker."""
from contextlib import closing
import json
from pathlib import Path
import pytest
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from tests.fixtures.native_ospf import A, C, G, H, START
from tests.fixtures.native_zone_business import generate
from tools.generation.traffic.workload import read_workload

@pytest.mark.parametrize('daemon', ['frr', 'bird'])
def test_zone_business_edges_and_fixed_communications(tmp_path, daemon):
    output = generate(tmp_path / daemon, daemon)
    workload = read_workload(output / 'results/traffic/workload.json')
    assert len(workload.communications) == 4
    assert {c.ground_station_id for c in workload.communications} == {G.value, H.value}
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

    assert 'route' not in json.dumps(workload.artifact())
    assert (output / 'visualizations/traffic-generation/index.html').exists()


def test_one_workload_across_delivery_windows(tmp_path):
    from datetime import timedelta
    output = generate(tmp_path / 'continued', 'bird', delivery_ends=(START + timedelta(seconds=115), START + timedelta(seconds=390)))
    assert len(list((output / 'results/traffic').glob('*.json'))) == 1
    assert len(read_workload(output / 'results/traffic/workload.json').communications) == 4


def test_traffic_selection_does_not_change_planning(tmp_path):
    from tools.observation.planner.reader import read_planner_history
    facts, planned, counts = [], [], []
    for name, enabled, pairs in (('off', False, None), ('two', True, [(G.value, 'service-probe')]), ('four', True, None)):
        output = generate(tmp_path / name, 'bird', tools_enabled=False, traffic_enabled=enabled, pairs=pairs)
        with closing(_open_runner_artifact(output / 'results/compiler/runner-artifact/manifest.json')) as artifact:
            facts.append([(b.nominal_time, b.executable['request']) for b in artifact.boundaries()])
        planned.append([e.plan for e in read_planner_history(output / 'results/planner/planning.jsonl.gz')])
        if enabled:
            counts.append(len(read_workload(output / 'results/traffic/workload.json').communications))
        else:
            assert not (output / 'results/traffic/workload.json').exists()
    assert facts[0] == facts[1] == facts[2]
    assert planned[0] == planned[1] == planned[2]
    assert counts == [2, 4]
