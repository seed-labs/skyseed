from datetime import datetime, timedelta, timezone
import subprocess
import sys

from seedemu import Routing, Docker
from skyseed import SkyEmulator, SkyBase, PlanCompiler, save_plans
from skyseed.pc.network import RoutePlan, RouteDestination, ForwardingRule, LogicalExit


def test_real_seed_deployment_compiles_and_restores_physical_targets(tiny_scene, tmp_path):
    emulator = SkyEmulator()
    base = SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    autonomous_system.loadScene(tiny_scene)
    autonomous_system.createNetwork("lan", "10.150.0.0/24")
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    emulator.getNode("G1").joinNetwork("lan", "10.150.0.1")
    emulator.render()
    deployment = tmp_path / "deployment"
    emulator.compile(Docker(internetMapEnabled=False), str(deployment))
    satellite = tiny_scene.nodes[0].node_id
    rule = ForwardingRule(RouteDestination("endpoint", "zone"), "push",
                          LogicalExit(("G1", satellite), "G1"), outgoing_label=20)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    plan = RoutePlan(start, start + timedelta(seconds=1), {"G1": (rule,)})
    compiler = PlanCompiler(deployment)
    physical, = compiler.compile_plans(route_plans=[plan])
    nodes = {node["node_id"]: node for node in physical.targets["nodes"]}
    assert nodes["G1"]["static_addresses"] == (("net_150_lan", "10.150.0.1"),)
    assert nodes[satellite]["static_addresses"] == ()
    assert nodes[satellite]["service"] != satellite
    path = tmp_path / "physical.plan"
    save_plans(path, [physical])
    code = """
import sys
from skyseed import load_plans
plan, = load_plans(sys.argv[1])
assert plan.stream == 'route'
assert plan.initial_operations[0].instructions[0].parameters['outgoing_label'] == 20
assert 'seedemu' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code, str(path)], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
