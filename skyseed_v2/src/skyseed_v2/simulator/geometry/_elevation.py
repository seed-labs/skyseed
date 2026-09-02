"""Own the private authoritative WGS84 elevation primitive."""

from __future__ import annotations

from math import acos, cos, degrees, isfinite, radians, sin, sqrt

from .position import EarthFixedPosition, ecef_to_geodetic


_LocalUp = tuple[float, float, float]


def _elevation_distance_m(
    observer: EarthFixedPosition,
    target: EarthFixedPosition,
) -> float:
    """Return the distance arithmetic historically used by elevation_deg."""

    if not isinstance(observer, EarthFixedPosition):
        raise TypeError("observer must be an EarthFixedPosition")
    if not isinstance(target, EarthFixedPosition):
        raise TypeError("target must be an EarthFixedPosition")
    delta = (
        target.x_m - observer.x_m,
        target.y_m - observer.y_m,
        target.z_m - observer.z_m,
    )
    return sqrt(sum(value * value for value in delta))


def _wgs84_local_up_unit_vector(
    observer: EarthFixedPosition,
) -> _LocalUp:
    """Return the observer's WGS84-geodetic local-up unit vector in ECEF."""

    if not isinstance(observer, EarthFixedPosition):
        raise TypeError("observer must be an EarthFixedPosition")
    latitude_deg, longitude_deg, _ = ecef_to_geodetic(observer)
    latitude_rad = radians(latitude_deg)
    longitude_rad = radians(longitude_deg)
    return (
        cos(latitude_rad) * cos(longitude_rad),
        cos(latitude_rad) * sin(longitude_rad),
        sin(latitude_rad),
    )


def _elevation_deg_from_local_up_and_distance(
    observer: EarthFixedPosition,
    target: EarthFixedPosition,
    local_up: _LocalUp,
    distance_m: float,
) -> float:
    """Return WGS84 elevation in degrees using precomputed physical values.

    ``local_up`` is a dimensionless ECEF unit vector and ``distance_m`` is the
    Euclidean observer-to-target distance in metres.  The final dot-product
    ratio is clamped to the trigonometric domain so identical physical inputs
    remain deterministic at floating-point boundaries.
    """

    if not isinstance(observer, EarthFixedPosition):
        raise TypeError("observer must be an EarthFixedPosition")
    if not isinstance(target, EarthFixedPosition):
        raise TypeError("target must be an EarthFixedPosition")
    if not isinstance(local_up, tuple) or len(local_up) != 3:
        raise TypeError("local_up must be a three-item tuple")
    if any(not isfinite(value) for value in local_up):
        raise ValueError("local_up must contain finite values")
    if not isfinite(distance_m) or distance_m < 0.0:
        raise ValueError("distance_m must be a non-negative finite number")
    if distance_m == 0.0:
        raise ValueError("elevation is undefined for coincident positions")

    delta = (
        target.x_m - observer.x_m,
        target.y_m - observer.y_m,
        target.z_m - observer.z_m,
    )
    sine_elevation = sum(
        direction * up
        for direction, up in zip(delta, local_up, strict=True)
    ) / distance_m
    return 90.0 - degrees(
        acos(min(1.0, max(-1.0, sine_elevation)))
    )


__all__: list[str] = []
