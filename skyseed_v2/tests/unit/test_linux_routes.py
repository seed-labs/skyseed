"""Offline 3B routes through real Plans, sealed deliveries and native execution."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import errno
from ipaddress import IPv4Address, IPv4Interface
import json
import socket
import struct

import pytest

from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from skyseed_v2.backend.linux.materials import _ROUTE_PROTOCOL, _ROUTE_PRIORITY
from skyseed_v2.backend.linux.netlink import _RouteSocket
from skyseed_v2.backend.linux.runtime import _LinuxRuntime
from skyseed_v2.backend.runner import lifecycle
from skyseed_v2.simulator import (
    RoutePlanningResult, ReachableRouteOutcome, LocalRouteOutcome, RouteCandidate,
    LogicalExit, PolicyRecord, RouteDisposition, UnreachableRouteOutcome, RouteUnreachableReason,
)
from templates.dataplane.threshold import scene
from templates.dataplane.threshold.scene import A, B, G, START
from tests.unit.test_linux_runtime import Kernel, produce, initialize, ChangingAccess
from tests.unit.test_runner_netlink import _attributes


class RouteKernel(Kernel):
    def __init__(self, monkeypatch, nodes):
        super().__init__(monkeypatch, nodes)
        self.fib = {key: {} for key in nodes}
        self.route_calls = []
        self.lose_route_response = False

    class Channel(Kernel.Channel):
        def native_route(self, destination, *, kind, protocol, priority, index=None, gateway=None, prefsrc=None, delete=False):
            assert protocol == _ROUTE_PROTOCOL and priority == _ROUTE_PRIORITY
            record = dict(destination=destination, kind=kind, protocol=protocol, priority=priority,
                          index=index, gateway=gateway, prefsrc=prefsrc)
            self.owner.event("route_delete" if delete else "route_add", self.key, record)
            self.owner.route_calls.append((self.key, delete, record))
            key = destination, priority
            routes = self.owner.fib[self.key]
            if delete:
                # Match protocol, nonzero priority, type, OIF, gateway and prefsrc,
                # as the exact kernel delete filters do. A different record survives.
                if routes.get(key) != record:
                    raise OSError(errno.ESRCH, "owned route absent")
                del routes[key]
            else:
                if key in routes:
                    raise OSError(errno.EEXIST, "exact route key occupied")
                if kind == "unicast":
                    assert index in self.owner.links[self.key].values()
                    assert any(gateway == str(IPv4Interface(a).ip) for values in self.owner.addresses.values()
                               for addresses in values.values() for a in addresses)
                    assert prefsrc + "/32" in self.owner.addresses[self.key][self.link_index("sv2identity")]
                routes[key] = record
            if self.owner.lose_route_response:
                self.owner.lose_route_response = False
                raise EOFError("lost route acknowledgement")

        def neighbour(self, *args, **kwargs):
            pytest.fail("native routes must use ordinary ARP")

        def delete_link(self, index):
            endpoints = [(self.key, index)]
            name = next((name for name, i in self.owner.links[self.key].items() if i == index), None)
            peer = self.owner.peers.get((self.key, name))
            if peer is not None:
                endpoints.append((peer[0], self.owner.links[peer[0]][peer[1]]))
            super().delete_link(index)
            for key, removed_index in endpoints:
                for route_key, route in tuple(self.owner.fib[key].items()):
                    if route["index"] == removed_index:
                        del self.owner.fib[key][route_key]

    def assert_clean(self):
        super().assert_clean()
        assert not self.kernel.routes
        assert all(not routes for routes in self.fib.values())


def runtime(monkeypatch, artifact):
    kernel = RouteKernel(monkeypatch, {n.value: n for n in artifact.physical_world_projection.node_ids})
    return kernel, _LinuxRuntime(executor=kernel, bindings=kernel.bindings)


def candidate(connections, source, peer, **data):
    connection = next(c.connection_id for c in connections
                      if {source, peer} == {c.connection_id.endpoint_a, c.connection_id.endpoint_b})
    return RouteCandidate(LogicalExit(connection, source), PolicyRecord(data))


class FullPaths:
    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        offset = int((timestamp - START).total_seconds())
        if offset == 15:
            return RoutePlanningResult()
        outcomes = []
        for d in demands:
            if d.source == G:
                continue
            if offset == 25:
                outcomes.append(UnreachableRouteOutcome(d, RouteUnreachableReason.NO_ELIGIBLE_PATH))
            else:
                path = (A, G) if d.source == A and offset == 10 else (A, B, G) if d.source == A else (B, G)
                outcomes.append(ReachableRouteOutcome(d, path[1], path))
        return RoutePlanningResult(tuple(outcomes))


class LocalSnapshots:
    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        offset = int((timestamp - START).total_seconds())
        d = next(d for d in demands if d.source == A)
        if offset == 20:
            return RoutePlanningResult()
        disposition = {5: RouteDisposition.DROP, 10: RouteDisposition.PASS}.get(offset, RouteDisposition.FORWARD)
        candidates = (candidate(connections, A, B if offset == 0 else G),) if disposition is RouteDisposition.FORWARD else ()
        return RoutePlanningResult((LocalRouteOutcome(d, disposition, candidates),))


def test_full_paths_deduplicate_change_only_and_withdraw_across_deliveries(tmp_path, monkeypatch):
    paths = produce(tmp_path, ends=(5, 15, 25), model=FullPaths())[2]
    first_routes = {}
    with closing(_open_runner_artifact(paths[0])) as first:
        kernel, adapter = runtime(monkeypatch, first)
        initialize(adapter, first)
        expected_counts = (2, 0, 1, 2, 2, 2)
        ordinal = 0
        for path in paths:
            with closing(_open_runner_artifact(path)) as artifact:
                calls = len(kernel.route_calls)
                plans = adapter.prepare_all(artifact.boundaries())
                assert len(kernel.route_calls) == calls
                for boundary, plan in zip(artifact.boundaries(), plans):
                    changes = [a for a in boundary.executable["request"]["actions"] if a["op"] == "route"]
                    assert len(changes) == expected_counts[ordinal]
                    result = adapter.activate(plan)
                    assert result["target_transition_complete"] and not result["generation_publication_occurred"]
                    if ordinal == 0:
                        first_routes = deepcopy(kernel.fib)
                        assert sum(map(len, kernel.fib.values())) == 2  # Shared B -> G demand deduplicated.
                        assert not kernel.fib[G.value]  # No fabricated reverse route.
                        addresses = {n.value: a for n, a in adapter.runtime_node_addresses().items()}
                        for source in (A, B):
                            route = next(iter(kernel.fib[source.value].values()))
                            assert route["destination"] == addresses[G.value]
                            assert route["prefsrc"] == addresses[source.value]
                            assert route["gateway"] != route["destination"]
                    elif ordinal == 1:
                        assert kernel.fib == first_routes
                    elif ordinal in (3, 5):
                        assert all(not routes for routes in kernel.fib.values())
                    ordinal += 1
        assert len(kernel.route_calls) == 10  # 5 exclusive additions and 5 exact withdrawals.
        adapter.cleanup()
        kernel.assert_clean()
        # Fresh invocation over the same compiled materials restores the same routes.
        other = _LinuxRuntime(executor=kernel, bindings=kernel.bindings)
        initialize(other, first)
        other.activate(other.prepare_all(first.boundaries())[0])
        for key, routes in first_routes.items():
            assert {k: {f: v for f, v in r.items() if f != "index"} for k, r in routes.items()} == {
                k: {f: v for f, v in r.items() if f != "index"} for k, r in kernel.fib[key].items()}
        other.cleanup()
        kernel.assert_clean()


def test_local_forward_drop_pass_and_empty_are_exact_partial_coverage(tmp_path, monkeypatch):
    path = produce(tmp_path, ends=(20,), model=LocalSnapshots())[2][0]
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plans = adapter.prepare_all(artifact.boundaries())
        assert not kernel.route_calls
        for plan, kind in zip(plans, ("unicast", "blackhole", None, "unicast", None)):
            adapter.activate(plan)
            assert not kernel.fib[B.value] and not kernel.fib[G.value]
            routes = tuple(kernel.fib[A.value].values())
            assert (routes[0]["kind"] if routes else None) == kind
            if kind == "blackhole":
                assert routes[0]["index"] is routes[0]["gateway"] is routes[0]["prefsrc"] is None
        adapter.cleanup()
        kernel.assert_clean()


def test_equivalent_full_path_and_local_hop_share_ownership(tmp_path, monkeypatch):
    class Mixed:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            a = next(d for d in demands if d.source == A)
            b = next(d for d in demands if d.source == B)
            outcomes = [ReachableRouteOutcome(a, B, (A, B, G))]
            if timestamp == START:
                outcomes.append(LocalRouteOutcome(b, candidates=(candidate(connections, B, G),)))
            elif timestamp == START + timedelta(seconds=5):
                outcomes.append(ReachableRouteOutcome(b, G, (B, G)))
            return RoutePlanningResult(tuple(outcomes))

    _, plans, paths = produce(tmp_path, ends=(5, 10), model=Mixed())
    assert any(isinstance(c.current, LocalRouteOutcome)
               for c in plans[0].transitions[0].route_outcome_transitions)
    with closing(_open_runner_artifact(paths[0])) as first:
        kernel, adapter = runtime(monkeypatch, first)
        initialize(adapter, first)
        for path in paths:
            with closing(_open_runner_artifact(path)) as artifact:
                for prepared in adapter.prepare_all(artifact.boundaries()):
                    adapter.activate(prepared)
        assert len(kernel.route_calls) == 2
        assert sum(map(len, kernel.fib.values())) == 2
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize("case", ["empty", "multiple", "outcome-policy", "candidate-policy", "local-forward", "local-drop"])
def test_unsupported_local_expressions_fail_offline_but_remain_model_values(tmp_path, case):
    class Model:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            source = G if case.startswith("local-") else A
            d = next(d for d in demands if d.source == source)
            candidates = (candidate(connections, source, A if source == G else B),)
            disposition = RouteDisposition.FORWARD
            data = PolicyRecord()
            if case == "empty": candidates = ()
            elif case == "multiple": candidates += (candidate(connections, A, G),)
            elif case == "outcome-policy": data = PolicyRecord(priority=9)
            elif case == "candidate-policy": candidates = (candidate(connections, A, B, weight=10),)
            elif case == "local-drop": disposition, candidates = RouteDisposition.DROP, ()
            return RoutePlanningResult((LocalRouteOutcome(d, disposition, candidates, data),))
    with pytest.raises(ValueError, match="unsupported native route lowering"):
        produce(tmp_path, ends=(0,), model=Model())


@pytest.mark.parametrize("delete", [False, True])
@pytest.mark.parametrize("kind", ["unicast", "blackhole"])
def test_native_netlink_route_ownership_gateway_and_source_payload(kind, delete):
    channel = _RouteSocket.__new__(_RouteSocket)
    calls = []
    channel.request = lambda *args, **kwargs: calls.append((args, kwargs))
    kwargs = dict(kind=kind, protocol=_ROUTE_PROTOCOL, priority=_ROUTE_PRIORITY, delete=delete)
    if kind == "unicast": kwargs.update(index=17, gateway="172.16.0.1", prefsrc="100.64.0.1")
    channel.native_route("100.64.0.3", **kwargs)
    (message, body), options = calls[0]
    assert message == (25 if delete else 24)
    assert options["flags"] == (0 if delete else 0x600)
    assert struct.unpack_from("=BBBBBBBBI", body) == (socket.AF_INET, 32, 0, 0, 254, _ROUTE_PROTOCOL, 0, 1 if kind == "unicast" else 6, 0)
    attrs = _attributes(body[12:])
    assert attrs[1] == IPv4Address("100.64.0.3").packed
    assert struct.unpack("=I", attrs[6]) == (_ROUTE_PRIORITY,)
    if kind == "unicast":
        assert set(attrs) == {1, 4, 5, 6, 7}
        assert struct.unpack("=I", attrs[4]) == (17,)
        assert attrs[5] == IPv4Address("172.16.0.1").packed
        assert attrs[7] == IPv4Address("100.64.0.1").packed
    else:
        assert set(attrs) == {1, 6}


class OneThenEmpty:
    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        if timestamp != START:
            return RoutePlanningResult()
        d = next(d for d in demands if d.source == A)
        return RoutePlanningResult((LocalRouteOutcome(d, candidates=(candidate(connections, A, B),)),))


def test_link_switch_and_recreation_bind_current_interfaces(tmp_path, monkeypatch):
    class Routes:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            access = next(c.connection_id for c in connections if G in (c.connection_id.endpoint_a, c.connection_id.endpoint_b))
            serving = access.endpoint_a if access.endpoint_b == G else access.endpoint_b
            outcomes = []
            for d in demands:
                if d.source != G:
                    path = (d.source, G) if d.source == serving else (d.source, serving, G)
                    outcomes.append(ReachableRouteOutcome(d, path[1], path))
            return RoutePlanningResult(tuple(outcomes))
    monkeypatch.setattr(scene, "SimpleGroundStationModel", ChangingAccess)
    paths = produce(tmp_path, ends=(5, 15, 20), model=Routes())[2]
    with closing(_open_runner_artifact(paths[0])) as first:
        kernel, adapter = runtime(monkeypatch, first)
        initialize(adapter, first)
        original = None
        for path in paths:
            with closing(_open_runner_artifact(path)) as artifact:
                for boundary, plan in zip(artifact.boundaries(), adapter.prepare_all(artifact.boundaries())):
                    before = len(kernel.events)
                    adapter.activate(plan)
                    events = kernel.events[before:]
                    withdrawals = [i for i, e in enumerate(events) if e[0] == "route_delete"]
                    links = [i for i, e in enumerate(events) if e[0] == "delete"]
                    if links:
                        assert withdrawals and max(withdrawals) < min(links)
                    a_route = next(iter(kernel.fib[A.value].values()))
                    if original is None:
                        original = dict(a_route)
                    if boundary.nominal_time == START + timedelta(seconds=20):
                        assert a_route["gateway"] == original["gateway"]
                        assert a_route["index"] != original["index"]
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize("phase", ["add", "change-delete", "route-ack", "helper-ack", "cleanup-delete", "cleanup-ack"])
def test_route_failure_and_lost_responses_keep_retryable_cleanup(tmp_path, monkeypatch, phase):
    path = produce(tmp_path, ends=(5,), model=LocalSnapshots())[2][0]
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plans = adapter.prepare_all(artifact.boundaries())
        if phase in {"change-delete", "cleanup-delete", "cleanup-ack"}:
            adapter.activate(plans[0])
        if phase == "add":
            kernel.fail = "route_add"
        elif phase in {"change-delete", "cleanup-delete"}:
            kernel.fail = "route_delete"
        elif phase == "route-ack":
            kernel.lose_route_response = True
        else:
            original = kernel.execute
            command_to_lose = "cleanup" if phase == "cleanup-ack" else "activate_boundary"
            lost = False
            def execute(command, request):
                nonlocal lost
                result = original(command, request)
                if command == command_to_lose and not lost:
                    lost = True
                    raise EOFError("lost helper response")
                return result
            monkeypatch.setattr(kernel, "execute", execute)
        with pytest.raises((OSError, EOFError, RuntimeError)):
            if phase.startswith("cleanup"):
                adapter.cleanup()
            else:
                adapter.activate(plans[1] if phase == "change-delete" else plans[0])
        assert adapter._cleanup_required
        if not phase.startswith("cleanup"):
            assert adapter._active_ordinal == (0 if phase == "change-delete" else -1)
        adapter.cleanup()
        kernel.assert_clean()


@pytest.mark.parametrize("field,value", [("protocol", 99), ("priority", 7), ("gateway", "172.16.99.1"),
    ("prefsrc", "192.0.2.1"), ("index", 3), ("kind", "blackhole")])
def test_withdrawal_preserves_externally_replaced_route(tmp_path, monkeypatch, field, value):
    path = produce(tmp_path, ends=(5,), model=OneThenEmpty())[2][0]
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plans = adapter.prepare_all(artifact.boundaries())
        adapter.activate(plans[0])
        routes = kernel.fib[A.value]
        key, record = next(iter(routes.items()))
        foreign = {**record, field: value}
        if field == "kind":
            foreign.update(index=None, gateway=None, prefsrc=None)
        del routes[key]
        foreign_key = foreign["destination"], foreign["priority"]
        routes[foreign_key] = foreign
        adapter.activate(plans[1])
        assert routes[foreign_key] == foreign
        assert not kernel.kernel.routes
        # The external writer releases its route before our owned link teardown.
        # Kernel expiry of routes using a removed veth is distinct from a FIB delete.
        del routes[foreign_key]
        adapter.cleanup()
        kernel.assert_clean()


def test_missing_owned_route_after_veth_disappearance_does_not_delete_foreign_route(tmp_path, monkeypatch):
    path = produce(tmp_path, ends=(5,), model=OneThenEmpty())[2][0]
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plans = adapter.prepare_all(artifact.boundaries())
        adapter.activate(plans[0])
        record = next(iter(kernel.fib[A.value].values()))
        kernel.kernel.nodes[A.value]["netlink"].delete_link(record["index"])
        assert not kernel.fib[A.value]
        foreign = {**record, "protocol": 99, "index": 3, "gateway": "192.0.2.2", "prefsrc": "192.0.2.2"}
        key = foreign["destination"], foreign["priority"]
        kernel.fib[A.value][key] = foreign
        adapter.activate(plans[1])
        adapter.cleanup()
        assert kernel.fib[A.value].pop(key) == foreign
        kernel.assert_clean()


def test_exclusive_collision_fails_without_claiming_foreign_route(tmp_path, monkeypatch):
    path = produce(tmp_path, ends=(0,), model=OneThenEmpty())[2][0]
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plan = adapter.prepare_all(artifact.boundaries())[0]
        destination = adapter.runtime_node_addresses()[G]
        foreign = dict(destination=destination, kind="unicast", protocol=99, priority=_ROUTE_PRIORITY,
                       index=3, gateway="192.0.2.2", prefsrc="192.0.2.2")
        key = destination, _ROUTE_PRIORITY
        kernel.fib[A.value][key] = foreign
        with pytest.raises(OSError) as failure:
            adapter.activate(plan)
        assert failure.value.errno == errno.EEXIST
        assert not kernel.kernel.routes
        adapter.cleanup()
        assert kernel.fib[A.value].pop(key) == foreign
        kernel.assert_clean()


def zone_materials(tmp_path, monkeypatch, *, unchanged_drop=False):
    from examples.Sky00_tiny_network import tiny_network
    from skyseed_v2.simulator import create_default_simulator, ZoneServicePlanningResult, ZoneServiceAssignment, RouteDemand, ZoneRouteDemandTarget
    from skyseed_v2.shared import NodeId
    from skyseed_v2.experiment import Producer
    from skyseed_v2.backend.execution_materials.sealing import _RunnerArtifactWriter
    from templates.models.physical_link import StaticPhysicalLinkModel
    from templates.models.inter_satellite import SimpleInterSatelliteModel
    from templates.models.ground_station import SimpleGroundStationModel
    from skyseed_v2.simulator.physical import ZoneServiceCandidate, InterSatelliteLinkCandidate, GroundStationAccessCandidate
    a, b = (NodeId(65001, "satellite", "satellite-" + str(i)) for i in (0, 1))
    g = NodeId(65001, "ground-station", "gs-new-york")
    class Zones:
        def assign_zone_service(self, world, candidates, previous, *, timestamp):
            seconds = int((timestamp - START).total_seconds())
            selected = (b if unchanged_drop else a) if seconds < 10 else (a if unchanged_drop else b) if seconds < 20 else None
            return ZoneServicePlanningResult(tuple(ZoneServiceAssignment(z.zone_id, selected) for z in world.get_zone_layout().zones))
    class Routes:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            owners = {v.service_key: v.satellite_id for v in assignments}
            outcomes = []
            for d in demands:
                if unchanged_drop:
                    if d.source == a:
                        outcomes.append(LocalRouteOutcome(d, RouteDisposition.DROP))
                    continue
                owner = owners[d.target.service_key]
                if owner is None:
                    outcomes.append(UnreachableRouteOutcome(d, RouteUnreachableReason.ZONE_UNSERVED))
                elif d.source == owner:
                    outcomes.append(LocalRouteOutcome(d, RouteDisposition.PASS))
                else:
                    paths = [(d.source,)]
                    for path in paths:
                        if path[-1] == owner:
                            outcomes.append(ReachableRouteOutcome(d, path[1], path))
                            break
                        for c in connections:
                            connection = c.connection_id
                            if path[-1] in (connection.endpoint_a, connection.endpoint_b):
                                peer = LogicalExit(connection, path[-1]).peer
                                if peer not in path:
                                    paths.append((*path, peer))
                    else:
                        raise AssertionError("fixture has no route to Zone owner")
            return RoutePlanningResult(tuple(outcomes))
    def factory(definition, **kwargs):
        zones = kwargs["configuration"].zone_layout_definition.zones
        kwargs.update(route_model=Routes(), zone_service_model=Zones(),
            physical_link_model=StaticPhysicalLinkModel(inter_satellite=(InterSatelliteLinkCandidate(a, b),),
                ground_station=(GroundStationAccessCandidate(g, a),),
                zone_service=tuple(ZoneServiceCandidate(z.zone_id, n) for z in zones for n in (a, b))),
            inter_satellite_model=SimpleInterSatelliteModel(((a, b),)),
            ground_station_model=SimpleGroundStationModel(), parameter_model=scene.FixedParameterModel(),
            route_demands=tuple(RouteDemand(n, ZoneRouteDemandTarget(zones[0].zone_id)) for n in (a, b, g)))
        return create_default_simulator(definition, **kwargs)
    monkeypatch.setattr(tiny_network.sky.simulator, "create_default_simulator", factory)
    simulator = tiny_network.create_simulator(tiny_network.build_emulator(), duration=timedelta(seconds=20), observation_interval=timedelta(seconds=5))
    with Producer(simulator.get_timeline(), simulator.get_physical_world(), simulator.get_physical_link_model(),
                  simulator.get_planner(), forwarding_backend="linux") as producer:
        writer = _RunnerArtifactWriter(tmp_path / "zones", forwarding_backend="linux")
        try:
            while (result := producer.produce_next_realization()) is not None:
                writer.append(result)
            return writer.finalize(), (a, b, g)
        finally:
            writer.close()


def test_zone_migration_preserves_unchanged_hop_and_withdraws_on_unserved(tmp_path, monkeypatch):
    path, (a, b, g) = zone_materials(tmp_path, monkeypatch)
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, adapter = runtime(monkeypatch, artifact)
        initialize(adapter, artifact)
        plans = adapter.prepare_all(artifact.boundaries())
        assert not kernel.route_calls
        ground_route = None
        for offset, plan in zip((0, 5, 10, 15, 20), plans):
            before = len(kernel.route_calls)
            adapter.activate(plan)
            if offset < 20:
                local, remote = (a, b) if offset < 10 else (b, a)
                assert not kernel.fib[local.value]
                assert len(kernel.fib[remote.value]) == 1
                current = next(iter(kernel.fib[g.value].values()))
                if ground_route is None:
                    ground_route = dict(current)
                assert current == ground_route
                if offset > 0:
                    assert not any(source == g.value for source, _, _ in kernel.route_calls[before:])
            else:
                assert all(not routes for routes in kernel.fib.values())
        adapter.cleanup()
        kernel.assert_clean()


def test_unchanged_drop_rejected_when_zone_becomes_local(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="cannot override a destination local"):
        zone_materials(tmp_path, monkeypatch, unchanged_drop=True)


def test_native_old_foundation_rejected_before_discovery(tmp_path, monkeypatch):
    path = produce(tmp_path, ends=(0,), model=OneThenEmpty())[2][0]
    manifest = json.loads(path.read_text())
    manifest["dataplane"]["contract"] = "skyseed-linux-ipv4/1"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: pytest.fail("resources acquired"))
    runner = lifecycle.Runner(compose_project_name="offline", runtime_artifact=path)
    with pytest.raises(ValueError, match="unsupported Linux IPv4 foundation"):
        runner.prepare()
    runner.cleanup()


@pytest.mark.parametrize("defect", ["preferred-source", "destination", "policy", "gateway", "predecessor", "local"])
def test_altered_sealed_routes_rejected_before_activation(tmp_path, monkeypatch, defect):
    from skyseed_v2.backend.linux.translation import _LinuxTranslation
    original = _LinuxTranslation.translate
    def malformed(self, *args):
        boundary = original(self, *args)
        change = next(a for a in boundary.executable["request"]["actions"] if a["op"] == "route")
        route = change["current"]
        if defect == "preferred-source":
            route["prefsrc"] = "192.0.2.1"
        elif defect == "destination":
            route["destination"] = "192.0.2.1"
        elif defect == "policy":
            route["weight"] = 3
        elif defect == "gateway":
            route["gateway"] = "192.0.2.1"
        elif defect == "predecessor":
            change["previous"] = {k: route[k] for k in ("key", "destination", "kind")}
            change["previous"]["kind"] = "blackhole"
        else:
            route["destination"] = route["prefsrc"]
        return boundary
    monkeypatch.setattr(_LinuxTranslation, "translate", malformed)
    path = produce(tmp_path, ends=(0,), model=OneThenEmpty())[2][0]
    if defect in {"preferred-source", "destination", "policy"}:
        monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: pytest.fail("resources acquired"))
        runner = lifecycle.Runner(compose_project_name="offline", runtime_artifact=path)
        with pytest.raises(ValueError, match="invalid Linux route"):
            runner.prepare()
        runner.cleanup()
    else:
        with closing(_open_runner_artifact(path)) as artifact:
            kernel, adapter = runtime(monkeypatch, artifact)
            initialize(adapter, artifact)
            with pytest.raises(ValueError, match="gateway|predecessor|local destination"):
                adapter.prepare_all(artifact.boundaries())
            assert not kernel.route_calls and not kernel.kernel.connections
            adapter.cleanup()
            kernel.assert_clean()


def test_nonempty_native_materials_remain_detached_from_models_compiler_and_bpf(tmp_path):
    from tests.unit.test_linux_runtime import test_detached_native_reader_and_runner_import_without_bpf as check_detached
    check_detached(produce(tmp_path, ends=(5,), model=OneThenEmpty()))


def test_shared_runner_executes_native_routes_across_three_deliveries(tmp_path, monkeypatch):
    from tests.unit import test_linux_runtime as foundation
    monkeypatch.setattr(foundation, "runtime", runtime)
    foundation.test_native_runner_activates_three_deliveries_and_reports_completed_frontiers(
        produce(tmp_path, ends=(5, 15, 20), model=FullPaths()), monkeypatch)


def test_foreign_logical_exit_rejected_by_shared_compiler_offline(tmp_path):
    from dataclasses import replace
    from skyseed_v2.shared import NodeId
    class Model:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            d = next(d for d in demands if d.source == A)
            real = candidate(connections, A, B).exit.connection
            foreign = replace(real, endpoint_b=NodeId(65001, "satellite", "foreign"))
            return RoutePlanningResult((LocalRouteOutcome(d, candidates=(RouteCandidate(LogicalExit(foreign, A)),)),))
    with pytest.raises(ValueError, match="foreign or non-operational exit"):
        produce(tmp_path, ends=(0,), model=Model())
