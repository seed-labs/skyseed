from __future__ import annotations

import gc
import importlib
from collections import Counter
import os
from pathlib import Path
import subprocess
import sys
import threading
import warnings
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from math import ceil, pi, sqrt
from random import Random
from types import MappingProxyType
import weakref

import astropy.units as u
from astropy.coordinates import CartesianRepresentation, GCRS, ITRS
from astropy.time import Time
from astropy.utils import iers
import pytest

import skyseed_v2
import skyseed_v2.simulator.geometry.coordinates as coordinates
import skyseed_v2.simulator.slicing.slicer as slicer_module
from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    CircularOrbitPositionModel,
    Connection,
    ConnectionId,
    ConnectionKind,
    ConnectionSlice,
    DirectionalConnectionParameters,
    EarthFixedPosition,
    GeometricConnectionModel,
    GroundStationRouteTarget,
    HopCountRouteModel,
    RouteKey,
    RouteSlice,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    SphericalCap,
    TopologySlice,
    ZoneRouteTarget,
)
from skyseed_v2.simulator.geometry import (
    ecef_distance_m,
    elevation_deg,
    geodetic_to_ecef,
    has_earth_line_of_sight,
)
from skyseed_v2.simulator.models.geometric_connection import (
    _GroundAccessPreference,
    _prepare_isl_pairs,
    _select_primary_ground_station_accesses,
    _select_ground_station_accesses,
)
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from skyseed_v2.simulator.topology import GroundStation, Satellite


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _satellite(
    satellite_id: str,
    *,
    orbit_id: str = "orbit-1",
    orbit_index: int = 0,
    altitude_km: float = 550.0,
    inclination_deg: float = 53.0,
    right_ascension_deg: float = 245.0,
    mean_anomaly_deg: float = 126.0,
    epoch_utc: datetime = EPOCH,
    max_simultaneous_ground_station_accesses: int | None = None,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(65001, "satellite", satellite_id),
        satellite_id=satellite_id,
        shell_id="shell-1",
        orbit_id=orbit_id,
        orbit_index=orbit_index,
        altitude_km=altitude_km,
        inclination_deg=inclination_deg,
        right_ascension_deg=right_ascension_deg,
        mean_anomaly_deg=mean_anomaly_deg,
        epoch_utc=epoch_utc,
        max_simultaneous_ground_station_accesses=(
            max_simultaneous_ground_station_accesses
        ),
    )


def _ground_station(
    station_id: str = "gs-1",
    *,
    max_simultaneous_satellite_accesses: int | None = None,
    latitude_deg: float = 40.7128,
    longitude_deg: float = -74.006,
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(65001, "ground-station", station_id),
        ground_station_id=station_id,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        elevation_m=10.0,
        station_type="gateway",
        operator="Skyseed",
        max_simultaneous_satellite_accesses=(
            max_simultaneous_satellite_accesses
        ),
    )


def _zone_layout() -> ZoneLayoutDefinition:
    return ZoneLayoutDefinition(
        zones=(
            ZoneDefinition(
                zone_id="near",
                priority=10,
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=35.0,
                    north_latitude_deg=45.0,
                    west_longitude_deg=-80.0,
                    east_longitude_deg=-70.0,
                ),
            ),
            ZoneDefinition(
                zone_id="far",
                priority=1,
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=-45.0,
                    north_latitude_deg=-35.0,
                    west_longitude_deg=90.0,
                    east_longitude_deg=100.0,
                ),
            ),
        ),
        allow_overlap=False,
    )


def _simulator(
    static_input: SimulatorStaticInput,
    *,
    end_time: datetime | None = EPOCH + timedelta(seconds=20),
    zone_layout: ZoneLayoutDefinition | None = None,
    position_model=None,
) -> tuple[Simulator, object]:
    simulator = Simulator(
        SimulationDefinition(
            static_input,
            SimulationTimeline(EPOCH, end_time, timedelta(seconds=10)),
        )
    )
    topology = simulator.create_topology(
        position_model=(
            CircularOrbitPositionModel()
            if position_model is None
            else position_model
        ),
        zone_layout_definition=zone_layout,
    )
    return simulator, topology


def _connection_model(
    *,
    zone_minimum_elevation_deg: float = 30.0,
    maximum_isl_distance_m: float = 5_000_000.0,
    maximum_ground_station_access_count: int = 1,
    ground_station_minimum_elevation_deg: float = 0.0,
    ground_station_maximum_distance_m: float | None = None,
) -> GeometricConnectionModel:
    return GeometricConnectionModel(
        maximum_isl_distance_m=maximum_isl_distance_m,
        ground_station_minimum_elevation_deg=(
            ground_station_minimum_elevation_deg
        ),
        maximum_ground_station_access_count=(
            maximum_ground_station_access_count
        ),
        ground_station_maximum_distance_m=(
            ground_station_maximum_distance_m
        ),
        zone_coverage_minimum_elevation_deg=zone_minimum_elevation_deg,
        bandwidth_bps_by_kind={
            ConnectionKind.INTER_SATELLITE: 1_000_000,
            ConnectionKind.GROUND_STATION_ACCESS: 2_000_000,
        },
        packet_loss_ppm_by_kind={
            ConnectionKind.INTER_SATELLITE: 10,
            ConnectionKind.GROUND_STATION_ACCESS: 20,
        },
        propagation_speed_m_per_s=299_792_458.0,
    )


class _CountingCircularPositionModel:
    def __init__(self) -> None:
        self.model = CircularOrbitPositionModel()
        self.calls = 0

    def compute_positions(self, topology, timestamp):
        self.calls += 1
        return self.model.compute_positions(topology, timestamp)


def test_shared_coordinates_and_position_model_are_complete_and_deterministic() -> None:
    equator = geodetic_to_ecef(0.0, 0.0, 0.0)
    assert equator.x_m == pytest.approx(6_378_137.0, abs=1e-6)
    assert equator.y_m == pytest.approx(0.0, abs=1e-6)
    assert equator.z_m == pytest.approx(0.0, abs=1e-6)
    assert equator.geographic == pytest.approx((0.0, 0.0, 0.0), abs=1e-7)
    with pytest.raises(FrozenInstanceError):
        equator.x_m = 0.0

    static_input = SimulatorStaticInput(
        (
            _satellite("sat-0"),
            _satellite("sat-1", orbit_index=1, mean_anomaly_deg=146.0),
        ),
        (_ground_station(),),
    )
    _, topology = _simulator(static_input)
    model = CircularOrbitPositionModel()
    first = model.compute_positions(topology, EPOCH)
    repeated = model.compute_positions(topology, EPOCH)

    assert first == repeated
    assert set(first) == {
        NodeId(65001, "satellite", "sat-0"),
        NodeId(65001, "satellite", "sat-1"),
        NodeId(65001, "ground-station", "gs-1"),
    }
    assert first[NodeId(65001, "ground-station", "gs-1")].geographic == (
        pytest.approx((40.7128, -74.006, 10.0), abs=1e-6)
    )
    assert ecef_distance_m(
        first[NodeId(65001, "satellite", "sat-0")],
        repeated[NodeId(65001, "satellite", "sat-0")],
    ) == 0.0
    with pytest.raises(TypeError):
        first[NodeId(65001, "satellite", "new")] = equator
    with pytest.raises(ValueError, match="Earth-orientation"):
        model.compute_positions(
            topology, datetime(2040, 1, 1, tzinfo=timezone.utc)
        )


def test_wgs84_line_of_sight_handles_open_segment_boundaries() -> None:
    semi_major_axis_m = 6_378_137.0
    surface = EarthFixedPosition(semi_major_axis_m, 0.0, 0.0)
    antipode = EarthFixedPosition(-semi_major_axis_m, 0.0, 0.0)
    same_side_satellite = EarthFixedPosition(
        semi_major_axis_m + 500_000.0, 0.0, 0.0
    )
    opposite_side_satellite = EarthFixedPosition(
        -semi_major_axis_m - 500_000.0, 0.0, 0.0
    )
    tangent_first = EarthFixedPosition(
        semi_major_axis_m, -1_000_000.0, 0.0
    )
    tangent_second = EarthFixedPosition(
        semi_major_axis_m, 1_000_000.0, 0.0
    )
    slightly_inside_surface_noise = EarthFixedPosition(
        semi_major_axis_m * (1.0 - 2e-11), 0.0, 0.0
    )
    materially_inside = EarthFixedPosition(
        semi_major_axis_m - 1_000.0, 0.0, 0.0
    )

    assert not has_earth_line_of_sight(surface, antipode)
    assert has_earth_line_of_sight(surface, same_side_satellite)
    assert not has_earth_line_of_sight(
        surface, opposite_side_satellite
    )
    assert has_earth_line_of_sight(tangent_first, tangent_second)
    assert has_earth_line_of_sight(
        slightly_inside_surface_noise, same_side_satellite
    )
    assert not has_earth_line_of_sight(
        materially_inside, same_side_satellite
    )


