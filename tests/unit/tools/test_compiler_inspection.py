from datetime import datetime, timedelta, timezone

import pytest

from skyseed.pc.compiler import BackendInstruction, PhysicalOperation, PhysicalPlan
from tools.observation.compiler.inspection import inspection_data, write_inspection


def targets():
    return {"nodes": ({"node_id": "A", "service": "container-a"},
                      {"node_id": "B", "service": "container-b"}),
            "links": ({"link_id": ("A", "B"), "endpoints": (
                {"node_id": "A", "interface": "ab", "address": "100.64.0.0/31", "port": 1, "mac": "02:00:00:00:00:01"},
                {"node_id": "B", "interface": "ba", "address": "100.64.0.1/31", "port": 2, "mac": "02:00:00:00:00:02"},
            )},), "destinations": ()}


def test_compiler_view_preserves_exact_time_dependencies_context_and_hex_fields(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    instruction = BackendInstruction("bpf.rule", "A", {"match": ("label", 20), "outgoing_label": 30,
                                                       "unavailable": "drop", "key_hex_le": "40000000",
                                                       "kind": "install", "action": "swap", "port": 1,
                                                       "destination": ("endpoint", "zone"),
                                                       "prefix": "198.19.0.1/32", "resource": 'link:["A","B"]'})
    operation = PhysicalOperation(start + timedelta(seconds=7), "install", "A", (instruction,))
    plan = PhysicalPlan(start, start + timedelta(seconds=8), "route", "bpf", "deployment",
                        targets(), (), (), (operation,))
    record, = inspection_data([plan])["plans"]
    assert record["operations"] == []
    assert record["context_operations"][0]["timestamp"] == "2026-01-01T00:00:07+00:00"
    assert record["context_operations"][0]["instructions"][0]["parameters"]["match"] == ["label", 20]
    diagram = record["views"]["context_operations"][0]["diagram"]
    assert diagram["match"] == ["label", 20] and diagram["outgoing_label"] == 30
    assert diagram["exit"]["local"]["interface"] == "ab"
    assert diagram["exit"]["peer"]["node"] == "B"
    assert record["views"]["operations"] == []
    page = write_inspection([plan], tmp_path)
    assert page.is_file() and (tmp_path / "compilation.json").is_file()


def test_link_view_keeps_direction_differences_and_builds_commands_from_exact_targets():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    target = targets()["links"][0]
    instructions = (
        BackendInstruction("veth.create", None, target),
        BackendInstruction("netem.replace", "A", {"interface": "ab", "delay_us": 4000,
                                                   "rate_bps": 1000000, "loss_percent": 0.25, "drop": False}),
        BackendInstruction("netem.replace", "B", {"interface": "ba", "delay_us": 8000,
                                                   "rate_bps": None, "loss_percent": 100, "drop": True}),
    )
    operation = PhysicalOperation(start, "establish", ("A", "B"), instructions)
    plan = PhysicalPlan(start, start + timedelta(seconds=1), "link", "bpf", "deployment",
                        targets(), operations=(operation,))
    view = inspection_data([plan])["plans"][0]["views"]["operations"][0]
    assert [(d["node"], d["delay_us"], d["drop"]) for d in view["diagram"]["directions"]] == [
        ("A", 4000, False), ("B", 8000, True)]
    assert view["steps"][0]["commands"][0]["text"] == "ip link add ab type veth peer name ba"
    assert view["steps"][1]["commands"][0]["text"] == "tc qdisc replace dev ab root netem delay 4000us rate 1000000bit loss 0.25%"
    assert view["steps"][2]["commands"][0]["text"] == "tc qdisc replace dev ba root netem delay 8000us loss 100%"


@pytest.mark.parametrize("kind", ["install", "withdraw"])
def test_linux_routes_show_alternative_conditions_and_withdrawal_does_not_install(kind):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parameters = {"kind": kind, "action": "forward", "prefix": "198.19.0.1/32",
                  "destination": ("endpoint", "zone"), "resource": 'link:["A","B"]',
                  "interface": "ab", "gateway": "100.64.0.1", "table": 254,
                  "protocol": 253, "priority": 1, "unavailable": "blackhole"}
    operation = PhysicalOperation(start, kind, "A", (BackendInstruction("linux.route", "A", parameters),))
    plan = PhysicalPlan(start, start + timedelta(seconds=1), "route", "linux", "deployment",
                        targets(), operations=(operation,))
    view = inspection_data([plan])["plans"][0]["views"]["operations"][0]
    assert view["diagram"]["match"] == ["prefix", "198.19.0.1/32"]
    commands = view["steps"][0]["commands"]
    if kind == "withdraw":
        assert len(commands) == 1
        assert commands[0]["text"] == "ip route del 198.19.0.1/32 via 100.64.0.1 dev ab table 254 proto 253 metric 1"
    else:
        assert [item["condition"] for item in commands] == [
            "出口链路可用时 / If exit is available", "出口链路缺失时 / If exit is absent"]
        assert commands[1]["text"] == "ip route replace blackhole 198.19.0.1/32 table 254 proto 253 metric 1"


def test_unknown_backend_instruction_remains_visible_without_invented_commands():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    instruction = BackendInstruction("research.action", "A", {"research_value": 7})
    operation = PhysicalOperation(start, "install", "A", (instruction,))
    plan = PhysicalPlan(start, start + timedelta(seconds=1), "route", "bpf", "deployment",
                        targets(), operations=(operation,))
    record = inspection_data([plan])["plans"][0]
    view = record["views"]["operations"][0]
    assert view["diagram"] is None and view["steps"][0]["commands"] == []
    assert record["operations"][0]["instructions"][0]["parameters"] == {"research_value": 7}
