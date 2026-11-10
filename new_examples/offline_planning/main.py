"""Generate deployment files, reusable P&C plans and inspectors without running the network."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
from time import perf_counter

from seedemu import Docker, Routing
from skyseed import SkyBase, SkyEmulator, PlanningControl, PlanCompiler, save_plans, load_plans
from skyseed.pc.link import PeriodicLinkPlanModel
from skyseed.pc.network import NetworkLayer, GroundStationZoneRoutingModel, FixedDelayDeliverySchedulingModel
from tools.observation._presentation.plan_details import write_plan_details

from .models import DownlinkLossModel
from .scene import START, build_scene
from .visualization import write_visualizations


def build_emulator(scene):
    emulator = SkyEmulator()
    base = SkyBase()
    satellite_as = base.createSkyAutonomousSystem(150)
    satellite_as.loadScene(scene)
    # Real static access networks supply deployment addresses to PlanCompiler.
    # Satellite-facing links are described later by PhysicalPlans.
    for index, station in enumerate(scene.get_ground_stations()):
        name = f"access{index}"
        satellite_as.createNetwork(name, f"10.150.{index}.0/24")
        satellite_as.getNode(station.node_id).joinNetwork(name, f"10.150.{index}.254")
        satellite_as.createHost(f"client{index}").joinNetwork(name, f"10.150.{index}.10")
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    return emulator


def configure_network(network, observations, *, label_forwarding=True):
    network.set_routing_hook(GroundStationZoneRoutingModel, model_options={
        "label_forwarding": label_forwarding, "observer": observations.append,
    })
    delays = {node.node_id: timedelta(milliseconds=200)
              for index, node in enumerate(network.scene.get_satellites()) if index % 2 == 0}
    network.set_delivery_scheduling_hook(FixedDelayDeliverySchedulingModel, model_options={"node_delays": delays})


def generate(output, *, planes=8, seconds=60):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    stage_seconds = {}
    begun = perf_counter()
    scene = build_scene(planes=planes)
    scene.dump(output / "scene.scene")
    emulator = build_emulator(scene)
    emulator.dump(str(output / "emulator.bin"))
    emulator.render()
    emulator.compile(Docker(internetMapEnabled=False), str(output / "deployment"))
    stage_seconds["scene_and_emulator"] = perf_counter() - begun

    pc = PlanningControl(scene)
    link = pc.get_layer("link-layer")
    network = pc.get_layer("network-layer")
    link_observations, route_observations, ip_observations = [], [], []
    link.set_plan_model(PeriodicLinkPlanModel, model_options={"observer": link_observations.append})
    link.set_perturbation_hook(DownlinssModel)
    configure_network(network, route_observations)
    # A separate NetworkLayer reuses the same base links for ordinary IP routes.
    # Linux receives real IP rules, not MPLS rules with their labels removed.
    ip_network = NetworkLayer(link)
    configure_network(ip_network, ip_observations, label_forwarding=False)

    begun = perf_counter()
    end = START + timedelta(seconds=seconds)
    cursor = START
    while cursor < end:
        stop = min(cursor + timedelta(seconds=20), end)
        # These are independent finite requests; the layer retains planning state.
        tuple(pc.generate_route_plans(start=cursor, end=stop))
        tuple(pc.generate_link_plans(start=cursor, end=stop))
        cursor = stop
    stage_seconds["mpls_link_and_route_planning"] = perf_counter() - begun

    begun = perf_counter()
    for plan in network.base_plans:
        ip_network.generate_plans(plan.start_time, plan.end_time)
    stage_seconds["ip_route_planning"] = perf_counter() - begun

    begun = perf_counter()
    write_plan_details(output / "link", kind="link", streams={
        "base": link.base_plans, "execution": link.execution_plans,
    })
    for directory, layer in (("network", network), ("network-ip", ip_network)):
        write_plan_details(output / directory, kind="route", streams={
            "base": layer.base_plans, "delivery": layer.delivery_plans,
        })
    stage_seconds["logical_files_and_details"] = perf_counter() - begun

    for backend, directory in (("bpf", "network"), ("linux", "network-ip")):
        begun = perf_counter()
        compiler = PlanCompiler(output / "deployment", forwarding_backend=backend)
        physical = compiler.compile_plans(
            link_plans=load_plans(output / "link/plans/execution_link.plan"),
            route_plans=load_plans(output / directory / "plans/delivery_route.plan"),
        )
        save_plans(output / "compiler" / f"{backend}.plan", physical)
        stage_seconds[f"{backend}_read_compile_write"] = perf_counter() - begun

    begun = perf_counter()
    write_visualizations(output, pc, ip_network, link_observations, route_observations, ip_observations)
    stage_seconds["visualization"] = perf_counter() - begun
    summary = {
        "nodes": len(scene.nodes), "satellites": len(scene.get_satellites()),
        "ground_stations": len(scene.get_ground_stations()), "access_clients": len(scene.get_ground_stations()),
        "zones": len(scene.endpoints), "start": START.isoformat(), "end": end.isoformat(),
        "link_batches": len(link.execution_plans), "route_batches_per_backend": len(network.delivery_plans),
        "delivery_end": network.delivery_plans[-1].end_time.isoformat(),
        "seconds": stage_seconds,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Open {output.resolve() / 'index.html'}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "output")
    parser.add_argument("--planes", type=int, default=8, help="16 satellites per plane")
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.planes < 1 or args.seconds <= 0:
        parser.error("--planes and --seconds must be positive")
    generate(args.output, planes=args.planes, seconds=args.seconds)


if __name__ == "__main__":
    main()