def test_iers_policy_is_scoped_and_import_has_no_global_side_effect() -> None:
    repository_root = Path(__file__).parents[2]
    import_check = subprocess.run(
        (
            sys.executable,
            "-c",
            "\n".join(
                (
                    "from astropy.utils import iers",
                    "iers.conf.auto_download = True",
                    "iers.conf.iers_degraded_accuracy = 'warn'",
                    "before = (iers.conf.auto_download, "
                    "iers.conf.iers_degraded_accuracy)",
                    "import skyseed_v2.simulator.geometry",
                    "after = (iers.conf.auto_download, "
                    "iers.conf.iers_degraded_accuracy)",
                    "print(f'before={before!r} after={after!r}')",
                    "assert after == before",
                )
            ),
        ),
        cwd=repository_root,
        env={
            **os.environ,
            "PYTHONPATH": str(repository_root / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_check.returncode == 0, import_check.stderr
    assert (
        import_check.stdout.strip()
        == "before=(True, 'warn') after=(True, 'warn')"
    )

    with iers.conf.set_temp("auto_download", True):
        with iers.conf.set_temp("iers_degraded_accuracy", "warn"):
            sentinel = (
                iers.conf.auto_download,
                iers.conf.iers_degraded_accuracy,
            )
            reloaded = importlib.reload(coordinates)
            assert (
                iers.conf.auto_download,
                iers.conf.iers_degraded_accuracy,
            ) == sentinel

            converted = reloaded._gcrs_cartesian_to_ecef(
                7_000_000.0, 0.0, 0.0, EPOCH
            )
            assert isinstance(converted, EarthFixedPosition)
            assert (
                iers.conf.auto_download,
                iers.conf.iers_degraded_accuracy,
            ) == sentinel

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with pytest.raises(ValueError, match="Earth-orientation"):
                    reloaded._gcrs_cartesian_to_ecef(
                        7_000_000.0,
                        0.0,
                        0.0,
                        datetime(2040, 1, 1, tzinfo=timezone.utc),
                    )
            assert not caught
            assert (
                iers.conf.auto_download,
                iers.conf.iers_degraded_accuracy,
            ) == sentinel


def test_circular_orbit_private_gcrs_conventions_have_independent_oracles() -> None:
    reference_radius_m = 6_378_137.0
    altitude_km = 550.0
    radius_m = reference_radius_m + altitude_km * 1_000.0
    gravitational_parameter = 3.986_004_418e14
    model = CircularOrbitPositionModel()

    def expected_ecef(
        inertial_xyz_m: tuple[float, float, float],
        timestamp: datetime,
    ) -> tuple[float, float, float]:
        obstime = Time(timestamp)
        gcrs = GCRS(
            CartesianRepresentation(
                inertial_xyz_m[0] * u.m,
                inertial_xyz_m[1] * u.m,
                inertial_xyz_m[2] * u.m,
            ),
            obstime=obstime,
        )
        itrs = gcrs.transform_to(ITRS(obstime=obstime)).cartesian
        return (
            itrs.x.to_value(u.m),
            itrs.y.to_value(u.m),
            itrs.z.to_value(u.m),
        )

    cases = (
        (
            _satellite(
                "axis-x",
                altitude_km=altitude_km,
                inclination_deg=0.0,
                right_ascension_deg=0.0,
                mean_anomaly_deg=0.0,
            ),
            (radius_m, 0.0, 0.0),
        ),
        (
            _satellite(
                "axis-y",
                altitude_km=altitude_km,
                inclination_deg=0.0,
                right_ascension_deg=90.0,
                mean_anomaly_deg=0.0,
            ),
            (0.0, radius_m, 0.0),
        ),
        (
            _satellite(
                "axis-z",
                altitude_km=altitude_km,
                inclination_deg=90.0,
                right_ascension_deg=0.0,
                mean_anomaly_deg=90.0,
            ),
            (0.0, 0.0, radius_m),
        ),
    )
    with iers.conf.set_temp("auto_download", False):
        with iers.conf.set_temp("iers_degraded_accuracy", "error"):
            for satellite_input, inertial_expected in cases:
                _, topology = _simulator(
                    SimulatorStaticInput((satellite_input,))
                )
                actual = model.compute_positions(topology, EPOCH)[
                    satellite_input.node_id
                ]
                assert (actual.x_m, actual.y_m, actual.z_m) == pytest.approx(
                    expected_ecef(inertial_expected, EPOCH),
                    abs=1e-6,
                )
                assert sqrt(
                    actual.x_m**2 + actual.y_m**2 + actual.z_m**2
                ) == pytest.approx(radius_m, abs=1e-6)

            periodic_satellite = cases[0][0]
            _, periodic_topology = _simulator(
                SimulatorStaticInput((periodic_satellite,))
            )
            period_seconds = 2.0 * pi * sqrt(
                radius_m**3 / gravitational_parameter
            )
            later = EPOCH + timedelta(seconds=period_seconds)
            first = model.compute_positions(periodic_topology, EPOCH)[
                periodic_satellite.node_id
            ]
            repeated = model.compute_positions(periodic_topology, later)[
                periodic_satellite.node_id
            ]

            def independently_to_gcrs(
                position: EarthFixedPosition,
                timestamp: datetime,
            ) -> tuple[float, float, float]:
                obstime = Time(timestamp)
                itrs = ITRS(
                    CartesianRepresentation(
                        position.x_m * u.m,
                        position.y_m * u.m,
                        position.z_m * u.m,
                    ),
                    obstime=obstime,
                )
                cartesian = itrs.transform_to(
                    GCRS(obstime=obstime)
                ).cartesian
                return (
                    cartesian.x.to_value(u.m),
                    cartesian.y.to_value(u.m),
                    cartesian.z.to_value(u.m),
                )

            assert independently_to_gcrs(
                repeated, later
            ) == pytest.approx(
                independently_to_gcrs(first, EPOCH),
                abs=0.01,
            )


def test_spherical_cap_queries_handle_date_line_and_pole() -> None:
    layout = ZoneLayoutDefinition(
        zones=(
            ZoneDefinition(
                zone_id="date-line",
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=-10.0,
                    north_latitude_deg=10.0,
                    west_longitude_deg=170.0,
                    east_longitude_deg=-170.0,
                ),
            ),
            ZoneDefinition(
                zone_id="north-pole",
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=80.0,
                    north_latitude_deg=90.0,
                    west_longitude_deg=-20.0,
                    east_longitude_deg=20.0,
                ),
            ),
        ),
        allow_overlap=False,
    )
    _, topology = _simulator(SimulatorStaticInput(), zone_layout=layout)
    zone_layout = topology.get_zone_layout()

    assert tuple(
        zone.zone_id
        for zone in zone_layout.find_intersecting_zones(
            SphericalCap((0.0, 179.0), 3.0)
        )
    ) == ("date-line",)
    assert tuple(
        zone.zone_id
        for zone in topology.find_intersecting_zones(
            SphericalCap((89.0, 0.0), 3.0)
        )
    ) == ("north-pole",)


def test_connection_model_ecef_index_handles_date_line_and_pole() -> None:
    satellites = (
        _satellite("date-line", mean_anomaly_deg=0.0),
        _satellite("north-pole", orbit_index=1, mean_anomaly_deg=180.0),
    )
    layout = ZoneLayoutDefinition(
        zones=(
            ZoneDefinition(
                zone_id="date-line",
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=-10.0,
                    north_latitude_deg=10.0,
                    west_longitude_deg=170.0,
                    east_longitude_deg=-170.0,
                ),
            ),
            ZoneDefinition(
                zone_id="north-pole",
                geometry=LatitudeLongitudeRectangleDefinition(
                    south_latitude_deg=80.0,
                    north_latitude_deg=90.0,
                    west_longitude_deg=-20.0,
                    east_longitude_deg=20.0,
                ),
            ),
        ),
        allow_overlap=False,
    )
    _, topology = _simulator(
        SimulatorStaticInput(satellites), zone_layout=layout
    )
    positions = {
        satellites[0].node_id: geodetic_to_ecef(0.0, 180.0, 560_000.0),
        satellites[1].node_id: geodetic_to_ecef(85.0, 0.0, 560_000.0),
    }
    model = _connection_model(zone_minimum_elevation_deg=10.0)
    result = model.compute_connection_slice(
        topology,
        EPOCH,
        positions,
        ConnectionSlice(),
    )

    assert result.zone_service_assignments == {
        "date-line": satellites[0].node_id,
        "north-pole": satellites[1].node_id,
    }
    counts = model._last_candidate_check_counts
    assert counts is not None
    assert counts.zone_pairs_before == 4
    assert counts.zone_pairs_after == 2


def test_connection_model_without_zone_layout_keeps_empty_assignment_semantics() -> None:
    satellite = _satellite("sat-0")
    static_input = SimulatorStaticInput((satellite,))
    _, topology = _simulator(static_input)
    positions = CircularOrbitPositionModel().compute_positions(topology, EPOCH)

    result = _connection_model().compute_connection_slice(
        topology,
        EPOCH,
        positions,
        ConnectionSlice(),
    )

    assert result.zone_service_assignments == {}


def test_connection_values_normalize_identity_units_and_deep_immutability() -> None:
    first = NodeId(2, "satellite", "b")
    second = NodeId(1, "satellite", "a")
    identifier = ConnectionId(
        ConnectionKind.INTER_SATELLITE, first, second
    )
    parameters = DirectionalConnectionParameters(1_000, 1, 100)
    connection_slice = ConnectionSlice(
        (
            Connection(
                identifier,
                parameters,
                parameters,
            ),
        ),
        {"zone-a": second},
    )

    assert identifier.endpoint_a is second
    assert identifier.endpoint_b is first
    assert connection_slice.get_connection(identifier).endpoint_a_to_b.delay_us == 1
    assert connection_slice.get_service_satellite("zone-a") is second
    with pytest.raises(TypeError):
        connection_slice.zone_service_assignments["zone-b"] = first
    with pytest.raises(ValueError, match="packet_loss"):
        DirectionalConnectionParameters(1, 0, 1_000_001)


def test_geometric_model_forms_mean_anomaly_rings_and_stable_cyclic_pairing() -> None:
    satellites = (
        _satellite("a0", orbit_index=9, mean_anomaly_deg=0.0),
        _satellite("a1", orbit_index=3, mean_anomaly_deg=120.0),
        _satellite("a2", orbit_index=7, mean_anomaly_deg=240.0),
        _satellite(
            "b0",
            orbit_id="orbit-2",
            orbit_index=8,
            right_ascension_deg=120.0,
            mean_anomaly_deg=0.0,
        ),
        _satellite(
            "b1",
            orbit_id="orbit-2",
            orbit_index=2,
            right_ascension_deg=120.0,
            mean_anomaly_deg=120.0,
        ),
        _satellite(
            "b2",
            orbit_id="orbit-2",
            orbit_index=6,
            right_ascension_deg=120.0,
            mean_anomaly_deg=240.0,
        ),
    )
    _, topology = _simulator(SimulatorStaticInput(satellites))
    y_by_id = {
        "a0": 0.0,
        "a1": 1_000.0,
        "a2": 2_000.0,
        "b0": 1_000.0,
        "b1": 2_000.0,
        "b2": 0.0,
    }
    positions = MappingProxyType(
        {
            satellite.node_id: EarthFixedPosition(
                7_000_000.0, y_by_id[satellite.satellite_id], 0.0
            )
            for satellite in satellites
        }
    )
    model = _connection_model(zone_minimum_elevation_deg=0.0)
    result = model.compute_connection_slice(
        topology, EPOCH, positions, ConnectionSlice()
    )
    pairs = {
        frozenset(
            (
                connection.connection_id.endpoint_a.entity_id,
                connection.connection_id.endpoint_b.entity_id,
            )
        )
        for connection in result.connections
    }

    assert len(result.connections) == 9
    assert {
        frozenset(("a0", "a1")),
        frozenset(("a1", "a2")),
        frozenset(("a2", "a0")),
    } <= pairs
    assert {
        frozenset(("a0", "b2")),
        frozenset(("a1", "b0")),
        frozenset(("a2", "b1")),
    } <= pairs


