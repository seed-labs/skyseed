"""Small real Planner/compiler scenes: local edges, identities, withdrawal/recreation."""
from contextlib import closing
from pathlib import Path
import pytest
from skyseed import adapters, emulator, simulator
from skyseed.experiment import ExperimentController
from skyseed.backend.linux.environment import NativeRoutingEnvironment
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from tests.fixtures.native_ospf import build_small, create_small_simulator, A, B, C, G, H, START
from tests.fixtures.native_satellite_edges import EdgeRoutes
from tests.fixtures.native_config import FixtureConfigRenderer


@pytest.mark.parametrize('daemon', ['frr', 'bird'])
def test_native_ospf_edges_do_not_precompute_transit(tmp_path, daemon):
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    static = adapters.create_simulator_static_input((system,))
    environment = NativeRoutingEnvironment(n.node_id for n in static.list_nodes())
    from examples.Sky06_s4_native_ospf.config_helpers import BirdConfigRenderer
    policy = BirdConfigRenderer() if daemon == 'bird' else FixtureConfigRenderer(daemon)
    for satellite in static.list_satellites():
        emulator.configure_native_software(system.get_satellite(satellite.satellite_id),
            daemon=daemon, configuration=policy(satellite.node_id, environment))
    addresses = {n.value: a for n, a in environment.node_addresses.items()}
    sim = create_small_simulator(static, EdgeRoutes((A, C)))
    ExperimentController(network, sim, tmp_path / daemon, entry_path=Path(__file__),
        forwarding_backend='linux', traffic_enabled=False, internet_map_enabled=False).generate()
    with closing(_open_runner_artifact(tmp_path / daemon / 'results/compiler/runner-artifact/manifest.json')) as artifact:
        assert {n['key']: n['node_address'] for n in artifact.dataplane['nodes']} == addresses
        boundaries = list(artifact.boundaries())
        actions = {b.nominal_time.second + b.nominal_time.minute * 60: b.executable['request']['actions'] for b in boundaries}
        routes = [a for batch in actions.values() for a in batch if a['op'] == 'route']
        assert routes  # Exercise the current native action contract, never an empty filter.
        assert {a['current']['key'] for a in routes if a['current']} == {A.value, B.value, C.value, G.value, H.value}
        # Only GS destinations at satellites, only explicit satellite destinations at GSs.
        for action in routes:
            route = action['current'] or action['previous']
            if route['key'] in (A.value, B.value, C.value):
                assert route['destination'] in (addresses[G.value], addresses[H.value])
            else:
                assert route['destination'] in (addresses[A.value], addresses[C.value])
        assert any(a['op'] == 'remove' for a in actions[60])
        assert any(a['op'] == 'create' for a in actions[90])
        removed = next(a['connection'] for a in actions[150] if a['op'] == 'remove')
        created = next(a['connection'] for a in actions[180] if a['op'] == 'create')
        assert removed['connection'] == created['connection']
        assert removed['address_a'] == created['address_a']
        connection = simulator.OperationalConnectionId(simulator.OperationalConnectionKind.INTER_SATELLITE, B, C)
        assert created['interface_a'] == environment.interface_name(connection, B)
        assert created['interface_b'] == environment.interface_name(connection, C)


@pytest.mark.parametrize('daemon', ['frr', 'bird'])
def test_custom_configuration_and_selected_nodes(daemon):
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    selected = system.satellites()[0]
    configuration = ('protocol device {}' if daemon == 'bird' else
                     {'zebra': 'hostname my-zebra', 'bgpd': 'router bgp 65040'})
    emulator.configure_native_software(selected, daemon=daemon, configuration=configuration)
    for node in (*system.satellites()[1:], *system.ground_stations()):
        assert not getattr(node, '_skyseed_native_software', None)
    files = dict(f.get() for f in selected.getFiles())
    assert files['/etc/bird/bird.conf' if daemon == 'bird' else '/etc/frr/bgpd.conf'] == (
        'protocol device {}' if daemon == 'bird' else 'router bgp 65040')


def test_map_description_exposes_existing_native_identity_without_interfaces(tmp_path):
    from examples.Sky06_s4_native_ospf.config_helpers import describe_node_identities
    from skyseed.deployment import Deployer
    from yaml import safe_load
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    static = adapters.create_simulator_static_input((system,))
    environment = NativeRoutingEnvironment(n.node_id for n in static.list_nodes())
    original = system.satellites()[0]
    original.setDescription('Authored description')
    nodes = (*system.satellites(), *system.ground_stations())
    interfaces = [tuple(n.getInterfaces()) for n in nodes]
    describe_node_identities(system, static, environment)
    assert original.getDescription().startswith('Authored description\n')
    for identity, node in [(s.node_id, system.get_satellite(s.satellite_id)) for s in static.list_satellites()] + [
            (s.node_id, system.get_ground_station(s.ground_station_id)) for s in static.list_ground_stations()]:
        assert environment.node_addresses[identity] + '/32' in node.getDescription()
        assert 'Zone service addresses are separate' in node.getDescription()
    assert [tuple(n.getInterfaces()) for n in nodes] == interfaces
    Deployer(tmp_path).prepare(network, internet_map_enabled=False)
    compose = safe_load((tmp_path / 'deployment/docker-compose.yml').read_text())
    descriptions = [s.get('labels', {}).get('org.seedsecuritylabs.seedemu.meta.description', '')
                    for s in compose['services'].values()]
    for address in environment.node_addresses.values():
        assert sum(f'Stable node identity IPv4: {address}/32' in d for d in descriptions) == 1


