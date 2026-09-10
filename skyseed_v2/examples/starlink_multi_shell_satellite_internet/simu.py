"""Reuse the explicit Starlink policy against one shared five-Shell world."""
from datetime import timedelta
from examples.starlink_gen1_satellite_internet.simu import (
    create_starlink_gen1_simulator, create_starlink_gen1_planner,
    starlink_gen1_model_descriptors,
)


def create_simulator(emulator, *, zones, settings):
    if settings["sampling_interval_seconds"] != 5 or settings["total_duration_seconds"] != 600:
        raise ValueError("this qualification example uses 5-second samples over 600 seconds")
    simulator = create_starlink_gen1_simulator(emulator, zone_layout_path=zones,
                                              observation_interval=timedelta(seconds=5))
    create_starlink_gen1_planner(simulator)
    return simulator