@pytest.mark.parametrize("duplicate_raan", (False, True))
def test_geometric_model_fails_closed_on_ambiguous_orbit_geometry(
    duplicate_raan: bool,
) -> None:
    if duplicate_raan:
        satellites = (
            _satellite("a", orbit_id="a", mean_anomaly_deg=0.0),
            _satellite(
                "b",
                orbit_id="b",
                right_ascension_deg=245.0,
                mean_anomaly_deg=90.0,
            ),
        )
        message = "unique RAAN"
    else:
        satellites = (
            _satellite("a", orbit_index=0, mean_anomaly_deg=0.0),
            _satellite("b", orbit_index=1, mean_anomaly_deg=0.0),
        )
        message = "unique mean_anomaly"
    _, topology = _simulator(SimulatorStaticInput(satellites))
    positions = {
        satellite.node_id: EarthFixedPosition(
            7_000_000.0, index * 1_000.0, 0.0
        )
        for index, satellite in enumerate(satellites)
    }

    with pytest.raises(ValueError, match=message):
        _connection_model().compute_connection_slice(
            topology, EPOCH, positions, ConnectionSlice()
        )


def test_geometric_model_applies_distance_and_earth_obstruction() -> None:
    satellites = (
        _satellite("a", orbit_index=0, mean_anomaly_deg=0.0),
        _satellite("b", orbit_index=1, mean_anomaly_deg=30.0),
    )
    _, topology = _simulator(SimulatorStaticInput(satellites))
    nearby = {
        satellites[0].node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        satellites[1].node_id: EarthFixedPosition(
            7_000_000.0, 1_000.0, 0.0
        ),
    }

    inactive = _connection_model(
        maximum_isl_distance_m=500.0
    ).compute_connection_slice(
        topology, EPOCH, nearby, ConnectionSlice()
    )
    active = _connection_model(
        maximum_isl_distance_m=2_000.0
    ).compute_connection_slice(
        topology, EPOCH, nearby, ConnectionSlice()
    )
    obstructed = _connection_model(
        maximum_isl_distance_m=20_000_000.0
    ).compute_connection_slice(
        topology,
        EPOCH,
        {
            satellites[0].node_id: EarthFixedPosition(
                7_000_000.0, 0.0, 0.0
            ),
            satellites[1].node_id: EarthFixedPosition(
                -7_000_000.0, 0.0, 0.0
            ),
        },
        ConnectionSlice(),
    )

    assert not inactive.connections
    assert len(active.connections) == 1
    assert not obstructed.connections


def test_geometric_model_uses_stricter_site_and_model_access_limits() -> None:
    satellites = (
        _satellite("sat-0", mean_anomaly_deg=0.0),
        _satellite("sat-1", orbit_index=1, mean_anomaly_deg=120.0),
        _satellite("sat-2", orbit_index=2, mean_anomaly_deg=240.0),
    )
    ground_stations = (
        _ground_station(
            "site-cap-1", max_simultaneous_satellite_accesses=1
        ),
        _ground_station(
            "site-cap-40", max_simultaneous_satellite_accesses=40
        ),
        _ground_station("site-unspecified"),
    )
    _, topology = _simulator(
        SimulatorStaticInput(satellites, ground_stations)
    )
    ground_position = geodetic_to_ecef(40.7128, -74.006, 10.0)
    radius = sqrt(
        ground_position.x_m**2
        + ground_position.y_m**2
        + ground_position.z_m**2
    )
    positions = {
        station.node_id: ground_position for station in ground_stations
    }
    for index, satellite in enumerate(satellites):
        scale = (radius + 550_000.0 + index * 1_000.0) / radius
        positions[satellite.node_id] = EarthFixedPosition(
            ground_position.x_m * scale,
            ground_position.y_m * scale,
            ground_position.z_m * scale,
        )

    result = _connection_model(
        maximum_isl_distance_m=1.0,
        maximum_ground_station_access_count=2,
    ).compute_connection_slice(
        topology,
        EPOCH,
        positions,
        ConnectionSlice(),
    )
    counts = Counter(
        endpoint.entity_id
        for connection in result.connections
        if connection.connection_id.kind
        is ConnectionKind.GROUND_STATION_ACCESS
        for endpoint in (
            connection.connection_id.endpoint_a,
            connection.connection_id.endpoint_b,
        )
        if endpoint.entity_kind == "ground-station"
    )

    assert counts == Counter(
        {"site-cap-1": 1, "site-cap-40": 2, "site-unspecified": 2}
    )


def test_optimized_geometry_matches_preoptimization_reference_exactly() -> None:
    satellites = tuple(
        _satellite(
            f"sat-{index}",
            orbit_index=index,
            mean_anomaly_deg=index * 60.0,
        )
        for index in range(6)
    )
    stations = (
        _ground_station("site-one", max_simultaneous_satellite_accesses=1),
        _ground_station("site-three", max_simultaneous_satellite_accesses=3),
    )
    _, topology = _simulator(
        SimulatorStaticInput(satellites, stations),
        zone_layout=_zone_layout(),
    )
    position_model = CircularOrbitPositionModel()

    def reference(
        model: GeometricConnectionModel,
        positions: MappingProxyType,
    ) -> ConnectionSlice:
        spatial_satellites = tuple(
            sorted(
                (
                    satellite
                    for autonomous_system in topology.autonomous_systems
                    for shell in autonomous_system.shells
                    for orbit in shell.orbits
                    for satellite in orbit.satellites
                ),
                key=lambda value: value.node_id.value,
            )
        )
        spatial_stations = tuple(
            sorted(
                (
                    station
                    for autonomous_system in topology.autonomous_systems
                    for station in autonomous_system.ground_stations
                ),
                key=lambda value: value.node_id.value,
            )
        )
        connections = []
        for first, second in _prepare_isl_pairs(topology, positions):
            distance = ecef_distance_m(positions[first], positions[second])
            if distance <= 5_000_000.0 and has_earth_line_of_sight(
                positions[first], positions[second]
            ):
                connections.append(
                    model._create_connection(
                        ConnectionKind.INTER_SATELLITE,
                        first,
                        second,
                        distance,
                    )
                )
        for station in spatial_stations:
            eligible = []
            for satellite in spatial_satellites:
                distance = ecef_distance_m(
                    positions[station.node_id], positions[satellite.node_id]
                )
                elevation = elevation_deg(
                    positions[station.node_id], positions[satellite.node_id]
                )
                if elevation >= 0.0 and has_earth_line_of_sight(
                    positions[station.node_id], positions[satellite.node_id]
                ):
                    eligible.append(
                        (
                            elevation,
                            satellite.node_id.value,
                            satellite,
                            distance,
                        )
                    )
            eligible.sort(key=lambda value: (-value[0], value[1]))
            limit = min(
                3,
                station.max_simultaneous_satellite_accesses or 3,
            )
            for _, _, satellite, distance in eligible[:limit]:
                connections.append(
                    model._create_connection(
                        ConnectionKind.GROUND_STATION_ACCESS,
                        station.node_id,
                        satellite.node_id,
                        distance,
                    )
                )
        candidates = {}
        for satellite in spatial_satellites:
            satellite_position = positions[satellite.node_id]
            cap = SphericalCap(
                (
                    satellite_position.latitude_deg,
                    satellite_position.longitude_deg,
                ),
                60.0,
            )
            for zone in topology.find_intersecting_zones(cap):
                representative = geodetic_to_ecef(
                    *zone.representative_point, 0.0
                )
                elevation = elevation_deg(representative, satellite_position)
                if elevation >= 30.0 and has_earth_line_of_sight(
                    representative, satellite_position
                ):
                    rank = (-elevation, satellite.node_id.value)
                    if zone.zone_id not in candidates or rank < candidates[
                        zone.zone_id
                    ][0]:
                        candidates[zone.zone_id] = (rank, satellite.node_id)
        assignments = {
            zone_id: value[1]
            for zone_id, value in sorted(candidates.items())
        }
        return ConnectionSlice(connections, assignments)

    for offset in (0, 10):
        timestamp = EPOCH + timedelta(seconds=offset)
        positions = position_model.compute_positions(topology, timestamp)
        optimized_model = _connection_model(
            maximum_ground_station_access_count=3
        )
        expected = reference(optimized_model, positions)
        actual = optimized_model.compute_connection_slice(
            topology, timestamp, positions, ConnectionSlice()
        )
        assert actual == expected


def test_capacitated_ground_access_falls_back_without_stranding_a_site() -> None:
    satellites = (
        _satellite(
            "s1",
            mean_anomaly_deg=0.0,
            max_simultaneous_ground_station_accesses=1,
        ),
        _satellite(
            "s2",
            orbit_index=1,
            mean_anomaly_deg=180.0,
            max_simultaneous_ground_station_accesses=1,
        ),
    )
    flexible = _ground_station(
        "g1-flexible",
        max_simultaneous_satellite_accesses=1,
        latitude_deg=0.0,
        longitude_deg=10.0,
    )
    constrained = _ground_station(
        "g2-constrained",
        max_simultaneous_satellite_accesses=1,
        latitude_deg=0.0,
        longitude_deg=0.0,
    )
    _, topology = _simulator(
        SimulatorStaticInput(satellites, (flexible, constrained))
    )
    positions = {
        flexible.node_id: geodetic_to_ecef(0.0, 10.0, 0.0),
        constrained.node_id: geodetic_to_ecef(0.0, 0.0, 0.0),
        satellites[0].node_id: geodetic_to_ecef(0.0, 0.0, 560_000.0),
        satellites[1].node_id: geodetic_to_ecef(0.0, 25.0, 560_000.0),
    }

    result = _connection_model(
        maximum_isl_distance_m=1.0,
        maximum_ground_station_access_count=2,
        ground_station_minimum_elevation_deg=10.0,
    ).compute_connection_slice(
        topology, EPOCH, positions, ConnectionSlice()
    )
    access_pairs = {
        frozenset(
            (
                connection.connection_id.endpoint_a,
                connection.connection_id.endpoint_b,
            )
        )
        for connection in result.connections
        if connection.connection_id.kind
        is ConnectionKind.GROUND_STATION_ACCESS
    }

    assert access_pairs == {
        frozenset((flexible.node_id, satellites[1].node_id)),
        frozenset((constrained.node_id, satellites[0].node_id)),
    }


