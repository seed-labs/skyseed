"""Orbit parameters, ownership and Satellite construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Mapping

from skyseed_v2.identifier import validate_identifier
from skyseed_v2._time import normalize_utc_datetime

from .satellite import Satellite
from .validation import _validate_angle


@dataclass(frozen=True, slots=True)
class OrbitParameters:
    """Hold fixed orbital values owned by one Orbit.

    Layer: entity-bound value object. Handwritten callers or static Emulator
    creation build it; Orbit owns it and Simulator-input generation reads it.
    It is not an Orbit, Satellite field, trajectory or dynamic position. It is
    root public because direct Orbit construction requires these values.
    """

    altitude_km: float
    inclination_deg: float
    right_ascension_deg: float
    epoch_utc: datetime

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
        object.__setattr__(
            self,
            "epoch_utc",
            normalize_utc_datetime(self.epoch_utc, "epoch_utc"),
        )


class Orbit:
    """Own the Satellites belonging to exactly one Shell Orbit.

    Layer: approved Emulator entity. `Shell.create_orbit` creates it;
    handwritten callers or static Emulator creation call its Satellite methods.
    It owns one OrbitParameters value and all Satellites in the Orbit. It is not
    a Config, trajectory service or population. It is root public because it is
    the approved Satellite ownership entry point.
    """

    def __init__(
        self,
        orbit_id: str,
        shell_id: str,
        parameters: OrbitParameters,
        satellite_ids: set[str],
        asn: int,
    ) -> None:
        self._orbit_id = validate_identifier(orbit_id, "orbit_id")
        self._shell_id = validate_identifier(shell_id, "shell_id")
        if not isinstance(parameters, OrbitParameters):
            raise TypeError("parameters must be an OrbitParameters value")
        self._parameters = parameters
        self._satellite_ids = satellite_ids
        self._asn = asn
        self._satellites: dict[str, Satellite] = {}
        self._satellite_indices: set[int] = set()

    @property
    def orbit_id(self) -> str:
        return self._orbit_id

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def parameters(self) -> OrbitParameters:
        return self._parameters

    @property
    def satellites(self) -> tuple[Satellite, ...]:
        return tuple(self._satellites.values())

    def get_satellite(self, satellite_id: str) -> Satellite:
        try:
            return self._satellites[satellite_id]
        except KeyError as error:
            raise KeyError(
                f"satellite {satellite_id!r} does not exist in orbit "
                f"{self.orbit_id!r}"
            ) from error

    def create_satellite(
        self,
        satellite_id: str,
        *,
        orbit_index: int,
        mean_anomaly_deg: float,
        payload: Mapping[str, str] | None = None,
    ) -> Satellite:
        """Create and own one Satellite with the Orbit's AS identity."""

        satellite_id = validate_identifier(satellite_id, "satellite_id")
        if satellite_id in self._satellite_ids:
            raise ValueError(f"satellite_id {satellite_id!r} already exists")
        if not isinstance(orbit_index, int) or orbit_index < 0:
            raise ValueError("orbit_index must be a non-negative integer")
        if orbit_index in self._satellite_indices:
            raise ValueError(
                f"orbit_index {orbit_index} already exists in orbit "
                f"{self.orbit_id!r}"
            )

        satellite = Satellite(
            satellite_id=satellite_id,
            shell_id=self.shell_id,
            orbit_id=self.orbit_id,
            orbit_index=orbit_index,
            mean_anomaly_deg=mean_anomaly_deg,
            asn=self._asn,
            payload=payload or {},
        )
        self._satellites[satellite_id] = satellite
        self._satellite_indices.add(orbit_index)
        self._satellite_ids.add(satellite_id)
        return satellite

    def create_satellites(
        self,
        count: int,
        *,
        satellite_id_prefix: str = "sat",
        start_index: int | None = None,
        mean_anomaly_start_deg: float = 0.0,
        mean_anomaly_step_deg: float | None = None,
        payload: Mapping[str, str] | None = None,
    ) -> tuple[Satellite, ...]:
        """Plan a deterministic batch, then delegate each entity creation."""

        if not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")
        if start_index is None:
            start_index = max(self._satellite_indices, default=-1) + 1
        if not isinstance(start_index, int) or start_index < 0:
            raise ValueError("start_index must be a non-negative integer")
        step = (
            360.0 / count
            if mean_anomaly_step_deg is None
            else float(mean_anomaly_step_deg)
        )
        if not isfinite(step):
            raise ValueError("mean_anomaly_step_deg must be finite")

        planned_satellites = []
        for offset in range(count):
            orbit_index = start_index + offset
            satellite_id = validate_identifier(
                f"{satellite_id_prefix}-{orbit_index}", "satellite_id"
            )
            mean_anomaly = (
                float(mean_anomaly_start_deg) + offset * step
            ) % 360.0
            planned_satellites.append(
                (satellite_id, orbit_index, mean_anomaly)
            )

        candidate_ids = [item[0] for item in planned_satellites]
        candidate_indices = [item[1] for item in planned_satellites]
        if len(set(candidate_ids)) != len(candidate_ids) or any(
            satellite_id in self._satellite_ids
            for satellite_id in candidate_ids
        ):
            raise ValueError("satellite batch contains an existing satellite_id")
        if any(index in self._satellite_indices for index in candidate_indices):
            raise ValueError("satellite batch contains an existing orbit_index")

        return tuple(
            self.create_satellite(
                satellite_id,
                orbit_index=orbit_index,
                mean_anomaly_deg=mean_anomaly,
                payload=payload,
            )
            for satellite_id, orbit_index, mean_anomaly in planned_satellites
        )
