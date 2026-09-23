"""Independent input entry points share aggregate parsing and frozen values."""
from pathlib import Path
import shutil

import pytest
from skyseed.configuration.emulator import (
    load_shell_configs, load_ground_station_configs, load_static_emulator_config,
    StaticEmulatorConfigLoadError,
)

DATA = Path(__file__).resolve().parents[2] / 'data/starlink'


def test_independent_and_scenario_relative_loading_are_identical(tmp_path, monkeypatch):
    target = tmp_path / 'data'
    shutil.copytree(DATA, target)
    monkeypatch.chdir(tmp_path)
    shells = load_shell_configs('data/shells.yaml')
    stations = load_ground_station_configs(target / 'ground_stations.yaml')
    system, = load_static_emulator_config('data/scenario.yaml').skyseed_autonomous_systems
    assert shells == system.shells and stations == system.ground_stations
    assert [(s.shell_id, sum(len(o.satellites) for o in s.explicit_orbits)) for s in shells] == [
        ('S1', 1584), ('S2', 1584), ('S3', 720), ('S4', 348), ('S5', 172)]
    assert len(stations) == 355
    assert all(s.epoch_utc.year == 2015 for s in shells)
    # File contents and phases stay frozen; current directory never resolves nested refs.
    assert load_ground_station_configs('data/ground_stations.yaml') == stations
    assert load_shell_configs(target / 'shells.yaml') == shells


@pytest.mark.parametrize('loader', [load_shell_configs, load_ground_station_configs])
@pytest.mark.parametrize('content', ['', 'schema_version: wrong', '[]', 'invalid: ['])
def test_independent_loader_errors_use_existing_family(tmp_path, loader, content):
    path = tmp_path / 'bad.yaml'
    path.write_text(content)
    with pytest.raises(StaticEmulatorConfigLoadError):
        loader(path)
    with pytest.raises(StaticEmulatorConfigLoadError):
        loader(tmp_path / 'absent.yaml')
