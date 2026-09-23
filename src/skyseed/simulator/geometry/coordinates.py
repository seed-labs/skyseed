"""Define Earth-fixed Position and WGS84/ECEF geometry operations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from math import isfinite, sqrt
from numbers import Real
from threading import RLock
from typing import Iterator

import astropy.units as u
from astropy.coordinates import (
    CartesianRepresentation,
    EarthLocation,
    GCRS,
    ITRS,
)
from astropy.time import Time
from astropy.utils import iers

from skyseed.shared.time import normalize_utc_datetime

from ._elevation import (
    _elevation_distance_m,
    _elevation_deg_from_local_up_and_distance,
    _wgs84_local_up_unit_vector,
)
from .position import EarthFixedPosition, ecef_to_geodetic

_WGS84_SEMI_MAJOR_AXIS_M = 6_378_137.0
_WGS84_INVERSE_FLATTENING = 298.257_223_563
_WGS84_SEMI_MINOR_AXIS_M = _WGS84_SEMI_MAJOR_AXIS_M * (
    1.0 - 1.0 / _WGS84_INVERSE_FLATTENING
)
_LOS_SURFACE_TOLERANCE = 1e-10
_IERS_POLICY_LOCK = RLock()


def geodetic_to_ecef(
    latitude_deg: Real,
    longitude_deg: Real,
    ellipsoidal_height_m: Real,
) -> EarthFixedPosition:
    """Convert WGS84 geodetic coordinates to EPSG:4978-equivalent ECEF."""

    latitude = _finite(latitude_deg, "latitude_deg")
    longitude = _finite(longitude_deg, "longitude_deg")
    height = _finite(ellipsoidal_height_m, "ellipsoidal_height_m")
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude_deg must be between -90 and 90")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude_deg must be between -180 and 180")
    x_m, y_m, z_m = _geodetic_coordinates_to_ecef(
        latitude, longitude, height
    )
    return EarthFixedPosition(x_m, y_m, z_m)


@lru_cache(maxsize=4096)
def _geodetic_coordinates_to_ecef(
    latitude_deg: float,
    longitude_deg: float,
    ellipsoidal_height_m: float,
) -> tuple[float, float, float]:
    """Return cached WGS84 Cartesian coordinates for validated scalars."""

    x, y, z = EarthLocation.from_geodetic(
        lon=longitude_deg * u.deg,
        lat=latitude_deg * u.deg,
        height=ellipsoidal_height_m * u.m,
        ellipsoid="WGS84",
    ).geocentric
    return (
        x.to_value(u.m),
        y.to_value(u.m),
        z.to_value(u.m),
    )


def ecef_distance_m(
    first: EarthFixedPosition,
    second: EarthFixedPosition,
) -> float:
    """Return Euclidean distance between two ECEF points in metres."""

    _require_position(first)
    _require_position(second)
    return sqrt(
        (first.x_m - second.x_m) ** 2
        + (first.y_m - second.y_m) ** 2
        + (first.z_m - second.z_m) ** 2
    )


def has_earth_line_of_sight(
    first: EarthFixedPosition,
    second: EarthFixedPosition,
) -> bool:
    """Return whether the open ECEF segment avoids the WGS84 interior."""

    _require_position(first)
    _require_position(second)
    delta = (
        second.x_m - first.x_m,
        second.y_m - first.y_m,
        second.z_m - first.z_m,
    )
    scaled_first = (
        first.x_m / _WGS84_SEMI_MAJOR_AXIS_M,
        first.y_m / _WGS84_SEMI_MAJOR_AXIS_M,
        first.z_m / _WGS84_SEMI_MINOR_AXIS_M,
    )
    scaled_delta = (
        delta[0] / _WGS84_SEMI_MAJOR_AXIS_M,
        delta[1] / _WGS84_SEMI_MAJOR_AXIS_M,
        delta[2] / _WGS84_SEMI_MINOR_AXIS_M,
    )
    first_level = sum(value * value for value in scaled_first) - 1.0
    scaled_second = tuple(
        origin + direction
        for origin, direction in zip(
            scaled_first, scaled_delta, strict=True
        )
    )
    second_level = sum(value * value for value in scaled_second) - 1.0
    if (
        first_level < -_LOS_SURFACE_TOLERANCE
        or second_level < -_LOS_SURFACE_TOLERANCE
    ):
        return False

    length_squared = sum(value * value for value in scaled_delta)
    if length_squared == 0.0:
        return True
    closest_parameter = -sum(
        origin * direction
        for origin, direction in zip(scaled_first, scaled_delta, strict=True)
    ) / length_squared
    if not 0.0 < closest_parameter < 1.0:
        return True
    closest_level = sum(
        (origin + closest_parameter * direction) ** 2
        for origin, direction in zip(
            scaled_first, scaled_delta, strict=True
        )
    ) - 1.0
    return closest_level >= 0.0


def elevation_deg(
    observer: EarthFixedPosition,
    target: EarthFixedPosition,
) -> float:
    """Return target elevation above the observer's WGS84 local horizon."""

    _require_position(observer)
    _require_position(target)
    return _elevation_deg_from_local_up_and_distance(
        observer,
        target,
        _wgs84_local_up_unit_vector(observer),
        _elevation_distance_m(observer, target),
    )


