from datetime import timedelta
import pytest
from skyseed.pc.network import RouteDestination, GroundStationZoneRoutingModel, ServiceAssignmentModel


def trace(rules, source, destination):
    node, label = source, None
    path = [node]
    for _ in range(len(rules) + 1):
        key = ("destination", destination.kind, destination.identity) if label is None else ("label", label)
        rule = next(rule for rule in rules[node] if rule.key == key)
        if rule.action in ("deliver", "pop_deliver"):
            assert rule.destination == destination
            return tuple(path)
        label = rule.outgoing_label
        node = rule.exit.peer
        path.append(node)
    raise AssertionError("forwarding loop")


def test_downstream_labels_are_local_swapped_and_retained_across_service_changes(epoch, network_factory):
    network = network_factory()
    network.scene.create_zone("first", latitude_deg=0, longitude_deg=0)
    class Services(ServiceAssignmentModel):
        def assign_service(self, scene, snapshot):
            # Different destinations allocate different labels in b's local space.
            return {"z": "b", "first": "c" if snapshot.timestamp < epoch + timedelta(seconds=5) else "d"}
    network.set_service_assignment_model(Services)
    observations = []
    network.set_routing_hook(GroundStationZoneRoutingModel, model_options={"observer": observations.append})
    network.generate_plans(epoch, epoch + timedelta(seconds=10))
    plan = network.base_plans[0]
    before = plan.state_at(epoch)
    after = plan.state_at(epoch + timedelta(seconds=5))
    destination = RouteDestination("endpoint", "first")
    assert trace(before, "g", destination) == ("g", "a", "b", "c")
    assert trace(after, "g", destination) == ("g", "a", "b", "d")
    old = next(rule for rule in before["b"] if rule.destination == destination)
    new = next(rule for rule in after["b"] if rule.destination == destination)
    assert old.incoming_label == new.incoming_label
    assert old.incoming_label != old.outgoing_label
    assert old.exit.peer == "c" and new.exit.peer == "d"
    bindings = network.routing_model.label_bindings()
    assert old.outgoing_label == bindings[("c", destination)]
    assert new.outgoing_label == bindings[("d", destination)]
    assert observations[0].paths[("g", destination)] == ("g", "a", "b", "c")
    # The supplied graph is directed: no reverse path is invented.
    assert observations[0].paths[("c", RouteDestination("node", "g"))] is None


def test_ground_nodes_cannot_be_transit_and_unserved_is_explicit(epoch, network_factory):
    network = network_factory(lambda _: [("a", "g"), ("g", "b"), ("b", "c")])
    network.scene.create_ground_station("other", latitude_deg=0, longitude_deg=1)
    class Services:
        def assign_service(self, scene, snapshot):
            return {"z": "c" if snapshot.timestamp == epoch else None}
    network.set_service_assignment_model(Services)
    observations = []
    network.set_routing_hook(GroundStationZoneRoutingModel, model_options={"observer": observations.append})
    network.generate_plans(epoch, epoch + timedelta(seconds=10))
    destination = RouteDestination("endpoint", "z")
    assert observations[0].paths[("g", destination)] == ("g", "b", "c")
    assert "a" not in network.base_plans[0].state_at(epoch)
    assert observations[1].assignments["z"] is None
    assert observations[1].paths[("g", destination)] is None
    assert network.base_plans[0].state_at(epoch + timedelta(seconds=5)) == {}


def test_destination_rules_share_path_algorithm_without_labels(epoch, network_factory):
    network = network_factory()
    class Services:
        def assign_service(self, scene, snapshot):
            return {"z": "c"}
    network.set_service_assignment_model(Services)
    network.set_routing_hook(GroundStationZoneRoutingModel, model_options={"label_forwarding": False})
    network.generate_plans(epoch, epoch + timedelta(seconds=1))
    rules = network.base_plans[0].state_at(epoch)
    assert rules["g"][0].action == "forward" and rules["g"][0].exit.peer == "a"
    assert rules["c"][0].action == "deliver"
    assert all(rule.incoming_label is None and rule.outgoing_label is None
               for values in rules.values() for rule in values)


