from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from yaml import safe_dump, safe_load

from skyseed_v2.configuration.emulator import (
    SCENARIO_SCHEMA_VERSION,
    SHELLS_SCHEMA_VERSION,
    StaticEmulatorConfigLoadError,
    load_static_emulator_config,
)
from skyseed_v2.configuration.emulator import (
    StaticEmulatorConfig,
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
                        "epoch_utc": "2026-01-01T08:00:00+08:00",
                        "generation": {
                            "orbit_count": 2,
                            "satellites_per_orbit": 3,
                            "max_simultaneous_ground_station_accesses": 2,
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


def test_valid_yaml_loads_only_immutable_static_config(
    tmp_path: Path,
) -> None:
    result = load_static_emulator_config(
        write_payload(tmp_path, valid_payload())
    )

    assert isinstance(result, StaticEmulatorConfig)
    assert result.skyseed_autonomous_systems[0].asn == 65001
    shell = result.skyseed_autonomous_systems[0].shells[0]
    assert shell.generated_orbit_layout is not None
    assert shell.generated_orbit_layout.orbit_count == 2
    assert (
        shell.generated_orbit_layout.max_simultaneous_ground_station_accesses
        == 2
    )
    assert shell.epoch_utc.isoformat() == "2026-01-01T00:00:00+00:00"
    assert result.skyseed_autonomous_systems[0].ground_stations[0].operator == (
        "Descriptive Operator"
    )
    assert (
        result.skyseed_autonomous_systems[0]
        .ground_stations[0]
        .max_simultaneous_satellite_accesses
        is None
    )
    assert result.ordinary_autonomous_systems[0].networks[0].prefix == (
        "10.202.0.0/24"
    )
    assert result.ebgp_peerings[0].peer_routers[0].router_id == "gs-a"
    assert not hasattr(result, "emulator")
    assert not hasattr(result, "compiler")
    with pytest.raises(FrozenInstanceError):
        result.internet_exchanges = ()


def test_selected_shell_keeps_exactly_one_configured_shell(
    tmp_path: Path,
) -> None:
    payload = valid_payload()
    second = payload["skyseed_autonomous_systems"][0]["shells"][0].copy()
    second["shell_id"] = "shell-b"
    second["altitude_km"] = 570.0
    payload["skyseed_autonomous_systems"][0]["shells"].append(second)
    payload["skyseed_autonomous_systems"][0]["selected_shell"] = "shell-b"

    loaded = load_static_emulator_config(write_payload(tmp_path, payload))

    assert tuple(
        shell.shell_id
        for shell in loaded.skyseed_autonomous_systems[0].shells
    ) == ("shell-b",)


@pytest.mark.parametrize("selected", ("missing", "", True))
def test_selected_shell_fails_closed_without_one_exact_match(
    tmp_path: Path,
    selected: object,
) -> None:
    payload = valid_payload()
    payload["skyseed_autonomous_systems"][0]["selected_shell"] = selected

    with pytest.raises(StaticEmulatorConfigLoadError, match="selected_shell"):
        load_static_emulator_config(write_payload(tmp_path, payload))


def test_yaml_requires_exactly_one_inter_orbit_phase_policy(
    tmp_path: Path,
) -> None:
    alternating = load_static_emulator_config(
        write_payload(tmp_path, valid_payload(), "alternating.yaml")
    )
    alternating_phase = alternating.skyseed_autonomous_systems[0].shells[
        0
    ].generated_orbit_layout.in_orbit_phase_distribution
    assert alternating_phase.alternating_orbit_offset_deg == 5.0
    assert alternating_phase.orbit_phase_step_deg is None

    linear_payload = valid_payload()
    linear_phase = linear_payload["skyseed_autonomous_systems"][0]["shells"][
        0
    ]["generation"]["phase"]
    linear_phase.pop("alternating_orbit_offset_deg")
    linear_phase["orbit_phase_step_deg"] = 7.5
    linear = load_static_emulator_config(
        write_payload(tmp_path, linear_payload, "linear.yaml")
    )
    loaded_linear_phase = linear.skyseed_autonomous_systems[0].shells[
        0
    ].generated_orbit_layout.in_orbit_phase_distribution
    assert loaded_linear_phase.alternating_orbit_offset_deg == 0.0
    assert loaded_linear_phase.orbit_phase_step_deg == 7.5

    missing_payload = valid_payload()
    missing_payload["skyseed_autonomous_systems"][0]["shells"][0][
        "generation"
    ]["phase"].pop("alternating_orbit_offset_deg")
    with pytest.raises(StaticEmulatorConfigLoadError, match="exactly one"):
        load_static_emulator_config(
            write_payload(tmp_path, missing_payload, "missing-policy.yaml")
        )

    ambiguous_payload = valid_payload()
    ambiguous_payload["skyseed_autonomous_systems"][0]["shells"][0][
        "generation"
    ]["phase"]["orbit_phase_step_deg"] = 7.5
    with pytest.raises(StaticEmulatorConfigLoadError, match="exactly one"):
        load_static_emulator_config(
            write_payload(tmp_path, ambiguous_payload, "ambiguous-policy.yaml")
        )


def test_ground_station_operator_key_is_required_and_yaml_null_is_valid(
    tmp_path: Path,
) -> None:
    nullable = valid_payload()
    nullable["skyseed_autonomous_systems"][0]["ground_stations"][0][
        "operator"
    ] = None
    loaded = load_static_emulator_config(
        write_payload(tmp_path, nullable, "nullable-operator.yaml")
    )
    assert loaded.skyseed_autonomous_systems[0].ground_stations[0].operator is None

    missing = valid_payload()
    del missing["skyseed_autonomous_systems"][0]["ground_stations"][0][
        "operator"
    ]
    with pytest.raises(StaticEmulatorConfigLoadError, match="missing required"):
        load_static_emulator_config(
            write_payload(tmp_path, missing, "missing-operator.yaml")
        )

    blank = valid_payload()
    blank["skyseed_autonomous_systems"][0]["ground_stations"][0][
        "operator"
    ] = "   "
    with pytest.raises(StaticEmulatorConfigLoadError, match="operator"):
        load_static_emulator_config(
            write_payload(tmp_path, blank, "blank-operator.yaml")
        )


@pytest.mark.parametrize("capability", (0, -1, True))
def test_ground_station_optional_access_capability_is_validated_from_yaml(
    tmp_path: Path,
    capability: int,
) -> None:
    payload = valid_payload()
    payload["skyseed_autonomous_systems"][0]["ground_stations"][0][
        "max_simultaneous_satellite_accesses"
    ] = capability
    with pytest.raises(
        StaticEmulatorConfigLoadError,
        match="max_simultaneous_satellite_accesses",
    ):
        load_static_emulator_config(
            write_payload(tmp_path, payload, "invalid-capability.yaml")
        )


def test_ground_station_positive_access_capability_loads_from_yaml(
    tmp_path: Path,
) -> None:
    payload = valid_payload()
    payload["skyseed_autonomous_systems"][0]["ground_stations"][0][
        "max_simultaneous_satellite_accesses"
    ] = 4

    loaded = load_static_emulator_config(
        write_payload(tmp_path, payload, "site-capability.yaml")
    )

    assert (
        loaded.skyseed_autonomous_systems[0]
        .ground_stations[0]
        .max_simultaneous_satellite_accesses
        == 4
    )


@pytest.mark.parametrize("capability", (0, -1, True))
def test_satellite_access_capability_is_validated_from_yaml(
    tmp_path: Path,
    capability: int,
) -> None:
    payload = valid_payload()
    payload["skyseed_autonomous_systems"][0]["shells"][0]["generation"][
        "max_simultaneous_ground_station_accesses"
    ] = capability
    with pytest.raises(
        StaticEmulatorConfigLoadError,
        match="max_simultaneous_ground_station_accesses",
    ):
        load_static_emulator_config(
            write_payload(tmp_path, payload, "invalid-satellite-capability.yaml")
        )


def test_unknown_schema_missing_field_and_unknown_field_fail_closed(
    tmp_path: Path,
) -> None:
    unknown_version = valid_payload()
    unknown_version["schema_version"] = "skyseed-static-emulator/v99"
    with pytest.raises(StaticEmulatorConfigLoadError, match="unsupported"):
        load_static_emulator_config(
            write_payload(tmp_path, unknown_version, "version.yaml")
        )

    missing = valid_payload()
    del missing["skyseed_autonomous_systems"][0]["shells"][0]["altitude_km"]
    with pytest.raises(StaticEmulatorConfigLoadError, match="missing required"):
        load_static_emulator_config(
            write_payload(tmp_path, missing, "missing.yaml")
        )

    dynamic = valid_payload()
    dynamic["epoch_utc"] = "2030-01-01T00:00:00Z"
    with pytest.raises(StaticEmulatorConfigLoadError, match="unknown fields"):
        load_static_emulator_config(
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
    with pytest.raises(StaticEmulatorConfigLoadError, match=message):
        load_static_emulator_config(write_payload(tmp_path, payload))


def test_duplicate_logical_ids_and_yaml_keys_fail_closed(tmp_path: Path) -> None:
    duplicate = valid_payload()
    duplicate["skyseed_autonomous_systems"][0]["shells"].append(
        duplicate["skyseed_autonomous_systems"][0]["shells"][0].copy()
    )
    with pytest.raises(StaticEmulatorConfigLoadError, match="duplicate shell_id"):
        load_static_emulator_config(
            write_payload(tmp_path, duplicate, "logical.yaml")
        )

    duplicate_key = tmp_path / "duplicate-key.yaml"
    duplicate_key.write_text(
        "schema_version: skyseed-static-emulator/v1\n"
        "schema_version: skyseed-static-emulator/v1\n"
        "skyseed_autonomous_systems: []\n",
        encoding="utf-8",
    )
    with pytest.raises(StaticEmulatorConfigLoadError, match="valid YAML"):
        load_static_emulator_config(duplicate_key)


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

    loaded = load_static_emulator_config(scenario_file)
    assert loaded.skyseed_autonomous_systems[0].ground_stations[0].ground_station_id == (
        "gs-a"
    )

    stations_file.unlink()
    with pytest.raises(StaticEmulatorConfigLoadError, match="cannot read"):
        load_static_emulator_config(scenario_file)

    payload["skyseed_autonomous_systems"][0][
        "ground_stations_file"
    ] = "../outside.yaml"
    with pytest.raises(StaticEmulatorConfigLoadError, match="example root"):
        load_static_emulator_config(
            write_payload(tmp_path, payload, "outside-reference.yaml")
        )


def test_external_shell_file_is_scoped_versioned_and_selects_one_shell(
    tmp_path: Path,
) -> None:
    payload = valid_payload()
    autonomous_system = payload["skyseed_autonomous_systems"][0]
    shells = autonomous_system.pop("shells")
    second = shells[0].copy()
    second["shell_id"] = "shell-b"
    second["altitude_km"] = 570.0
    shells.append(second)
    autonomous_system["selected_shell"] = "shell-b"
    autonomous_system["shells_file"] = "shells.yaml"
    shells_file = tmp_path / "shells.yaml"
    shells_file.write_text(
        safe_dump(
            {"schema_version": SHELLS_SCHEMA_VERSION, "shells": shells},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    loaded = load_static_emulator_config(write_payload(tmp_path, payload))

    assert tuple(
        shell.shell_id
        for shell in loaded.skyseed_autonomous_systems[0].shells
    ) == ("shell-b",)


def test_external_shell_file_fail_closed_contracts(tmp_path: Path) -> None:
    payload = valid_payload()
    autonomous_system = payload["skyseed_autonomous_systems"][0]
    shells = autonomous_system.pop("shells")
    autonomous_system["shells_file"] = "shells.yaml"
    shells_file = tmp_path / "shells.yaml"
    shells_file.write_text(
        safe_dump(
            {"schema_version": SHELLS_SCHEMA_VERSION, "shells": shells},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(StaticEmulatorConfigLoadError, match="selected_shell"):
        load_static_emulator_config(write_payload(tmp_path, payload, "missing.yaml"))

    autonomous_system["selected_shell"] = "shell-a"
    duplicate = [shells[0], shells[0].copy()]
    shells_file.write_text(
        safe_dump(
            {"schema_version": SHELLS_SCHEMA_VERSION, "shells": duplicate},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(StaticEmulatorConfigLoadError, match="exactly one"):
        load_static_emulator_config(write_payload(tmp_path, payload, "duplicate.yaml"))

    shells_file.write_text(
        safe_dump(
            {"schema_version": "wrong/v1", "shells": shells},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(StaticEmulatorConfigLoadError, match="schema_version"):
        load_static_emulator_config(write_payload(tmp_path, payload, "schema.yaml"))

    malformed = [dict(shells[0])]
    del malformed[0]["epoch_utc"]
    shells_file.write_text(
        safe_dump(
            {"schema_version": SHELLS_SCHEMA_VERSION, "shells": malformed},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(StaticEmulatorConfigLoadError, match="epoch_utc"):
        load_static_emulator_config(write_payload(tmp_path, payload, "malformed.yaml"))


@pytest.mark.parametrize("reference", ("../shells.yaml", "/tmp/shells.yaml"))
def test_external_shell_file_rejects_unscoped_paths(
    tmp_path: Path,
    reference: str,
) -> None:
    payload = valid_payload()
    autonomous_system = payload["skyseed_autonomous_systems"][0]
    autonomous_system.pop("shells")
    autonomous_system["selected_shell"] = "shell-a"
    autonomous_system["shells_file"] = reference

    with pytest.raises(StaticEmulatorConfigLoadError, match="example root"):
        load_static_emulator_config(write_payload(tmp_path, payload))


def test_inline_and_external_shells_are_mutually_exclusive(tmp_path: Path) -> None:
    payload = valid_payload()
    payload["skyseed_autonomous_systems"][0]["shells_file"] = "shells.yaml"

    with pytest.raises(StaticEmulatorConfigLoadError, match="both shells"):
        load_static_emulator_config(write_payload(tmp_path, payload))


def test_sequence_and_mapping_order_do_not_change_the_result(tmp_path: Path) -> None:
    first_payload = valid_payload()
    second_payload = safe_load(safe_dump(first_payload, sort_keys=True))
    second_payload["skyseed_autonomous_systems"].reverse()
    second_payload["internet_exchanges"].reverse()
    second_payload["ordinary_autonomous_systems"].reverse()
    second_payload["ebgp_peerings"].reverse()

    first = load_static_emulator_config(
        write_payload(tmp_path, first_payload, "first.yaml")
    )
    second = load_static_emulator_config(
        write_payload(tmp_path, second_payload, "second.yaml")
    )
    assert first == second
