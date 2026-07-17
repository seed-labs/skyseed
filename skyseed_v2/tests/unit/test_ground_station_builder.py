import pytest

from skyseed_v2 import (
    GroundStationBuilder,
    GroundStationSpecification,
    ground_station_id_for,
)
from skyseed_v2.seed import SkyseedAutonomousSystem


def generated_station(identity_key: str, display_name: str) -> GroundStationSpecification:
    return GroundStationSpecification(
        display_name=display_name,
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="Starlink",
        station_type="gateway",
        identity_key=identity_key,
    )


def test_generated_ground_station_ids_are_stable_and_order_independent() -> None:
    specifications = (
        generated_station("station-b", "Station B"),
        generated_station("station-a", "Station A"),
    )
    first = GroundStationBuilder().build(
        SkyseedAutonomousSystem(65000), specifications
    )
    second = GroundStationBuilder().build(
        SkyseedAutonomousSystem(65000), reversed(specifications)
    )

    assert tuple(station.ground_station_id for station in first.ground_stations) == tuple(
        station.ground_station_id for station in second.ground_stations
    )
    assert all(station.operator == "Starlink" for station in first.ground_stations)
    assert {station.getAsn() for station in first.ground_stations} == {65000}
    assert all(station.service_config == {} for station in first.ground_stations)


def test_explicit_id_is_preserved_by_v2_builder() -> None:
    specification = GroundStationSpecification(
        display_name="Explicit",
        latitude_deg=0.0,
        longitude_deg=0.0,
        elevation_m=None,
        operator="Operator",
        station_type="gateway",
        ground_station_id="ground-station-explicit",
    )
    result = GroundStationBuilder().build(
        SkyseedAutonomousSystem(65000), specification
    )

    assert ground_station_id_for(specification) == "ground-station-explicit"
    assert result.ground_stations[0].ground_station_id == "ground-station-explicit"


def test_duplicate_generated_ids_fail_before_creating_any_station() -> None:
    autonomous_system = SkyseedAutonomousSystem(65000)
    duplicate = generated_station("same-key", "Same")

    with pytest.raises(ValueError, match="duplicate ground_station_id"):
        GroundStationBuilder().build(autonomous_system, (duplicate, duplicate))

    assert autonomous_system.ground_stations() == ()
