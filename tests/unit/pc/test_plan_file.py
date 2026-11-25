from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pytest

from skyseed import save_plans, load_plans
from skyseed.pc.physical import LinkCandidate
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkPlan, LinkOperation
from skyseed.pc.network import RoutePlan, RouteOperation, RouteDestination, LogicalExit, ForwardingRule


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def connection():
    identity = ("a", "b")
    reports = {
        "a": LinkCandidate("a", "b", distance_m=300000, propagation_delay_s=0.001,
                           data={"samples": (1, 2), "mapping": {"tuple": [3]}, "at": START}),
        "b": LinkCandidate("b", "a", propagation_delay_s=0.002),
    }
    return LinkConfiguration(ConnectionCandidate(identity, reports),
                             LinkProperties(identity, 1000, 0.001, data={"radio": ["x", 2]}),
                             LinkProperties(identity, 500, 0.002, 10))


def test_link_roundtrip_retains_initial_state_directional_evidence_and_slice(tmp_path):
    value = connection()
    updated = replace(value, reverse_properties=replace(value.reverse_properties, bandwidth_bps=250))
    plan = LinkPlan(START, START + timedelta(seconds=10), {value.id: value}, (
        LinkOperation(START + timedelta(seconds=3), "update", value.id, updated),
        LinkOperation(START + timedelta(seconds=8), "withdraw", value.id)))
    path = tmp_path / "nested/links.plan"
    save_plans(path, iter((plan,)))
    restored, = load_plans(path)
    assert restored == plan
    assert restored.initial_links[value.id].candidate.reports["a"].data["samples"] == (1, 2)
    assert restored.initial_links[value.id].candidate.reports["a"].data["at"] == START
    at_four = restored.state_at(START + timedelta(seconds=4))[value.id]
    assert at_four.properties_from("a").bandwidth_bps == 1000
    assert at_four.properties_from("b").bandwidth_bps == 250
    assert restored.state_at(START + timedelta(seconds=8)) == {}
    # A saved excerpt can be read without its preceding plan.
    excerpt = restored.slice(START + timedelta(seconds=4), START + timedelta(seconds=9))
    save_plans(path, [excerpt])
    assert tuple(load_plans(path)) == (excerpt,)


def test_route_batches_keep_new_operations_separate_from_old_tail(tmp_path):
    destination = RouteDestination("endpoint", "zone")
    label_rule = ForwardingRule(destination, "swap", LogicalExit(("a", "b"), "a"), 20, 30,
                                data={"cost": 2.5, "choices": ("b",)})
    updated = replace(label_rule, outgoing_label=31)
    at_seven = START + timedelta(seconds=7)
    install = RouteOperation(at_seven, "a", "install", label_rule)
    first = RoutePlan(START, START + timedelta(seconds=8), {}, (install,))
    ip_rule = ForwardingRule(RouteDestination("prefix", "10.0.0.0/24"), "deliver")
    second = RoutePlan(START + timedelta(seconds=5), START + timedelta(seconds=10), {}, (
        RouteOperation(START + timedelta(seconds=5), "b", "install", ip_rule),
        RouteOperation(at_seven, "a", "replace", updated)), (install,))
    path = tmp_path / "routes.plan"
    save_plans(path, (value for value in (first, second)))
    restored = tuple(load_plans(path))
    assert restored == (first, second)
    assert restored[1].context_operations == (install,)
    assert len(restored[1].operations) == 2
    assert restored[1].state_at(START + timedelta(seconds=6)) == {"b": (ip_rule,)}
    assert restored[1].state_at(at_seven)["a"] == (updated,)
    # Saving only the second batch still preserves the prior installation it needs.
    save_plans(path, [second])
    only, = load_plans(path)
    assert only.state_at(at_seven) == second.state_at(at_seven)
    # A later excerpt starts with both the adopted label rule and the IP rule.
    excerpt = second.slice(START + timedelta(seconds=8), second.end_time)
    save_plans(path, [excerpt])
    only, = load_plans(path)
    assert only.initial_rules == {"a": (updated,), "b": (ip_rule,)}
    assert only.operations == () and only.context_operations == ()


def test_empty_and_one_shot_sequences_are_supported(tmp_path):
    path = tmp_path / "empty.plan"
    save_plans(path, iter(()))
    assert list(load_plans(path)) == []
    first = LinkPlan(START, START + timedelta(seconds=1))
    consumed = []

    def sequence():
        for index in range(3):
            consumed.append(index)
            yield replace(first, start_time=START + timedelta(seconds=index),
                          end_time=START + timedelta(seconds=index + 1))

    save_plans(path, sequence())
    assert consumed == [0, 1, 2]
    assert [plan.start_time for plan in load_plans(path)] == [START + timedelta(seconds=i) for i in range(3)]


@pytest.mark.parametrize("failure", ["mixed", "unsupported-data", "producer", "none"])
def test_failed_save_preserves_previous_complete_file(tmp_path, failure):
    path = tmp_path / "plans.plan"
    original = LinkPlan(START, START + timedelta(seconds=1))
    save_plans(path, [original])
    before = path.read_bytes()

    def values():
        if failure == "none":
            yield None
            return
        yield original
        if failure == "mixed":
            yield RoutePlan(START, START + timedelta(seconds=1))
        elif failure == "unsupported-data":
            value = connection()
            value = replace(value, properties=replace(value.properties, data={"live": object()}))
            yield LinkPlan(START, START + timedelta(seconds=1), {value.id: value})
        else:
            raise RuntimeError("producer failed")

    with pytest.raises((TypeError, RuntimeError)):
        save_plans(path, values())
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("damage", ["version", "truncated-json", "missing-end", "wrong-count"])
def test_incomplete_or_unsupported_files_report_the_path(tmp_path, damage):
    path = tmp_path / "damaged.plan"
    save_plans(path, [LinkPlan(START, START + timedelta(seconds=1))])
    lines = path.read_text().splitlines()
    if damage == "version":
        header = json.loads(lines[0])
        header["version"] = 999
        lines[0] = json.dumps(header)
    elif damage == "truncated-json":
        lines[1] = lines[1][:20]
    elif damage == "missing-end":
        lines.pop()
    else:
        lines[-1] = '{"end": 2}'
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="damaged.plan"):
        list(load_plans(path))
