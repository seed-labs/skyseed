from datetime import datetime, timedelta, timezone
from skyseed import Scene
from skyseed.pc.physical import PhysicalLayer
from skyseed.pc.link import LinkPlan
from skyseed.pc.network import RoutePlan, RouteOperation, ForwardingRule, RouteDestination
from tools.observation.network import inspection
from tools.observation.network.inspection import write_inspection


def test_inspector_keeps_real_events_tail_and_unknown_mapping(tmp_path, inspect_plans):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scene = Scene()
    scene.create_ground_station("g", latitude_deg=0, longitude_deg=0)
    scene.create_zone("unserved-or-unknown", latitude_deg=10, longitude_deg=0)
    physical = PhysicalLayer(scene)
    rule = ForwardingRule(RouteDestination("node", "g"), "deliver")
    base = RoutePlan(start, start + timedelta(seconds=6), {}, (
        RouteOperation(start + timedelta(seconds=5), "g", "install", rule),))
    delivered = RoutePlan(start, start + timedelta(seconds=8), {}, (
        RouteOperation(start + timedelta(seconds=7), "g", "install", rule),))
    links = LinkPlan(start, base.end_time)
    data = inspect_plans(inspection, physical, [base], [delivered], [links], timestamps=[start])
    assert [frame["timestamp"] for frame in data["frames"]] == [
        (start + timedelta(seconds=seconds)).isoformat() for seconds in (0, 5, 6, 7)]
    assert data["frames"][1]["base"]["g"][0]["action"] == "deliver"
    assert data["frames"][1]["delivery"] == {}
    assert data["frames"][-1]["delivery"]["g"][0]["action"] == "deliver"
    assert data["frames"][-1]["base_held"] and data["frames"][-1]["links_held"]
    assert all(frame["assignments"] is None for frame in data["frames"])
    assert ("endpoint", "unserved-or-unknown") in data["destinations"]
    page = write_inspection(data, tmp_path)
    assert page.is_file() and (tmp_path / "network.json").is_file()


def test_inspector_accepts_equivalent_rule_order_at_segment_boundary(inspect_plans):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    middle = start + timedelta(seconds=5)
    end = start + timedelta(seconds=10)
    scene = Scene()
    scene.create_ground_station("g", latitude_deg=0, longitude_deg=0)
    first_rule = ForwardingRule(RouteDestination("prefix", "10.0.0.0/24"), "deliver")
    second_rule = ForwardingRule(RouteDestination("prefix", "10.1.0.0/24"), "deliver")
    first = RoutePlan(start, middle, {"g": (first_rule, second_rule)})
    second = RoutePlan(middle, end, {"g": (second_rule, first_rule)})
    data = inspect_plans(inspection, PhysicalLayer(scene), [first, second], [first, second],
                           [LinkPlan(start, end)], timestamps=[middle])
    for frame in data["frames"]:
        for branch in ("base", "delivery"):
            assert {rule["destination"]["identity"] for rule in frame[branch]["g"]} == {
                "10.0.0.0/24", "10.1.0.0/24"}


def test_inspector_projects_overlapping_delivery_batches_without_repeating_context(inspect_plans):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    five, seven, end = (start + timedelta(seconds=value) for value in (5, 7, 8))
    scene = Scene()
    for node in ("a", "b"):
        scene.create_ground_station(node, latitude_deg=0, longitude_deg=0)
    bounds = {"south_deg": -5, "north_deg": 5, "west_deg": 170, "east_deg": -170}
    scene.create_zone("dateline", latitude_deg=0, longitude_deg=180, bounds=bounds)
    rule = ForwardingRule(RouteDestination("prefix", "x"), "deliver")
    changed = ForwardingRule(rule.destination, "deliver", data={"version": 2})
    original = RouteOperation(seven, "a", "install", rule)
    first = RoutePlan(start, end, {}, (original,))
    second = RoutePlan(five, end, {}, (RouteOperation(five, "b", "install", rule),
                                      RouteOperation(seven, "a", "replace", changed)), (original,))
    data = inspect_plans(inspection, PhysicalLayer(scene), [RoutePlan(start, end)], [first, second],
                           [LinkPlan(start, end)])
    assert len(data["events"]["delivery"]) == 3
    assert set(data["frames"][1]["delivery"]) == {"b"}
    assert data["frames"][-1]["delivery"]["a"][0]["data"] == {"version": 2}
    assert data["endpoints"][0]["data"]["bounds"] == bounds