def test_satellite_ground_access_caps_are_independent_and_exclude_isls() -> None:
    satellites = (
        _satellite(
            "cap-one",
            mean_anomaly_deg=0.0,
            max_simultaneous_ground_station_accesses=1,
        ),
        _satellite(
            "cap-two",
            orbit_index=1,
            mean_anomaly_deg=180.0,
            max_simultaneous_ground_station_accesses=2,
        ),
    )
    stations = tuple(
        _ground_station(
            f"station-{index}",
            max_simultaneous_satellite_accesses=1,
            latitude_deg=0.0,
            longitude_deg=0.0,
        )
        for index in range(3)
    )
    _, topology = _simulator(
        SimulatorStaticInput(satellites, stations)
    )
    positions = {
        station.node_id: geodetic_to_ecef(0.0, 0.0, 0.0)
        for station in stations
    }
    positions.update(
        {
            satellites[0].node_id: geodetic_to_ecef(
                0.0, 0.0, 560_000.0
            ),
            satellites[1].node_id: geodetic_to_ecef(
                0.0, 0.0, 561_000.0
            ),
        }
    )

    result = _connection_model(
        maximum_isl_distance_m=2_000.0,
        maximum_ground_station_access_count=1,
        ground_station_minimum_elevation_deg=80.0,
    ).compute_connection_slice(
        topology, EPOCH, positions, ConnectionSlice()
    )
    ground_degrees = Counter(
        endpoint
        for connection in result.connections
        if connection.connection_id.kind
        is ConnectionKind.GROUND_STATION_ACCESS
        for endpoint in (
            connection.connection_id.endpoint_a,
            connection.connection_id.endpoint_b,
        )
        if endpoint.entity_kind == "satellite"
    )

    assert ground_degrees == Counter(
        {satellites[0].node_id: 1, satellites[1].node_id: 2}
    )
    assert sum(
        connection.connection_id.kind is ConnectionKind.INTER_SATELLITE
        for connection in result.connections
    ) == 1


def test_capacitated_ground_access_is_deterministic_for_mapping_order() -> None:
    satellites = (
        _satellite(
            "first",
            mean_anomaly_deg=0.0,
            max_simultaneous_ground_station_accesses=1,
        ),
        _satellite(
            "second",
            orbit_index=1,
            mean_anomaly_deg=180.0,
            max_simultaneous_ground_station_accesses=1,
        ),
    )
    stations = tuple(
        _ground_station(
            f"station-{index}",
            max_simultaneous_satellite_accesses=1,
            latitude_deg=0.0,
            longitude_deg=0.0,
        )
        for index in range(2)
    )
    _, topology = _simulator(SimulatorStaticInput(satellites, stations))
    values = {
        **{
            station.node_id: geodetic_to_ecef(0.0, 0.0, 0.0)
            for station in stations
        },
        satellites[0].node_id: geodetic_to_ecef(0.0, 0.0, 560_000.0),
        satellites[1].node_id: geodetic_to_ecef(0.0, 0.0, 561_000.0),
    }
    model = _connection_model(
        maximum_isl_distance_m=2_000.0,
        ground_station_minimum_elevation_deg=80.0,
    )

    forward = model.compute_connection_slice(
        topology, EPOCH, values, ConnectionSlice()
    )
    reverse = model.compute_connection_slice(
        topology,
        EPOCH + timedelta(seconds=10),
        dict(reversed(tuple(values.items()))),
        forward,
    )

    assert reverse == forward


def test_ground_access_allocator_matches_independent_two_phase_oracle() -> None:
    rng = Random(20260826)

    def primary_optimum(
        eligible: tuple[tuple[bool, ...], ...],
        satellite_limits: tuple[int, ...],
    ) -> tuple[int, int]:
        edges = tuple(
            (ground_index, satellite_index, rank)
            for ground_index, row in enumerate(eligible)
            for rank, satellite_index in enumerate(
                (
                    index
                    for index, is_eligible in enumerate(row)
                    if is_eligible
                ),
                start=1,
            )
        )
        best = (0, 0)
        for mask in range(1 << len(edges)):
            ground_counts = [0] * len(eligible)
            satellite_counts = [0] * len(satellite_limits)
            rank_sum = 0
            valid = True
            for edge_index, (
                ground_index,
                satellite_index,
                rank,
            ) in enumerate(edges):
                if not mask & (1 << edge_index):
                    continue
                ground_counts[ground_index] += 1
                satellite_counts[satellite_index] += 1
                rank_sum += rank
                if (
                    ground_counts[ground_index] > 1
                    or satellite_counts[satellite_index]
                    > satellite_limits[satellite_index]
                ):
                    valid = False
                    break
            if valid:
                best = max(
                    best,
                    (
                        sum(count > 0 for count in ground_counts),
                        -rank_sum,
                    ),
                )
        return best

    def residual_redundancy_optimum(
        eligible: tuple[tuple[bool, ...], ...],
        ground_limits: tuple[int, ...],
        satellite_limits: tuple[int, ...],
        primary_pairs: set[tuple[int, int]],
    ) -> int:
        covered_ground = {pair[0] for pair in primary_pairs}
        edges = tuple(
            (ground_index, satellite_index)
            for ground_index, row in enumerate(eligible)
            for satellite_index, is_eligible in enumerate(row)
            if (
                is_eligible
                and ground_index in covered_ground
                and (ground_index, satellite_index) not in primary_pairs
            )
        )
        primary_ground_counts = Counter(pair[0] for pair in primary_pairs)
        primary_satellite_counts = Counter(pair[1] for pair in primary_pairs)
        best = 0
        for mask in range(1 << len(edges)):
            ground_counts = Counter(primary_ground_counts)
            satellite_counts = Counter(primary_satellite_counts)
            valid = True
            for edge_index, (ground_index, satellite_index) in enumerate(edges):
                if not mask & (1 << edge_index):
                    continue
                ground_counts[ground_index] += 1
                satellite_counts[satellite_index] += 1
                if (
                    ground_counts[ground_index] > ground_limits[ground_index]
                    or satellite_counts[satellite_index]
                    > satellite_limits[satellite_index]
                ):
                    valid = False
                    break
            if valid:
                best = max(best, sum(ground_counts.values()) - len(primary_pairs))
        return best

    for case_index in range(96):
        ground_count = 2 + case_index % 3
        satellite_count = 2 + case_index % 2
        ground_limits = tuple(
            1 + rng.randrange(min(3, satellite_count))
            for _ in range(ground_count)
        )
        satellite_limits = tuple(
            1 + rng.randrange(2) for _ in range(satellite_count)
        )
        eligible = tuple(
            tuple(bool(rng.getrandbits(1)) for _ in range(satellite_count))
            for _ in range(ground_count)
        )
        satellites = tuple(
            Satellite(
                NodeId(65001, "satellite", f"oracle-s{index}"),
                index,
                index * 30.0,
                satellite_limits[index],
            )
            for index in range(satellite_count)
        )
        preferences = tuple(
            _GroundAccessPreference(
                ground_station=GroundStation(
                    NodeId(65001, "ground-station", f"oracle-g{ground_index}"),
                    0.0,
                    float(ground_index),
                    0.0,
                    "gateway",
                    None,
                    ground_limits[ground_index],
                ),
                effective_limit=ground_limits[ground_index],
                candidates=tuple(
                    (
                        90.0 - satellite_index,
                        (
                            satellite.node_id.asn,
                            satellite.node_id.entity_kind,
                            satellite.node_id.entity_id,
                        ),
                        satellite.node_id,
                        500_000.0 + satellite_index,
                    )
                    for satellite_index, satellite in enumerate(satellites)
                    if eligible[ground_index][satellite_index]
                ),
            )
            for ground_index in range(ground_count)
        )

        selected = _select_ground_station_accesses(
            preferences, satellites
        )
        selected_reversed = _select_ground_station_accesses(
            tuple(
                _GroundAccessPreference(
                    value.ground_station,
                    value.effective_limit,
                    tuple(reversed(value.candidates)),
                )
                for value in reversed(preferences)
            ),
            tuple(reversed(satellites)),
        )
        selected_pairs = {
            (ground_index, satellite_index)
            for ground_index, preference in enumerate(preferences)
            for satellite_index, satellite in enumerate(satellites)
            if satellite.node_id
            in selected[preference.ground_station.node_id]
        }
        ordered_candidates = {
            preference.ground_station.node_id: tuple(
                sorted(
                    preference.candidates,
                    key=lambda candidate: (-candidate[0], candidate[1]),
                )
            )
            for preference in preferences
        }
        primary = _select_primary_ground_station_accesses(
            ordered_candidates,
            {
                satellite.node_id: satellite_limits[index]
                for index, satellite in enumerate(satellites)
            },
        )
        primary_pairs = {
            (ground_index, satellite_index)
            for ground_index, preference in enumerate(preferences)
            for satellite_index, satellite in enumerate(satellites)
            if primary.get(preference.ground_station.node_id)
            == satellite.node_id
        }
        primary_rank_sum = sum(
            next(
                rank
                for rank, candidate in enumerate(
                    ordered_candidates[
                        preferences[ground_index].ground_station.node_id
                    ],
                    start=1,
                )
                if candidate[2] == satellites[satellite_index].node_id
            )
            for ground_index, satellite_index in primary_pairs
        )

        assert selected_reversed == selected
        assert (len(primary_pairs), -primary_rank_sum) == primary_optimum(
            eligible, satellite_limits
        )
        assert primary_pairs <= selected_pairs
        assert len(selected_pairs) - len(primary_pairs) == (
            residual_redundancy_optimum(
                eligible,
                ground_limits,
                satellite_limits,
                primary_pairs,
            )
        )
        assert all(
            sum(pair[0] == ground_index for pair in selected_pairs)
            <= ground_limits[ground_index]
            for ground_index in range(ground_count)
        )
        assert all(
            sum(pair[1] == satellite_index for pair in selected_pairs)
            <= satellite_limits[satellite_index]
            for satellite_index in range(satellite_count)
        )


