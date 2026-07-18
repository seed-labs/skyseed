"""Fixed values shared by the Satellites in one Orbit."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .validation import _validate_angle


@dataclass(frozen=True, slots=True)
class OrbitParameters:
    """Hold fixed orbital values shared by Satellites in one Orbit.

    Layer: entity-bound value object. Handwritten callers or the specification
    Applier create it; Orbit and Satellite expose it. It is not an Orbit,
    trajectory or dynamic position. It is root public because direct entity
    construction requires these validated values.
    """

    altitude_km: float
    inclination_deg: float
    right_ascension_deg: float

    def __post_init__(self) -> None:
        altitude = float(self.altitude_km)
        inclination = float(self.inclination_deg)
        right_ascension = _validate_angle(
            self.right_ascension_deg, "right_ascension_deg"
        )
        if not isfinite(altitude) or altitude <= 0.0:
            raise ValueError("altitude_km must be a positive finite number")
        if not isfinite(inclination) or not 0.0 <= inclination <= 180.0:
            raise ValueError("inclination_deg must be between 0 and 180")
        object.__setattr__(self, "altitude_km", altitude)
        object.__setattr__(self, "inclination_deg", inclination)
        object.__setattr__(self, "right_ascension_deg", right_ascension)
