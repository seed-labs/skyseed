from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from yaml import safe_dump, safe_load

from skyseed_v2.serialization import (
    SCENARIO_SCHEMA_VERSION,
    StaticEmulatorScenarioLoadError,
    StaticEmulatorScenarioLoader,
)
from skyseed_v2.specifications import (
    StaticEmulatorScenarioSpecification,
)


def valid_payload() -> dict:
    return {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "skyseed_autonomous_systems": [
            {
                "asn": 65001,
                "shells": [
                    {
                        "shell_id": "shell-a",
                        "altitude_km": 550.0,
                        "inclination_deg": 53.0,
                        "generation": {
                            "orbit_count": 2,
                            "satellites_per_orbit": 3,
                            "raan": {"start_deg": 0.0, "step_deg": 180.0},
                            "phase": {
                                "start_deg": 0.0,
                                "step_deg": 120.0,
                                "alternating_orbit_offset_deg": 5.0,
                            },
                        },
                    }
                ],
                "ground_stations": [
                    {
                        "ground_station_id": "gs-a",
                        "display_name": "Station A",
                        "latitude_deg": 40.0,
                        "longitude_deg": -74.0,
                        "elevation_m": 10.0,
                        "operator": "Descriptive Operator",
                        "station_type": "gateway",
                        "ix_attachments": [
                            {"ix_id": 100, "address": "10.100.0.10"}
                        ],
                    }
                ],
            }
        ],
        "internet_exchanges": [
            {"ix_id": 100, "prefix": "10.100.0.0/24", "route_server": False}
        ],
        "ordinary_autonomous_systems": [
            {
                "asn": 65002,
                "networks": [
                    {"network_id": "service-net", "prefix": "10.202.0.0/24"}
                ],
                "routers": [
                    {
                        "router_id": "ground-router",
                        "network_attachments": [
                            {
                                "network_id": "service-net",
                                "address": "10.202.0.254",
                            }
                        ],
                        "ix_attachments": [
                            {"ix_id": 100, "address": "10.100.0.254"}
                        ],
                    }
                ],
            }
        ],
        "ebgp_peerings": [
            {
                "ix_id": 100,
                "first": {"asn": 65001, "router_id": "gs-a"},
                "second": {"asn": 65002, "router_id": "ground-router"},
                "relationship": "unfiltered",
            }
        ],
    }


def write_payload(tmp_path: Path, payload: dict, name: str = "scenario.yaml") -> Path:
    path = tmp_path / name
    path.write_text(safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_valid_scenario_loads_only_immutable_generic_specifications(
    tmp_path: Path,
) -> None:
    result = StaticEmulatorScenarioLoader().load(
        write_payload(tmp_path, valid_payload())
    )

    assert isinstance(result, StaticEmulatorScenarioSpecification)
    assert result.skyseed_autonomous_systems[0].asn == 65001
    assert result.skyseed_autonomous_systems[0].shells[0].generation.orbit_count == 2
    assert result.skyseed_autonomous_systems[0].ground_stations[0].operator == (
        "Descriptive Operator"
    )
    assert result.ordinary_autonomous_systems[0].networks[0].prefix == (
        "10.202.0.0/24"
    )
    assert result.ebgp_peerings[0].first.router_id == "gs-a"
    assert not hasattr(result, "emulator")
    assert not hasattr(result, "compiler")
    with pytest.raises(FrozenInstanceError):
        result.internet_exchanges = ()


def test_unknown_schema_missing_field_and_unknown_field_fail_closed(
    tmp_path: Path,
) -> None:
    unknown_version = valid_payload()
    unknown_version["schema_version"] = "skyseed-static-emulator/v99"
    with pytest.raises(StaticEmulatorScenarioLoadError, match="unsupported"):
        StaticEmulatorScenarioLoader().load(
            write_payload(tmp_path, unknown_version, "version.yaml")
        )

    missing = valid_payload()
    del missing["skyseed_autonomous_systems"][0]["shells"][0]["altitude_km"]
    with pytest.raises(StaticEmulatorScenarioLoadError, match="missing required"):
        StaticEmulatorScenarioLoader().load(
            write_payload(tmp_path, missing, "missing.yaml")
        )

    dynamic = valid_payload()
    dynamic["epoch_utc"] = "2030-01-01T00:00:00Z"
    with pytest.raises(StaticEmulatorScenarioLoadError, match="unknown fields"):
        StaticEmulatorScenarioLoader().load(
            write_payload(tmp_path, dynamic, "dynamic.yaml")
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda value: value["skyseed_autonomous_systems"][0].update(asn=0),
            "positive integer",
        ),
        (
            lambda value: value["skyseed_autonomous_systems"][0]["shells"][0].update(
                inclination_deg=181
            ),
            "inclination",
        ),
        (
            lambda value: value["skyseed_autonomous_systems"][0][
                "ground_stations"
            ][0].update(latitude_deg=91),
            "latitude",
        ),
        (
            lambda value: value["internet_exchanges"][0].update(
                prefix="10.100.0.1/24"
            ),
            "canonical IPv4",
        ),
        (
            lambda value: value["ordinary_autonomous_systems"][0]["routers"][
                0
            ]["ix_attachments"][0].update(address="not-an-address"),
            "IPv4 address",
        ),
        (
            lambda value: value["skyseed_autonomous_systems"][0]["shells"][0][
                "generation"
            ]["raan"].update(step_deg=360.0),
            "duplicate Orbits",
        ),
    ),
)
def test_invalid_static_values_fail_closed(tmp_path: Path, mutate, message: str) -> None:
    payload = valid_payload()
    mutate(payload)
    with pytest.raises(StaticEmulatorScenarioLoadError, match=message):
        StaticEmulatorScenarioLoader().load(write_payload(tmp_path, payload))


