"""Public P&C requests through real geometry, both plan branches and plan files."""
from datetime import datetime, timedelta, timezone
import subprocess
import sys

from skyseed import Scene, PlanningControl, save_plans, load_plans
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import NodeState, GeometricCandidateModel
from skyseed.pc.physical.earth_geometry import ground_ecef
from skyseed.pc.link import LinkPlan
from skyseed.pc.network import FixedDelayDeliverySchedulingModel, GroundStationZoneRoutingModel
import pytest


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_pc(*, label_forwarding=True):
    scene = Scene()
    for index, node in enumerate(("a", "b", "c", "d")):
        scene.create_satellite(node, position=ScenePositionSource("test/static", {"longitude_deg": index * 10}))
    scene.create_ground_station("g", latitude_deg=0, longitude_deg=0)
    scene.create_zone("z", latitude_deg=0, longitude_deg=30)

    class Positions:
        calls = 0

        def compute_positions(self, scene, satellite_ids, timestamp):
            self.calls += 1
            return {node: NodeState(ground_ecef(0, scene.get_node(node).position_source.data["longitude_deg"], 550000))
                    for node in satellite_ids}

    class Outage:
        calls = 0

        def perturb(self, plan):
            self.calls += 1
            return LinkPlan(plan.start_time, plan.end_time)

    pc = PlanningControl(scene)
    physical = pc.get_layer("physical-layer")
    physical.set_position_model(Positions)
    physical.set_candidate_model(GeometricCandidateModel, model_options={"max_isl_distance_m": 1_500_000})
    link = pc.get_layer("link-layer")
    link.set_selection_hook(None)
    link.set_perturbation_hook(Outage)
    network = pc.get_layer("network-layer")
    network.set_routing_hook(GroundStationZoneRoutingModel, model_options={"label_forwarding": label_forwarding})
    network.set_delivery_scheduling_hook(FixedDelayDeliverySchedulingModel,
                                        model_options={"node_delays": {"d": timedelta(seconds=7)}})
    return pc