def test_ground_access_quality_primaries_are_frozen_before_redundancy() -> None:
    satellites = tuple(
        Satellite(
            NodeId(65001, "satellite", f"quality-s{index}"),
            index,
            index * 180.0,
            2,
        )
        for index in range(2)
    )
    ground_stations = tuple(
        GroundStation(
            NodeId(65001, "ground-station", f"quality-g{index}"),
            0.0,
            float(index),
            0.0,
            "gateway",
            None,
            2,
        )
        for index in range(2)
    )
    preferred_satellites = (
        satellites,
        tuple(reversed(satellites)),
    )
    preferences = tuple(
        _GroundAccessPreference(
            ground_station=ground_station,
            effective_limit=2,
            candidates=tuple(
                (
                    80.0 - rank,
                    (
                        satellite.node_id.asn,
                        satellite.node_id.entity_kind,
                        satellite.node_id.entity_id,
                    ),
                    satellite.node_id,
                    500_000.0 + rank,
                )
                for rank, satellite in enumerate(
                    preferred_satellites[index]
                )
            ),
        )
        for index, ground_station in enumerate(ground_stations)
    )
    ordered_candidates = {
        value.ground_station.node_id: value.candidates
        for value in preferences
    }
    primaries = _select_primary_ground_station_accesses(
        ordered_candidates,
        {satellite.node_id: 2 for satellite in satellites},
    )
    selected = _select_ground_station_accesses(preferences, satellites)

    assert primaries == {
        ground_stations[0].node_id: satellites[0].node_id,
        ground_stations[1].node_id: satellites[1].node_id,
    }
    assert all(
        primary in selected[ground_station_id]
        for ground_station_id, primary in primaries.items()
    )
    assert selected == {
        ground_station.node_id: frozenset(
            satellite.node_id for satellite in satellites
        )
        for ground_station in ground_stations
    }


def test_full_main_chain_publishes_connections_routes_and_paths() -> None:
    first_satellite = _satellite("sat-0")
    second_satellite = _satellite(
        "sat-1", orbit_index=1, mean_anomaly_deg=146.0
    )
    ground_station = _ground_station()
    position_model = _CountingCircularPositionModel()
    simulator, _ = _simulator(
        SimulatorStaticInput(
            (first_satellite, second_satellite), (ground_station,)
        ),
        zone_layout=_zone_layout(),
        position_model=position_model,
    )
    slicer = simulator.create_slicer(
        connection_model=_connection_model(),
        route_model=HopCountRouteModel(),
    )

    slice_zero = slicer.get_slice(0)
    first_slice, second_slice = slicer.create_slices(2)
    ground_target = GroundStationRouteTarget(ground_station.node_id)
    zone_target = ZoneRouteTarget("near")

    assert isinstance(slice_zero, TopologySlice)
    assert slice_zero.timestamp is None
    assert not slice_zero.positions
    assert first_slice.timestamp == EPOCH
    assert second_slice.timestamp == EPOCH + timedelta(seconds=10)
    assert position_model.calls == 2
    assert simulator.get_topology().get_position_model() is position_model
    assert not hasattr(slicer, "get_position_model")
    isl = first_slice.connection_slice.get_connection_for_endpoints(
        ConnectionKind.INTER_SATELLITE,
        first_satellite.node_id,
        second_satellite.node_id,
    )
    isl_distance = ecef_distance_m(
        first_slice.get_position(first_satellite.node_id),
        first_slice.get_position(second_satellite.node_id),
    )
    assert isl.endpoint_a_to_b.delay_us == ceil(
        isl_distance / 299_792_458.0 * 1_000_000.0
    )
    assert first_slice.connection_slice.get_connection_for_endpoints(
        ConnectionKind.GROUND_STATION_ACCESS,
        ground_station.node_id,
        first_satellite.node_id,
    )
    assert first_slice.connection_slice.get_service_satellite("near") == (
        first_satellite.node_id
    )
    with pytest.raises(KeyError, match="not served"):
        first_slice.connection_slice.get_service_satellite("far")
    assert first_slice.route_slice.get_next_hop(
        second_satellite.node_id, ground_target
    ) == first_satellite.node_id
    assert first_slice.route_slice.get_next_hop(
        ground_station.node_id, zone_target
    ) == first_satellite.node_id
    assert slicer.resolve_path(
        1, second_satellite.node_id, ground_target
    ) == (
        second_satellite.node_id,
        first_satellite.node_id,
        ground_station.node_id,
    )
    with pytest.raises(LookupError, match="unserved"):
        slicer.resolve_path(
            1, ground_station.node_id, ZoneRouteTarget("far")
        )
    with pytest.raises(KeyError, match="Unknown Zone"):
        slicer.resolve_path(
            1, ground_station.node_id, ZoneRouteTarget("unknown")
        )


def test_hop_count_route_is_deterministic_sparse_and_omits_terminals() -> None:
    satellites = tuple(
        _satellite(
            name,
            orbit_index=index,
            mean_anomaly_deg=index * 60.0,
        )
        for index, name in enumerate(("a", "b", "c", "d", "e"))
    )
    station = _ground_station()
    _, topology = _simulator(
        SimulatorStaticInput(satellites, (station,))
    )
    parameters = DirectionalConnectionParameters(1, 0, 0)

    def connection(first: NodeId, second: NodeId) -> Connection:
        kind = (
            ConnectionKind.GROUND_STATION_ACCESS
            if "ground-station" in {first.entity_kind, second.entity_kind}
            else ConnectionKind.INTER_SATELLITE
        )
        return Connection(
            ConnectionId(kind, first, second), parameters, parameters
        )

    by_name = {value.satellite_id: value.node_id for value in satellites}
    connections = (
        connection(by_name["a"], by_name["c"]),
        connection(by_name["c"], by_name["d"]),
        connection(by_name["a"], by_name["b"]),
        connection(by_name["b"], by_name["d"]),
        connection(by_name["d"], station.node_id),
    )
    model = HopCountRouteModel()
    target = GroundStationRouteTarget(station.node_id)
    first = model.compute_route_slice(
        topology, EPOCH, ConnectionSlice(connections), RouteSlice()
    )
    second = model.compute_route_slice(
        topology,
        EPOCH,
        ConnectionSlice(reversed(connections)),
        RouteSlice(),
    )

    assert first == second
    assert first.get_next_hop(by_name["a"], target) == by_name["b"]
    with pytest.raises(TypeError):
        first.routes[by_name["a"]][target] = by_name["c"]
    with pytest.raises(KeyError, match="unreachable"):
        first.get_next_hop(station.node_id, target)
    with pytest.raises(KeyError, match="unreachable"):
        first.get_next_hop(by_name["e"], target)


def test_hop_count_route_excludes_ground_stations_from_backbone_transit() -> None:
    first = _satellite("first", mean_anomaly_deg=0.0)
    second = _satellite(
        "second", orbit_index=1, mean_anomaly_deg=180.0
    )
    relay = _ground_station("relay")
    ground_target = _ground_station("target")
    _, topology = _simulator(
        SimulatorStaticInput(
            (first, second), (relay, ground_target)
        ),
        zone_layout=_zone_layout(),
    )
    parameters = DirectionalConnectionParameters(1, 0, 0)

    def connection(first_node: NodeId, second_node: NodeId) -> Connection:
        kind = (
            ConnectionKind.GROUND_STATION_ACCESS
            if "ground-station"
            in {first_node.entity_kind, second_node.entity_kind}
            else ConnectionKind.INTER_SATELLITE
        )
        return Connection(
            ConnectionId(kind, first_node, second_node),
            parameters,
            parameters,
        )

    transit_only = (
        connection(first.node_id, relay.node_id),
        connection(relay.node_id, second.node_id),
    )
    zone_target = ZoneRouteTarget("near")
    zone_routes = HopCountRouteModel().compute_route_slice(
        topology,
        EPOCH,
        ConnectionSlice(
            transit_only,
            {"near": second.node_id},
        ),
        RouteSlice(),
    )
    with pytest.raises(KeyError, match="unreachable"):
        zone_routes.get_next_hop(first.node_id, zone_target)
    assert zone_routes.get_next_hop(relay.node_id, zone_target) == (
        second.node_id
    )

    ground_routes = HopCountRouteModel().compute_route_slice(
        topology,
        EPOCH,
        ConnectionSlice(
            transit_only
            + (
                connection(second.node_id, ground_target.node_id),
            )
        ),
        RouteSlice(),
    )
    target = GroundStationRouteTarget(ground_target.node_id)
    with pytest.raises(KeyError, match="unreachable"):
        ground_routes.get_next_hop(first.node_id, target)
    assert ground_routes.get_next_hop(second.node_id, target) == (
        ground_target.node_id
    )

    satellite_backbone = connection(first.node_id, second.node_id)
    legal_connections = ConnectionSlice(
        transit_only + (satellite_backbone,),
        {"near": second.node_id},
    )
    legal_routes = HopCountRouteModel().compute_route_slice(
        topology,
        EPOCH,
        legal_connections,
        RouteSlice(),
    )
    assert legal_routes.get_next_hop(first.node_id, zone_target) == (
        second.node_id
    )
    assert legal_routes.get_next_hop(relay.node_id, zone_target) == (
        second.node_id
    )


class _FixedPositionModel:
    def __init__(self, positions):
        self.positions = positions
        self.calls = 0

    def compute_positions(self, topology, timestamp):
        self.calls += 1
        return self.positions


class _EmptyConnectionModel:
    def compute_connection_slice(
        self,
        topology,
        timestamp,
        positions,
        previous_connection_slice,
    ):
        return ConnectionSlice()


class _FailOnceRouteModel:
    def __init__(self):
        self.calls = 0

    def compute_route_slice(
        self,
        topology,
        timestamp,
        connection_slice,
        previous_route_slice,
    ):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("injected route failure")
        return RouteSlice()


class _FailOncePositionModel:
    def __init__(self, positions):
        self.positions = positions
        self.fail = True

    def compute_positions(self, topology, timestamp):
        if self.fail:
            self.fail = False
            raise RuntimeError("injected Position failure")
        return self.positions


class _FailOnceConnectionModel:
    def __init__(self) -> None:
        self.fail = True

    def compute_connection_slice(
        self,
        topology,
        timestamp,
        positions,
        previous_connection_slice,
    ):
        if self.fail:
            self.fail = False
            raise RuntimeError("injected Connection failure")
        return ConnectionSlice()


