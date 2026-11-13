"""Precompute the S4 plans and save optional drawing observations."""
from datetime import timedelta
from pathlib import Path

from skyseed import Scene, PlanningControl, PlanCompiler, save_plans
from skyseed.pc.link import PeriodicLinkPlanModel
from tools.observation.plan_views import save_context
from .experiment.ut.routing import TerminalRoutingModel
from .experiment.perturbation import RegionalGroundOutage
from .experiment.configuration import save_configuration


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
    scene = Scene.load(output / "scene/scene.scene")
    pc = PlanningControl(scene)
    physical = pc.get_layer("physical-layer")
    link = pc.get_layer("link-layer")
    network = pc.get_layer("network-layer")

    # Optional Tool observations: preserve decisions that plans do not contain.
    link_evaluations = []
    route_evaluations = []
    link.set_plan_model(PeriodicLinkPlanModel, model_options={"observer": link_evaluations.append})
    # Example extensions: UT ingress classification and a regional GS outage.
    network.set_routing_hook(TerminalRoutingModel, model_options={
        "ground_stations_per_zone": 3, "observer": route_evaluations.append})
    link.set_perturbation_hook(RegionalGroundOutage)

    duration = 600
    start = scene.get_shells()[0].epoch
    end = start + timedelta(seconds=duration)
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(seconds=60), end)
        # Consume the returned iterators to carry out each planning request.
        list(pc.generate_route_plans(start=cursor, end=stop))
        list(pc.generate_link_plans(start=cursor, end=stop))
        cursor = stop

    logical = output / "plans/logical"
    save_plans(logical / "base_link.plan", link.base_plans)
    save_plans(logical / "execution_link.plan", link.execution_plans)
    save_plans(logical / "base_route.plan", network.base_plans)
    save_plans(logical / "delivery_route.plan", network.delivery_plans)

    compiler = PlanCompiler(output / "seed/deployment", forwarding_backend="bpf")
    compiled = compiler.compile_plans(link_plans=link.execution_plans, route_plans=network.delivery_plans)
    save_plans(output / "plans/physical/bpf.plan", compiled)

    # Optional display data. Reopening views later never calls the models.
    save_context(
        output / "observations/plans.json", physical,
        link_plans=[*link.base_plans, *link.execution_plans],
        route_plans=[*network.base_plans, *network.delivery_plans],
        link_evaluations=link_evaluations, route_evaluations=route_evaluations,
        physical_times=[plan.start_time for plan in link.base_plans],
        position_times=[start + timedelta(seconds=value) for value in range(0, duration + 6, 5)],
    )
    save_configuration(output, pc, duration, route_evaluations)
    print(f"Plans saved: {output / 'plans'}; run view.py to inspect them.")
