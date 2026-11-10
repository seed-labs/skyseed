"""Static Scene geometry preserves input epochs and never invokes Physical."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from tools.observation.scene import inspection


def test_static_inputs_preserve_groups_epochs_and_scene(tmp_path):
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scene = inspection.demo_scene(epoch)
    scene.create_constellation('empty', data={'note': 'no satellites yet'})
    scene.create_ground_station('ungrouped', latitude_deg=0, longitude_deg=0)
    scene.dump(tmp_path / 'before.scene')
    inspection.write_inspection(inspection.inspection_data(scene), tmp_path / 'view')
    scene.dump(tmp_path / 'after.scene')
    saved = json.loads((tmp_path / 'view/scene.json').read_text())
    groups = {g['group_id']: g for g in saved['groups']}
    nodes = {n['node_id']: n for n in saved['nodes']}
    assert groups['empty']['data'] == {'note': 'no satellites yet'}
    assert groups['demo/shell-0/plane-0']['parent_id'] == 'demo/shell-0'
    assert groups['demo/shell-0']['epoch'] == epoch.isoformat()
    assert nodes['ungrouped']['group_id'] is None
    assert saved['geometry']['ground']['ungrouped'] == [6378137, 0, 0]
    assert len(saved['geometry']['orbits']) == 4  # One outline per shared orbit, not per satellite.
    assert (tmp_path / 'before.scene').read_bytes() == (tmp_path / 'after.scene').read_bytes()


def test_polar_orbit_outline_and_initial_phase_have_known_geometry():
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scene = Scene(); scene.create_constellation('c')
    scene.create_shell('polar', constellation='c', altitude_km=500, inclination_deg=90, epoch=epoch)
    scene.create_orbit('o', shell='polar', raan_deg=90)
    scene.create_satellite('north', orbit='o', mean_anomaly_deg=90)
    geometry = inspection.inspection_data(scene)['geometry']
    radius = 6378137 + 500000
    assert geometry['satellites']['north'] == pytest.approx((0, 0, radius), abs=1e-8)
    line = geometry['orbits'][0]['points_m']
    assert line[0] == pytest.approx(line[-1], abs=1e-8)
    assert all(abs(x) < 1e-8 for x, y, z in line)
    assert all(y*y + z*z == pytest.approx(radius*radius) for x, y, z in line)
    assert max(z for x, y, z in line) == pytest.approx(radius)
    assert min(z for x, y, z in line) == pytest.approx(-radius)


def test_cli_inspects_circular_and_custom_inputs_without_importing_pc(tmp_path):
    scene = inspection.demo_scene(datetime(2026, 1, 1, tzinfo=timezone.utc))
    scene.create_satellite('external', position=ScenePositionSource(
        'recorded-track', {'file': 'not-opened.csv', 'epoch': '2024-01-01T00:00:00Z'}))
    scene.dump(tmp_path / 'custom.scene')
    program = '''
import runpy, sys
sys.argv = ['inspection', '--scene', sys.argv[1], '--output', sys.argv[2]]
runpy.run_module('tools.observation.scene.inspection', run_name='__main__')
assert not any(name == 'skyseed.pc' or name.startswith('skyseed.pc.') for name in sys.modules)
'''
    subprocess.run([sys.executable, '-c', program, str(tmp_path / 'custom.scene'), str(tmp_path / 'view')],
                   cwd=Path(__file__).resolve().parents[3], check=True, capture_output=True, text=True)
    saved = json.loads((tmp_path / 'view/scene.json').read_text())
    external = next(n for n in saved['nodes'] if n['node_id'] == 'external')
    assert external['position_source']['data']['file'] == 'not-opened.csv'
    assert external['position_source']['data']['epoch'] == '2024-01-01T00:00:00Z'
    assert 'external' not in saved['geometry']['satellites']
    assert not any(o['node_id'] == 'external' for o in saved['geometry']['orbits'])
    assert len(saved['geometry']['satellites']) == 64


def test_independent_circular_source_keeps_its_own_epoch_and_outline():
    scene = Scene()
    scene.create_satellite('solo', position=ScenePositionSource('circular-orbit/v1', {
        'altitude_m': 500000, 'inclination_deg': 0, 'raan_deg': 0,
        'mean_anomaly_deg': 180, 'epoch': '2025-02-01T00:00:00+00:00'}))
    data = inspection.inspection_data(scene)
    assert data['geometry']['satellites']['solo'] == pytest.approx((-6878137, 0, 0), abs=1e-8)
    assert data['geometry']['orbits'][0]['node_id'] == 'solo'
    assert data['geometry']['orbits'][0]['orbit_id'] is None
    assert data['nodes'][0]['position_source']['data']['epoch'] == '2025-02-01T00:00:00+00:00'