class _StaticConnectionModel:
    def __init__(self, connection_slice):
        self.connection_slice = connection_slice

    def compute_connection_slice(
        self,
        topology,
        timestamp,
        positions,
        previous_connection_slice,
    ):
        return self.connection_slice


class _StaticRouteModel:
    def __init__(self, route_slice):
        self.route_slice = route_slice

    def compute_route_slice(
        self,
        topology,
        timestamp,
        connection_slice,
        previous_route_slice,
    ):
        return self.route_slice


class _RejectingContextConnectionModel:
    def __init__(self, model) -> None:
        self.model = model
        self.claimed = False
        self.released = False
        self.fail_claim = True

    def _claim_slicer_context(
        self, owner, topology, reference_timestamp
    ) -> None:
        self.claimed = True
        self.model._claim_slicer_context(
            owner, topology, reference_timestamp
        )
        if self.fail_claim:
            self.fail_claim = False
            raise RuntimeError("injected context claim failure")

    def _release_slicer_context(self, owner) -> None:
        self.released = True
        self.model._release_slicer_context(owner)

    def _prepare_for_slicer(
        self, owner, topology, timestamp, positions
    ) -> None:
        self.model._prepare_for_slicer(
            owner, topology, timestamp, positions
        )

    def compute_connection_slice(
        self,
        topology,
        timestamp,
        positions,
        previous_connection_slice,
    ):
        return self.model.compute_connection_slice(
            topology,
            timestamp,
            positions,
            previous_connection_slice,
        )


def test_slicer_rejects_custom_connection_output_over_site_access_cap() -> None:
    satellites = (
        _satellite("first", mean_anomaly_deg=0.0),
        _satellite("second", orbit_index=1, mean_anomaly_deg=180.0),
    )
    station = _ground_station(
        "hard-cap", max_simultaneous_satellite_accesses=1
    )
    parameters = DirectionalConnectionParameters(1_000, 1, 0)
    connection_slice = ConnectionSlice(
        Connection(
            ConnectionId(
                ConnectionKind.GROUND_STATION_ACCESS,
                satellite.node_id,
                station.node_id,
            ),
            parameters,
            parameters,
        )
        for satellite in satellites
    )
    positions = {
        satellites[0].node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        satellites[1].node_id: EarthFixedPosition(
            7_000_000.0, 1_000.0, 0.0
        ),
        station.node_id: geodetic_to_ecef(40.7128, -74.006, 10.0),
    }
    simulator, _ = _simulator(
        SimulatorStaticInput(satellites, (station,)),
        end_time=EPOCH,
        position_model=_FixedPositionModel(positions),
    )
    slicer = simulator.create_slicer(
        connection_model=_StaticConnectionModel(connection_slice),
        route_model=HopCountRouteModel(),
    )

    with pytest.raises(
        ValueError,
        match="ConnectionModel output exceeds GroundStation.*hard-cap",
    ):
        slicer.create_next_slice()

    assert slicer.get_retained_slice_ids() == (0,)
    assert slicer.get_latest_slice_id() == 0
    assert slicer.get_next_timestamp() == EPOCH


def test_slicer_rejects_custom_connection_output_over_satellite_access_cap() -> None:
    satellite = _satellite(
        "hard-cap",
        max_simultaneous_ground_station_accesses=2,
    )
    stations = tuple(_ground_station(f"station-{index}") for index in range(3))
    parameters = DirectionalConnectionParameters(1_000, 1, 0)
    connection_slice = ConnectionSlice(
        Connection(
            ConnectionId(
                ConnectionKind.GROUND_STATION_ACCESS,
                satellite.node_id,
                station.node_id,
            ),
            parameters,
            parameters,
        )
        for station in stations
    )
    positions = {
        satellite.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        **{
            station.node_id: geodetic_to_ecef(
                station.latitude_deg,
                station.longitude_deg,
                station.elevation_m,
            )
            for station in stations
        },
    }
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,), stations),
        end_time=EPOCH,
        position_model=_FixedPositionModel(positions),
    )
    slicer = simulator.create_slicer(
        connection_model=_StaticConnectionModel(connection_slice),
        route_model=HopCountRouteModel(),
    )

    with pytest.raises(
        ValueError,
        match="ConnectionModel output exceeds Satellite.*hard-cap",
    ):
        slicer.create_next_slice()

    assert slicer.get_retained_slice_ids() == (0,)
    assert slicer.get_latest_slice_id() == 0
    assert slicer.get_next_timestamp() == EPOCH


def test_slicer_failure_is_atomic_and_retries_same_id_and_timestamp() -> None:
    satellite = _satellite("only")
    position_model = _FixedPositionModel(
        {satellite.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0)}
    )
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,)),
        end_time=EPOCH,
        position_model=position_model,
    )
    route_model = _FailOnceRouteModel()
    slicer = simulator.create_slicer(
        connection_model=_EmptyConnectionModel(),
        route_model=route_model,
    )

    with pytest.raises(RuntimeError, match="injected"):
        slicer.create_next_slice()
    assert slicer.get_latest_slice_id() == 0
    assert slicer.get_next_timestamp() == EPOCH

    retried = slicer.create_next_slice()
    assert retried.slice_id == 1
    assert retried.timestamp == EPOCH
    assert position_model.calls == 2
    assert slicer.is_exhausted()
    with pytest.raises(StopIteration):
        slicer.create_next_slice()
    with pytest.raises(RuntimeError, match="already"):
        simulator.create_slicer(
            connection_model=_EmptyConnectionModel(),
            route_model=HopCountRouteModel(),
        )


def test_durable_slice_append_failure_is_explicitly_post_publication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    satellite = _satellite("only")
    position_model = _FixedPositionModel(
        {
            satellite.node_id: EarthFixedPosition(
                7_000_000.0, 0.0, 0.0
            )
        }
    )
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,)),
        end_time=EPOCH + timedelta(seconds=10),
        position_model=position_model,
    )
    slicer = simulator.create_slicer(
        connection_model=_EmptyConnectionModel(),
        route_model=HopCountRouteModel(),
    )
    history_path = tmp_path / "slice-history.jsonl"
    slicer._enable_durable_history(history_path)
    original_append = slicer_module._append_jsonl

    def fail_first_real_slice(path, record):
        if (
            record.get("record_type") == "slice"
            and record.get("slice_id") == 1
        ):
            raise OSError("injected durable append failure")
        return original_append(path, record)

    monkeypatch.setattr(
        slicer_module, "_append_jsonl", fail_first_real_slice
    )

    with pytest.raises(
        slicer_module._PublishedSliceHistoryError,
        match="Slice 1 is already published.*must not be retried",
    ) as first_error:
        slicer.create_next_slice()

    assert first_error.value.slice_id == 1
    assert slicer.get_latest_slice_id() == 1
    assert slicer.get_slice(1).timestamp == EPOCH
    assert slicer.get_next_timestamp() == EPOCH + timedelta(seconds=10)
    with pytest.raises(
        slicer_module._PublishedSliceHistoryError,
        match="Slice 1 is already published.*must not be retried",
    ):
        slicer.create_next_slice()
    assert slicer.get_latest_slice_id() == 1
    assert slicer.get_next_timestamp() == EPOCH + timedelta(seconds=10)


@pytest.mark.parametrize("failure_stage", ("position", "connection", "route"))
def test_every_model_failure_retries_the_same_pending_timestamp_and_id(
    failure_stage: str,
) -> None:
    satellite = _satellite("only")
    positions = {
        satellite.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0)
    }
    position_model = (
        _FailOncePositionModel(positions)
        if failure_stage == "position"
        else _FixedPositionModel(positions)
    )
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,)),
        end_time=EPOCH,
        position_model=position_model,
    )
    connection_model = (
        _FailOnceConnectionModel()
        if failure_stage == "connection"
        else _EmptyConnectionModel()
    )
    route_model = (
        _FailOnceRouteModel()
        if failure_stage == "route"
        else HopCountRouteModel()
    )
    slicer = simulator.create_slicer(
        connection_model=connection_model,
        route_model=route_model,
    )

    with pytest.raises(RuntimeError, match="injected"):
        slicer.create_next_slice()
    assert slicer.get_retained_slice_ids() == (0,)
    assert slicer.get_latest_slice_id() == 0
    assert slicer.get_next_timestamp() == EPOCH

    retried = slicer.create_next_slice()
    assert retried.slice_id == 1
    assert retried.timestamp == EPOCH
    assert slicer.is_exhausted()


def test_slicer_accepts_intentionally_absent_reachable_route() -> None:
    first = _satellite("first", mean_anomaly_deg=0.0)
    second = _satellite(
        "second", orbit_index=1, mean_anomaly_deg=180.0
    )
    station = _ground_station()
    parameters = DirectionalConnectionParameters(1, 0, 0)
    connection_slice = ConnectionSlice(
        (
            Connection(
                ConnectionId(
                    ConnectionKind.INTER_SATELLITE,
                    first.node_id,
                    second.node_id,
                ),
                parameters,
                parameters,
            ),
            Connection(
                ConnectionId(
                    ConnectionKind.GROUND_STATION_ACCESS,
                    second.node_id,
                    station.node_id,
                ),
                parameters,
                parameters,
            ),
        )
    )
    positions = {
        first.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        second.node_id: EarthFixedPosition(
            7_000_000.0, 1_000.0, 0.0
        ),
        station.node_id: geodetic_to_ecef(40.7128, -74.006, 10.0),
    }
    simulator, _ = _simulator(
        SimulatorStaticInput((first, second), (station,)),
        end_time=EPOCH,
        position_model=_FixedPositionModel(positions),
    )
    slicer = simulator.create_slicer(
        connection_model=_StaticConnectionModel(connection_slice),
        route_model=_StaticRouteModel(RouteSlice()),
    )

    published = slicer.create_next_slice()
    target = GroundStationRouteTarget(station.node_id)
    assert published.slice_id == 1
    assert published.timestamp == EPOCH
    assert not published.route_slice.routes
    with pytest.raises(LookupError, match="unreachable"):
        slicer.resolve_path(1, first.node_id, target)
    assert slicer.is_exhausted()


