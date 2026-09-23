"""Ordinary GS business interfaces survive actual offline SEED compilation."""
from dataclasses import replace
from importlib import import_module
from ipaddress import IPv4Network

import pytest
import yaml
from skyseed import configuration, emulator, adapters
from skyseed.deployment.seed import _InterfaceLessRuntimeDocker
from skyseed.backend.linux.materials import _LINK_POOLS

EXAMPLES = ('Sky01_s5_custom_models.s5', 'Sky02_s4_default.s4', 'Sky03_s3_ospf.s3_ospf',
            'Sky04_single_shell_continuation.single_shell', 'Sky05_five_shells.five_shells',
            'Sky06_s4_native_ospf.s4_native_ospf')


@pytest.mark.parametrize('entry', (EXAMPLES[0], EXAMPLES[1], EXAMPLES[2], EXAMPLES[5]))
def test_file_driven_construction_accepts_generated_orbits(monkeypatch, entry):
    module = import_module('examples.' + entry)
    shells = configuration.emulator.load_shell_configs(module.SHELLS_FILE)
    selected_id = 'S5' if 'Sky01_' in entry else 'S3' if 'Sky03_' in entry else 'S4'
    original, = (s for s in shells if s.shell_id == selected_id)
    generated = replace(original, explicit_orbits=(),
        generated_orbit_layout=configuration.emulator.GeneratedOrbitLayout(
            orbit_count=2, satellites_per_orbit=3,
            in_orbit_phase_distribution=configuration.emulator.InOrbitPhaseDistribution(
                start_deg=10, orbit_phase_step_deg=7.5)))
    monkeypatch.setattr(configuration.emulator, 'load_shell_configs', lambda path: (generated,))
    network = module.build_emulator()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    shell, = system.shells
    assert shell.shell_id == selected_id
    assert [o.parameters.epoch_utc for o in shell.orbits] == [original.epoch_utc] * 2
    assert [o.parameters.right_ascension_deg for o in shell.orbits] == [0, 180]
    assert [s.mean_anomaly_deg for o in shell.orbits for s in o.satellites] == [10, 130, 250, 17.5, 137.5, 257.5]
    for satellite in system.satellites():
        assert satellite.payload == {}
        assert satellite.max_simultaneous_ground_station_accesses is None
        assert satellite.maximum_simultaneous_inter_satellite_links is None
    assert str(system.getNetwork('ground-lan').getPrefix()) == '203.0.0.0/16'


@pytest.mark.parametrize('entry', EXAMPLES)
def test_shared_ground_business_lan_render_and_deployment(tmp_path, monkeypatch, entry):
    module = import_module('examples.'+entry)
    load = configuration.emulator.load_shell_configs
    # Keep the actual loaded GS inventory; one satellite per Shell is enough for
    # this static SEED contract, with no orbit or network simulation.
    def small_shells(path):
        return tuple(replace(s,explicit_orbits=(replace(s.explicit_orbits[0],
            satellites=s.explicit_orbits[0].satellites[:1]),)) for s in load(path))
    monkeypatch.setattr(configuration.emulator,'load_shell_configs',small_shells)
    network = module.build_emulator()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    stations = sorted(system.ground_stations(),key=lambda s:s.ground_station_id)
    assert len(stations) == (10 if 'Sky01_' in entry else 355)
    before = adapters.create_simulator_static_input((system,))
    prefix = system.getNetwork('ground-lan').getPrefix()
    assert all(not prefix.overlaps(IPv4Network(p)) for p in (*_LINK_POOLS,'198.18.0.0/15','192.168.66.0/24','10.128.0.0/9'))
    network.render()
    assigned = []
    for index, station in enumerate(stations,start=2):
        interface, = [i for i in station.getInterfaces() if i.getNet().getName() == 'ground-lan']
        assert interface.getAddress() == prefix[index]
        assigned.append(str(interface.getAddress()))
    assert len(set(assigned)) == len(stations)
    assert str(prefix[1]) not in assigned  # Default Docker-managed bridge gateway.
    assert adapters.create_simulator_static_input((system,)) == before
    monkeypatch.chdir(tmp_path)
    network.compile(_InterfaceLessRuntimeDocker(internet_map_enabled=False),'deployment')
    compose = yaml.safe_load((tmp_path/'deployment/docker-compose.yml').read_text())
    net_name, = [name for name,value in compose['networks'].items()
                 if any(p.get('subnet') == str(prefix) for p in value['ipam']['config'])]
    deployed = [value['networks'][net_name]['ipv4_address'] for value in compose['services'].values()
                if net_name in value.get('networks',{})]
    assert sorted(deployed) == sorted(assigned)
    assert len(compose['networks']) == 1