def test_service_addresses_are_added_withdrawn_without_zone_route_policy(tmp_path):
    from tests.fixtures.generate_native_ospf import generate
    generate(tmp_path / 'service', 'bird')
    with closing(_open_runner_artifact(tmp_path / 'service/results/compiler/runner-artifact/manifest.json')) as artifact:
        assert len(artifact.dataplane['zone_addresses']) == 1
        address = artifact.dataplane['zone_addresses'][0]
        operations = [(int((b.nominal_time - START).total_seconds()), a['op'], a['key'], a['address'])
            for b in artifact.boundaries() for a in b.executable['request']['actions']
            if a['op'] in ('address_add', 'address_delete')]
        assert operations == [(30, 'address_add', C.value, address),
                              (150, 'address_delete', C.value, address),
                              (300, 'address_add', C.value, address)]


@pytest.mark.parametrize('daemon', ['frr', 'bird'])
def test_configuration_scans_shared_station_inventory_once(daemon):
    from types import SimpleNamespace
    from skyseed.backend.linux.environment import NativeRoutingEnvironment
    actual = NativeRoutingEnvironment((A, B, C, G, H))
    class CountedAddresses(dict):
        scans = 0
        def __iter__(self):
            self.scans += 1
            return super().__iter__()
    addresses = CountedAddresses(actual.node_addresses)
    env = SimpleNamespace(node_addresses=addresses, interface_name=actual.interface_name,
        identity_interface=actual.identity_interface, route_protocol=actual.route_protocol,
        link_address_pools=actual.link_address_pools)
    from examples.Sky06_s4_native_ospf.config_helpers import BirdConfigRenderer
    renderer = BirdConfigRenderer() if daemon == 'bird' else FixtureConfigRenderer(daemon)
    rendered = [renderer(node, env) for node in (A, B, C)]
    assert addresses.scans == 1
    for node, config in zip((A, B, C), rendered):
        text = config if daemon == 'bird' else config['ospfd']
        assert addresses[node] in text
        if daemon == 'bird':
            assert all(addresses[gs] + '/32' in text for gs in (G, H))
        else:
            from ipaddress import ip_network
            admitted = set()
            for line in text.splitlines():
                if not line.startswith('ip prefix-list GS-IDENTITIES '):
                    continue
                fields = line.split()
                prefix = ip_network(fields[6])
                assert fields[7:] == (['ge', '32'] if prefix.prefixlen < 32 else [])
                admitted.update(str(address) for address in prefix)
            assert admitted == {addresses[gs] for gs in (G, H)}
        assert '${' not in text


def test_formal_native_scene_uses_existing_s4_and_default_zones():
    from examples.Sky06_s4_native_ospf import s4_native_ospf as entry
    network = entry.build_emulator()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    shell, = system.shells
    assert shell.shell_id == 'S4'
    assert len(shell.orbits) == 6
    assert [len(orbit.satellites) for orbit in shell.orbits] == [58] * 6
    static = adapters.create_simulator_static_input((system,))
    assert len(static.list_satellites()) == 348
    assert {s.shell_id for s in static.list_satellites()} == {'S4'}
    assert len(static.list_ground_stations()) == 355
    assert str(system.getNetwork('ground-lan').getPrefix()) == '203.0.0.0/16'
    definition = simulator.SimulationDefinition(static_input=static,
        timeline=simulator.SimulationTimeline(START, START + entry.DEFAULT_DURATION, entry.CADENCE))
    simulation = simulator.create_default_simulator(definition,
        route_model=entry.EdgeRoutes())
    assert len(simulation.get_physical_world().get_zone_layout().zones) == 732
    simulation.shutdown()


def test_frr_compact_prefix_policy_admits_exactly_the_full_gs_identity_set():
    from ipaddress import ip_network
    from skyseed.shared import NodeId
    from skyseed.backend.linux.environment import NativeRoutingEnvironment
    stations = tuple(NodeId(65040, 'ground-station', f'g{i:03}') for i in range(355))
    environment = NativeRoutingEnvironment((*stations, A))
    text = FixtureConfigRenderer('frr')(A, environment)['ospfd']
    rules = [line.split() for line in text.splitlines()
             if line.startswith('ip prefix-list GS-IDENTITIES ')]
    assert len(rules) < len(stations)
    admitted = set()
    for fields in rules:
        prefix = ip_network(fields[6])
        assert fields[7:] == (['ge', '32'] if prefix.prefixlen < 32 else [])
        admitted.update(str(address) for address in prefix)
    assert admitted == {environment.node_addresses[station] for station in stations}
    assert environment.node_addresses[A] not in admitted
