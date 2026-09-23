"""Offline native material/lifecycle checks with real small Plans and fake netlink."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import errno
from ipaddress import IPv4Interface, IPv4Network
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from skyseed.backend import ForwardingBackend, compile_plan
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter
from skyseed.backend.linux import network
from skyseed.backend.linux.runtime import _LinuxRuntime
from skyseed.backend.linux.runtime_command import _LinuxKernel
from skyseed.backend.runner import lifecycle
from skyseed.experiment import Producer, ExperimentController
from skyseed.simulator import RoutePlanningResult, ParameterPlanningResult, OperationalConnection, EffectiveConnectionParameters
from templates.dataplane.threshold import scene
from templates.dataplane.threshold.scene import A, B, G, START


class EmptyRoutes:
    def plan_route_outcomes(self, *args, **kwargs):
        return RoutePlanningResult()


class ChangingAccess:
    def select_ground_station_accesses(self, world, candidates, state):
        offset = int((state.timestamp - START).total_seconds())
        satellite = A if offset < 10 or offset >= 20 else B
        return tuple(c for c in candidates if c.satellite_id == satellite)


class ChangingParameters:
    def plan_effective_parameters(self, timestamp, observations, previous_memory):
        offset = int((timestamp - START).total_seconds())
        return ParameterPlanningResult(tuple(OperationalConnection(v.connection_id,
            EffectiveConnectionParameters(1_000_000, 100 + offset, 0),
            EffectiveConnectionParameters(2_000_000, 200 + offset, 10)) for v in observations))


def produce(root, *, ends=(5, 15, 20), backend="linux", model=None):
    simulator = scene.create_scene(model or EmptyRoutes(), duration=ends[-1])
    paths, plans, previous = [], [], None
    with Producer(simulator.get_timeline(), simulator.get_physical_world(), simulator.get_physical_link_model(),
                  simulator.get_planner(), forwarding_backend=backend) as producer:
        for index, end in enumerate(ends):
            writer = _RunnerArtifactWriter(root / f"batch-{index}", continuation=previous,
                forwarding_backend=backend, final=index == len(ends)-1)
            try:
                while (value := producer.produce_next_realization(delivery_end=START + timedelta(seconds=end))) is not None:
                    plans.append(producer.latest_plan)
                    writer.append(value)
                paths.append(writer.finalize())
            finally:
                writer.close()
            previous = writer
    return simulator, plans, paths


@pytest.fixture
def materials(tmp_path, monkeypatch):
    monkeypatch.setattr(scene, "SimpleGroundStationModel", ChangingAccess)
    monkeypatch.setattr(scene, "FixedParameterModel", ChangingParameters)
    return produce(tmp_path / "materials")


class Kernel:
    """The actual native adapter and ownership code execute against these sockets."""
    def __init__(self, monkeypatch, nodes):
        self.kernel = _LinuxKernel()
        self.events = []
        self.fail = None
        self.static_network = "192.0.2.0/24"
        self.links = {key: {"lo": 1, "dummy0": 2, "ground0": 3} for key in nodes}
        self.addresses = {key: {2: {"192.0.2.1/32"}, 3: {"192.0.2.2/24"}} for key in nodes}
        self.next_index = 10
        self.peers = {}
        self.bindings = {node: SimpleNamespace(node_id=node, pid=100+i, process_start_time_ticks=1, netns_inode=i,
            image_id="test", service_name=node.value, container_id=node.value, container_name=node.value, started_at="now")
            for i, node in enumerate(nodes.values())}
        self.by_fd = {}
        self.calls = []
        def retain(records, live):
            for record in records:
                fd = os.open("/dev/null", os.O_RDONLY)
                self.by_fd[fd] = record["key"]
                live[record["key"]] = {**record, "namespace_fd": fd, "indexes": {}, "maps": {},
                                       "netlink": self.Channel(self, record["key"])}
        monkeypatch.setattr(network, "_retain_namespaces", retain)
        monkeypatch.setattr(network, "_setns", lambda fd: None)
        original_read = Path.read_text
        monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: "0" if str(path) == "/proc/sys/net/ipv4/conf/all/rp_filter" else original_read(path, *a, **kw))

    def execute(self, action, request):
        self.calls.append(action)
        if isinstance(request, str):
            request = json.loads(request)["request"]
        return getattr(self.kernel, action)(request)

    def event(self, op, *args):
        self.events.append((op, *args))
        if self.fail == op:
            self.fail = None
            raise OSError(errno.EIO, "injected " + op)

    class Channel:
        def __init__(self, owner, key):
            self.owner, self.key = owner, key
        def initial_links(self, **kwargs):
            if any(n.startswith("sv2") for n in self.owner.links[self.key]):
                raise RuntimeError("Runner interface already exists")
            return 1
        def ipv4_address_facts(self):
            self.owner.event("inventory", self.key)
            return tuple((IPv4Interface(a).ip, IPv4Interface(a).network.prefixlen)
                         for values in self.owner.addresses[self.key].values() for a in values)
        def ipv4_route_networks(self):
            return (IPv4Network(self.owner.static_network),)
        def create_dummy(self, name):
            self.owner.event("dummy", self.key)
            if name in self.owner.links[self.key]:
                raise OSError(errno.EEXIST, name)
            self.owner.next_index += 1
            self.owner.links[self.key][name] = self.owner.next_index
        def link_index(self, name):
            if name not in self.owner.links[self.key]:
                raise OSError(errno.ENODEV, name)
            return self.owner.links[self.key][name]
        def create_veth(self, name, peer, fd, *, mac, group, peer_group):
            assert mac is None and group == peer_group == 0
            self.owner.event("veth", self.key, name)
            peer_key = self.owner.by_fd[fd]
            for key, interface in ((self.key, name), (peer_key, peer)):
                if interface in self.owner.links[key]:
                    raise OSError(errno.EEXIST, interface)
            for key, interface in ((self.key, name), (peer_key, peer)):
                self.owner.next_index += 1
                self.owner.links[key][interface] = self.owner.next_index
            self.owner.peers[self.key, name] = peer_key, peer
            self.owner.peers[peer_key, peer] = self.key, name
        def delete_link(self, index):
            self.owner.event("delete", self.key, index)
            names = [n for n, i in self.owner.links[self.key].items() if i == index]
            if not names:
                raise OSError(errno.ENODEV, str(index))
            name = names[0]
            assert name.startswith("sv2")
            for key, interface in ((self.key, name), self.owner.peers.get((self.key, name), (None, None))):
                if key is not None:
                    i = self.owner.links[key].pop(interface)
                    self.owner.addresses[key].pop(i, None)
                    self.owner.peers.pop((key, interface), None)
        def address(self, index, address, *, delete=False, exclusive=False):
            assert exclusive
            self.owner.event("address", self.key, index, address, delete)
            address = str(IPv4Interface(address))
            owned = self.owner.addresses[self.key].setdefault(index, set())
            if delete:
                owned.remove(address)
            else:
                assert address not in owned
                owned.add(address)
        def native_ipv4(self, index):
            self.owner.event("forwarding", self.key, index)
        def netem(self, index, parameters):
            self.owner.event("qdisc", self.key, index, parameters)
        def set_link(self, index, *, up):
            self.owner.event("up", self.key, index)
        def close(self):
            pass

    def assert_clean(self):
        assert not self.kernel.nodes and not self.kernel.connections
        assert all(v == {"lo": 1, "dummy0": 2, "ground0": 3} for v in self.links.values())
        assert all(v == {2: {"192.0.2.1/32"}, 3: {"192.0.2.2/24"}} for v in self.addresses.values())


def runtime(monkeypatch, artifact):
    kernel = Kernel(monkeypatch, {n.value: n for n in artifact.physical_world_projection.node_ids})
    adapter = _LinuxRuntime(executor=kernel, bindings=kernel.bindings)
    return kernel, adapter


def initialize(adapter, artifact):
    return adapter.initialize(artifact.physical_world_projection, zone_prefixes=artifact.zone_prefixes,
        runtime_capacity_demand=artifact.runtime_capacity_demand, dataplane=artifact.dataplane)


def test_native_continuation_reuses_addresses_and_links_without_future_mutations(materials, monkeypatch):
    _, semantic_plans, paths = materials
    assert all(not p.transitions[0].route_outcome_transitions for p in semantic_plans)
    with closing(_open_runner_artifact(paths[0])) as first:
        kernel, adapter = runtime(monkeypatch, first)
        initialize(adapter, first)
        prepared = adapter.prepare_all(first.boundaries())
        assert not kernel.kernel.connections
        adapter.activate(prepared[0])
        original = {key: dict(links) for key, links in kernel.links.items()}
        adapter.activate(prepared[1])
        assert kernel.links == original  # parameter-only changes reuse actual interfaces
        first_addresses = dict(adapter._allocations)
        for path in paths[1:]:
            with closing(_open_runner_artifact(path)) as following:
                before = list(kernel.events)
                prepared = adapter.prepare_all(following.boundaries())
                assert kernel.events == before  # readiness exposes no future interface/address
                for plan in prepared:
                    result = adapter.activate(plan)
                    assert result["target_transition_complete"] and not result["generation_publication_occurred"]
        assert adapter._active_ordinal == 4
        assert all(adapter._allocations[key] == value for key, value in first_addresses.items())
        assert len([e for e in kernel.events if e[0] == "inventory"]) == 3
        assert len([e for e in kernel.events if e[0] == "veth"]) == 4  # retained ISL, access A/B/A
        adapter.cleanup()
        kernel.assert_clean()
        # New invocation uses the same materials after complete cleanup.
        second = _LinuxRuntime(executor=kernel, bindings=kernel.bindings)
        initialize(second, first)
        second.activate(second.prepare_all(first.boundaries())[0])
        second.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize("phase", ["dummy", "address", "qdisc", "delete"])
def test_native_partial_failures_preserve_cleanup_ownership(materials, monkeypatch, phase):
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        if phase in {"dummy", "address"}:
            kernel.fail = phase
            with pytest.raises(OSError, match="injected"):
                initialize(adapter, artifact)
        else:
            initialize(adapter, artifact)
            plans = adapter.prepare_all(artifact.boundaries())
            if phase == "qdisc":
                kernel.fail = phase
                with pytest.raises(OSError) as failure:
                    adapter.activate(plans[0])
                assert failure.value.evidence["live_state_may_be_partial"]
                assert adapter._active_ordinal == -1
            else:
                adapter.activate(plans[0])
                kernel.fail = phase
                with pytest.raises(RuntimeError, match="owned resources"):
                    adapter.cleanup()
                assert adapter._cleanup_required
        adapter.cleanup()
        kernel.assert_clean()


def test_native_rejects_collisions_without_touching_seed_resources(materials, monkeypatch):
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        kernel.static_network = "172.16.0.0/16"
        initialize(adapter, artifact)
        with pytest.raises(ValueError, match="collides"):
            adapter.prepare_all(artifact.boundaries())
        assert not kernel.kernel.connections
        adapter.cleanup()
        kernel.assert_clean()
        kernel.links[A.value]["sv2identity"] = 999
        adapter = _LinuxRuntime(executor=kernel, bindings=kernel.bindings)
        with pytest.raises(RuntimeError, match="already exists"):
            initialize(adapter, artifact)
        adapter.cleanup()
        assert kernel.links[A.value]["sv2identity"] == 999


def test_selection_defaults_native_assembly_and_incompatible_inputs(tmp_path, monkeypatch):
    from examples.Sky00_tiny_network import tiny_network
    from skyseed.backend.selection import _emulator_backend
    bpf = tiny_network.build_emulator()
    assert _emulator_backend(bpf) is ForwardingBackend.BPF
    native = tiny_network.build_emulator()
    simulator = scene.create_scene(EmptyRoutes(), duration=0)
    controller = ExperimentController(native, simulator, tmp_path, entry_path=Path(__file__), forwarding_backend="linux")
    assert _emulator_backend(native) is ForwardingBackend.LINUX
    assert controller._forwarding_backend is ForwardingBackend.LINUX
    assert not controller._traffic_enabled
    with pytest.raises(ValueError, match="incompatible"):
        ExperimentController(native, simulator, tmp_path, entry_path=Path(__file__), dataplane_program=object())
    sending = ExperimentController(native, simulator, tmp_path, entry_path=Path(__file__), traffic_enabled=True)
    assert sending._traffic_enabled and not sending._traffic_telemetry_enabled
    with pytest.raises(ValueError, match="telemetry"):
        ExperimentController(native, simulator, tmp_path, entry_path=Path(__file__), traffic_telemetry_enabled=True)
    with pytest.raises(ValueError, match="forwarding_backend"):
        ExperimentController(native, simulator, tmp_path, entry_path=Path(__file__), forwarding_backend="other")


def test_unsupported_policy_fails_offline_and_default_model_meaning_is_unchanged(tmp_path):
    with pytest.raises(ValueError, match="unsupported native route lowering"):
        produce(tmp_path / "nonempty", ends=(0,), model=scene.ThresholdRoutes())
    simulator = scene.create_scene(EmptyRoutes(), duration=0)
    with Producer(simulator.get_timeline(), simulator.get_physical_world(), simulator.get_physical_link_model(), simulator.get_planner()) as producer:
        result = producer.produce_next_realization()
        assert result.forwarding_backend == "bpf"
        native = compile_plan(producer.latest_plan, simulator.get_physical_world(), None, forwarding_backend="linux")
        writer = _RunnerArtifactWriter(tmp_path / "wrong")
        with pytest.raises(ValueError, match="different forwarding"):
            writer.append(native)
        writer.close()
        with pytest.raises(ValueError, match="different forwarding"):
            compile_plan(producer.latest_plan, simulator.get_physical_world(), native)


def test_detached_native_reader_and_runner_import_without_bpf(materials):
    script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, *args):
  if fullname.startswith(('skyseed.backend.bpf', 'skyseed.backend.compiler', 'skyseed.emulator', 'templates', 'examples')):
   raise AssertionError('detached import: ' + fullname)
sys.meta_path.insert(0, Block())
from pathlib import Path
from skyseed.backend import Runner
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.backend.linux.runtime import _LinuxRuntime
from skyseed.backend.runner.runtime_command import main
with __import__('contextlib').closing(_open_runner_artifact(Path(sys.argv[1]))) as artifact:
 assert artifact.forwarding_backend == 'linux'
 assert len(artifact.boundaries()) == 2
Runner(compose_project_name='offline', runtime_artifact=Path(sys.argv[1]), forwarding_backend='linux')
sys.argv = ['runtime_command', 'serve-linux']
sys.stdin = __import__('io').StringIO('{"action":"cleanup","request":{}}' + chr(10))
assert main() == 0
'''
    result = subprocess.run([sys.executable, "-B", "-c", script, str(materials[2][0])], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_runner_rejects_wrong_backend_before_discovery(materials, monkeypatch):
    monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: pytest.fail("resources acquired"))
    runner = lifecycle.Runner(compose_project_name="offline", runtime_artifact=materials[2][0], forwarding_backend="bpf")
    with pytest.raises(ValueError, match="incompatible"):
        runner.prepare()
    runner.cleanup()


@pytest.mark.parametrize("defect", ["old-version", "unknown-foundation"])
def test_native_incompatible_materials_fail_before_discovery(materials, monkeypatch, defect):
    path = materials[2][0]
    manifest = json.loads(path.read_text())
    if defect == "old-version":
        manifest["format_version"] = 7
    else:
        manifest["dataplane"]["contract"] = "unknown"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: pytest.fail("resources acquired"))
    runner = lifecycle.Runner(compose_project_name="offline", runtime_artifact=path)
    with pytest.raises(ValueError, match="version|foundation"):
        runner.prepare()
    runner.cleanup()


def test_native_link_collision_after_inventory_preserves_foreign_device(materials, monkeypatch):
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plans = adapter.prepare_all(artifact.boundaries())
        connection = next(a["connection"] for a in artifact.boundaries()[0].executable["request"]["actions"] if a["op"] == "create")
        links = kernel.links[connection["node_a"]]
        links[connection["interface_a"]] = 999
        with pytest.raises(OSError) as failure:
            adapter.activate(plans[0])
        assert failure.value.errno == errno.EEXIST
        adapter.cleanup()
        assert links.pop(connection["interface_a"]) == 999
        kernel.assert_clean()


@pytest.mark.parametrize("action", ["initialize", "cleanup"])
def test_native_lost_response_keeps_cleanup_retryable(materials, monkeypatch, action):
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        original = kernel.execute
        lost = False
        def execute(command, request):
            nonlocal lost
            result = original(command, request)
            if command == action and not lost:
                lost = True
                raise EOFError("lost helper response")
            return result
        monkeypatch.setattr(kernel, "execute", execute)
        with pytest.raises(EOFError, match="lost"):
            initialize(adapter, artifact)
            adapter.cleanup()
        assert adapter._cleanup_required
        adapter.cleanup()
        kernel.assert_clean()


def test_native_runner_activates_three_deliveries_and_reports_completed_frontiers(materials, monkeypatch):
    from time import monotonic, sleep
    paths = materials[2]
    with closing(_open_runner_artifact(paths[0])) as first:
        kernel, _ = runtime(monkeypatch, first)
    monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: kernel.bindings)
    monkeypatch.setattr(lifecycle._Deployment, "foundation_identity", lambda *a: "native-offline")
    runner = None
    class Helper:
        def __init__(self, **kwargs):
            assert kwargs["forwarding_backend"] == "linux"
        def start(self): pass
        def stop(self): pass
        def _set_main_deadline(self, deadline): pass
        def execute(self, action, request):
            result = kernel.execute(action, request)
            if action == "activate_boundary" and json.loads(request)["request"]["ordinal"] == 2:
                assert runner.append_batch(paths[2])
            return result
    monkeypatch.setattr(lifecycle, "_PrivilegedHelper", Helper)
    monkeypatch.setattr(lifecycle, "monotonic", lambda: monotonic() * 100)
    monkeypatch.setattr(lifecycle, "sleep", lambda seconds: sleep(seconds / 100))
    runner = lifecycle.Runner(compose_project_name="native", runtime_artifact=paths[0], forwarding_backend="linux")
    runner.append_batch(paths[1])
    try:
        runner.prepare()
        assert runner.status()["activation_count"] == 1
        runner.run()
        assert runner.status()["activation_count"] == 5
        assert runner.status()["active_frontier"] == (START + timedelta(seconds=20)).isoformat()
        events = [v for v in runner.inspection_snapshot()["events"] if v["event"] == "activation_end"]
        assert len(events) == 5
        assert all(v["target_transition_complete"] and not v["generation_publication_occurred"] for v in events)
    finally:
        runner.cleanup()
    kernel.assert_clean()


def test_zone_service_migration_and_withdrawal_runs_without_routes(tmp_path, monkeypatch):
    from examples.Sky00_tiny_network import tiny_network
    from skyseed.simulator import create_default_simulator, ZoneServicePlanningResult, ZoneServiceAssignment
    from skyseed.shared import NodeId
    satellite_a = NodeId(65001, "satellite", "satellite-0")
    satellite_b = NodeId(65001, "satellite", "satellite-1")
    class Zones:
        def assign_zone_service(self, world, candidates, previous, *, timestamp):
            offset = int((timestamp - START).total_seconds())
            selected = satellite_a if offset == 0 else satellite_b if offset == 10 else None
            return ZoneServicePlanningResult(tuple(ZoneServiceAssignment(z.zone_id, selected) for z in world.get_zone_layout().zones))
    def simulator_factory(definition, **kwargs):
        from templates.models.physical_link import StaticPhysicalLinkModel
        from skyseed.simulator.physical import ZoneServiceCandidate
        kwargs.update(route_model=EmptyRoutes(), zone_service_model=Zones(),
            physical_link_model=StaticPhysicalLinkModel(zone_service=tuple(
                ZoneServiceCandidate(z.zone_id, n) for z in kwargs["configuration"].zone_layout_definition.zones
                for n in (satellite_a, satellite_b))))
        return create_default_simulator(definition, **kwargs)
    monkeypatch.setattr(tiny_network.sky.simulator, "create_default_simulator", simulator_factory)
    simulator = tiny_network.create_simulator(tiny_network.build_emulator(), duration=timedelta(seconds=20))
    with Producer(simulator.get_timeline(), simulator.get_physical_world(), simulator.get_physical_link_model(),
                  simulator.get_planner(), forwarding_backend="linux") as producer:
        writer = _RunnerArtifactWriter(tmp_path / "zones", forwarding_backend="linux")
        while (result := producer.produce_next_realization()) is not None:
            writer.append(result)
        path = writer.finalize()
        writer.close()
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        class NoRepeatedZoneMembership(list):
            def __contains__(self, value):
                pytest.fail("runtime rescanned the fixed Zone address list")
        adapter._foundation["zone_addresses"] = NoRepeatedZoneMembership(adapter._foundation["zone_addresses"])
        plans = adapter.prepare_all(artifact.boundaries())
        zones = set(a + "/32" for a in artifact.dataplane["zone_addresses"])
        assert len(zones) == 2
        for index, selected in enumerate((satellite_a, satellite_b, None)):
            adapter.activate(plans[index])
            for node in kernel.kernel.nodes.values():
                present = kernel.addresses[node["key"]][node["identity"]] & zones
                assert present == (zones if selected is not None and node["key"] == selected.value else set())
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize("empty_routes", [True, False])
def test_complete_native_generation_uses_seed_preparation_without_bpf(tmp_path, monkeypatch, empty_routes):
    from examples.Sky00_tiny_network import tiny_network
    from skyseed.simulator import create_default_simulator
    from skyseed.experiment.execution import _read_and_verify_execution_definition
    from skyseed.backend.bpf.dataplane import program
    def simulator_factory(definition, **kwargs):
        if empty_routes:
            kwargs["route_model"] = EmptyRoutes()
        return create_default_simulator(definition, **kwargs)
    monkeypatch.setattr(tiny_network.sky.simulator, "create_default_simulator", simulator_factory)
    monkeypatch.setattr(program, "prepare_program", lambda *a, **kw: pytest.fail("BPF preparation invoked"))
    emulator = tiny_network.build_emulator()
    simulator = tiny_network.create_simulator(emulator, duration=timedelta(seconds=10))
    output = tmp_path / "generated"
    controller = ExperimentController(emulator, simulator, output, entry_path=Path(__file__),
        forwarding_backend="linux", internet_map_enabled=False)
    controller.generate()
    definition = _read_and_verify_execution_definition(output)
    assert definition["backend"] == "linux" and not definition["traffic_enabled"]
    with closing(_open_runner_artifact(output / definition["runner_artifact"]["manifest"])) as artifact:
        assert artifact.forwarding_backend == "linux" and artifact.boundary_count == 2
        assert any(a["op"] == "route" for b in artifact.boundaries()
                   for a in b.executable["request"]["actions"]) is (not empty_routes)
    # SEED emits these existing options in node startup scripts, not Compose sysctls.
    text = "\n".join(p.read_text() for p in (output / "deployment").rglob("*") if p.is_file())
    assert "net.ipv4.ip_forward=1" in text
    assert "echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter" in text
    assert not (output / "results/traffic/probe-plan.json").exists()


class CountedRanges:
    """Count interval probes, not elapsed time, in the actual consumers."""
    @staticmethod
    def install(monkeypatch, module):
        from skyseed.backend.linux.materials import _IPv4Ranges
        instances = []
        class Sequence:
            def __init__(self, values):
                self.values, self.probes = values, 0
            def __len__(self):
                return len(self.values)
            def __getitem__(self, index):
                self.probes += 1
                return self.values[index]
        class Ranges(_IPv4Ranges):
            def __init__(self, networks):
                super().__init__(networks)
                self._starts, self._ends = Sequence(self._starts), Sequence(self._ends)
                instances.append(self)
            @property
            def probes(self):
                return self._starts.probes + self._ends.probes
        monkeypatch.setattr(module, "_IPv4Ranges", Ranges)
        return instances


def test_native_allocation_indexes_exclusions_once(monkeypatch, record_property):
    from ipaddress import IPv4Address
    from skyseed.backend.linux import translation
    from skyseed.backend.execution_materials.actions import _PhysicalWorldProjection
    ranges = CountedRanges.install(monkeypatch, translation)
    # Nonadjacent identities prevent this bound from relying on a contiguous Zone set.
    zones = tuple(str(IPv4Address(int(IPv4Address("198.18.0.0")) + i * 4)) + "/32" for i in range(732))
    compiler = translation._LinuxTranslation(_PhysicalWorldProjection((A, B), ()), zones)
    result = [compiler._allocate_link() for _ in range(100)]
    expected = [(str(IPv4Address(int(IPv4Address("172.16.0.0")) + i * 2)) + "/31",
                 str(IPv4Address(int(IPv4Address("172.16.0.1")) + i * 2)) + "/31") for i in range(100)]
    assert result == expected
    assert len(ranges) == 1
    assert ranges[0].probes <= 100 * ((733).bit_length() + 2)
    record_property("allocation_interval_probes_732_zones_100_links", ranges[0].probes)


def test_native_allocation_skips_overlaps_and_exhausts_deterministically(monkeypatch):
    from skyseed.backend.linux import translation
    from skyseed.backend.execution_materials.actions import _PhysicalWorldProjection
    monkeypatch.setattr(translation.NativeRoutingEnvironment, "link_address_pools",
                        ("172.16.0.0/29", "10.0.0.0/29", "192.168.0.0/31"))
    zones = ("172.16.0.0/32", "172.16.0.1/32", "172.16.0.4/31", "172.16.0.5/32", "10.0.0.0/29")
    def allocate(prefixes):
        compiler = translation._LinuxTranslation(_PhysicalWorldProjection((A, B), ()), prefixes)
        result = [compiler._allocate_link() for _ in range(3)]
        with pytest.raises(ValueError, match="pools are exhausted"):
            compiler._allocate_link()
        return result
    expected = [("172.16.0.2/31", "172.16.0.3/31"), ("172.16.0.6/31", "172.16.0.7/31"),
                ("192.168.0.0/31", "192.168.0.1/31")]
    assert allocate(zones) == allocate(tuple(reversed(zones))) == expected


@pytest.mark.parametrize("backend", ["linux", "bpf"])
def test_fixed_identity_audits_use_one_index_and_keep_zone_collisions(materials, monkeypatch, backend, record_property):
    from ipaddress import IPv4Address
    from skyseed.backend.linux import runtime_command as native_command
    from skyseed.backend.bpf.dataplane import runtime_command as bpf_command
    command = native_command if backend == "linux" else bpf_command
    ranges = CountedRanges.install(monkeypatch, command)
    zones = [str(IPv4Address(int(IPv4Address("198.18.0.0")) + i * 4)) for i in range(732)]
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        dataplane = artifact.dataplane
        dataplane["zone_addresses"] = zones
        if backend == "linux":
            def audit():
                adapter.initialize(artifact.physical_world_projection, zone_prefixes=(),
                    runtime_capacity_demand=artifact.runtime_capacity_demand, dataplane=dataplane)
        else:
            def audit():
                return command._audit_node_addresses({"node_address_range": dataplane["node_address_range"],
                    "zone_prefixes": [a + "/32" for a in zones], "nodes": dataplane["nodes"]})
            monkeypatch.setattr(command, "_assert_netns", lambda node: None)
            monkeypatch.setattr(command, "_namespace_ipv4_networks", lambda node:
                ((IPv4Network("192.0.2.0/24"),), ((254, IPv4Network(kernel.static_network)),)))
        audit()
        assert len(ranges) == 1
        observed = len(dataplane["nodes"]) * (3 if backend == "linux" else 2)
        assert ranges[0].probes <= observed * ((733).bit_length() + 2)
        record_property(backend + "_identity_audit_interval_probes", ranges[0].probes)
        adapter.cleanup()
        kernel.assert_clean()
        # Broad and exact route overlaps must both still reject a fixed Zone identity.
        for prefix in ("198.18.0.0/16", zones[-1] + "/32"):
            kernel.static_network = prefix
            with pytest.raises(RuntimeError, match="collid"):
                audit()
            adapter.cleanup()
            kernel.assert_clean()


def test_native_material_schema_and_ip_parse_once_before_runtime(materials, monkeypatch, record_property):
    import ipaddress
    from skyseed.backend.linux import materials as schema
    calls = {"schemas": 0, "link_ips": 0}
    validate = schema._validate_request
    def validate_once(*args):
        calls["schemas"] += 1
        return validate(*args)
    class CountedInterface(ipaddress.IPv4Interface):
        def __init__(self, value):
            calls["link_ips"] += 1
            super().__init__(value)
    monkeypatch.setattr(schema, "_validate_request", validate_once)
    monkeypatch.setattr(ipaddress, "IPv4Interface", CountedInterface)
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        assert calls["schemas"] == artifact.boundary_count
        expected_ips = 2 * sum(a["op"] in {"create", "remove"} for b in artifact.boundaries()
                              for a in b.executable["request"]["actions"])
        assert calls["link_ips"] == expected_ips
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        before = dict(calls)
        plans = adapter.prepare_all(artifact.boundaries())
        assert calls == before
        record_property("runtime_repeated_request_schema_checks", calls["schemas"] - before["schemas"])
        record_property("runtime_repeated_link_ip_parses", calls["link_ips"] - before["link_ips"])
        adapter.activate(plans[0])
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize("defect", ["link-ip", "operation", "timestamp", "chronology", "zone"])
def test_native_material_validation_and_runtime_checks_remain_separate(tmp_path, monkeypatch, defect):
    from skyseed.backend.linux.translation import _LinuxTranslation
    original = _LinuxTranslation.translate
    def malformed(self, *args):
        boundary = original(self, *args)
        request = boundary.executable["request"]
        if defect == "link-ip":
            c = next(a["connection"] for a in request["actions"] if a["op"] == "create")
            c["address_b"] = c["address_a"]
        elif defect == "operation":
            request["actions"].append({"op": "route"})
        elif defect == "timestamp":
            request["boundary_timestamp"] = "2000-01-01T00:00:00+00:00"
        elif defect == "chronology":
            request["ordinal"] += 1
            request["previous_ordinal"] += 1
        else:
            request["actions"].append({"op": "address_add", "key": A.value, "address": "198.18.99.1"})
        return boundary
    monkeypatch.setattr(_LinuxTranslation, "translate", malformed)
    path = produce(tmp_path / "invalid", ends=(0,))[2][0]
    if defect in {"link-ip", "operation", "timestamp"}:
        monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: pytest.fail("resources acquired"))
        runner = lifecycle.Runner(compose_project_name="offline", runtime_artifact=path)
        with pytest.raises(ValueError, match="distinct|invalid Linux route|timestamp"):
            runner.prepare()
        runner.cleanup()
    else:
        with closing(_open_runner_artifact(path)) as artifact:
            kernel, adapter = runtime(monkeypatch, artifact)
            initialize(adapter, artifact)
            with pytest.raises(ValueError, match="chronological|undeclared Zone"):
                adapter.prepare_all(artifact.boundaries())
            assert not kernel.kernel.connections
            adapter.cleanup()
            kernel.assert_clean()


def test_native_full_s3_descriptor_capacity_in_isolated_process():
    # Exercise real descriptor allocation past a 1024 soft limit without changing
    # pytest's own limits or requiring namespaces/Docker.
    code = """
