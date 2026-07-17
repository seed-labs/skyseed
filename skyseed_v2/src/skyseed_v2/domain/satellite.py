"""Static Shell, Orbit, and Satellite domain model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Iterable, Mapping

from seedemu.core import Node, Scope, ScopeTier, ScopeType

from skyseed_v2.contracts import RuntimeNodeReference


SATELLITE_REGISTRY_TYPE = "satnode"
SATELLITE_ROLE_LABEL = "Satellite"
SKYSEED_METADATA_PREFIX = "skyseed."


class SatelliteRole(Enum):
    """A Skyseed role distinct from every upstream SEED NodeRole."""

    SATELLITE = SATELLITE_ROLE_LABEL


def validate_identifier(value: str, field_name: str) -> str:
    """Validate identifiers that must remain stable in Registry and output names."""

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


def _validate_angle(value: float, field_name: str, *, inclusive_upper: bool = False) -> float:
    value = float(value)
    upper_valid = value <= 360.0 if inclusive_upper else value < 360.0
    if not isfinite(value) or value < 0.0 or not upper_valid:
        upper = "360" if inclusive_upper else "360 (exclusive)"
        raise ValueError(f"{field_name} must be between 0 and {upper}")
    return value


@dataclass(frozen=True, slots=True)
class OrbitParameters:
    """Fixed parameters shared by every Satellite in one Orbit."""

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


class Satellite(Node):
    """The one Skyseed Satellite object and its SEED node capability."""

    def __init__(
        self,
        *,
        satellite_id: str,
        shell_id: str,
        orbit_id: str,
        orbit_index: int,
        orbit_parameters: OrbitParameters,
        mean_anomaly_deg: float,
        asn: int,
        payload: Mapping[str, str] | None = None,
    ) -> None:
        self._initialize_static_state(
            satellite_id=satellite_id,
            shell_id=shell_id,
            orbit_id=orbit_id,
            orbit_index=orbit_index,
            orbit_parameters=orbit_parameters,
            mean_anomaly_deg=mean_anomaly_deg,
            payload=payload or {},
        )
        self._initialize_seed_node(asn)
        self._initialize_skyseed_identity()

    def _initialize_static_state(
        self,
        *,
        satellite_id: str,
        shell_id: str,
        orbit_id: str,
        orbit_index: int,
        orbit_parameters: OrbitParameters,
        mean_anomaly_deg: float,
        payload: Mapping[str, str],
    ) -> None:
        self._satellite_id = validate_identifier(satellite_id, "satellite_id")
        self._shell_id = validate_identifier(shell_id, "shell_id")
        self._orbit_id = validate_identifier(orbit_id, "orbit_id")
        if not isinstance(orbit_index, int) or orbit_index < 0:
            raise ValueError("orbit_index must be a non-negative integer")
        if not isinstance(orbit_parameters, OrbitParameters):
            raise TypeError("orbit_parameters must be an OrbitParameters value")
        copied_payload = dict(payload)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in copied_payload.items()
        ):
            raise TypeError("payload keys and values must be strings")

        self._orbit_index = orbit_index
        self._orbit_parameters = orbit_parameters
        self._mean_anomaly_deg = _validate_angle(
            mean_anomaly_deg, "mean_anomaly_deg"
        )
        self._payload = MappingProxyType(copied_payload)

    def _initialize_seed_node(self, asn: int) -> None:
        if not isinstance(asn, int) or asn <= 0:
            raise ValueError("asn must be a positive integer")
        super().__init__(
            name=self.satellite_id,
            role=SatelliteRole.SATELLITE,
            asn=asn,
        )

    def _initialize_skyseed_identity(self) -> None:
        self.setDisplayName(self.satellite_id)
        self.setLabel(
            f"{SKYSEED_METADATA_PREFIX}entity_type", SATELLITE_ROLE_LABEL
        )
        self.setLabel(
            f"{SKYSEED_METADATA_PREFIX}satellite_id", self.satellite_id
        )
        self.setLabel(f"{SKYSEED_METADATA_PREFIX}shell_id", self.shell_id)
        self.setLabel(f"{SKYSEED_METADATA_PREFIX}orbit_id", self.orbit_id)
        self.setLabel(
            f"{SKYSEED_METADATA_PREFIX}runtime_node_ref",
            self.runtime_reference.value,
        )

    @property
    def satellite_id(self) -> str:
        return self._satellite_id

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def orbit_id(self) -> str:
        return self._orbit_id

    @property
    def orbit_index(self) -> int:
        return self._orbit_index

    @property
    def orbit_parameters(self) -> OrbitParameters:
        return self._orbit_parameters

    @property
    def mean_anomaly_deg(self) -> float:
        return self._mean_anomaly_deg

    @property
    def payload(self) -> Mapping[str, str]:
        return self._payload

    @property
    def runtime_reference(self) -> RuntimeNodeReference:
        return RuntimeNodeReference(
            asn=self.getAsn(),
            registry_type=SATELLITE_REGISTRY_TYPE,
            node_name=self.getName(),
        )

    def scope(self) -> Scope:
        # SEED has no public ScopeType extension registry. ANY preserves
        # global/AS/node option precedence without claiming Host identity.
        return Scope(
            ScopeTier.Node,
            node_type=ScopeType.ANY,
            node_id=self.getName(),
            as_id=self.getAsn(),
        )

class Orbit:
    """An Orbit owned by exactly one Shell."""

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
                f"satellite {satellite_id!r} does not exist in orbit {self.orbit_id!r}"
            ) from error

    def create_satellite(
        self,
        satellite_id: str,
        *,
        orbit_index: int,
        mean_anomaly_deg: float,
        payload: Mapping[str, str] | None = None,
    ) -> Satellite:
        satellite_id = validate_identifier(satellite_id, "satellite_id")
        if satellite_id in self._satellite_ids:
            raise ValueError(f"satellite_id {satellite_id!r} already exists")
        if not isinstance(orbit_index, int) or orbit_index < 0:
            raise ValueError("orbit_index must be a non-negative integer")
        if orbit_index in self._satellite_indices:
            raise ValueError(
                f"orbit_index {orbit_index} already exists in orbit {self.orbit_id!r}"
            )

        satellite = Satellite(
            satellite_id=satellite_id,
            shell_id=self.shell_id,
            orbit_id=self.orbit_id,
            orbit_index=orbit_index,
            orbit_parameters=self.parameters,
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
        """Atomically create a deterministic batch of Satellites."""

        if not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")
        if start_index is None:
            start_index = max(self._satellite_indices, default=-1) + 1
        if not isinstance(start_index, int) or start_index < 0:
            raise ValueError("start_index must be a non-negative integer")
        step = 360.0 / count if mean_anomaly_step_deg is None else float(mean_anomaly_step_deg)
        if not isfinite(step):
            raise ValueError("mean_anomaly_step_deg must be finite")

        specifications = []
        for offset in range(count):
            orbit_index = start_index + offset
            satellite_id = validate_identifier(
                f"{satellite_id_prefix}-{orbit_index}", "satellite_id"
            )
            mean_anomaly = (float(mean_anomaly_start_deg) + offset * step) % 360.0
            specifications.append((satellite_id, orbit_index, mean_anomaly))

        candidate_ids = [specification[0] for specification in specifications]
        candidate_indices = [specification[1] for specification in specifications]
        if len(set(candidate_ids)) != len(candidate_ids) or any(
            satellite_id in self._satellite_ids for satellite_id in candidate_ids
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
            for satellite_id, orbit_index, mean_anomaly in specifications
        )


class Shell:
    """A Shell that exclusively owns its Orbits."""

    def __init__(
        self,
        shell_id: str,
        *,
        asn: int,
        satellite_ids: set[str] | None = None,
    ) -> None:
        self._shell_id = validate_identifier(shell_id, "shell_id")
        self._orbits: dict[str, Orbit] = {}
        self._satellite_ids = satellite_ids if satellite_ids is not None else set()
        self._asn = asn

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def orbits(self) -> tuple[Orbit, ...]:
        return tuple(self._orbits.values())

    def create_orbit(
        self, orbit_id: str, *, parameters: OrbitParameters
    ) -> Orbit:
        orbit_id = validate_identifier(orbit_id, "orbit_id")
        if orbit_id in self._orbits:
            raise ValueError(
                f"orbit_id {orbit_id!r} already exists in shell {self.shell_id!r}"
            )
        orbit = Orbit(
            orbit_id=orbit_id,
            shell_id=self.shell_id,
            parameters=parameters,
            satellite_ids=self._satellite_ids,
            asn=self._asn,
        )
        self._orbits[orbit_id] = orbit
        return orbit

    def get_orbit(self, orbit_id: str) -> Orbit:
        try:
            return self._orbits[orbit_id]
        except KeyError as error:
            raise KeyError(
                f"orbit {orbit_id!r} does not exist in shell {self.shell_id!r}"
            ) from error

    def iter_satellites(self) -> Iterable[Satellite]:
        for orbit in self._orbits.values():
            yield from orbit.satellites
