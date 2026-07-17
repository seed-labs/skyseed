from dataclasses import fields
from json import dumps, loads
from pathlib import Path

import pytest

from examples.reference.v1_static_input_adapter import V1StaticInputAdapter
from skyseed_v2 import GroundStationBuilder, ground_station_id_for
from skyseed_v2.seed import SkyseedAutonomousSystem


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
V1_ROOT = REPOSITORY_ROOT / "skyseed_v1"


def adapter(
    *,
    orbit_path: Path | None = None,
    gateway_path: Path | None = None,
    binding_path: Path | None = None,
) -> V1StaticInputAdapter:
    return V1StaticInputAdapter(
        orbit_catalog_path=(
            orbit_path
            or V1_ROOT / "data/orbits/planned_shell_orbit_data_s1_s5.json"
        ),
        gateway_catalog_path=(
            gateway_path or V1_ROOT / "data/ground/starlink_gateways.json"
        ),
        gateway_binding_path=(
            binding_path
            or V1_ROOT / "data/ground/s4_visualization_gateway_binding_12.json"
        ),
    )


def test_reference_adapter_returns_only_generic_static_specifications() -> None:
    result = adapter().load(
        reference_shell_label="S4",
        logical_shell_id="scale-shell",
        operator="Starlink",
    )
    generation = result.shell.generation
    assert generation is not None

    assert result.shell.shell_id == "scale-shell"
    assert result.shell.altitude_km == 560.0
    assert result.shell.inclination_deg == 97.6
    assert generation.orbit_count == 6
    assert generation.satellites_per_orbit == 58
    assert generation.raan.step_deg == 60.0
    assert generation.phase.alternating_orbit_offset_deg == pytest.approx(
        180.0 / 58, abs=0.000001
    )
    assert len(result.ground_stations) == 12
    assert all(item.operator == "Starlink" for item in result.ground_stations)
    assert all(item.ground_station_id is None for item in result.ground_stations)
    assert all(not ground_station_id_for(item).startswith("gw") for item in result.ground_stations)
    assert all(
        "epoch" not in field.name for field in fields(type(result.shell))
    )
    assert all(not hasattr(item, "service_address") for item in result.ground_stations)

    built = GroundStationBuilder().build(
        SkyseedAutonomousSystem(65000), result.ground_stations
    )
    assert len(built.ground_stations) == 12
    assert all(station.service_config == {} for station in built.ground_stations)


def test_reference_adapter_is_independent_of_json_record_order(tmp_path: Path) -> None:
    orbit_payload = loads(
        (V1_ROOT / "data/orbits/planned_shell_orbit_data_s1_s5.json").read_text()
    )
    binding_payload = loads(
        (V1_ROOT / "data/ground/s4_visualization_gateway_binding_12.json").read_text()
    )
    reversed_orbit = tmp_path / "orbits.json"
    reversed_binding = tmp_path / "bindings.json"
    orbit_payload["selected_records"].reverse()
    reversed_orbit.write_text(dumps(orbit_payload))
    reversed_binding.write_text(dumps(list(reversed(binding_payload))))

    original = adapter().load(
        reference_shell_label="S4",
        logical_shell_id="scale-shell",
        operator="Starlink",
    )
    reordered = adapter(
        orbit_path=reversed_orbit,
        binding_path=reversed_binding,
    ).load(
        reference_shell_label="S4",
        logical_shell_id="scale-shell",
        operator="Starlink",
    )

    assert reordered == original


def test_reference_adapter_fails_closed_on_invalid_or_missing_data(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json")
    with pytest.raises(ValueError, match="valid JSON"):
        adapter(orbit_path=invalid).load(
            reference_shell_label="S4",
            logical_shell_id="scale-shell",
            operator="Starlink",
        )

    binding_payload = loads(
        (V1_ROOT / "data/ground/s4_visualization_gateway_binding_12.json").read_text()
    )
    binding_payload[0]["visualization_gateway_id"] = "missing-source"
    missing = tmp_path / "missing-binding.json"
    missing.write_text(dumps(binding_payload))
    with pytest.raises(ValueError, match="unknown catalog id"):
        adapter(binding_path=missing).load(
            reference_shell_label="S4",
            logical_shell_id="scale-shell",
            operator="Starlink",
        )