import os, resource
from skyseed.backend.linux.runtime_command import _reserve_foundation_descriptors
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (min(1024, soft), hard))
_reserve_foundation_descriptors(1075)
assert resource.getrlimit(resource.RLIMIT_NOFILE)[1] == hard
fds = []
try:
    for _ in range(2 * 1075):
        fds.append(os.open('/dev/null', os.O_RDONLY))
finally:
    for fd in fds:
        os.close(fd)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_low_hard_limit_rejects_before_namespace_ownership(materials, monkeypatch):
    from skyseed.backend.linux import runtime_command as command
    monkeypatch.setattr(command.resource, "getrlimit", lambda kind: (32, 32))
    monkeypatch.setattr(command.resource, "setrlimit", lambda *args: pytest.fail("hard limit must not change"))
    with closing(_open_runner_artifact(materials[2][0])) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        monkeypatch.setattr(network, "_retain_namespaces", lambda *args: pytest.fail("must reject before retaining namespaces"))
        with pytest.raises(RuntimeError, match="hard limit is 32"):
            initialize(adapter, artifact)
        kernel.assert_clean()


@pytest.mark.parametrize("limits", [(8192, 8192), (-1, -1)])
def test_native_sufficient_descriptor_limit_is_unchanged(monkeypatch, limits):
    from skyseed.backend.linux import runtime_command as command
    monkeypatch.setattr(command.resource, "getrlimit", lambda kind: limits)
    monkeypatch.setattr(command.resource, "setrlimit", lambda *args: pytest.fail("limit already sufficient"))
    command._reserve_foundation_descriptors(1075)
