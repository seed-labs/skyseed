"""Generate selected shells, real GS sites and geographic zones."""
from datetime import datetime
from pathlib import Path

import yaml
from skyseed import Scene


# Select presets here; the rest of the example consumes the saved Scene.
SHELLS = ("S4",)                  # e.g. ("S5",) or ("S1", "S2", "S3")

# Fixed geographic selection, made before calculating coverage or paths.
STATIONS = {
    "North America": ["Redmond", "Cabo San Lucas", "Iqaluit", "Sambro Creek", "Virginia Beach"],
    "South America": ["Punta Arenas", "Manaus", "Lima", "Bogota", "Porto Alegre"],
    "Europe": ["Goonhilly", "Frankfurt", "Kaunas", "Ibi", "Marsala",
               "Woodwalton", "Hoofddorp", "Aerzen", "Milano"],
    "Africa": ["Lekki"],
    "Asia": ["Akita", "Otaru", "Hitachinaka", "Yamaguchi",
             "Singapore", "Cikarang Barat", "Kendari"],
    "Oceania": ["Cataby", "Awarua", "Toonpan", "Broken Hill", "Suva"],
}

# Keep the source dataset's spelling separate from the displayed site name.
SOURCE_NAMES = {"Hoofddorp": "Hoofddorf"}


def build_scene(shells=SHELLS):
    data = Path(__file__).resolve().parents[2] / "data/starlink"
    presets = yaml.safe_load((data / "shells.yaml").read_text())["shells"]
    station_file = data / "ground_stations.yaml"
    stations = yaml.safe_load(station_file.read_text())["ground_stations"]

    # This example's fixed traffic endpoints; models and UT read this metadata.
    scene = Scene(data={"experiment": {
        "ut_latitude_deg": 30.2741,
        "ut_longitude_deg": 120.1551,
        "ut_address": "192.0.2.2",
        "selection": "Hangzhou UT location; fixed before route calculation",
        "ground_station_source": "data/starlink/ground_stations.yaml, frozen 2026-08-25",
    }})
    scene.create_constellation("Starlink")
    for name in shells:
        settings = dict(presets[name])
        settings["epoch"] = datetime.fromisoformat(settings.pop("epoch_utc"))
        scene.create_shell(name, constellation="Starlink", **settings)

    for region, names in STATIONS.items():
        group = scene.create_ground_station_group(region)
        for name in names:
            source_name = SOURCE_NAMES.get(name, name)
            matches = [station for station in stations if station["display_name"].startswith(f"Starlink Gateway - {source_name},")]
            if len(matches) != 1:
                raise ValueError(f"GS {name!r} in {region!r}: expected one match for {source_name!r} "
                                 f"in {station_file}, found {len(matches)}")
            station, = matches
            scene.create_ground_station(name, node_id=station["ground_station_id"], group=group.group_id,
                                         latitude_deg=station["latitude_deg"],
                                         longitude_deg=station["longitude_deg"], elevation_m=station["elevation_m"])
    scene.create_default_zones()
    experiment = scene.data["experiment"]
    experiment["zone"] = containing_zone(scene, experiment["ut_latitude_deg"],
                                         experiment["ut_longitude_deg"])
    return scene


def containing_zone(scene, latitude, longitude):
    """Locate the UT in this example's rectangular default Zone grid."""
    for zone in scene.get_endpoints("geographic-zone/v1"):
        bounds = zone.data["bounds"]
        west, east = bounds["west_deg"], bounds["east_deg"]
        in_longitude = west <= longitude < east
        if west > east:
            in_longitude = longitude >= west or longitude < east
        if bounds["south_deg"] <= latitude < bounds["north_deg"] and in_longitude:
            return zone.endpoint_id
    raise ValueError(f"UT location ({latitude}, {longitude}) is outside the Zone grid")


if __name__ == "__main__":
    output = Path(__file__).parent / "output/scene"
    output.mkdir(parents=True, exist_ok=True)
    scene = build_scene()
    scene.dump(output / "scene.scene")
    print(f"Scene saved: {len(scene.get_satellites())} satellites, "
          f"{len(scene.get_ground_stations())} GS, {len(scene.endpoints)} zones")