def test_slicer_requires_topology_and_failed_creation_publishes_nothing() -> None:
    definition = SimulationDefinition(
        SimulatorStaticInput(),
        SimulationTimeline(EPOCH, EPOCH, timedelta(seconds=10)),
    )
    simulator = Simulator(definition)
    with pytest.raises(RuntimeError, match="Topology"):
        simulator.create_slicer(
            connection_model=_EmptyConnectionModel(),
            route_model=HopCountRouteModel(),
        )
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_slicer()

    with pytest.raises(TypeError, match="position_model"):
        simulator.create_topology(position_model=object())
    simulator.create_topology(position_model=_FixedPositionModel({}))
    with pytest.raises(TypeError, match="unexpected keyword"):
        simulator.create_slicer(
            position_model=object(),
            connection_model=_EmptyConnectionModel(),
            route_model=HopCountRouteModel(),
        )
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_slicer()


def test_slicer_creation_failure_releases_model_owner_and_private_binding() -> None:
    satellite = _satellite("only")
    position_model = _FixedPositionModel(
        {satellite.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0)}
    )
    simulator, topology = _simulator(
        SimulatorStaticInput((satellite,)),
        position_model=position_model,
    )
    concrete_connection_model = _connection_model()
    connection_model = _RejectingContextConnectionModel(
        concrete_connection_model
    )

    with pytest.raises(RuntimeError, match="context claim"):
        simulator.create_slicer(
            connection_model=connection_model,
            route_model=HopCountRouteModel(),
        )
    assert connection_model.claimed
    assert connection_model.released
    assert concrete_connection_model._binding_owner_ref is None
    assert concrete_connection_model._binding_topology is None
    assert concrete_connection_model._binding_reference_timestamp is None
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_slicer()

    slicer = simulator.create_slicer(
        connection_model=connection_model,
        route_model=HopCountRouteModel(),
    )
    assert slicer.get_slice(0).slice_id == 0
    assert concrete_connection_model._binding_topology is topology
    assert concrete_connection_model._binding_reference_timestamp == EPOCH


def test_model_instances_cannot_be_reused_by_concurrent_slicers() -> None:
    satellite = _satellite("only")
    static_input = SimulatorStaticInput((satellite,))
    first_simulator, _ = _simulator(static_input)
    second_simulator, _ = _simulator(static_input)
    connection_model = _EmptyConnectionModel()
    route_model = HopCountRouteModel()

    first_simulator.create_slicer(
        connection_model=connection_model,
        route_model=route_model,
    )
    with pytest.raises(ValueError, match="another Slicer"):
        second_simulator.create_slicer(
            connection_model=connection_model,
            route_model=route_model,
        )
    with pytest.raises(RuntimeError, match="not been created"):
        second_simulator.get_slicer()


def test_model_claim_is_atomic_under_concurrent_slicer_creation() -> None:
    satellite = _satellite("only")
    static_input = SimulatorStaticInput((satellite,))
    simulators_and_topologies = (
        _simulator(static_input),
        _simulator(static_input),
    )
    connection_model = _connection_model()
    route_model = HopCountRouteModel()
    barrier = threading.Barrier(3)
    outcomes: list[tuple[str, int, object]] = []

    def claim(index: int) -> None:
        barrier.wait()
        try:
            slicer = simulators_and_topologies[index][0].create_slicer(
                connection_model=connection_model,
                route_model=route_model,
            )
        except Exception as error:
            outcomes.append(("failure", index, error))
        else:
            outcomes.append(("success", index, slicer))

    threads = (
        threading.Thread(target=claim, args=(0,)),
        threading.Thread(target=claim, args=(1,)),
    )
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    successes = [outcome for outcome in outcomes if outcome[0] == "success"]
    failures = [outcome for outcome in outcomes if outcome[0] == "failure"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0][2], ValueError)
    assert "another Slicer" in str(failures[0][2])
    winner_index = successes[0][1]
    assert connection_model._binding_topology is (
        simulators_and_topologies[winner_index][1]
    )


def test_dead_slicer_releases_model_claim_and_private_binding() -> None:
    satellite = _satellite("only")
    static_input = SimulatorStaticInput((satellite,))
    connection_model = _connection_model()
    route_model = HopCountRouteModel()
    first_simulator, _ = _simulator(static_input)
    first_slicer = first_simulator.create_slicer(
        connection_model=connection_model,
        route_model=route_model,
    )
    first_slicer_reference = weakref.ref(first_slicer)

    del first_slicer
    del first_simulator
    gc.collect()
    assert first_slicer_reference() is None

    second_simulator, second_topology = _simulator(static_input)
    second_slicer = second_simulator.create_slicer(
        connection_model=connection_model,
        route_model=route_model,
    )
    assert second_slicer.get_slice(0).slice_id == 0
    assert connection_model._binding_topology is second_topology
    assert connection_model._binding_reference_timestamp == EPOCH


def test_pre_slicer_connection_call_cannot_pollute_stable_pairing_reference() -> None:
    satellites = (
        _satellite("first", mean_anomaly_deg=0.0),
        _satellite(
            "second", orbit_index=1, mean_anomaly_deg=180.0
        ),
    )
    position_model = CircularOrbitPositionModel()
    simulator, topology = _simulator(
        SimulatorStaticInput(satellites),
        position_model=position_model,
    )
    connection_model = _connection_model(
        maximum_isl_distance_m=20_000_000.0
    )
    premature_timestamp = EPOCH + timedelta(seconds=10)
    premature_positions = position_model.compute_positions(
        topology, premature_timestamp
    )
    connection_model.compute_connection_slice(
        topology,
        premature_timestamp,
        premature_positions,
        ConnectionSlice(),
    )
    assert (
        connection_model._prepared_reference_timestamp
        == premature_timestamp
    )

    slicer = simulator.create_slicer(
        connection_model=connection_model,
        route_model=HopCountRouteModel(),
    )
    assert connection_model._prepared_reference_timestamp is None
    first_slice = slicer.create_next_slice()
    prepared_pairs = connection_model._prepared_isl_pairs
    assert first_slice.timestamp == EPOCH
    assert connection_model._prepared_reference_timestamp == EPOCH

    connection_model.compute_connection_slice(
        topology,
        EPOCH + timedelta(seconds=20),
        position_model.compute_positions(
            topology, EPOCH + timedelta(seconds=20)
        ),
        first_slice.connection_slice,
    )
    assert connection_model._prepared_reference_timestamp == EPOCH
    second_slice = slicer.create_next_slice()
    assert second_slice.timestamp == EPOCH + timedelta(seconds=10)
    assert connection_model._prepared_reference_timestamp == EPOCH
    assert connection_model._prepared_isl_pairs == prepared_pairs


def test_pairing_preparation_failure_leaves_no_half_state_and_retries() -> None:
    satellites = (
        _satellite("first", orbit_index=0, mean_anomaly_deg=0.0),
        _satellite("second", orbit_index=1, mean_anomaly_deg=0.0),
    )
    positions = {
        satellite.node_id: EarthFixedPosition(
            7_000_000.0, index * 1_000.0, 0.0
        )
        for index, satellite in enumerate(satellites)
    }
    simulator, _ = _simulator(
        SimulatorStaticInput(satellites),
        end_time=EPOCH,
        position_model=_FixedPositionModel(positions),
    )
    connection_model = _connection_model()
    slicer = simulator.create_slicer(
        connection_model=connection_model,
        route_model=HopCountRouteModel(),
    )

    for _ in range(2):
        with pytest.raises(ValueError, match="unique mean_anomaly"):
            slicer.create_next_slice()
        assert slicer.get_latest_slice_id() == 0
        assert slicer.get_next_timestamp() == EPOCH
        assert connection_model._prepared_topology is None
        assert connection_model._prepared_reference_timestamp is None
        assert connection_model._prepared_isl_pairs is None


def test_downstream_failure_retry_keeps_start_time_pairing() -> None:
    satellites = (
        _satellite("first", mean_anomaly_deg=0.0),
        _satellite(
            "second", orbit_index=1, mean_anomaly_deg=180.0
        ),
    )
    positions = {
        satellite.node_id: EarthFixedPosition(
            7_000_000.0, index * 1_000.0, 0.0
        )
        for index, satellite in enumerate(satellites)
    }
    simulator, _ = _simulator(
        SimulatorStaticInput(satellites),
        end_time=EPOCH,
        position_model=_FixedPositionModel(positions),
    )
    connection_model = _connection_model()
    slicer = simulator.create_slicer(
        connection_model=connection_model,
        route_model=_FailOnceRouteModel(),
    )

    with pytest.raises(RuntimeError, match="injected route failure"):
        slicer.create_next_slice()
    first_prepared_pairs = connection_model._prepared_isl_pairs
    assert first_prepared_pairs
    assert connection_model._prepared_reference_timestamp == EPOCH
    assert slicer.get_latest_slice_id() == 0
    assert slicer.get_next_timestamp() == EPOCH

    retried = slicer.create_next_slice()
    assert retried.slice_id == 1
    assert retried.timestamp == EPOCH
    assert connection_model._prepared_reference_timestamp == EPOCH
    assert connection_model._prepared_isl_pairs == first_prepared_pairs


def test_unbounded_batch_is_finite_and_create_all_requires_bound() -> None:
    satellite = _satellite("only")
    position_model = _FixedPositionModel(
        {satellite.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0)}
    )
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,)),
        end_time=None,
        position_model=position_model,
    )
    slicer = simulator.create_slicer(
        connection_model=_EmptyConnectionModel(),
        route_model=HopCountRouteModel(),
    )

    assert tuple(value.slice_id for value in slicer.create_slices(3)) == (
        1,
        2,
        3,
    )
    assert not slicer.is_exhausted()
    with pytest.raises(RuntimeError, match="bounded"):
        slicer.create_all_slices()


