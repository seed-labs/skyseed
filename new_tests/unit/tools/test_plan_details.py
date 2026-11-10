from datetime import datetime, timedelta, timezone
import json

from skyseed import load_plans
from skyseed.pc.physical import LinkCandidate
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkPlan, LinkProperties
from skyseed.pc.network import RoutePlan, RouteOperation, RouteDestination, ForwardingRule
from tools.observation._presentation.plan_details import archive_summary, write_plan_details


def test_restored_display_distinguishes_prior_context_from_new_operations(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = ForwardingRule(RouteDestination("endpoint", "zone"), "pop_deliver", incoming_label=20)
    install = RouteOperation(start + timedelta(seconds=7), "a", "install", rule)
    withdraw = RouteOperation(start + timedelta(seconds=9), "a", "withdraw", rule)
    plan = RoutePlan(start + timedelta(seconds=5), start + timedelta(seconds=10), {}, (withdraw,), (install,))
    details = write_plan_details(tmp_path, kind="route", streams={"delivery": [plan]})
    result, = details["archives"]
    path = tmp_path / "plans" / result["href"]
    assert tuple(load_plans(path)) == (plan,)
    assert result["equivalent"]
    shown, = result["plans"]
    assert [item["kind"] for item in shown["operations"]] == ["withdraw"]
    assert [item["kind"] for item in shown["context"]] == ["install"]
    assert shown["context"][0]["detail"] == "label 20 → pop_deliver"
    assert shown["context"][0]["destination"] == ["endpoint", "zone"]
    assert shown["context"][0]["nodes"] == ["a"]
    # Comparison is with actual saved values, not just matching record counts.
    different = RoutePlan(plan.start_time, plan.end_time)
    assert not archive_summary(path, [different], write_seconds=0)["equivalent"]


def test_link_details_keep_directional_values_and_an_empty_branch(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candidate = ConnectionCandidate(("a", "b"), {
        "a": LinkCandidate("a", "b"), "b": LinkCandidate("b", "a")})
    link = LinkConfiguration(candidate, LinkProperties(candidate.id, 100, 0.1, 2),
                             LinkProperties(candidate.id, 200, 0.2, 3))
    plan = LinkPlan(start, start + timedelta(seconds=5), {link.id: link})
    details = write_plan_details(tmp_path, kind="link", streams={"base": [plan], "execution": []})
    base, execution = details["archives"]
    assert base["equivalent"] and execution["equivalent"]
    assert tuple(load_plans(tmp_path / "plans" / base["href"])) == (plan,)
    assert tuple(load_plans(tmp_path / "plans" / execution["href"])) == ()
    initial, = base["plans"][0]["initial"]
    assert initial["nodes"] == initial["link_id"] == ["a", "b"]
    assert initial["detail"] == "a: 100 bit/s, 0.1 s, loss 2 ppm; b: 200 bit/s, 0.2 s, loss 3 ppm"
    fields = json.loads(initial["fields"])
    assert fields["properties"]["packet_loss_ppm"] == 2
    assert fields["reverse_properties"]["bandwidth_bps"] == 200
    assert execution["plans"] == []
    page = (tmp_path / "plans" / "index.html").read_text()
    assert "/*INSPECTION_DATA*/null" not in page
