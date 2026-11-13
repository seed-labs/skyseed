from datetime import datetime, timedelta, timezone
import struct

import pytest
import yaml

from skyseed import PlanCompiler, save_plans, load_plans
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkPlan, LinkOperation
from skyseed.pc.network import RoutePlan, RouteOperation, RouteDestination, LogicalExit, ForwardingRule
from skyseed.pc.physical import LinkCandidate
from skyseed.pc.compiler.bpf import encode_rule
from skyseed.pc.compiler import BackendInstruction, PhysicalOperation, PhysicalPlan


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(second):
    return START + timedelta(seconds=second)


def connection(*, reverse=True, rate=1_000_000):
    reports = {"a": LinkCandidate("a", "b", propagation_delay_s=0.004)}
    if reverse:
        reports["b"] = LinkCandidate("b", "a", propagation_delay_s=0.008)
    return LinkConfiguration(ConnectionCandidate(("a", "b"), reports),
                             LinkProperties(("a", "b"), rate, 0.004, 25),
                             LinkProperties(("a", "b"), 2_000_000, 0.008, 50))


def route(action="push", *, incoming=None, outgoing=30):
    return ForwardingRule(RouteDestination("endpoint", "zone"), action,
                          LogicalExit(("a", "b"), "a"), incoming, outgoing)


def test_independent_streams_keep_asymmetric_shaping_time_and_dependencies(deployment):
    value = connection()
    links = [LinkPlan(at(0), at(5), {}, (LinkOperation(at(1), "establish", value.id, value),)),
             LinkPlan(at(5), at(10), {value.id: value}, (LinkOperation(at(8), "withdraw", value.id),))]
    routes = [RoutePlan(at(0), at(12), {}, (RouteOperation(at(10.2), "a", "install", route()),))]
    plans = tuple(PlanCompiler(deployment).compile_plans(link_plans=iter(links), route_plans=iter(routes)))
    assert [plan.stream for plan in plans] == ["link", "route", "link"]
    event = plans[0].operations[0]
    assert event.timestamp == at(1)
    assert [item.opcode for item in event.instructions] == [
        "veth.create", "interface.configure", "interface.configure", "netem.replace", "netem.replace", "link.activate"]
    shaping = [item.parameters for item in event.instructions if item.opcode == "netem.replace"]
    assert [(item["delay_us"], item["rate_bps"], item["loss_percent"]) for item in shaping] == [
        (4000, 1_000_000, 0.0025), (8000, 2_000_000, 0.005)]
    assert all(item.requires for item in event.instructions[1:])
    assert plans[1].operations[0].timestamp == at(10.2)
    assert [item.opcode for item in plans[2].operations[0].instructions] == ["link.unbind", "veth.delete"]
    target = plans[0].targets["links"][0]
    assert target == plans[1].targets["links"][0] == plans[2].targets["links"][0]
    assert [item["address"] for item in target["endpoints"]] == ["100.64.0.0/31", "100.64.0.1/31"]


@pytest.mark.parametrize("reverse,rate,expected", [(False, 1000, [False, True]), (True, 0, [True, False])])
def test_missing_direction_and_zero_rate_are_drops(deployment, reverse, rate, expected):
    value = connection(reverse=reverse, rate=rate)
    logical = LinkPlan(at(0), at(1), {value.id: value})
    physical, = PlanCompiler(deployment).compile_plans(link_plans=[logical])
    shaping = [item.parameters for item in physical.initial_operations[0].instructions if item.opcode == "netem.replace"]
    assert [item["drop"] for item in shaping] == expected
    for item in shaping:
        if item["drop"]:
            assert item["rate_bps"] is None and item["loss_percent"] == 100


def test_route_only_compilation_records_missing_link_policy_without_creating_link(deployment):
    rule = route("forward", outgoing=None)
    logical = RoutePlan(at(0), at(3), {"a": (rule,)}, (RouteOperation(at(2), "a", "withdraw", rule),))
    physical, = PlanCompiler(deployment, forwarding_backend="linux").compile_plans(route_plans=[logical])
    initial, = physical.initial_operations[0].instructions
    assert initial.opcode == "linux.route"
    assert initial.parameters["unavailable"] == "blackhole"
    assert initial.parameters["gateway"] == "100.64.0.1"
    assert initial.parameters["resource"].startswith("link:")
    assert initial.requires == ()  # Do not postpone Delivery while waiting for an interface.
    withdrawn, = physical.operations[0].instructions
    assert withdrawn.parameters["kind"] == "withdraw"
    assert all(item.opcode != "veth.create" for event in physical.initial_operations for item in event.instructions)