def test_route_first_lookahead_is_reused_by_independent_link_requests(tmp_path):
    pc = make_pc()
    physical = pc.get_layer("physical-layer")
    link = pc.get_layer("link-layer")
    network = pc.get_layer("network-layer")
    assert physical.scene is pc.scene is link.scene is network.scene
    assert network.link is link and link.physical is physical
    assert pc.get_layer("network-layer") is network
    positions = physical.position_model
    routes = tuple(pc.generate_route_plans(start=START, end=START + timedelta(seconds=10),
                                           link_lookahead=timedelta(seconds=5)))
    assert link.base_plans[-1].end_time == START + timedelta(seconds=15)
    assert link.execution_plans == () and link.perturbation_model.calls == 0
    assert network.base_plans[-1].end_time == START + timedelta(seconds=10)
    assert routes[0].end_time == START + timedelta(seconds=12, microseconds=1)
    calls = positions.calls
    links = tuple(pc.generate_link_plans(start=START, end=START + timedelta(seconds=10)))
    assert links[0].end_links == {} and link.perturbation_model.calls == 1
    assert positions.calls == calls
    # Re-reading the same interval does not re-apply perturbation or rebuild models.
    assert tuple(pc.generate_link_plans(start=START, end=START + timedelta(seconds=10))) == links
    assert link.perturbation_model.calls == 1 and physical.position_model is positions
    # Continue from requested end, not the extended route tail.
    routes += tuple(pc.generate_route_plans(start=START + timedelta(seconds=10), end=START + timedelta(seconds=15)))
    links += tuple(pc.generate_link_plans(start=START + timedelta(seconds=10), end=START + timedelta(seconds=15)))
    assert link.perturbation_model.calls == 1
    assert routes[1].context_operations
    assert network.delivery_state_at(START + timedelta(seconds=12))["d"]
    for name, plans in (("links", links), ("routes", routes)):
        path = tmp_path / f"{name}.plan"
        save_plans(path, iter(plans))
        assert tuple(load_plans(path)) == plans
    # A fresh process needs neither the custom model classes nor the original Scene.
    code = """
import sys
from skyseed import load_plans
routes = tuple(load_plans(sys.argv[1]))
links = tuple(load_plans(sys.argv[2]))
assert routes[-1].end_rules['d']
assert all(not plan.end_links for plan in links)
assert not any(name == 'seedemu' or name.startswith('seedemu.') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path / "routes.plan"), str(tmp_path / "links.plan")],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_link_first_and_disabled_route_request_do_not_compute_routes():
    pc = make_pc()
    link = pc.get_layer("link-layer")
    network = pc.get_layer("network-layer")
    tuple(pc.generate_link_plans(start=START, end=START + timedelta(seconds=3)))
    assert network.base_plans == () and link.base_plans[0].end_links == {}
    network.set_precomputed_routing(False)
    assert tuple(pc.generate_route_plans(start=START, end=START + timedelta(seconds=100),
                                         link_lookahead=timedelta(seconds=100))) == ()
    assert link.base_plans[-1].end_time == START + timedelta(seconds=3)
    network.set_precomputed_routing(True)
    routes = tuple(pc.generate_route_plans(start=START, end=START + timedelta(seconds=10)))
    # Pending setup started by the earlier link request still completes at +5.
    assert network.base_plans[0].state_at(START + timedelta(seconds=5))["g"]
    assert routes


@pytest.mark.parametrize("backend", ["bpf", "linux"])
def test_offline_workflow_through_real_deployment_and_both_compilers(tmp_path, backend):
    from seedemu import Docker, Routing
    from skyseed import SkyEmulator, SkyBase, PlanCompiler

    pc = make_pc(label_forwarding=backend == "bpf")
    scene_file = tmp_path / "scene.scene"
    pc.scene.dump(scene_file)
    emulator = SkyEmulator()
    base = SkyBase()
    satellite_as = base.createSkyAutonomousSystem(150)
    satellite_as.loadScene(Scene.load(scene_file))
    satellite_as.createNetwork("access", "10.150.0.0/24")
    satellite_as.getNode("g").joinNetwork("access", "10.150.0.254")
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    emulator.render()
    emulator.compile(Docker(internetMapEnabled=False), str(tmp_path / "deployment"))

    links, routes = [], []
    for start, end in ((0, 2), (2, 6), (6, 10)):
        interval = {"start": START + timedelta(seconds=start), "end": START + timedelta(seconds=end)}
        routes.extend(pc.generate_route_plans(**interval, link_lookahead=timedelta(seconds=1)))
        links.extend(pc.generate_link_plans(**interval))
    # Setup at +5 survives request boundaries; delayed +12 delivery survives +10.
    assert pc.get_layer("link-layer").base_plans[-1].end_time == START + timedelta(seconds=11)
    assert routes[-1].end_time > START + timedelta(seconds=12)
    assert routes[-1].context_operations
    assert all(not plan.end_links for plan in links)
    assert routes[-1].end_rules["g"]  # Routing still uses base links during an execution outage.

    whole = make_pc(label_forwarding=backend == "bpf")
    whole_routes = tuple(whole.generate_route_plans(
        start=START, end=START + timedelta(seconds=10), link_lookahead=timedelta(seconds=1)))
    assert tuple(op for plan in routes for op in plan.operations) == whole_routes[0].operations
    assert routes[-1].end_rules == whole_routes[-1].end_rules
    assert tuple(op for plan in pc.get_layer("link-layer").base_plans for op in plan.operations) == tuple(
        op for plan in whole.get_layer("link-layer").base_plans for op in plan.operations)

    for name, plans in (("links", links), ("routes", routes)):
        save_plans(tmp_path / f"{name}.plan", plans)
        assert tuple(load_plans(tmp_path / f"{name}.plan")) == tuple(plans)
    compiler = PlanCompiler(tmp_path / "deployment", forwarding_backend=backend)
    physical = tuple(compiler.compile_plans(
        link_plans=load_plans(tmp_path / "links.plan"), route_plans=load_plans(tmp_path / "routes.plan")))
    incremental_compiler = PlanCompiler(tmp_path / "deployment", forwarding_backend=backend)
    incremental = []
    for link, route in zip(links, routes, strict=True):
        incremental.extend(incremental_compiler.compile_plans(link_plans=[link], route_plans=[route]))
    assert tuple(incremental) == physical
    compiled_routes = [plan for plan in physical if plan.stream == "route"]
    for logical, compiled in zip(routes, compiled_routes, strict=True):
        assert [op.timestamp for op in compiled.operations] == [op.timestamp for op in logical.operations]
        assert [op.timestamp for op in compiled.context_operations] == [op.timestamp for op in logical.context_operations]
        for operation in compiled.operations:
            instruction, = operation.instructions
            assert instruction.opcode == ("bpf.rule" if backend == "bpf" else "linux.route")
            assert instruction.parameters["unavailable"] == ("drop" if backend == "bpf" else "blackhole")
    assert any(plan.targets["links"] for plan in compiled_routes)
    save_plans(tmp_path / "physical.plan", physical)
    assert tuple(load_plans(tmp_path / "physical.plan")) == physical
    # Reading and recompiling saved inputs is independent of Scene, models and SEED.
    code = """
import sys
from pathlib import Path
from skyseed import PlanCompiler, load_plans
output = Path(sys.argv[1])
compiler = PlanCompiler(output / 'deployment', forwarding_backend=sys.argv[2])
restored = tuple(load_plans(output / 'physical.plan'))
assert tuple(compiler.compile_plans(link_plans=load_plans(output / 'links.plan'),
                                  route_plans=load_plans(output / 'routes.plan'))) == restored
assert 'seedemu' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path), backend],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
