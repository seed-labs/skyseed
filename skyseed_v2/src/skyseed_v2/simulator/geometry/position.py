"""Define the Earth-fixed position value and its derived geodetic view."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import isfinite

import astropy.units as u
from astropy.coordinates import EarthLocation


@dataclass(frozen=True, slots=True)
class EarthFixedPosition:
    """Represent one authoritative WGS84 Earth-fixed Cartesian Position.

    Layer: immutable Simulator physical value. PositionModel creates it,
    PhysicalFrame owns the timestamped mapping, and physical/legacy consumers
    read it. It is not a geographic state, trajectory, velocity or third-party
    frame value; it is public from
    ``skyseed_v2.simulator`` as the sole formal Position representation.
    """

    x_m: float
    y_m: float
    z_m: float

    def __post_init__(self) -> None:
        for field_name in ("x_m", "y_m", "z_m"):
            value = float(getattr(self, field_name))
            if not isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, value)

    @property
    def latitude_deg(self) -> float:
        return ecef_to_geodetic(self)[0]

    @property
    def longitude_deg(self) -> float:
        return ecef_to_geodetic(self)[1]

    @property
    def ellipsoidal_height_m(self) -> float:
        return ecef_to_geodetic(self)[2]

    @property
    def geographic(self) -> tuple[float, float, float]:
        """Return the derived WGS84 latitude, longitude and ellipsoidal height."""

        return ecef_to_geodetic(self)


def ecef_to_geodetic(
    position: EarthFixedPosition,
) -> tuple[float, float, float]:
    """Derive WGS84 latitude, longitude and ellipsoidal height from ECEF."""

    if not isinstance(position, EarthFixedPosition):
        raise TypeError("position must be an EarthFixedPosition")
    return _ecef_coordinates_to_geodetic(
        position.x_m,
        position.y_m,
        position.z_m,
    )


@lru_cache(maxsize=4096)
def _ecef_coordinates_to_geodetic(
    x_m: float,
    y_m: float,
    z_m: float,
) -> tuple[float, float, float]:
    """Return a cached WGS84 geodetic view for validated ECEF scalars."""

    geodetic = EarthLocation.from_geocentric(
        x_m * u.m,
        y_m * u.m,
        z_m * u.m,
    ).to_geodetic(ellipsoid="WGS84")
    return (
        float(geodetic.lat.to_value(u.deg)),
        float(geodetic.lon.to_value(u.deg)),
        float(geodetic.height.to_value(u.m)),
    )


__all__ = ["EarthFixedPosition", "ecef_to_geodetic"]
