"""Run the canonical Skyseed v2 Slice / Difference main chain."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.complete_static_emulator.emu import build_emulator
from seedemu.layers import Base
from skyseed_v2.configuration.simulator import load_zone_layout_definition
from skyseed_v2.shared import NodeId
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.simulator import (
    AccumulatedDeadbandParameterPlanningModel,
    CircularOrbitPositionModel,
    ConnectionKind,
    DefaultCoordinationPolicy,
    DependencyAwareForwardingPlanningModel,
    GeometricConnectionModel,
    GroundStationRouteTarget,
    HopCountRouteModel,
    MidpointConnectionPlanningModel,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    ZoneRouteTarget,
)
from skyseed_v2.emulator_to_simulator import create_simulator_static_input

# User-adjustable deterministic simulation parameters.
DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_END_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_SLICE_INTERVAL = timedelta(seconds=10)
UNBOUNDED_TIMESTAMPS_TO_DISPLAY = 4
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
ZONE_QUERY_POINTS = (
    ("single", 34.0522, -118.2437),
    ("overlap", 40.7128, -74.0060),
)


def main() -> None:
    emulator = build_emulator()

    # Emulator-to-Simulator input adaptation in this external assembly entrypoint.
    base = emulator.getLayer("Base")
    if not isinstance(base, Base):
        raise TypeError("complete example Base layer must be a Base")
    skyseed_autonomous_systems = get_skyseed_autonomous_systems(base)
    simulator_input = create_simulator_static_input(skyseed_autonomous_systems)

    # Simulator construction and static topology materialization.
    timeline = SimulationTimeline(
        start_time=DEFAULT_START_TIME,
        end_time=DEFAULT_END_TIME,
        slice_interval=DEFAULT_SLICE_INTERVAL,
    )
    definition = SimulationDefinition(
        static_input=simulator_input,
        timeline=timeline,
    )
    simulator = Simulator(definition)
    # YAML is the standard path for layouts of this size. The same immutable
    # ZoneLayoutDefinition may also be assembled programmatically for small uses.
    zone_layout_definition = load_zone_layout_definition(ZONE_LAYOUT_FILE)
    topology = simulator.create_topology(
        zone_layout_definition=zone_layout_definition,
    )

    position_model = CircularOrbitPositionModel()
    connection_model = GeometricConnectionModel(
        maximum_isl_distance_m=5_000_000.0,
        ground_station_minimum_elevation_deg=0.0,
        maximum_ground_station_access_count=1,
        ground_station_maximum_distance_m=None,
        zone_coverage_minimum_elevation_deg=30.0,
        bandwidth_bps_by_kind={
            ConnectionKind.INTER_SATELLITE: 1_000_000_000,
            ConnectionKind.GROUND_STATION_ACCESS: 500_000_000,
        },
        packet_loss_ppm_by_kind={
            ConnectionKind.INTER_SATELLITE: 100,
            ConnectionKind.GROUND_STATION_ACCESS: 200,
        },
        propagation_speed_m_per_s=299_792_458.0,
    )
    route_model = HopCountRouteModel()
    slicer = simulator.create_slicer(
        position_model=position_model,
        connection_model=connection_model,
        route_model=route_model,
    )

    satellite_id = NodeId(65001, "satellite", "satellite-0")
    ground_station_id = NodeId(65001, "ground-station", "gs-new-york")
    autonomous_system = topology.get_autonomous_system(65001)
    shell = topology.get_shell(65001, "shell-1")
    orbit = topology.get_orbit(65001, "shell-1", "orbit-1")
    satellite = orbit.get_satellite(satellite_id)
    ground_station = autonomous_system.get_ground_station(ground_station_id)

    assert topology.get_satellite(satellite_id) is satellite
    assert topology.get_ground_station(ground_station_id) is ground_station
    print(
        "created Simulator static topology: "
        f"AS{autonomous_system.asn}/{shell.shell_id}/{orbit.orbit_id}/"
        f"{satellite.satellite_id}, {ground_station.ground_station_id}"
    )
    timestamps = timeline.iter_timestamps()
    if not timeline.is_bounded:
        timestamps = islice(timestamps, UNBOUNDED_TIMESTAMPS_TO_DISPLAY)
    print(
        "timeline timestamps: "
        + ", ".join(timestamp.isoformat() for timestamp in timestamps)
    )
    for label, latitude_deg, longitude_deg in ZONE_QUERY_POINTS:
        matches = topology.find_zones(latitude_deg, longitude_deg)
        authoritative = topology.resolve_zone(latitude_deg, longitude_deg)
        print(
            f"{label} physical matches: "
            f"{', '.join(zone.zone_id for zone in matches)}; "
            f"authoritative Zone: {authoritative.zone_id}"
        )

    slice_zero = slicer.get_slice(0)
    first_slice, second_slice = slicer.create_slices(2)
    initial_difference = slicer.create_difference(0, 1)
    next_difference = slicer.create_difference(1, 2)
    planner = simulator.create_planner(
        connection_model=MidpointConnectionPlanningModel(
            minimum_dwell=timedelta(seconds=5),
        ),
        parameter_model=AccumulatedDeadbandParameterPlanningModel(
            bandwidth_bps_threshold=10_000_000,
            delay_us_threshold=5,
            packet_loss_ppm_threshold=50,
        ),
        forwarding_model=DependencyAwareForwardingPlanningModel(),
        coordination_policy=DefaultCoordinationPolicy(),
    )
    initial_plan = planner.get_plan()
    first_satellite_id = satellite_id
    second_satellite_id = NodeId(65001, "satellite", "satellite-1")
    first_satellite_position = first_slice.get_position(first_satellite_id)
    ground_station_position = first_slice.get_position(ground_station_id)
    isl = first_slice.connection_slice.get_connection_for_endpoints(
        ConnectionKind.INTER_SATELLITE,
        first_satellite_id,
        second_satellite_id,
    )
    access = first_slice.connection_slice.get_connection_for_endpoints(
        ConnectionKind.GROUND_STATION_ACCESS,
        ground_station_id,
        first_satellite_id,
    )
    served_zone = "new-york-metro"
    service_satellite = (
        first_slice.connection_slice.get_service_satellite(served_zone)
    )
    ground_target = GroundStationRouteTarget(ground_station_id)
    zone_target = ZoneRouteTarget(served_zone)
    ground_next_hop = first_slice.route_slice.get_next_hop(
        second_satellite_id, ground_target
    )
    zone_next_hop = first_slice.route_slice.get_next_hop(
        ground_station_id, zone_target
    )
    path = slicer.resolve_path(
        first_slice.slice_id,
        second_satellite_id,
        ground_target,
    )
    try:
        first_slice.connection_slice.get_service_satellite("americas")
    except KeyError:
        unserved = "americas"
    else:  # pragma: no cover - canonical geometry is deterministic
        raise AssertionError("canonical americas Zone must be unserved")

    print(
        f"slice 0: id={slice_zero.slice_id}, timestamp={slice_zero.timestamp}, "
        f"positions={len(slice_zero.positions)}"
    )
    print(
        "real Slice timestamps: "
        f"{first_slice.timestamp.isoformat()}, "
        f"{second_slice.timestamp.isoformat()}"
    )
    print(
        "Position samples: "
        f"{first_satellite_id.value}={first_satellite_position.geographic}; "
        f"{ground_station_id.value}={ground_station_position.geographic}"
    )
    print(
        "effective Connections: "
        f"ISL={isl.connection_id!r}; GS access={access.connection_id!r}"
    )
    print(
        f"Zone service: {served_zone} -> {service_satellite.value}; "
        f"legally unserved: {unserved}"
    )
    print(
        f"next hops: G={ground_next_hop.value}; Z={zone_next_hop.value}"
    )
    print("resolved path: " + " -> ".join(node.value for node in path))
    initial_connection = initial_difference.added_connections[0]
    initial_assignment = (
        initial_difference.zone_service_assignment_patches[0]
    )
    initial_route = initial_difference.route_patches[0]
    print(
        "Difference 0->1: "
        f"added_connections={len(initial_difference.added_connections)}, "
        "connection_parameter_patches="
        f"{len(initial_difference.connection_parameter_patches)}, "
        "zone_service_assignment_patches="
        f"{len(initial_difference.zone_service_assignment_patches)}, "
        f"route_patches={len(initial_difference.route_patches)}"
    )
    print(
        "Difference 0->1 samples: "
        f"Connection={initial_connection.connection_id!r}; "
        f"Zone={initial_assignment.zone_id}->"
        f"{initial_assignment.service_satellite_id.value}; "
        f"Route={initial_route.route_key!r}->"
        f"{initial_route.next_hop.value}"
    )
    if next_difference.is_empty:
        next_change = "legal empty Difference"
    elif next_difference.connection_parameter_patches:
        next_change = repr(
            next_difference.connection_parameter_patches[0]
        )
    elif next_difference.added_connections:
        next_change = repr(next_difference.added_connections[0])
    elif next_difference.removed_connection_ids:
        next_change = repr(next_difference.removed_connection_ids[0])
    elif next_difference.zone_service_assignment_patches:
        next_change = repr(
            next_difference.zone_service_assignment_patches[0]
        )
    else:
        next_change = repr(next_difference.route_patches[0])
    print(
        "Difference 1->2: "
        f"empty={next_difference.is_empty}, "
        f"added_connections={len(next_difference.added_connections)}, "
        f"removed_connections={len(next_difference.removed_connection_ids)}, "
        "connection_parameter_patches="
        f"{len(next_difference.connection_parameter_patches)}, "
        "zone_service_assignment_patches="
        f"{len(next_difference.zone_service_assignment_patches)}, "
        f"route_patches={len(next_difference.route_patches)}; "
        f"evidence={next_change}"
    )
    print(
        "initial Plan coverage: "
        f"{initial_plan.start_slice_id}->{initial_plan.end_slice_id}; "
        f"Connection={len(initial_plan.connection_transitions)}, "
        f"Parameter={len(initial_plan.parameter_transitions)}, "
        f"Forwarding={len(initial_plan.forwarding_transitions)}, "
        f"Precedence={len(initial_plan.forwarding_precedences)}, "
        f"times={len(initial_plan.temporal_index)}"
    )
    initial_decision = planner.get_planning_decisions()[0]
    print(
        "planning evidence: "
        f"{initial_decision.decision} by {initial_decision.decided_by}: "
        f"{initial_decision.reason}"
    )

    third_slice = slicer.create_next_slice()
    updated_plan = planner.synchronize()
    print(
        "incremental Plan synchronization: "
        f"new_slice={third_slice.slice_id}, "
        f"coverage={updated_plan.start_slice_id}->"
        f"{updated_plan.end_slice_id}, "
        f"consumed_differences={len(planner.get_consumed_differences())}, "
        f"Connection={len(updated_plan.connection_transitions)}, "
        f"Parameter={len(updated_plan.parameter_transitions)}, "
        f"Forwarding={len(updated_plan.forwarding_transitions)}, "
        f"Precedence={len(updated_plan.forwarding_precedences)}"
    )


if __name__ == "__main__":
    main()
