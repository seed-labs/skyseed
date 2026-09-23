"""Concrete native software preparation through real SEED node facilities."""
from copy import deepcopy

import pytest
from seedemu.core import BaseSystem
from seedemu.layers import Base, Routing
from skyseed import emulator
from tests.fixtures.native_ospf import build_small


def _satellite():
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    return system.satellites()[0]


def _payload(node):
    return (dict(f.get() for f in node.getFiles()), set(node.getSoftware()),
            list(node.getBuildCommands()), list(node.getStartCommands()), node.getBaseSystem())


@pytest.mark.parametrize('make_node', [_satellite, lambda: Base().createAutonomousSystem(65040).createRouter('r')])
@pytest.mark.parametrize('daemon', ['bird', 'frr'])
def test_concrete_configuration_preserves_authored_policy_and_seed_startup(make_node, daemon):
    node = make_node()
    node.addSoftware('curl')
    node.setFile('/etc/author-note', 'keep this file')
    node.appendStartCommand('echo author-start')
    previous = _payload(node)
    authored = ('router id 10.0.0.1;\nprotocol device {}\nprotocol kernel custom {\n'
                ' include "/etc/skyseed/bird-kernel-options.conf";\n netlink rx buffer 8388608;\n}\n'
                if daemon == 'bird' else
                {'zebra': 'hostname my-zebra\nzebra nexthop kernel enable\n',
                 'bgpd': 'router bgp 65040\n'})
    original = deepcopy(authored)
    emulator.configure_native_software(node, daemon=daemon, configuration=authored)
    files, software, build, start, base = _payload(node)
    assert authored == original
    assert files['/etc/author-note'] == 'keep this file'
    assert software >= previous[1]
    assert build[:len(previous[2])] == previous[2]
    assert start[:len(previous[3])] == previous[3]
    assert base == BaseSystem.SEEDEMU_ROUTER or base.contains(BaseSystem.SEEDEMU_ROUTER)
    if daemon == 'bird':
        assert files['/etc/bird/bird.conf'] == authored
        assert files['/etc/skyseed/bird-kernel-options.conf'] == 'netlink rx buffer 4194304;\n'
        assert 'bird2' in software
        assert build[-2:] == ['mkdir -p /usr/share/doc/bird2/examples/', 'touch /usr/share/doc/bird2/examples/bird.conf']
        assert start[-2:] == [('mkdir -p /run/bird', False), ('bird -f -c /etc/bird/bird.conf', True)]
    else:
        assert files['/etc/frr/zebra.conf'] == 'no zebra nexthop kernel enable\n' + authored['zebra']
        assert files['/etc/frr/bgpd.conf'] == authored['bgpd']
        assert 'frr' in software
        assert ('/usr/lib/frr/zebra -f /etc/frr/zebra.conf', True) in start
        assert start[-3] == ('mkdir -p /run/frr && chown frr:frr /run/frr', False)
        assert start[-2] == ('/usr/lib/frr/zebra -f /etc/frr/zebra.conf', True)
        assert start[-1] == ('while [ ! -S /run/frr/zserv.api ]; do sleep 0.1; done; exec /usr/lib/frr/bgpd -f /etc/frr/bgpd.conf', True)
        assert not any('ospfd' in command for command, _ in start)
    snapshot = _payload(node)
    with pytest.raises(ValueError, match='already prepared'):
        emulator.configure_native_software(node, daemon=daemon, configuration=authored)
    assert _payload(node) == snapshot


@pytest.mark.parametrize('daemon,configuration', [
    ('bird', ''), ('bird', {}), ('frr', {'ospfd': 'router ospf'}),
    ('frr', {'zebra': ''}), ('frr', {'zebra': 'hostname r', 'invalid': 'x'}),
    ('invalid', 'config'), ('bird', lambda *_: 'callback'), ('bird', ' \n'),
    ('frr', {'zebra': 'valid', '../../shell': 'bad'}), ('frr', {'zebra': None}),
    ('frr', [('zebra', 'valid')]),
])
def test_adapter_validation_rejects_invalid_input_before_node_mutation(daemon, configuration):
    node = _satellite()
    before = _payload(node)
    with pytest.raises(ValueError, match='expected bird text or frr daemon configurations including zebra'):
        emulator.configure_native_software(node, daemon=daemon, configuration=configuration)
    assert _payload(node) == before
    assert not getattr(node, '_skyseed_native_software', None)


def test_direct_node_facilities_remain_an_unadapted_alternative():
    node = _satellite()
    text = 'hostname direct\n'
    node.addSoftware('frr')
    node.setFile('/etc/frr/zebra.conf', text)
    node.appendStartCommand('/usr/lib/frr/zebra -f /etc/frr/zebra.conf', fork=True)
    assert dict(f.get() for f in node.getFiles())['/etc/frr/zebra.conf'] == text


@pytest.mark.parametrize('daemon,configuration', [('bird', 'protocol device {}'), ('frr', {'zebra': 'hostname r'})])
def test_adapter_needs_no_routing_convenience_methods_and_rejects_cross_daemon_duplicate(daemon, configuration):
    assert not hasattr(Routing, 'configureSoftware')
    assert not hasattr(Routing, 'validateSoftwareConfiguration')
    node = _satellite()
    emulator.configure_native_software(node, daemon=daemon, configuration=configuration)
    before = _payload(node)
    other = 'frr' if daemon == 'bird' else 'bird'
    with pytest.raises(ValueError, match='already prepared'):
        emulator.configure_native_software(node, daemon=other,
            configuration={'zebra': 'hostname r'} if other == 'frr' else 'protocol device {}')
    assert _payload(node) == before


def test_native_software_public_owner_replaces_experiment_wrapper():
    import importlib.util
    from skyseed import experiment
    from skyseed.emulator.native_software import configure_native_software
    assert emulator.configure_native_software is configure_native_software
    assert not hasattr(experiment, 'configure_native_routing')
    assert importlib.util.find_spec('skyseed.experiment.native_routing') is None
