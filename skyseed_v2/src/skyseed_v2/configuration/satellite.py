"""Immutable Shell, Orbit, and Satellite construction Config values."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from skyseed_v2.identifier import validate_identifier

from ._validation import _angle, _positive_integer


@dataclass(frozen=True, slots=True)
class RaanDistributionConfig:
    """Specify uniform right-ascension input for generated Orbits.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it; a Shell generation config consumes it. It is not an
    Orbit or runtime trajectory and is public from configuration.
    """

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
class InOrbitPhaseDistributionConfig:
    """Specify uniform phase input for generated Satellites.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it for `OrbitGenerationConfig`; it is not Satellite state
    or a runtime position model and is public from configuration.
    """

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
class OrbitGenerationConfig:
    """Validate deterministic Orbit/Satellite generation input for one Shell.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it; `create_static_emulator` expands it. It is not a
    population, factory or entity owner and is public from configuration.
    """

    orbit_count: int
    satellites_per_orbit: int
    raan: RaanDistributionConfig = RaanDistributionConfig()
    phase: InOrbitPhaseDistributionConfig = (
        InOrbitPhaseDistributionConfig()
    )

    def __post_init__(self) -> None:
        _positive_integer(self.orbit_count, "orbit_count")
        _positive_integer(self.satellites_per_orbit, "satellites_per_orbit")
        if not isinstance(self.raan, RaanDistributionConfig):
            raise TypeError("raan must be a RaanDistributionConfig")
        if not isinstance(self.phase, InOrbitPhaseDistributionConfig):
            raise TypeError(
                "phase must be an InOrbitPhaseDistributionConfig"
            )
        raan_step = (
            360.0 / self.orbit_count
            if self.raan.step_deg is None
            else self.raan.step_deg
        )
        raan_values = tuple(
            (self.raan.start_deg + index * raan_step) % 360.0
            for index in range(self.orbit_count)
        )
        if len({round(value, 10) for value in raan_values}) != len(raan_values):
            raise ValueError("RAAN distribution generates duplicate Orbits")

        phase_step = (
            360.0 / self.satellites_per_orbit
            if self.phase.step_deg is None
            else self.phase.step_deg
        )
        phase_values = tuple(
            (self.phase.start_deg + index * phase_step) % 360.0
            for index in range(self.satellites_per_orbit)
        )
        if len({round(value, 10) for value in phase_values}) != len(phase_values):
            raise ValueError("phase distribution generates duplicate Satellites")


@dataclass(frozen=True, slots=True)
class SatelliteConfig:
    """Describe one explicit Satellite construction input within an Orbit.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it; `create_static_emulator` passes its values to
    `Orbit.create_satellite`. It is not a Satellite entity or Simulator input
    and is public from configuration.
    """

    satellite_id: str
    index_in_orbit: int
    mean_anomaly_deg: float
    payload_items: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "satellite_id",
            validate_identifier(self.satellite_id, "satellite_id"),
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
            raise TypeError(
                "payload_items must contain non-empty string keys and values"
            )
        if len({key for key, _ in payload_items}) != len(payload_items):
            raise ValueError("payload_items contains a duplicate key")
        object.__setattr__(self, "payload_items", tuple(sorted(payload_items)))


@dataclass(frozen=True, slots=True)
class OrbitConfig:
    """Describe one explicit Orbit and its Satellite construction inputs.

    Layer: immutable Config. Input callers create it and the
    `create_static_emulator` consumes it; it is not owned by a Shell and is not
    an Emulator Orbit. It is public for programmatic file-equivalent input.
    """

    orbit_id: str
    right_ascension_deg: float
    satellites: tuple[SatelliteConfig, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "orbit_id", validate_identifier(self.orbit_id, "orbit_id")
        )
        object.__setattr__(
            self,
            "right_ascension_deg",
            _angle(self.right_ascension_deg, "right_ascension_deg"),
        )
        satellites = tuple(self.satellites)
        if not satellites:
            raise ValueError("satellites must contain at least one config")
        if any(
            not isinstance(item, SatelliteConfig) for item in satellites
        ):
            raise TypeError("satellites must contain SatelliteConfig values")
        ids = [item.satellite_id for item in satellites]
        indices = [item.index_in_orbit for item in satellites]
        if len(set(ids)) != len(ids):
            raise ValueError("satellites contains a duplicate satellite_id")
        if len(set(indices)) != len(indices):
            raise ValueError("satellites contains a duplicate index_in_orbit")
        object.__setattr__(
            self,
            "satellites",
            tuple(sorted(satellites, key=lambda item: item.satellite_id)),
        )


@dataclass(frozen=True, slots=True)
class ShellConfig:
    """Describe one Shell through generation rules or explicit Orbit inputs.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it; `create_static_emulator` reads it before calling
    `create_shell`. It is not a Shell, population or ownership layer and is
    public from configuration.
    """

    shell_id: str
    altitude_km: float
    inclination_deg: float
    generation: OrbitGenerationConfig | None = None
    orbits: tuple[OrbitConfig, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "shell_id", validate_identifier(self.shell_id, "shell_id")
        )
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
            self.generation, OrbitGenerationConfig
        ):
            raise TypeError("generation must be an OrbitGenerationConfig")
        if any(not isinstance(item, OrbitConfig) for item in orbits):
            raise TypeError("orbits must contain OrbitConfig values")
        if (self.generation is None) == (not orbits):
            raise ValueError(
                "specify exactly one of generation or explicit orbits"
            )
        ids = [item.orbit_id for item in orbits]
        if len(set(ids)) != len(ids):
            raise ValueError("orbits contains a duplicate orbit_id")
        object.__setattr__(
            self,
            "orbits",
            tuple(sorted(orbits, key=lambda item: item.orbit_id)),
        )
