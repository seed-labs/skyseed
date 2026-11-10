"""Synthetic circular constellation, ground stations and geographic destinations."""
from datetime import datetime, timezone

from skyseed import Scene


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build_scene(*, planes=8):
    scene = Scene(data={"description": "Offline Emulator and P&C example; synthetic constellation"})
    scene.create_constellation(
        "demo", planes=planes, satellites_per_plane=16,
        altitude_km=550, inclination_deg=53, epoch=START,
    )
    scene.create_ground_station_group("ground")
    for name, latitude, longitude in (("Beijing", 40, 116), ("Quito", 0, -78.5), ("London", 51.5, 0)):
        scene.create_ground_station(name, latitude_deg=latitude, longitude_deg=longitude, group="ground")
        scene.create_zone(
            f"{name}-zone", latitude_deg=latitude, longitude_deg=longitude + 8,
            bounds={"south_deg": latitude - 5, "north_deg": latitude + 5,
                    "west_deg": longitude + 2, "east_deg": longitude + 14},
        )
    for name, latitude, longitude in (("Pacific", 20, 160), ("Atlantic", 0, -30), ("CentralAsia", 40, 70)):
        scene.create_zone(
            name, latitude_deg=latitude, longitude_deg=longitude,
            bounds={"south_deg": latitude - 5, "north_deg": latitude + 5,
                    "west_deg": longitude - 6, "east_deg": longitude + 6},
        )
    return scene
