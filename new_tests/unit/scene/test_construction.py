from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import subprocess
import sys
import pytest
from skyseed.scene import (
    Constellation, GroundStation, GroundStationGroup, Satellite, SatelliteOrbit,
    SatelliteShell, Scene, ScenePositionSource,
)


def test_import_and_construct_without_seed_or_runtime():
    result = subprocess.run([sys.executable, "-c", "from skyseed.scene import Scene; import sys; "
                            "Scene(); assert 'seedemu' not in sys.modules; "
                            "assert 'skyseed.experiment' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_constellation_geometry_identity_and_per_source_epoch():
    scene = Scene()
    constellation = scene.create_constellation("first", planes=2, satellites_per_plane=2,
                                               altitude_km=550, inclination_deg=53,
                                               epoch=datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))))
    shell = scene.get_shells("first")[0]
    orbits = scene.get_orbits(shell.group_id)
    satellites = scene.get_satellites()
    assert isinstance(constellation, Constellation) and isinstance(shell, SatelliteShell)
    assert all(isinstance(orbit, SatelliteOrbit) for orbit in orbits)
    assert shell.constellation is constellation
    assert all(satellite.orbit.shell is shell for satellite in satellites)
    assert scene.get_satellites(orbits[0].group_id) == satellites[:2]
    sources = [node.position.data for node in satellites]
    assert [source["raan_deg"] for source in sources] == [0, 0, 180, 180]
    assert [source["mean_anomaly_deg"] for source in sources] == [0, 180, 0, 180]
    assert {source["epoch"] for source in sources} == {"2026-01-01T00:00:00+00:00"}
    assert {source["altitude_m"] for source in sources} == {550000}
    assert len({node.node_id for node in scene.nodes}) == 4
    # A position query is a detached value, not a second writable geometry owner.
    sources[0]["altitude_m"] = 1
    assert shell.altitude_m == 550000 and satellites[0].position.data["altitude_m"] == 550000
    with pytest.raises(FrozenInstanceError):
        shell.altitude_m = 1
    scene.create_constellation("second", planes=1, satellites_per_plane=1,
                               altitude_km=600, inclination_deg=90,
                               epoch=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert scene.nodes[-1].position.data["epoch"].startswith("2027-")


def test_custom_position_inputs_and_optional_metadata_need_no_model_parameters():
    source = {"file": "measurements/trajectory.csv", "time_column": "utc"}
    scene = Scene()
    node = scene.create_satellite("survey:alpha", position=ScenePositionSource("research-trajectory", source))
    source["file"] = "changed.csv"
    assert node.position.data["file"] == "measurements/trajectory.csv"
    assert node.group_id is None and node.data == {}
    assert node.position.kind == "research-trajectory"
    station = scene.create_ground_station("G1", latitude_deg=-90, longitude_deg=180)
    assert isinstance(station, GroundStation) and station.group is None
    assert station.elevation_m == 0 and station.position.data["elevation_m"] == 0


def test_conflicts_and_missing_groups_do_not_partially_add_constellation(tiny_scene):
    before = tiny_scene.nodes, tiny_scene.groups
    with pytest.raises(ValueError, match="already exist"):
        tiny_scene.create_constellation("demo", planes=2, satellites_per_plane=2,
                                        altitude_km=550, inclination_deg=53,
                                        epoch=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(KeyError):
        tiny_scene.create_satellite("unowned", orbit="missing", mean_anomaly_deg=0)
    # A node ID collision late in a generated constellation also leaves no groups behind.
    tiny_scene.create_satellite("other/shell-0/plane-0/sat-1", position=ScenePositionSource("custom"))
    groups = tiny_scene.groups
    with pytest.raises(ValueError, match="already exist"):
        tiny_scene.create_constellation("other", planes=1, satellites_per_plane=2,
                                        altitude_km=550, inclination_deg=53,
                                        epoch=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert tiny_scene.groups == groups == before[1]
    assert tiny_scene.nodes[:-1] == before[0]


def test_invalid_coordinates_epoch_and_unsupported_user_objects_are_rejected():
    scene = Scene()
    with pytest.raises(ValueError, match="latitude"):
        scene.create_ground_station("G1", latitude_deg=91, longitude_deg=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        scene.create_constellation("bad", planes=1, satellites_per_plane=1,
                                    altitude_km=550, inclination_deg=53, epoch=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="all constellation"):
        scene.create_constellation("incomplete", planes=1)
    with pytest.raises(ValueError, match="JSON"):
        Scene(data={"algorithm": object()})
    assert scene.nodes == () and scene.groups == ()


def test_manual_shells_with_equal_geometry_keep_distinct_identity_and_data():
    scene = Scene()
    source = {"operator": "A", "research": {"label": "original"}}
    constellation = scene.create_constellation("A", data=source)
    source["research"]["label"] = "external edit"
    for name in ("low-1", "low-2"):
        shell = scene.create_shell(name, constellation="A", altitude_km=550, inclination_deg=53,
                                    epoch=datetime(2026, 1, 1, tzinfo=timezone.utc), data={"label": name})
        orbit = scene.create_orbit(name + "/orbit", shell=name, raan_deg=0, data={"role": "survey"})
        satellite = scene.create_satellite(name + "/sat", orbit=orbit.group_id,
                                            mean_anomaly_deg=0, data={"payload": "camera"})
        assert satellite.orbit is orbit and orbit.shell is shell
    assert len(scene.get_shells("A")) == 2
    assert scene.get_node_ids(groups=["low-1"]) == ["low-1/sat"]
    assert scene.get_node_ids(groups=["low-2"]) == ["low-2/sat"]
    assert constellation.data["research"]["label"] == "original"
    scene.get_group("low-1").data["label"] = "updated"
    assert scene.get_node("low-1/sat").orbit.shell.data["label"] == "updated"
    assert scene.get_group("low-2").data["label"] == "low-2"


def test_ground_station_groups_and_overlapping_selection_are_explicit(tiny_scene):
    group = tiny_scene.create_ground_station_group("ground-a", data={"operator": "A"})
    station = tiny_scene.create_ground_station("G2", latitude_deg=50, longitude_deg=10, group="ground-a")
    assert isinstance(group, GroundStationGroup) and station.group is group
    assert tiny_scene.get_ground_station_groups() == (group,)
    assert tiny_scene.get_ground_stations("ground-a") == (station,)
    assert len(tiny_scene.get_ground_stations()) == 2
    assert tiny_scene.get_node_ids(groups=["demo", "demo/shell-0", "ground-a", "demo"]) == [
        "demo/shell-0/plane-0/sat-0", "demo/shell-0/plane-0/sat-1", "G2",
    ]
    assert tiny_scene.get_node_ids(groups=["demo", "ground-a"], kind="ground_station") == ["G2"]
    assert tiny_scene.get_node_ids(kind="ground_station") == ["G1", "G2"]
    assert tiny_scene.get_node_ids(groups=[]) == []
    assert len(tiny_scene.get_node_ids()) == 4
    with pytest.raises(KeyError):
        tiny_scene.get_node_ids(groups=["unknown"])
    with pytest.raises(TypeError):
        tiny_scene.get_node_ids(groups="demo")
    with pytest.raises(ValueError):
        tiny_scene.get_node_ids(kind="operator-a")


def test_wrong_relationships_or_foreign_objects_cannot_be_adopted(tiny_scene):
    other = Scene()
    foreign_constellation = other.create_constellation("demo")
    before = tiny_scene.nodes, tiny_scene.groups
    with pytest.raises(ValueError, match="owning object"):
        tiny_scene.add_group(SatelliteShell(group_id="foreign", constellation=foreign_constellation,
                                           altitude_m=550000, inclination_deg=53,
                                           epoch=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    with pytest.raises(TypeError, match="GroundStationGroup"):
        tiny_scene.create_ground_station("wrong", latitude_deg=0, longitude_deg=0, group="demo")
    with pytest.raises(TypeError, match="SatelliteShell"):
        tiny_scene.create_orbit("wrong", shell="demo", raan_deg=0)
    with pytest.raises(ValueError, match="both"):
        tiny_scene.create_satellite("ambiguous", orbit="demo/shell-0/plane-0", mean_anomaly_deg=0,
                                    position=ScenePositionSource("custom"))
    assert (tiny_scene.nodes, tiny_scene.groups) == before