def _gcrs_cartesian_to_ecef(
    x_m: float,
    y_m: float,
    z_m: float,
    timestamp: datetime,
) -> EarthFixedPosition:
    """Convert a private GCRS Cartesian point to formal ECEF."""

    utc = normalize_utc_datetime(timestamp, "timestamp")
    with _offline_iers_policy():
        _require_iers_coverage(utc)
        obstime = Time(utc)
        gcrs = GCRS(
            CartesianRepresentation(x_m * u.m, y_m * u.m, z_m * u.m),
            obstime=obstime,
        )
        itrs = gcrs.transform_to(ITRS(obstime=obstime)).cartesian
    return EarthFixedPosition(
        itrs.x.to_value(u.m),
        itrs.y.to_value(u.m),
        itrs.z.to_value(u.m),
    )


def _gcrs_cartesian_batch_to_ecef(
    points_m: tuple[tuple[float, float, float], ...],
    timestamp: datetime,
) -> tuple[EarthFixedPosition, ...]:
    """Convert private GCRS Cartesian points in one vectorized transform."""

    if not points_m:
        return ()
    utc = normalize_utc_datetime(timestamp, "timestamp")
    with _offline_iers_policy():
        _require_iers_coverage(utc)
        obstime = Time(utc)
        representation = CartesianRepresentation(
            [point[0] for point in points_m] * u.m,
            [point[1] for point in points_m] * u.m,
            [point[2] for point in points_m] * u.m,
        )
        itrs = GCRS(representation, obstime=obstime).transform_to(
            ITRS(obstime=obstime)
        ).cartesian
    return tuple(
        EarthFixedPosition(x, y, z)
        for x, y, z in zip(
            itrs.x.to_value(u.m),
            itrs.y.to_value(u.m),
            itrs.z.to_value(u.m),
            strict=True,
        )
    )


def _require_position(position: EarthFixedPosition) -> None:
    if not isinstance(position, EarthFixedPosition):
        raise TypeError("position must be an EarthFixedPosition")


@contextmanager
def _offline_iers_policy() -> Iterator[None]:
    """Apply deterministic offline IERS policy without persistent mutation."""

    with _IERS_POLICY_LOCK:
        with iers.conf.set_temp("auto_download", False):
            with iers.conf.set_temp("iers_degraded_accuracy", "error"):
                yield


def _require_iers_coverage(timestamp: datetime) -> None:
    first, last = _iers_coverage_bounds()
    if not first <= timestamp <= last:
        raise ValueError(
            "timestamp is outside bundled Earth-orientation data coverage"
        )


@lru_cache(maxsize=1)
def _iers_coverage_bounds() -> tuple[datetime, datetime]:
    table = iers.IERS_Auto.open()
    first_mjd = float(table["MJD"][0].value)
    last_mjd = float(table["MJD"][-1].value)
    return (
        Time(first_mjd, format="mjd", scale="utc").to_datetime(
            timezone=timezone.utc
        ),
        Time(last_mjd, format="mjd", scale="utc").to_datetime(
            timezone=timezone.utc
        ),
    )


def _finite(value: Real, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a finite number")
    converted = float(value)
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted


__all__ = [
    "ecef_distance_m",
    "ecef_to_geodetic",
    "elevation_deg",
    "geodetic_to_ecef",
    "has_earth_line_of_sight",
]
