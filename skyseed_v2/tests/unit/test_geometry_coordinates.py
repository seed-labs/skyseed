from __future__ import annotations

from math import cos, radians, sin
from pathlib import Path

import pytest

from skyseed_v2.simulator.geometry import (
    EarthFixedPosition,
    ecef_distance_m,
    elevation_deg,
    geodetic_to_ecef,
)
from skyseed_v2.simulator.geometry._elevation import (
    _elevation_distance_m,
    _elevation_deg_from_local_up_and_distance,
    _wgs84_local_up_unit_vector,
)


def test_elevation_handles_coincident_horizon_below_and_overhead_positions() -> None:
    observer = geodetic_to_ecef(0.0, 0.0, 0.0)
    overhead = EarthFixedPosition(
        observer.x_m + 1_000.0,
        observer.y_m,
        observer.z_m,
    )
    horizon = EarthFixedPosition(
        observer.x_m,
        observer.y_m + 1_000.0,
        observer.z_m,
    )
    below_horizon = EarthFixedPosition(
        observer.x_m - 1_000.0,
        observer.y_m + 1_000.0,
        observer.z_m,
    )

    assert elevation_deg(observer, overhead) == pytest.approx(90.0)
    assert elevation_deg(observer, horizon) == pytest.approx(0.0, abs=1e-12)
    assert elevation_deg(observer, below_horizon) == pytest.approx(-45.0)
    with pytest.raises(ValueError, match="undefined for coincident positions"):
        elevation_deg(observer, observer)


def test_wgs84_local_up_uses_degree_inputs_and_nonzero_observer_altitude() -> None:
    latitude_deg = 47.25
    longitude_deg = -122.75
    observer = geodetic_to_ecef(
        latitude_deg,
        longitude_deg,
        12_345.0,
    )
    local_up = _wgs84_local_up_unit_vector(observer)
    latitude_rad = radians(latitude_deg)
    longitude_rad = radians(longitude_deg)

    assert local_up == pytest.approx(
        (
            cos(latitude_rad) * cos(longitude_rad),
            cos(latitude_rad) * sin(longitude_rad),
            sin(latitude_rad),
        ),
        abs=1e-15,
    )
    overhead = EarthFixedPosition(
        observer.x_m + 100_000.0 * local_up[0],
        observer.y_m + 100_000.0 * local_up[1],
        observer.z_m + 100_000.0 * local_up[2],
    )
    assert elevation_deg(observer, overhead) == pytest.approx(90.0)


def test_public_elevation_delegates_to_the_authoritative_primitive_exactly() -> None:
    observer = geodetic_to_ecef(23.5, -70.25, 1_234.0)
    target = geodetic_to_ecef(24.0, -69.75, 550_000.0)
    local_up = _wgs84_local_up_unit_vector(observer)
    distance_m = _elevation_distance_m(observer, target)

    optimized = _elevation_deg_from_local_up_and_distance(
        observer,
        target,
        local_up,
        distance_m,
    )

    assert optimized == elevation_deg(observer, target)


def test_precomputed_physical_distance_reuses_the_same_elevation_formula() -> None:
    observer = geodetic_to_ecef(23.5, -70.25, 1_234.0)
    target = geodetic_to_ecef(24.0, -69.75, 550_000.0)

    optimized = _elevation_deg_from_local_up_and_distance(
        observer,
        target,
        _wgs84_local_up_unit_vector(observer),
        ecef_distance_m(observer, target),
    )

    assert optimized == pytest.approx(elevation_deg(observer, target), abs=1e-12)


def test_optimized_elevation_clamps_both_numerical_domain_boundaries() -> None:
    observer = EarthFixedPosition(6_378_137.0, 0.0, 0.0)
    local_up = (1.0, 0.0, 0.0)
    slightly_short_distance_m = 1.0 - 2.0**-52
    overhead = EarthFixedPosition(observer.x_m + 1.0, 0.0, 0.0)
    directly_below = EarthFixedPosition(observer.x_m - 1.0, 0.0, 0.0)

    assert _elevation_deg_from_local_up_and_distance(
        observer,
        overhead,
        local_up,
        slightly_short_distance_m,
    ) == 90.0
    assert _elevation_deg_from_local_up_and_distance(
        observer,
        directly_below,
        local_up,
        slightly_short_distance_m,
    ) == -90.0


def test_production_has_one_elevation_formula_and_no_model_copy() -> None:
    repository = Path(__file__).resolve().parents[2]
    production_roots = (
        repository / "src" / "skyseed_v2",
        repository / "tools",
    )
    formula_owners = tuple(
        path.relative_to(repository).as_posix()
        for root in production_roots
        for path in sorted(root.rglob("*.py"))
        if "sine_elevation =" in path.read_text(encoding="utf-8")
    )
    assert formula_owners == (
        "src/skyseed_v2/simulator/geometry/_elevation.py",
    )

    physical_core_source = (
        repository
        / "src"
        / "skyseed_v2"
        / "simulator"
        / "physical"
        / "models"
        / "links"
        / "_batched_geometry.py"
    ).read_text(encoding="utf-8")
    assert "def _local_up(" not in physical_core_source
    assert "def _elevation_deg_with_distance(" not in physical_core_source
    assert "_elevation_degrees_array(" in physical_core_source
    assert "_wgs84_local_up_unit_vector(" in physical_core_source