def test_slicer_uses_timeline_iterator_as_its_only_timestamp_authority(
    monkeypatch,
) -> None:
    timestamps = (
        EPOCH,
        EPOCH + timedelta(seconds=7),
        EPOCH + timedelta(seconds=31),
    )

    iterator_calls = 0

    def irregular_timestamps(self):
        nonlocal iterator_calls
        iterator_calls += 1
        yield from timestamps

    monkeypatch.setattr(
        SimulationTimeline,
        "iter_timestamps",
        irregular_timestamps,
    )
    satellite = _satellite("only")
    position_model = _FixedPositionModel(
        {
            satellite.node_id: EarthFixedPosition(
                7_000_000.0, 0.0, 0.0
            )
        }
    )
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,)),
        end_time=EPOCH + timedelta(minutes=1),
        position_model=position_model,
    )
    slicer = simulator.create_slicer(
        connection_model=_EmptyConnectionModel(),
        route_model=HopCountRouteModel(),
    )

    slices = slicer.create_all_slices()
    assert tuple(value.slice_id for value in slices) == (1, 2, 3)
    assert tuple(value.timestamp for value in slices) == timestamps
    assert slicer.get_next_timestamp() is None
    assert iterator_calls == 1
    slicer.create_difference(1, 2)
    slicer.create_difference(2, 3)
    assert iterator_calls == 1

    slicer_source = Path(
        sys.modules["skyseed_v2.simulator.slicing.slicer"].__file__
    ).read_text(encoding="utf-8")
    assert "_next_timeline_timestamp(self._timestamp_iterator)" in slicer_source
    assert "timestamp + self._timeline.slice_interval" not in slicer_source
    assert "_timeline_timestamp_for_slice_id" not in slicer_source
    assert "self._topology.frame_at(timestamp)" in slicer_source
    assert "compute_positions(" not in slicer_source
    assert "_position_model" not in slicer_source


def test_generated_progress_does_not_depend_on_retained_prefix_size() -> None:
    satellite = _satellite("only")
    position_model = _FixedPositionModel(
        {
            satellite.node_id: EarthFixedPosition(
                7_000_000.0, 0.0, 0.0
            )
        }
    )
    simulator, _ = _simulator(
        SimulatorStaticInput((satellite,)),
        end_time=None,
        position_model=position_model,
    )
    slicer = simulator.create_slicer(
        connection_model=_EmptyConnectionModel(),
        route_model=HopCountRouteModel(),
    )
    _, second, third = slicer.create_slices(3)

    assert isinstance(slicer._slices, dict)
    release = slicer._release_before(2)
    assert release == {
        "released_slice_count": 2,
        "retained_slice_count": 2,
        "timestamp_index_count": 2,
    }
    assert slicer._retained_publication_timestamps == {
        2: second.timestamp,
        3: third.timestamp,
    }

    fourth = slicer.create_next_slice()
    assert fourth.slice_id == 4
    assert fourth.timestamp == EPOCH + timedelta(seconds=30)
    assert slicer.get_latest_slice() is fourth
    assert slicer.get_retained_slice_ids() == (2, 3, 4)
    assert slicer.get_retained_slice_id_range() == (2, 4)
    assert slicer.create_difference(3, 4).previous_slice_id == 3
    with pytest.raises(KeyError, match="not retained"):
        slicer.get_slice(1)
    assert second.slice_id == 2
    assert third.slice_id == 3


def test_slicer_rejects_ground_station_transit_without_consuming_progress() -> None:
    first = _satellite("first", mean_anomaly_deg=0.0)
    second = _satellite(
        "second", orbit_index=1, mean_anomaly_deg=180.0
    )
    relay = _ground_station("relay")
    parameters = DirectionalConnectionParameters(1, 0, 0)

    def connection(first_node: NodeId, second_node: NodeId) -> Connection:
        return Connection(
            ConnectionId(
                ConnectionKind.GROUND_STATION_ACCESS,
                first_node,
                second_node,
            ),
            parameters,
            parameters,
        )

    connection_slice = ConnectionSlice(
        (
            connection(first.node_id, relay.node_id),
            connection(relay.node_id, second.node_id),
        ),
        {"near": second.node_id},
    )
    zone_target = ZoneRouteTarget("near")
    ground_target = GroundStationRouteTarget(relay.node_id)
    illegal_routes = RouteSlice(
        (
            (RouteKey(first.node_id, zone_target), relay.node_id),
            (RouteKey(relay.node_id, zone_target), second.node_id),
            (RouteKey(first.node_id, ground_target), relay.node_id),
            (RouteKey(second.node_id, ground_target), relay.node_id),
        )
    )

    class IllegalOnceRouteModel:
        def __init__(self) -> None:
            self.first_call = True
            self.valid_model = HopCountRouteModel()

        def compute_route_slice(
            self,
            topology,
            timestamp,
            current_connections,
            previous_route_slice,
        ):
            if self.first_call:
                self.first_call = False
                return illegal_routes
            return self.valid_model.compute_route_slice(
                topology,
                timestamp,
                current_connections,
                previous_route_slice,
            )

    positions = {
        first.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        second.node_id: EarthFixedPosition(7_000_000.0, 1_000.0, 0.0),
        relay.node_id: geodetic_to_ecef(40.7128, -74.006, 10.0),
    }
    simulator, _ = _simulator(
        SimulatorStaticInput((first, second), (relay,)),
        end_time=EPOCH,
        zone_layout=_zone_layout(),
        position_model=_FixedPositionModel(positions),
    )
    slicer = simulator.create_slicer(
        connection_model=_StaticConnectionModel(connection_slice),
        route_model=IllegalOnceRouteModel(),
    )

    with pytest.raises(ValueError, match="GroundStation transit"):
        slicer.create_next_slice()
    assert slicer.get_latest_slice_id() == 0
    assert slicer.get_next_timestamp() == EPOCH

    published = slicer.create_next_slice()
    assert published.slice_id == 1
    assert published.timestamp == EPOCH
    assert slicer.resolve_path(1, relay.node_id, zone_target) == (
        relay.node_id,
        second.node_id,
    )

    slicer._slices[1] = TopologySlice(
        slice_id=1,
        timestamp=published.timestamp,
        positions=published.positions,
        connection_slice=published.connection_slice,
        route_slice=illegal_routes,
    )
    with pytest.raises(ValueError, match="GroundStation transit"):
        slicer.resolve_path(1, first.node_id, zone_target)


@pytest.mark.parametrize("wrong_terminal", (False, True))
def test_slicer_rejects_route_loops_and_wrong_terminals(
    wrong_terminal: bool,
) -> None:
    first = _satellite("first")
    second = _satellite(
        "second", orbit_index=1, mean_anomaly_deg=146.0
    )
    first_station = _ground_station("target")
    second_station = _ground_station("other")
    parameters = DirectionalConnectionParameters(1, 0, 0)
    target = GroundStationRouteTarget(first_station.node_id)
    if wrong_terminal:
        connections = ConnectionSlice(
            (
                Connection(
                    ConnectionId(
                        ConnectionKind.GROUND_STATION_ACCESS,
                        first.node_id,
                        second_station.node_id,
                    ),
                    parameters,
                    parameters,
                ),
            )
        )
        routes = RouteSlice(
            ((RouteKey(first.node_id, target), second_station.node_id),)
        )
        message = "wrong terminal"
    else:
        connections = ConnectionSlice(
            (
                Connection(
                    ConnectionId(
                        ConnectionKind.INTER_SATELLITE,
                        first.node_id,
                        second.node_id,
                    ),
                    parameters,
                    parameters,
                ),
            )
        )
        routes = RouteSlice(
            (
                (RouteKey(first.node_id, target), second.node_id),
                (RouteKey(second.node_id, target), first.node_id),
            )
        )
        message = "loop"
    positions = {
        node.node_id: EarthFixedPosition(
            7_000_000.0, index * 1_000.0, 0.0
        )
        for index, node in enumerate(
            (first, second, first_station, second_station)
        )
    }
    simulator, _ = _simulator(
        SimulatorStaticInput(
            (first, second), (first_station, second_station)
        ),
        position_model=_FixedPositionModel(positions),
    )
    slicer = simulator.create_slicer(
        connection_model=_StaticConnectionModel(connections),
        route_model=_StaticRouteModel(routes),
    )

    with pytest.raises(ValueError, match=message):
        slicer.create_next_slice()
    assert slicer.get_latest_slice_id() == 0


def test_slicer_distinguishes_stale_next_hop_and_wrong_zone_terminal() -> None:
    first = _satellite("first")
    second = _satellite(
        "second", orbit_index=1, mean_anomaly_deg=146.0
    )
    station = _ground_station()
    parameters = DirectionalConnectionParameters(1, 0, 0)
    positions = {
        first.node_id: EarthFixedPosition(7_000_000.0, 0.0, 0.0),
        second.node_id: EarthFixedPosition(7_000_000.0, 1_000.0, 0.0),
        station.node_id: geodetic_to_ecef(40.7128, -74.006, 10.0),
    }

    stale_simulator, _ = _simulator(
        SimulatorStaticInput((first, second), (station,)),
        position_model=_FixedPositionModel(positions),
    )
    stale_target = GroundStationRouteTarget(station.node_id)
    stale_slicer = stale_simulator.create_slicer(
        connection_model=_StaticConnectionModel(ConnectionSlice()),
        route_model=_StaticRouteModel(
            RouteSlice(
                (
                    (
                        RouteKey(first.node_id, stale_target),
                        second.node_id,
                    ),
                )
            )
        ),
    )
    with pytest.raises(ValueError, match="no current Connection"):
        stale_slicer.create_next_slice()

    zone_simulator, _ = _simulator(
        SimulatorStaticInput((first, second), (station,)),
        zone_layout=_zone_layout(),
        position_model=_FixedPositionModel(positions),
    )
    zone_connections = ConnectionSlice(
        (
            Connection(
                ConnectionId(
                    ConnectionKind.GROUND_STATION_ACCESS,
                    station.node_id,
                    second.node_id,
                ),
                parameters,
                parameters,
            ),
        ),
        {"near": first.node_id, "far": second.node_id},
    )
    near_target = ZoneRouteTarget("near")
    zone_slicer = zone_simulator.create_slicer(
        connection_model=_StaticConnectionModel(zone_connections),
        route_model=_StaticRouteModel(
            RouteSlice(
                (
                    (
                        RouteKey(station.node_id, near_target),
                        second.node_id,
                    ),
                )
            )
        ),
    )
    with pytest.raises(ValueError, match="wrong terminal"):
        zone_slicer.create_next_slice()


def test_slice_difference_contracts_are_simulator_public_but_not_package_root() -> None:
    import skyseed_v2.simulator as simulator

    for name in (
        "EarthFixedPosition",
        "PhysicalFrame",
        "PositionModel",
        "ConnectionModel",
        "RouteModel",
        "ConnectionSlice",
        "RouteSlice",
        "TopologySlice",
        "Slicer",
    ):
        assert name in simulator.__all__
        assert hasattr(simulator, name)
        assert not hasattr(skyseed_v2, name)