def test_local_labels_use_one_canonical_table_abi_and_linux_rejects_them(deployment):
    rule = route("swap", incoming=20)
    logical = RoutePlan(at(0), at(1), {"a": (rule,)})
    physical, = PlanCompiler(deployment).compile_plans(route_plans=[logical])
    material = physical.initial_operations[0].instructions[0].parameters
    assert material["match"] == ("label", 20)
    assert bytes.fromhex(material["key_hex_le"]) == b"\x40\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14"
    assert struct.unpack("<III", bytes.fromhex(material["value_hex_le"])) == (2, 1, 30)
    assert material["unavailable"] == "drop"
    program = physical.targets["bpf_program"]
    assert program["publication"] == "shared-generation"
    assert program["shared_maps"] == ("active_generation", "generation_readers")
    with pytest.raises(ValueError, match="IP rules"):
        tuple(PlanCompiler(deployment, forwarding_backend="linux").compile_plans(route_plans=[logical]))
    prefix_key, _ = encode_rule(("prefix", "192.0.2.0/24"), "deliver", byteorder="big")
    assert prefix_key == bytes.fromhex("0000003800000001c0000200")


def test_context_and_tail_restore_without_becoming_new_operations(deployment, tmp_path):
    rule = route()
    install = RouteOperation(at(7), "a", "install", rule)
    first = RoutePlan(at(0), at(8), {}, (install,))
    second = RoutePlan(at(5), at(10), {}, (RouteOperation(at(8), "a", "withdraw", rule),), (install,))
    compiler = PlanCompiler(deployment)
    a, = compiler.compile_plans(route_plans=[first])
    b, = compiler.compile_plans(route_plans=[second])
    assert b.context_operations == a.operations
    assert [op.timestamp for op in b.operations] == [at(8)]
    assert a.targets["links"] == b.targets["links"]
    path = tmp_path / "physical.plan"
    save_plans(path, [b])
    assert tuple(load_plans(path)) == (b,)
    assert b.context_operations[0].instructions[0].parameters["destination"] == ("endpoint", "zone")


def test_unknown_nodes_pool_overlap_and_deployment_identity(deployment):
    first = PlanCompiler(deployment)
    assert PlanCompiler(deployment).deployment_id == first.deployment_id
    rule = ForwardingRule(RouteDestination("node", "unknown"), "deliver")
    with pytest.raises(ValueError, match="unknown.*absent"):
        tuple(first.compile_plans(route_plans=[RoutePlan(at(0), at(1), {"a": (rule,)})]))
    path = deployment / "docker-compose.yml"
    compose = yaml.safe_load(path.read_text())
    compose["networks"] = {"ground": {"ipam": {"config": [{"subnet": "198.18.0.0/24"}]}}}
    path.write_text(yaml.safe_dump(compose))
    with pytest.raises(ValueError, match="overlaps"):
        PlanCompiler(deployment)
    assert PlanCompiler(deployment, node_address_pool="192.0.2.0/24").deployment_id != first.deployment_id


def test_repeated_batches_do_not_recompile_previous_history(deployment, monkeypatch):
    compiler = PlanCompiler(deployment)
    calls = []
    original = compiler._targets.link

    def counted(identity):
        calls.append(identity)
        return original(identity)

    monkeypatch.setattr(compiler._targets, "link", counted)
    rule = route()
    for index in range(40):
        logical = RoutePlan(at(index), at(index + 1), {"a": (rule,)})
        tuple(compiler.compile_plans(route_plans=[logical]))
    assert len(calls) == 40
    assert len(compiler._targets.links) == 1
    assert len(compiler._targets.endpoints) == 1


def test_two_logical_destinations_cannot_silently_overwrite_one_backend_match(deployment):
    node_rule = ForwardingRule(RouteDestination("node", "a"), "deliver")
    prefix_rule = ForwardingRule(RouteDestination("prefix", "198.18.0.1/32"), "deliver")
    logical = RoutePlan(at(0), at(1), {"a": (node_rule, prefix_rule)})
    with pytest.raises(ValueError, match="same match"):
        tuple(PlanCompiler(deployment).compile_plans(route_plans=[logical]))


def test_dynamic_interface_does_not_overwrite_a_seed_interface(deployment):
    logical = RoutePlan(at(0), at(1), {"a": (route(),)})
    physical, = PlanCompiler(deployment).compile_plans(route_plans=[logical])
    interface = physical.targets["links"][0]["endpoints"][0]["interface"]
    path = deployment / "docker-compose.yml"
    compose = yaml.safe_load(path.read_text())
    compose["services"]["a"]["labels"]["org.seedsecuritylabs.seedemu.meta.net.0.name"] = interface
    path.write_text(yaml.safe_dump(compose))
    with pytest.raises(ValueError, match="interface name collision"):
        tuple(PlanCompiler(deployment).compile_plans(route_plans=[logical]))


def test_custom_compiler_material_remains_readable_without_claiming_backend_support(tmp_path):
    command = BackendInstruction("research.route", "a", {"metric": 2})
    operation = PhysicalOperation(at(1), "install", "a", (command,))
    plan = PhysicalPlan(at(0), at(2), "route", "research", "deployment", {},
                        operations=(operation,), backend_abi="research.v1")
    path = tmp_path / "research.plan"
    save_plans(path, [plan])
    restored, = load_plans(path)
    assert restored == plan
    assert restored.backend_abi == "research.v1"
