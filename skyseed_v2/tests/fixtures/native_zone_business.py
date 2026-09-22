"""Small real GS↔Zone workload; uses the Example policy and automatic Traffic."""
from dataclasses import replace
from pathlib import Path
import sys
from skyseed_v2 import adapters, emulator, simulator
from skyseed_v2.experiment import ExperimentController
from skyseed_v2.backend.linux.environment import NativeRoutingEnvironment
from skyseed_v2.simulator import physical
from examples.Sky06_s4_native_ospf.edge_routes import EdgeRoutes
from tests.fixtures.native_config import FixtureConfigRenderer
from tests.fixtures.native_ospf import (
    build_small, create_small_simulator, ScheduledPhysicalLinks, A, C, G, H, START,
)


class BusinessLinks(ScheduledPhysicalLinks):
    def compute_observation(self, world, frame, request):
        result = super().compute_observation(world, frame, request)
        return replace(result, zone_service_candidates=tuple(
            physical.ZoneServiceCandidate(z.zone_id, node)
            for z in world.get_zone_layout().zones for node in (A, C)))


class BusinessService:
    def assign_zone_service(self, world, candidates, previous, *, timestamp):
        seconds = (timestamp - START).total_seconds()
        node = C if seconds < 120 or seconds >= 300 else A if seconds < 240 else None
        return simulator.ZoneServicePlanningResult(tuple(
            simulator.ZoneServiceAssignment(z.zone_id, node) for z in world.get_zone_layout().zones))


def generate(output, daemon, *, tools_enabled=True, delivery_ends=None,
             traffic_enabled=True, maximum_probes=4, duration=390, readiness=False):
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    static = adapters.create_simulator_static_input((system,))
    environment = NativeRoutingEnvironment(n.node_id for n in static.list_nodes())
    policy = FixtureConfigRenderer(daemon)
    for satellite in static.list_satellites():
        emulator.configure_native_software(system.get_satellite(satellite.satellite_id),
            daemon=daemon, configuration=policy(satellite.node_id, environment))
    simulation = create_small_simulator(static,
        EdgeRoutes(), duration=duration, service=True, route_demands=None,
        physical_links=BusinessLinks(service=True), zone_service=BusinessService())
    from examples.Sky06_s4_native_ospf.readiness import prepare
    if readiness and daemon != 'bird':
        raise ValueError('the Example preparation policy is BIRD-only')
    output = ExperimentController(network, simulation, output, entry_path=Path(__file__),
        preparation=prepare if readiness else None,
        forwarding_backend='linux', traffic_enabled=traffic_enabled, traffic_telemetry_enabled=False,
        traffic_options={'maximum_persistent_probes': maximum_probes, 'maximum_activation_window_probes': 0,
                         'persistent_interval_seconds': 2.0},
        internet_map_enabled=False, deploy_parallel=2, runtime_workers=2, delivery_ends=delivery_ends,
        scenario_parameters={'daemon': daemon, 'stable_addresses': {n.value: a for n, a in environment.node_addresses.items()}}).generate()
    if tools_enabled:
        import tools
        tools.write_experiment_tools(output, physical_world=simulation.get_physical_world())
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--daemon', choices=('frr', 'bird'), required=True)
    args = parser.parse_args()
    generate(args.output, args.daemon)