def test_duplicate_logical_ids_and_yaml_keys_fail_closed(tmp_path: Path) -> None:
    duplicate = valid_payload()
    duplicate["skyseed_autonomous_systems"][0]["shells"].append(
        duplicate["skyseed_autonomous_systems"][0]["shells"][0].copy()
    )
    with pytest.raises(StaticEmulatorScenarioLoadError, match="duplicate shell_id"):
        StaticEmulatorScenarioLoader().load(
            write_payload(tmp_path, duplicate, "logical.yaml")
        )

    duplicate_key = tmp_path / "duplicate-key.yaml"
    duplicate_key.write_text(
        "schema_version: skyseed-static-emulator/v1\n"
        "schema_version: skyseed-static-emulator/v1\n"
        "skyseed_autonomous_systems: []\n",
        encoding="utf-8",
    )
    with pytest.raises(StaticEmulatorScenarioLoadError, match="valid YAML"):
        StaticEmulatorScenarioLoader().load(duplicate_key)


def test_external_ground_station_file_is_scoped_versioned_and_required(
    tmp_path: Path,
) -> None:
    payload = valid_payload()
    station = payload["skyseed_autonomous_systems"][0].pop("ground_stations")[0]
    payload["skyseed_autonomous_systems"][0][
        "ground_stations_file"
    ] = "ground_stations.yaml"
    stations_file = tmp_path / "ground_stations.yaml"
    stations_file.write_text(
        safe_dump(
            {
                "schema_version": "skyseed-ground-stations/v1",
                "ground_stations": [station],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    scenario_file = write_payload(tmp_path, payload)

    loaded = StaticEmulatorScenarioLoader().load(scenario_file)
    assert loaded.skyseed_autonomous_systems[0].ground_stations[0].ground_station_id == (
        "gs-a"
    )

    stations_file.unlink()
    with pytest.raises(StaticEmulatorScenarioLoadError, match="cannot read"):
        StaticEmulatorScenarioLoader().load(scenario_file)

    payload["skyseed_autonomous_systems"][0][
        "ground_stations_file"
    ] = "../outside.yaml"
    with pytest.raises(StaticEmulatorScenarioLoadError, match="example root"):
        StaticEmulatorScenarioLoader().load(
            write_payload(tmp_path, payload, "outside-reference.yaml")
        )


def test_sequence_and_mapping_order_do_not_change_the_result(tmp_path: Path) -> None:
    first_payload = valid_payload()
    second_payload = safe_load(safe_dump(first_payload, sort_keys=True))
    second_payload["skyseed_autonomous_systems"].reverse()
    second_payload["internet_exchanges"].reverse()
    second_payload["ordinary_autonomous_systems"].reverse()
    second_payload["ebgp_peerings"].reverse()

    first = StaticEmulatorScenarioLoader().load(
        write_payload(tmp_path, first_payload, "first.yaml")
    )
    second = StaticEmulatorScenarioLoader().load(
        write_payload(tmp_path, second_payload, "second.yaml")
    )
    assert first == second
