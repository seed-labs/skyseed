"""Fixed injection ownership and actual C behavior, without namespace/kernel mutation."""
from contextlib import closing
import errno
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace

import pytest

from skyseed.backend.bpf.dataplane import DataPlane, DataPlaneBinding, AttachmentScope, BpfProgram
from skyseed.backend.bpf.dataplane import local_ingress as local, runtime_command as command
from skyseed.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.backend.linux import network
from tests.unit.test_runner_runtime_optimization import _Kernel
from tests.unit.test_satellite_route_targets import Snapshots, produce_routes
from tests.unit.test_bpf_logical_ports import native_program
from tests.unit.test_dataplane_structured import native_handler, program, produce, ROOT, DATA
from templates.dataplane.threshold.scene import A, B, G


class FoundationKernel(_Kernel):
    """Real initialization, activation and cleanup with fake sockets/libbpf FDs."""
    def __init__(self, monkeypatch, root):
        super().__init__(monkeypatch)
        command._RETIREMENT_POOL.shutdown()
        monkeypatch.setattr(command, '_RETIREMENT_POOL', None)
        monkeypatch.setattr(command, '_RETIREMENT_WORKERS', 0)
        self.pins, self.objects, self.closed = {}, [], []
        self.rules, self.routes, self.neighbours, self.addresses, self.peers = {}, {}, set(), {}, {}
        serial = iter(range(100000, 200000))
        def create(name, **kwargs):
            fd = next(serial)
            self.maps[fd] = {}
            return fd
        def pin(fd, path):
            self.pins[path] = fd
            path.touch()
        def load(*args, **kwargs):
            obj = SimpleNamespace(private=77, closed=False)
            def close(): obj.closed = True
            obj.close = close
            self.objects.append(obj)
            return obj, next(serial)
        def retain(nodes):
            for node in nodes:
                key = node['key']
                self.nodes[key] = {**node, 'namespace_fd': next(serial), 'maps': {}, 'indexes': {},
                                   'netlink': self.Channel(self, key)}
                self.links[key, 'lo'], self.links[key, 'ground0'] = 1, 2
                self.addresses[key, 2] = {'192.0.2.1/24'}
        for name, value in [('_INITIALIZATION_REQUESTED', False), ('_OWNED_PIN_ROOT', None),
                            ('_FOUNDATION_OUTCOME', 'no-mutation'), ('_OWNED_NODE_ADDRESSES', set()),
                            ('_GENERATION_FD', -1)]:
            monkeypatch.setattr(command, name, value)
        monkeypatch.setattr(command, '_pin_root', lambda _: root)
        monkeypatch.setattr(command, '_retain_namespaces', retain)
        monkeypatch.setattr(command, '_assert_netns', lambda _: None)
        monkeypatch.setattr(command, '_reserve_foundation_descriptors', lambda *a: None)
        monkeypatch.setattr(command, '_probe_batch_update_support', lambda: True)
        monkeypatch.setattr(command, '_create_map', create)
        monkeypatch.setattr(command, '_pin_object', pin)
        monkeypatch.setattr(command, '_open_pinned', self.pins.__getitem__)
        monkeypatch.setattr(command, '_load_object', load)
        monkeypatch.setattr(network, '_setns', lambda _: None)
        original_read = Path.read_text
        monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: '0' if str(p) == '/proc/sys/net/ipv4/conf/all/rp_filter' else original_read(p, *a, **kw))
        original_close = command.os.close
        monkeypatch.setattr(command.os, 'close', lambda fd: self.closed.append(fd) if isinstance(fd, int) and fd >= 100000 else original_close(fd))

    def execute(self, action, request):
        import json
        if isinstance(request, str):
            request = json.loads(request)['request']
        self.actions.append((action, request))
        return command._dispatch_action(action, request)

    class Channel(_Kernel.Channel):
        def initial_links(self, **kwargs):
            assert not any(n.startswith('sv2') for k, n in self.kernel.links if k == self.key)
            return 1
        def ipv4_address_facts(self):
            return ((IPv4Address('192.0.2.1'), 24),)
        def ipv4_route_inventory(self):
            return ((254, IPv4Network('192.0.2.0/24')),)
        def audit_local_ingress(self, table, priorities, routes):
            self._op('audit-local', table, priorities)
            assert not self.kernel.routes and not self.kernel.rules
        def create_dummy(self, name):
            self._op('dummy', name)
            self.next_index += 1
            self.kernel.links[self.key, name] = self.next_index
        def address(self, index, address, **kwargs):
            self._op('address', index, address, **kwargs)
            values = self.kernel.addresses.setdefault((self.key, index), set())
            if kwargs.get('delete'):
                values.remove(address)
            else:
                values.add(address)
        def create_veth(self, name, peer_name, peer_fd, **kwargs):
            kwargs.setdefault('group', 0)
            kwargs.setdefault('peer_group', 0)
            super().create_veth(name, peer_name, peer_fd, **kwargs)
            peer = next(n for n in self.kernel.nodes.values() if n['namespace_fd'] == peer_fd)
            self.kernel.peers[self.key, name] = peer['key'], peer_name
            self.kernel.peers[peer['key'], peer_name] = self.key, name
        def link_index(self, name):
            if (self.key, name) not in self.kernel.links:
                raise OSError(errno.ENODEV, name)
            return super().link_index(name)
        def _remove_pair(self, name):
            first = self.key, name
            for key in (first, self.kernel.peers.get(first)):
                if key is None:
                    continue
                index = self.kernel.links.pop(key)
                self.kernel.groups.pop(key, None)
                self.kernel.peers.pop(key, None)
                self.kernel.addresses.pop((key[0], index), None)
                self.kernel.neighbours = {v for v in self.kernel.neighbours if v[:2] != (key[0], index)}
        def local_ingress_ipv4(self, index):
            self._op('local-ipv4', index)
        def local_output_route(self, index, destination, **kwargs):
            self._op('fixed-route', index, destination, **kwargs)
            key = self.key, destination
            value = index, {k: v for k, v in kwargs.items() if k != 'delete'}
            if kwargs.get('delete'):
                if key not in self.kernel.routes:
                    raise OSError(errno.ESRCH, 'absent route')
                assert self.kernel.routes.pop(key) == value
            else:
                assert key not in self.kernel.routes
                self.kernel.routes[key] = value
        def local_output_rule(self, destination, **kwargs):
            self._op('fixed-rule', destination, **kwargs)
            key = self.key, destination
            value = {k: v for k, v in kwargs.items() if k != 'delete'}
            if kwargs.get('delete'):
                if key not in self.kernel.rules:
                    raise OSError(errno.ENOENT, 'absent rule')
                assert self.kernel.rules.pop(key) == value
            else:
                assert key not in self.kernel.rules
                self.kernel.rules[key] = value
        def neighbour(self, index, destination, mac, **kwargs):
            self._op('neighbour', index, destination, mac, **kwargs)
            self.kernel.neighbours.add((self.key, index, destination, mac))
        def close(self): pass

    def assert_clean(self):
        assert not self.nodes and not self.routes and not self.rules and not self.neighbours and not self.peers
        assert all(n in ('lo', 'ground0') for _, n in self.links)
        assert all(index == 2 and values == {'192.0.2.1/24'} for (_, index), values in self.addresses.items())
        assert all(obj.closed for obj in self.objects)


