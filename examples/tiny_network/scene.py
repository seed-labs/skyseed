"""A two-satellite, one-GS scene that can be used directly or saved."""
from datetime import datetime, timezone
from skyseed.scene import Scene


def build_scene():
    scene = Scene()
    scene.create_constellation(
        "demo", planes=1, satellites_per_plane=2,
        altitude_km=550, inclination_deg=53,
        epoch=datetime(2026, 1, 1, tzinfo=timezone.utc),
        data={"operator": "example"},
    )
    scene.create_ground_station_group("ground", data={"operator": "example"})
    scene.create_ground_station("G1", latitude_deg=40, longitude_deg=116, group="ground")
    scene.data["description"] = "Small shared scene for static construction"
    return scene


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Scene file to write, for example tiny.scene")
    args = parser.parse_args()
    build_scene().dump(args.path)
