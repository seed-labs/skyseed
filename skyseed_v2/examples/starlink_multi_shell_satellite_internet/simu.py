"""Reuse the explicit Starlink policy against one shared five-Shell world."""
from datetime import timedelta
from examples.starlink_gen1_satellite_internet.simu import (
    create_starlink_gen1_simulator, create_starlink_gen1_planner,
    starlink_gen1_model_descriptors as _shared_descriptors,
)


def create_simulator(emulator, *, zones, settings):
    if settings["sampling_interval_seconds"] != 5 or settings["total_duration_seconds"] != 600:
        raise ValueError("this qualification example uses 5-second samples over 600 seconds")
    simulator = create_starlink_gen1_simulator(emulator, zone_layout_path=zones,
                                              observation_interval=timedelta(seconds=5))
    if not settings["independent_shell_planning"]:
        raise ValueError("this example requires independent Shell planning")
    world = simulator.get_physical_world()
    scopes = {sat.node_id: shell.shell_id for system in world.autonomous_systems
              for shell in system.shells for orbit in shell.orbits for sat in orbit.satellites}
    create_starlink_gen1_planner(simulator, satellite_scopes=scopes,
        ground_station_scope_limits={scope: settings["ground_station_slots_per_shell"]
                                     for scope in settings["shells"]})
    return simulator


def starlink_gen1_model_descriptors():
    descriptors = _shared_descriptors()
    descriptors["ground_station"]["configuration"].update(
        default_ground_station_limit=10, station_capacity="explicit total 10",
        ground_station_scope_limits={scope:2 for scope in ("S1","S2","S3","S4","S5")},
        allocation="independent per scope; preparing and operational share quota; no borrowing")
    descriptors["zone_service"]["configuration"]["identity"] = "network_scope plus geographic zone_id; explicit unserved per scope"
    descriptors["route"]["configuration"]["network_scope"] = "Shell membership; scoped graphs; GS endpoints cannot transit"
    return descriptors