def runtime(monkeypatch, root, artifact, *, continued=False):
    kernel = FoundationKernel(monkeypatch, root)
    adapter = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins', continuation_enabled=continued,
        batch_capacity_factor=4, bindings={n: SimpleNamespace(pid=i+100, process_start_time_ticks=1, netns_inode=i+200)
                                         for i, n in enumerate(artifact.physical_world_projection.node_ids)})
    return kernel, adapter


def initialize(adapter, artifact):
    return adapter.initialize(artifact.physical_world_projection, zone_prefixes=artifact.zone_prefixes,
        dataplane=artifact.dataplane, runtime_capacity_demand=artifact.runtime_capacity_demand)


@pytest.mark.parametrize('legacy', ['contract', 'endpoint-instances', 'route-actions'])
def test_incompatible_bpf_material_requires_regeneration(tmp_path, legacy):
    from copy import deepcopy
    from skyseed.backend.bpf.dataplane.materials import _validate_foundation, _validate_executable
    paths, _ = produce(tmp_path / 'materials', selection=program(tmp_path), ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        keys = tuple(n['key'] for n in artifact.dataplane['nodes'])
        value = deepcopy(artifact.dataplane)
        if legacy == 'contract':
            value['contract'] = 'skyseed-tc-elf/2'
        elif legacy == 'endpoint-instances':
            for node in value['nodes']:
                node['instances'] = [{'program': node.pop('program'), 'interfaces': None}]
        else:
            executable = deepcopy(next(iter(artifact.boundaries())).executable)
            request = executable['request']
            request['operation_nodes'].append({'key': keys[0], 'qdisc': [], 'zone_address_delete': [],
                'zone_address_add': [], 'route_replace': [], 'route_withdraw': []})
            with pytest.raises(ValueError, match='node action schema'):
                _validate_executable(executable, set(keys))
            return
        with pytest.raises(ValueError, match='regenerate'):
            _validate_foundation(value, keys)


def test_fixed_foundation_survives_routes_and_detached_continuation(tmp_path, monkeypatch):
    sim, _, paths = produce_routes(tmp_path / 'materials', monkeypatch, Snapshots(), backend='bpf')
    sim.shutdown()
    adapter = None
    try:
        for batch, path in enumerate(paths):
            with closing(_open_runner_artifact(path)) as artifact:
                if adapter is None:
                    kernel, adapter = runtime(monkeypatch, tmp_path / 'pins', artifact, continued=True)
                    initialize(adapter, artifact)
                    assert len(kernel.rules) == len(kernel.routes) == len(kernel.neighbours) == 3
                    stable = dict(kernel.rules), dict(kernel.routes), set(kernel.neighbours)
                    fixed = {k: i for k, i in kernel.links.items() if k[1] in (local._LOCAL_INPUT, local._LOCAL_OUTPUT)}
                    assert len(fixed) == 6
                    for node in kernel.nodes.values():
                        local_index = kernel.links[node['key'], local._LOCAL_INPUT]
                        assert kernel.maps[node['maps']['ingress_bindings']][local_index] == local._LOCAL_PORT
                        assert local._LOCAL_PORT not in kernel.maps[node['maps']['egress_bindings']]
                kernel.operations.clear()
                plans = adapter.prepare_all(artifact.boundaries(), logical_ports=artifact.logical_ports,
                    retirements=artifact.retirements,
                    **({'runtime_capacity_demand': artifact.runtime_capacity_demand} if batch else {}))
                for plan in plans:
                    adapter.activate(plan)
                assert not any(e[0] in ('fixed-rule', 'fixed-route', 'route', 'neighbour') for e in kernel.operations)
                assert (kernel.rules, kernel.routes, kernel.neighbours) == stable
                assert all(kernel.links[k] == i for k, i in fixed.items())
                for e in kernel.operations:
                    if e[0] == 'attach':
                        assert e[2][1] == kernel.nodes[e[1]]['maps']['tc_ingress@0']
                assert all(obj.private == 77 and not obj.closed for obj in kernel.objects)
                while adapter.reclaim().get('work_units'):
                    pass
    finally:
        if adapter is not None:
            adapter.cleanup()
    kernel.assert_clean()


@pytest.mark.parametrize('coverage', ['empty', 'partial', 'all'])
def test_injection_only_on_covered_nodes_and_cleanup_allows_another_run(tmp_path, monkeypatch, coverage):
    selection = (DataPlane() if coverage == 'empty' else
        DataPlane((DataPlaneBinding(BpfProgram(ROOT / 'templates/dataplane/pass.bpf.c'), AttachmentScope(nodes=(A,))),))
        if coverage == 'partial' else program(tmp_path))
    paths, _ = produce(tmp_path / 'materials', selection=selection, ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        for _ in range(2):
            with monkeypatch.context() as patch:
                kernel, adapter = runtime(patch, tmp_path / 'pins', artifact)
                try:
                    result = initialize(adapter, artifact)
                    count = {'empty': 0, 'partial': 1, 'all': 3}[coverage]
                    assert len(kernel.rules) == len(kernel.routes) == len(kernel.neighbours) == count
                    assert len(kernel.objects) == count
                    assert result['capacity_audit']['pinned_object_count'] == len(kernel.pins)
                finally:
                    adapter.cleanup()
                kernel.assert_clean()


@pytest.mark.parametrize('failure', ['create', 'local-ipv4', 'attach', 'neighbour', 'fixed-route', 'fixed-rule'])
def test_partial_injection_initialization_retains_exact_cleanup(tmp_path, monkeypatch, failure):
    paths, _ = produce(tmp_path / 'materials', selection=DataPlane((DataPlaneBinding(
        BpfProgram(ROOT / 'templates/dataplane/pass.bpf.c'), AttachmentScope(nodes=(A,))),)), ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        kernel, adapter = runtime(monkeypatch, tmp_path / 'pins', artifact)
        kernel.fail = failure
        with pytest.raises(Exception, match=failure):
            initialize(adapter, artifact)
        kernel.fail = None
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize('failure', ['lost-ack', 'cleanup-retry', 'rp-filter'])
def test_uncertain_setup_and_cleanup_keep_ownership(tmp_path, monkeypatch, failure):
    paths, _ = produce(tmp_path / 'materials', selection=program(tmp_path), ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        kernel, adapter = runtime(monkeypatch, tmp_path / 'pins', artifact)
        if failure == 'lost-ack':
            original = kernel.Channel.local_output_rule
            def uncertain(channel, *args, **kwargs):
                original(channel, *args, **kwargs)
                if not kwargs.get('delete'):
                    raise OSError(errno.EIO, 'lost rule acknowledgement')
            monkeypatch.setattr(kernel.Channel, 'local_output_rule', uncertain)
            with pytest.raises(Exception, match='lost rule acknowledgement'):
                initialize(adapter, artifact)
        elif failure == 'rp-filter':
            original = Path.read_text
            monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: '1' if str(p) == '/proc/sys/net/ipv4/conf/all/rp_filter' else original(p, *a, **kw))
            with pytest.raises(Exception, match='rp_filter=0'):
                initialize(adapter, artifact)
            assert not kernel.objects
            assert not any(op[0] in ('dummy', 'create', 'fixed-rule', 'fixed-route') for op in kernel.operations)
        else:
            initialize(adapter, artifact)
            kernel.fail = 'fixed-rule'
            with pytest.raises(RuntimeError, match='cleanup left residue'):
                adapter.cleanup()
            assert kernel.rules and kernel.routes and kernel.nodes
            assert all('local_ingress' in n for n in kernel.nodes.values())
            assert all(not obj.closed for obj in kernel.objects)
            assert (tmp_path / 'pins').exists()
            kernel.fail = None
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize('collision', ['create_veth', 'local_output_route', 'local_output_rule'])
def test_exclusive_collision_never_deletes_foreign_resource(collision):
    calls = []
    class Channel:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                if name == collision and not kwargs.get('delete'):
                    raise OSError(errno.EEXIST, 'foreign resource')
                if name == 'link_index':
                    return 7 if args[0] == local._LOCAL_OUTPUT else 8
            return call
    node = {'netlink': Channel(), 'namespace_fd': 9, 'node_address': '100.64.0.1',
            'maps': {'ingress_bindings': 10}}
    with pytest.raises(OSError, match='foreign resource'):
        local._create_local_ingress(node, ('100.64.0.0/24',), 11, lambda *a: None)
    local._delete_local_ingress(node)
    assert not any(name == collision and kw.get('delete') for name, _, kw in calls)
    assert any(name == 'delete_link' for name, _, _ in calls) == (collision != 'create_veth')
    assert 'local_ingress' not in node


def test_zone_steering_is_fixed_for_many_zone_destinations(tmp_path, monkeypatch):
    paths, _ = produce(tmp_path / 'materials', selection=program(tmp_path), ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        kernel, adapter = runtime(monkeypatch, tmp_path / 'pins', artifact)
        zones = tuple(f'198.18.0.{i}/32' for i in range(1, 101))
        try:
            adapter.initialize(artifact.physical_world_projection, zone_prefixes=zones,
                dataplane=artifact.dataplane, runtime_capacity_demand=artifact.runtime_capacity_demand)
            assert len(kernel.rules) == len(kernel.routes) == 6
            assert len(kernel.neighbours) == 3
            assert {dest for _, dest in kernel.routes} == {artifact.dataplane['node_address_range'], '198.18.0.0/15'}
            for (node, _), (_, route) in kernel.routes.items():
                assert route['prefsrc'] == kernel.nodes[node]['node_address']
                assert route['gateway'] == local._LOCAL_GATEWAY
        finally:
            adapter.cleanup()
        kernel.assert_clean()


def test_dynamic_access_replacement_reuses_fixed_injection(tmp_path, monkeypatch):
    from tests.unit.test_linux_runtime import ChangingAccess, produce as produce_empty
    from templates.dataplane.threshold import scene
    monkeypatch.setattr(scene, 'SimpleGroundStationModel', ChangingAccess)
    sim, _, paths = produce_empty(tmp_path / 'materials', backend='bpf')
    sim.shutdown()
    adapter = None
    access_indices = []
    try:
        for batch, path in enumerate(paths):
            with closing(_open_runner_artifact(path)) as artifact:
                if adapter is None:
                    kernel, adapter = runtime(monkeypatch, tmp_path / 'pins', artifact, continued=True)
                    initialize(adapter, artifact)
                    fixed = {key: i for key, i in kernel.links.items() if key[1] in (local._LOCAL_INPUT, local._LOCAL_OUTPUT)}
                kernel.operations.clear()
                plans = adapter.prepare_all(artifact.boundaries(), logical_ports=artifact.logical_ports,
                    retirements=artifact.retirements,
                    **({'runtime_capacity_demand': artifact.runtime_capacity_demand} if batch else {}))
                for ready in plans:
                    adapter.activate(ready)
                    access_indices.append(tuple(sorted(kernel.nodes[G.value]['indexes'].values())))
                    while adapter.reclaim().get('work_units'):
                        pass
                assert all(kernel.links[key] == i for key, i in fixed.items())
                assert not any(e[0] in ('fixed-rule', 'fixed-route', 'route', 'neighbour') for e in kernel.operations)
        assert len(set(access_indices)) >= 3
    finally:
        if adapter is not None:
            adapter.cleanup()
    kernel.assert_clean()


@pytest.mark.parametrize('mode,action', [(0, 7), (1, 7), (11, 7), (12, 2), (14, 2), (15, 2),
                                      (16, 0), (20, 2), (21, 2), (22, 0), (23, 2)])
def test_actual_local_handler_dispositions_and_reader_release(native_program, mode, action):
    kind, run = native_program
    result = run(mode, local=True)
    assert result['action'] == (action if kind == 'default' else 0)
    assert result['readers'] == 0 and result['events'] == 1 and result['facts'] == 3


def test_actual_local_default_uses_source_plan_first_hop(tmp_path, monkeypatch, native_program):
    kind, run = native_program
    sim, _, paths = produce_routes(tmp_path / 'materials', monkeypatch, Snapshots(), backend='bpf', ends=(0,))
    sim.shutdown()
    with closing(_open_runner_artifact(paths[0])) as artifact:
        from skyseed.backend.bpf.dataplane.materials import _MaterialCursor
        draft = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane).apply(next(iter(artifact.boundaries())))
        target = next(n['node_address'] for n in artifact.dataplane['nodes'] if n['key'] == B.value)
        port = struct.unpack_from('<I', draft.generation_by_node[G].forwarding[0, target][1], 4)[0]
        connection, ports = next((c, p) for c, p in artifact.logical_ports.items() if {c.endpoint_a, c.endpoint_b} == {A, G})
        assert port == ports[0 if connection.endpoint_a == G else 1]
        result = run(local=True, route=port)
        if kind == 'default':
            assert result['action'] == 7 and result['exit'] == port
        else:
            assert result['action'] == 0  # A user handler remains free to PASS.


@pytest.mark.parametrize('length', [64, 200])
def test_actual_structured_local_candidates_share_program_state(tmp_path, native_handler, length):
    paths, _ = produce(tmp_path / 'materials', selection=program(tmp_path), ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        from skyseed.backend.bpf.dataplane.materials import _MaterialCursor
        draft = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane).apply(next(iter(artifact.boundaries())))
        encoded = next(iter(draft.generation_by_node[A].forwarding.values()))[1][4:]
        path = tmp_path / 'value.bin'
        path.write_bytes(encoded)
        port = struct.unpack_from('<I', encoded, 12 if length < 128 else 20)[0]
        output = subprocess.check_output([str(native_handler), str(path), str(length), '100', '2', '2'], text=True)
        assert tuple(map(int, output.split())) == (7, port + 100, 0, 2)
