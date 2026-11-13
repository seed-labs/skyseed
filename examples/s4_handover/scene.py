"""Generate S4 from shell parameters, select real GS sites and create zones."""
from datetime import datetime
from pathlib import Path

import yaml
from skyseed import Scene


# Fixed geographic selection, made before calculating coverage or paths.
STATIONS = {
    "North America": ["Redmond", "Cabo San Lucas", "Iqaluit", "Sambro Creek", "Virginia Beach"],
    "South America": ["Punta Arenas", "Manaus", "Lima", "Bogota", "Porto Alegre"],
    "Europe": ["Goonhilly", "Frankfurt", "Kaunas", "Ibi", "Marsala",
               "Woodwalton", "Hoofddorf", "Aerzen", "Milano"],
    "Africa": ["Lekki"],
    "Asia": ["Akita", "Otaru", "Hitachinaka", "Yamaguchi",
             "Singapore", "Cikarang Barat", "Kendari"],
    "Oceania": ["Cataby", "Awarua", "Toonpan", "Broken Hill", "Suva"],
}


def build_scene():
    data = Path(__file__).resolve().parents[2] / "data/starlink"
    s4 = yaml.safe_load((data / "shells.yaml").read_text())["shells"]["S4"]
    stations = yaml.safe_load((data / "ground_stations.yaml").read_text())["ground_stations"]

    # This example's fixed traffic endpoints; models and UT read this metadata.
    scene = Scene(data={"experiment": {
        "ut_latitude_deg": 30.2741,
        "ut_longitude_deg": 120.1551,
        "ut_address": "192.0.2.2",
        "selection": "Hangzhou UT location; fixed before route calculation",
        "ground_station_source": "data/starlink/ground_stations.yaml, frozen 2026-08-25",
    }})
    scene.create_constellation("Starlink")
    # A preset supplies ordinary construction parameters, not satellite records.
    # For a custom shell, change its name and e.g. satellites_per_plane to 60.
    scene.create_shell(
        "S4", constellation="Starlink",
        planes=s4["planes"], satellites_per_plane=s4["satellites_per_plane"],
        altitude_km=s4["altitude_km"], inclination_deg=s4["inclination_deg"],
        epoch=datetime.fromisoformat(s4["epoch_utc"]),
        raan_start_deg=s4["raan_start_deg"], phase_factor=s4["phase_factor"],
        id_prefix=s4["id_prefix"], first_orbit=s4["first_orbit"],
    )

    for region, names in STATIONS.items():
        group = scene.create_ground_station_group(region)
        for name in names:
            matches = [station for station in stations if station["display_name"].startswith(f"Starlink Gateway - {name},")]
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
