"""Immutable, backend-independent specifications for static construction."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if not value[0].isalnum() or any(
        not (character.isalnum() or character in "_.-") for character in value
    ):
        raise ValueError(
            f"{field_name} must start with an alphanumeric character and contain "
            "only alphanumeric characters, '_', '.', or '-'"
        )
    return value


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _angle(value: float, field_name: str) -> float:
    normalized = float(value)
    if not isfinite(normalized) or not 0.0 <= normalized < 360.0:
        raise ValueError(f"{field_name} must be between 0 and 360 (exclusive)")
    return normalized


def _positive_integer(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class RaanDistributionSpecification:
    """Uniform right-ascension generation parameters for a Shell."""

    start_deg: float = 0.0
    step_deg: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_deg", _angle(self.start_deg, "start_deg"))
        if self.step_deg is not None:
            step = float(self.step_deg)
            if not isfinite(step) or step <= 0.0:
                raise ValueError("step_deg must be a positive finite number")
            object.__setattr__(self, "step_deg", step)


@dataclass(frozen=True, slots=True)
class InOrbitPhaseDistributionSpecification:
    """Uniform in-orbit phase and alternating plane offset parameters."""

    start_deg: float = 0.0
    step_deg: float | None = None
    alternating_orbit_offset_deg: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_deg", _angle(self.start_deg, "start_deg"))
        object.__setattr__(
            self,
            "alternating_orbit_offset_deg",
            _angle(
                self.alternating_orbit_offset_deg,
                "alternating_orbit_offset_deg",
            ),
        )
        if self.step_deg is not None:
            step = float(self.step_deg)
            if not isfinite(step) or step <= 0.0:
                raise ValueError("step_deg must be a positive finite number")
            object.__setattr__(self, "step_deg", step)


@dataclass(frozen=True, slots=True)
class OrbitGenerationSpecification:
    """Rules for generating every Orbit and Satellite in one Shell."""

    orbit_count: int
    satellites_per_orbit: int
    raan: RaanDistributionSpecification = RaanDistributionSpecification()
    phase: InOrbitPhaseDistributionSpecification = (
        InOrbitPhaseDistributionSpecification()
    )

    def __post_init__(self) -> None:
        _positive_integer(self.orbit_count, "orbit_count")
        _positive_integer(self.satellites_per_orbit, "satellites_per_orbit")
        if not isinstance(self.raan, RaanDistributionSpecification):
            raise TypeError("raan must be a RaanDistributionSpecification")
        if not isinstance(self.phase, InOrbitPhaseDistributionSpecification):
            raise TypeError(
                "phase must be an InOrbitPhaseDistributionSpecification"
            )


@dataclass(frozen=True, slots=True)
class SatelliteSpecification:
    """One explicitly specified Satellite within an Orbit."""

    satellite_id: str
    index_in_orbit: int
    mean_anomaly_deg: float
    payload_items: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "satellite_id", _identifier(self.satellite_id, "satellite_id")
        )
        if (
            not isinstance(self.index_in_orbit, int)
            or isinstance(self.index_in_orbit, bool)
            or self.index_in_orbit < 0
        ):
            raise ValueError("index_in_orbit must be a non-negative integer")
        object.__setattr__(
            self,
            "mean_anomaly_deg",
            _angle(self.mean_anomaly_deg, "mean_anomaly_deg"),
        )
        payload_items = tuple(self.payload_items)
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            for key, value in payload_items
        ):
            raise TypeError("payload_items must contain non-empty string keys and values")
        if len({key for key, _ in payload_items}) != len(payload_items):
            raise ValueError("payload_items contains a duplicate key")
        object.__setattr__(self, "payload_items", tuple(sorted(payload_items)))


@dataclass(frozen=True, slots=True)
class OrbitSpecification:
    """One explicitly specified Orbit and its explicit Satellites."""

    orbit_id: str
    right_ascension_deg: float
    satellites: tuple[SatelliteSpecification, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "orbit_id", _identifier(self.orbit_id, "orbit_id"))
        object.__setattr__(
            self,
            "right_ascension_deg",
            _angle(self.right_ascension_deg, "right_ascension_deg"),
        )
        satellites = tuple(self.satellites)
        if not satellites:
            raise ValueError("satellites must contain at least one specification")
        if any(not isinstance(item, SatelliteSpecification) for item in satellites):
            raise TypeError("satellites must contain SatelliteSpecification values")
        ids = [item.satellite_id for item in satellites]
        indices = [item.index_in_orbit for item in satellites]
        if len(set(ids)) != len(ids):
            raise ValueError("satellites contains a duplicate satellite_id")
        if len(set(indices)) != len(indices):
            raise ValueError("satellites contains a duplicate index_in_orbit")
        object.__setattr__(
            self, "satellites", tuple(sorted(satellites, key=lambda item: item.satellite_id))
        )


@dataclass(frozen=True, slots=True)
class ShellSpecification:
    """One static Shell, expressed by generation rules or explicit Orbits."""

    shell_id: str
    altitude_km: float
    inclination_deg: float
    generation: OrbitGenerationSpecification | None = None
    orbits: tuple[OrbitSpecification, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "shell_id", _identifier(self.shell_id, "shell_id"))
        altitude = float(self.altitude_km)
        inclination = float(self.inclination_deg)
        if not isfinite(altitude) or altitude <= 0.0:
            raise ValueError("altitude_km must be a positive finite number")
        if not isfinite(inclination) or not 0.0 <= inclination <= 180.0:
            raise ValueError("inclination_deg must be between 0 and 180")
        object.__setattr__(self, "altitude_km", altitude)
        object.__setattr__(self, "inclination_deg", inclination)

        orbits = tuple(self.orbits)
        if self.generation is not None and not isinstance(
            self.generation, OrbitGenerationSpecification
        ):
            raise TypeError("generation must be an OrbitGenerationSpecification")
        if any(not isinstance(item, OrbitSpecification) for item in orbits):
            raise TypeError("orbits must contain OrbitSpecification values")
        if (self.generation is None) == (not orbits):
            raise ValueError("specify exactly one of generation or explicit orbits")
        ids = [item.orbit_id for item in orbits]
        if len(set(ids)) != len(ids):
            raise ValueError("orbits contains a duplicate orbit_id")
        object.__setattr__(
            self, "orbits", tuple(sorted(orbits, key=lambda item: item.orbit_id))
        )


@dataclass(frozen=True, slots=True)
class GroundStationSpecification:
    """Static GroundStation data with explicit or generated logical identity."""

    display_name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None
    operator: str
    station_type: str
    ground_station_id: str | None = None
    identity_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name"))
        object.__setattr__(self, "operator", _text(self.operator, "operator"))
        object.__setattr__(
            self, "station_type", _text(self.station_type, "station_type")
        )
        latitude = float(self.latitude_deg)
        longitude = float(self.longitude_deg)
        elevation = None if self.elevation_m is None else float(self.elevation_m)
        if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        if not isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")
        if elevation is not None and not isfinite(elevation):
            raise ValueError("elevation_m must be finite when provided")
        object.__setattr__(self, "latitude_deg", latitude)
        object.__setattr__(self, "longitude_deg", longitude)
        object.__setattr__(self, "elevation_m", elevation)

        has_explicit_id = self.ground_station_id is not None
        has_identity_key = self.identity_key is not None
        if has_explicit_id == has_identity_key:
            raise ValueError(
                "specify exactly one of ground_station_id or identity_key"
            )
        if has_explicit_id:
            object.__setattr__(
                self,
                "ground_station_id",
                _identifier(self.ground_station_id, "ground_station_id"),
            )
        else:
            object.__setattr__(
                self, "identity_key", _text(self.identity_key, "identity_key")
            )