@pytest.mark.parametrize("prefixes", [
    ("0.0.0.0/0", "10.0.0.0/8", "10.1.2.3/32"),
    ("::/0", "2001:db8::/32", "2001:db8::1/128"),
])
def test_custom_ip_prefix_routes_keep_identity_through_updates_and_delivery(epoch, network_factory, prefixes):
    from skyseed.pc.network import ForwardingRule, LogicalExit, FixedDelayDeliverySchedulingModel

    network = network_factory(lambda _: [("g", "a"), ("g", "b")])
    default_prefix, network_prefix, host_prefix = prefixes

    class PrefixRoutes:
        calls = 0

        def compute_routes(self, scene, timestamp, link_plans):
            self.calls += 1
            elapsed = (timestamp - epoch).total_seconds()
            exit_a = LogicalExit(("g", "a"), "g")
            rules = [
                ForwardingRule(RouteDestination("prefix", default_prefix), "forward", exit_a),
                ForwardingRule(RouteDestination("prefix", network_prefix), "forward", exit_a),
            ]
            if elapsed < 10:
                peer = "a" if elapsed < 5 else "b"
                rules.append(ForwardingRule(
                    RouteDestination("prefix", host_prefix), "forward",
                    LogicalExit(("g", peer), "g"), data={"experiment": "prefix-change"}))
            return {"g": tuple(rules)}

    network.set_routing_hook(PrefixRoutes)
    network.set_delivery_scheduling_hook(FixedDelayDeliverySchedulingModel,
                                        model_options={"node_delays": {"g": timedelta(seconds=1.25)}})
    delivery, = network.generate_plans(epoch, epoch + timedelta(seconds=11))
    base, = network.base_plans

    def prefix_table(rules):
        return {rule.destination.identity: rule for rule in rules.get("g", ())}

    # A default route and overlapping network/host prefixes remain separate entries.
    at_start = prefix_table(base.state_at(epoch))
    assert set(at_start) == set(prefixes)
    assert all(rule.incoming_label is None and rule.outgoing_label is None
               for rule in at_start.values())
    assert delivery.state_at(epoch) == {}
    assert prefix_table(delivery.state_at(epoch + timedelta(seconds=1.25))) == at_start

    at_change = prefix_table(base.state_at(epoch + timedelta(seconds=5)))
    assert at_change[host_prefix].exit.peer == "b"
    assert at_change[host_prefix].key == at_start[host_prefix].key
    assert at_change[host_prefix].data == {"experiment": "prefix-change"}
    assert at_change[default_prefix] == at_start[default_prefix]
    assert at_change[network_prefix] == at_start[network_prefix]
    assert prefix_table(delivery.state_at(epoch + timedelta(seconds=6)))[host_prefix].exit.peer == "a"
    assert prefix_table(delivery.state_at(epoch + timedelta(seconds=6.25)))[host_prefix].exit.peer == "b"
    assert host_prefix not in prefix_table(base.state_at(epoch + timedelta(seconds=10)))
    assert host_prefix in prefix_table(delivery.state_at(epoch + timedelta(seconds=10.5)))
    assert set(prefix_table(delivery.end_rules)) == {default_prefix, network_prefix}
    host_updates = [(op.kind, (op.timestamp - epoch).total_seconds()) for op in delivery.operations
                    if op.rule.destination.identity == host_prefix]
    assert host_updates == [("install", 1.25), ("replace", 6.25), ("withdraw", 11.25)]
    assert network.routing_model.calls == 3


def test_disconnected_topology_change_reuses_relevant_reverse_search(epoch, network_factory, monkeypatch):
    from collections import deque
    from skyseed.scene import ScenePositionSource
    import skyseed.pc.network.routing_model as routing_module
    def edges(seconds):
        isolated = [("d", "e")] if seconds == 0 else [("e", "d")]
        return [("g", "a"), ("a", "b"), ("b", "c")] + isolated
    network = network_factory(edges)
    network.scene.create_satellite("e", position=ScenePositionSource("test/static", {}))
    class Services:
        def assign_service(self, scene, snapshot):
            return {"z": "c"}
    network.set_service_assignment_model(Services)
    visited = []
    class CountingQueue(deque):
        def popleft(self):
            value = super().popleft()
            visited.append(value)
            return value
    monkeypatch.setattr(routing_module, "deque", CountingQueue)
    first = network.generate_plans(epoch, epoch + timedelta(seconds=3))[0]
    work = len(visited)
    second = network.generate_plans(first.end_time, epoch + timedelta(seconds=8))[0]
    assert first.end_rules == second.end_rules
    assert work > 0 and len(visited) == work
