"""Real geometric candidates, Link adoption, service assignment and label rules."""
from datetime import datetime, timedelta, timezone
from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, GeometricCandidateModel
from skyseed.pc.physical.earth_geometry import ground_ecef
from skyseed.pc.link import LinkLayer, LinkPlan
from skyseed.pc.network import NetworkLayer, RouteDestination


def test_base_links_feed_gz_while_perturbed_execution_is_empty():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scene = Scene()
    for index, node in enumerate(("a", "b", "c", "d")):
        scene.create_satellite(node, position=ScenePositionSource("test/static", {"longitude_deg": index * 10}))
    scene.create_ground_station("g", latitude_deg=0, longitude_deg=0)
    scene.create_zone("z", latitude_deg=0, longitude_deg=30)
    class StaticPositions:
        def compute_positions(self, scene, satellite_ids, timestamp):
            return {node: NodeState(ground_ecef(0, scene.get_node(node).position_source.data["longitude_deg"], 550000))
                    for node in satellite_ids}
    class Outage:
        def perturb(self, plan):
            return LinkPlan(plan.start_time, plan.end_time)
    physical = PhysicalLayer(scene)
    physical.set_position_model(StaticPositions)
    physical.set_candidate_model(GeometricCandidateModel, model_options={"max_isl_distance_m": 1_500_000})
    link = LinkLayer(physical)
    link.set_selection_hook(None)
    link.set_perturbation_hook(Outage)
    network = NetworkLayer(link)
    plan = network.generate_plans(start, start + timedelta(seconds=10))[0]
    assert plan.state_at(start) == {}  # Default Link setup takes five seconds.
    rules = plan.state_at(start + timedelta(seconds=5))
    destination = RouteDestination("endpoint", "z")
    node, label, path = "g", None, ["g"]
    for _ in range(5):
        key = ("destination", "endpoint", "z") if label is None else ("label", label)
        rule = next(rule for rule in rules[node] if rule.key == key)
        assert rule.destination == destination
        if rule.action == "pop_deliver":
            break
        label = rule.outgoing_label
        node = rule.exit.peer
        path.append(node)
    assert path == ["g", "b", "c", "d"]
    assert network.service_assignments_at(start + timedelta(seconds=5))["z"] == "d"
    assert link.generate_plan(start, plan.end_time).end_links == {}
    assert network.base_plans[0].state_at(start + timedelta(seconds=5)) == rules
