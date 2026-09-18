"""Immutable physical entities and their local identity/value validation."""
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from types import MappingProxyType
from typing import Mapping
from skyseed_v2.shared import NodeId
from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.shared.time import normalize_utc_datetime
from .capability import InterSatelliteLinkCapability

_SATELLITE_KIND = "satellite"
_GROUND_STATION_KIND = "ground-station"

@dataclass(frozen=True, slots=True)
class PhysicalSatellite:
    """Represent one Simulator-owned static PhysicalSatellite.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and PhysicalOrbit owns it. It is not an Emulator entity,
    SEED Node, runtime
    position or resource handle. It is public from ``skyseed_v2.simulator`` so
    callers can read results returned by the PhysicalWorld facade. Its optional
    ``InterSatelliteLinkCapability`` is static physical capacity, not a
    candidate, terminal-direction claim or operational selection.
    """

    node_id: NodeId
    orbit_index: int
    mean_anomaly_deg: float
    max_simultaneous_ground_station_accesses: int | None = None
    inter_satellite_link_capability: InterSatelliteLinkCapability | None = None

    def __post_init__(self) -> None:
        _validate_node_identity(self.node_id, _SATELLITE_KIND)
        if (
            isinstance(self.orbit_index, bool)
            or not isinstance(self.orbit_index, int)
            or self.orbit_index < 0
        ):
            raise ValueError("orbit_index must be a non-negative integer")
        object.__setattr__(
            self,
            "mean_anomaly_deg",
            _validate_angle(self.mean_anomaly_deg, "mean_anomaly_deg"),
        )
        capability = _validate_optional_positive_integer(
            self.max_simultaneous_ground_station_accesses,
            "max_simultaneous_ground_station_accesses",
        )
        object.__setattr__(
            self, "max_simultaneous_ground_station_accesses", capability
        )
        if self.inter_satellite_link_capability is not None and not isinstance(
            self.inter_satellite_link_capability,
            InterSatelliteLinkCapability,
        ):
            raise TypeError(
                "inter_satellite_link_capability must be an "
                "InterSatelliteLinkCapability or None"
            )

    @property
    def satellite_id(self) -> str:
        return self.node_id.entity_id


@dataclass(frozen=True, slots=True)
class PhysicalGroundStation:
    """Represent one Simulator-owned static PhysicalGroundStation.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and a Simulator-side AS owns it. It is not an
    Emulator Router, network
    attachment, service or runtime resource. It is public from
    ``skyseed_v2.simulator`` so callers can read PhysicalWorld facade results.
    """

    node_id: NodeId
    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    station_type: str
    operator: str | None
    max_simultaneous_satellite_accesses: int | None = None

    def __post_init__(self) -> None:
        _validate_node_identity(self.node_id, _GROUND_STATION_KIND)
        latitude = _finite_float(self.latitude_deg, "latitude_deg")
        longitude = _finite_float(self.longitude_deg, "longitude_deg")
        elevation = _finite_float(self.elevation_m, "elevation_m")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")
        _validate_text(self.station_type, "station_type")
        operator = _validate_optional_text(self.operator, "operator")
        capability = _validate_optional_positive_integer(
            self.max_simultaneous_satellite_accesses,
            "max_simultaneous_satellite_accesses",
        )
        object.__setattr__(self, "latitude_deg", latitude)
        object.__setattr__(self, "longitude_deg", longitude)
        object.__setattr__(self, "elevation_m", elevation)
        object.__setattr__(self, "operator", operator)
        object.__setattr__(
            self, "max_simultaneous_satellite_accesses", capability
        )

    @property
    def ground_station_id(self) -> str:
        return self.node_id.entity_id


@dataclass(frozen=True, slots=True, init=False)
class PhysicalOrbit:
    """Own one Simulator-side PhysicalOrbit's parameters and Satellites.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and one PhysicalShell owns it. It is not an Emulator
    PhysicalOrbit, trajectory service or
    dynamic position cache. It is public from ``skyseed_v2.simulator`` so facade
    callers can navigate the approved static hierarchy.
    """

    _asn: int
    _shell_id: str
    _orbit_id: str
    _altitude_km: float
    _inclination_deg: float
    _right_ascension_deg: float
    _epoch_utc: datetime
    _satellites: tuple[PhysicalSatellite, ...]
    _satellites_by_node_id: Mapping[NodeId, PhysicalSatellite] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError("PhysicalOrbit values are created by PhysicalWorld materialization")

    @classmethod
    def _create(
        cls,
        *,
        asn: int,
        shell_id: str,
        orbit_id: str,
        altitude_km: float,
        inclination_deg: float,
        right_ascension_deg: float,
        epoch_utc: datetime,
        satellites: Iterable[PhysicalSatellite],
    ) -> PhysicalOrbit:
        _validate_asn(asn)
        shell_id = validate_identifier(shell_id, "shell_id")
        orbit_id = validate_identifier(orbit_id, "orbit_id")
        altitude = _finite_float(altitude_km, "altitude_km")
        inclination = _finite_float(inclination_deg, "inclination_deg")
        right_ascension = _validate_angle(
            right_ascension_deg, "right_ascension_deg"
        )
        if altitude <= 0.0:
            raise ValueError("altitude_km must be positive")
        if not 0.0 <= inclination <= 180.0:
            raise ValueError("inclination_deg must be between 0 and 180")

        satellite_values = tuple(satellites)
        if not satellite_values:
            raise ValueError("PhysicalOrbit must contain at least one PhysicalSatellite")
        by_node_id: dict[NodeId, PhysicalSatellite] = {}
        indices: set[int] = set()
        for satellite in satellite_values:
            if not isinstance(satellite, PhysicalSatellite):
                raise TypeError("satellites must contain PhysicalSatellite values")
            if satellite.node_id.asn != asn:
                raise ValueError("PhysicalSatellite ASN must match its owning PhysicalOrbit")
            if satellite.node_id in by_node_id:
                raise ValueError(f"duplicate NodeId: {satellite.node_id.value}")
            if satellite.orbit_index in indices:
                raise ValueError(
                    f"duplicate orbit_index {satellite.orbit_index} in PhysicalOrbit"
                )
            by_node_id[satellite.node_id] = satellite
            indices.add(satellite.orbit_index)

        instance = object.__new__(cls)
        object.__setattr__(instance, "_asn", asn)
        object.__setattr__(instance, "_shell_id", shell_id)
        object.__setattr__(instance, "_orbit_id", orbit_id)
        object.__setattr__(instance, "_altitude_km", altitude)
        object.__setattr__(instance, "_inclination_deg", inclination)
        object.__setattr__(instance, "_right_ascension_deg", right_ascension)
        object.__setattr__(
            instance,
            "_epoch_utc",
            normalize_utc_datetime(epoch_utc, "epoch_utc"),
        )
        object.__setattr__(instance, "_satellites", satellite_values)
        object.__setattr__(
            instance, "_satellites_by_node_id", MappingProxyType(by_node_id)
        )
        return instance

    @property
    def asn(self) -> int:
        return self._asn

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def orbit_id(self) -> str:
        return self._orbit_id

    @property
    def altitude_km(self) -> float:
        return self._altitude_km

    @property
    def inclination_deg(self) -> float:
        return self._inclination_deg

    @property
    def right_ascension_deg(self) -> float:
        return self._right_ascension_deg

    @property
    def epoch_utc(self) -> datetime:
        return self._epoch_utc

    @property
    def satellites(self) -> tuple[PhysicalSatellite, ...]:
        return self._satellites

    def get_satellite(self, node_id: NodeId) -> PhysicalSatellite:
        _validate_query_node_id(node_id, _SATELLITE_KIND)
        try:
            return self._satellites_by_node_id[node_id]
        except KeyError as error:
            raise KeyError(f"PhysicalSatellite not found: {node_id.value}") from error


@dataclass(frozen=True, slots=True, init=False)
class PhysicalShell:
    """Own Simulator-side Orbits within one AS-qualified PhysicalShell.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and one Simulator-side AS owns it. It is not an
    Emulator PhysicalShell, public
    PhysicalShell-ID value type or population helper. It is public from
    ``skyseed_v2.simulator`` for navigation through PhysicalWorld results.
    """

    _asn: int
    _shell_id: str
    _orbits: tuple[PhysicalOrbit, ...]
    _orbits_by_id: Mapping[str, PhysicalOrbit] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError("PhysicalShell values are created by PhysicalWorld materialization")

    @classmethod
    def _create(
        cls, *, asn: int, shell_id: str, orbits: Iterable[PhysicalOrbit]
    ) -> PhysicalShell:
        _validate_asn(asn)
        shell_id = validate_identifier(shell_id, "shell_id")
        orbit_values = tuple(orbits)
        if not orbit_values:
            raise ValueError("PhysicalShell must contain at least one PhysicalOrbit")
        by_id: dict[str, PhysicalOrbit] = {}
        for orbit in orbit_values:
            if not isinstance(orbit, PhysicalOrbit):
                raise TypeError("orbits must contain PhysicalOrbit values")
            if orbit.asn != asn or orbit.shell_id != shell_id:
                raise ValueError("PhysicalOrbit identity must match its owning PhysicalShell")
            if orbit.orbit_id in by_id:
                raise ValueError(f"duplicate PhysicalOrbit ID: {orbit.orbit_id}")
            by_id[orbit.orbit_id] = orbit

        instance = object.__new__(cls)
        object.__setattr__(instance, "_asn", asn)
        object.__setattr__(instance, "_shell_id", shell_id)
        object.__setattr__(instance, "_orbits", orbit_values)
        object.__setattr__(instance, "_orbits_by_id", MappingProxyType(by_id))
        return instance

    @property
    def asn(self) -> int:
        return self._asn

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def orbits(self) -> tuple[PhysicalOrbit, ...]:
        return self._orbits

    def get_orbit(self, orbit_id: str) -> PhysicalOrbit:
        if not isinstance(orbit_id, str):
            raise TypeError("orbit_id must be a string")
        try:
            return self._orbits_by_id[orbit_id]
        except KeyError as error:
            raise KeyError(f"PhysicalOrbit not found: {orbit_id}") from error


@dataclass(frozen=True, slots=True, init=False)
class PhysicalAutonomousSystem:
    """Own Simulator-side Shells and GroundStations for one ASN.

    Layer: immutable Simulator physical-domain entity and static owner.
    PhysicalWorld materialization creates it and PhysicalWorld owns it. It is not a seedemu
    AutonomousSystem, Emulator entity or runtime resource registry. It is public
    from ``skyseed_v2.simulator`` for facade-returned hierarchy navigation.
    """

    _asn: int
    _shells: tuple[PhysicalShell, ...]
    _ground_stations: tuple[PhysicalGroundStation, ...]
    _shells_by_id: Mapping[str, PhysicalShell] = field(
        repr=False, compare=False, hash=False
    )
    _ground_stations_by_node_id: Mapping[NodeId, PhysicalGroundStation] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError(
            "PhysicalAutonomousSystem values are created by PhysicalWorld materialization"
        )

    @classmethod
    def _create(
        cls,
        *,
        asn: int,
        shells: Iterable[PhysicalShell],
        ground_stations: Iterable[PhysicalGroundStation],
    ) -> PhysicalAutonomousSystem:
        _validate_asn(asn)
        shell_values = tuple(shells)
        ground_station_values = tuple(ground_stations)
        shells_by_id: dict[str, PhysicalShell] = {}
        ground_stations_by_node_id: dict[NodeId, PhysicalGroundStation] = {}

        for shell in shell_values:
            if not isinstance(shell, PhysicalShell):
                raise TypeError("shells must contain PhysicalShell values")
            if shell.asn != asn:
                raise ValueError("PhysicalShell ASN must match its owning AS")
            if shell.shell_id in shells_by_id:
                raise ValueError(f"duplicate PhysicalShell ID: {shell.shell_id}")
            shells_by_id[shell.shell_id] = shell

        for ground_station in ground_station_values:
            if not isinstance(ground_station, PhysicalGroundStation):
                raise TypeError(
                    "ground_stations must contain PhysicalGroundStation values"
                )
            if ground_station.node_id.asn != asn:
                raise ValueError("PhysicalGroundStation ASN must match its owning AS")
            if ground_station.node_id in ground_stations_by_node_id:
                raise ValueError(
                    f"duplicate NodeId: {ground_station.node_id.value}"
                )
            ground_stations_by_node_id[ground_station.node_id] = ground_station

        instance = object.__new__(cls)
        object.__setattr__(instance, "_asn", asn)
        object.__setattr__(instance, "_shells", shell_values)
        object.__setattr__(instance, "_ground_stations", ground_station_values)
        object.__setattr__(
            instance, "_shells_by_id", MappingProxyType(shells_by_id)
        )
        object.__setattr__(
            instance,
            "_ground_stations_by_node_id",
            MappingProxyType(ground_stations_by_node_id),
        )
        return instance

    @property
    def asn(self) -> int:
        return self._asn

    @property
    def shells(self) -> tuple[PhysicalShell, ...]:
        return self._shells

    @property
    def ground_stations(self) -> tuple[PhysicalGroundStation, ...]:
        return self._ground_stations

    def get_shell(self, shell_id: str) -> PhysicalShell:
        if not isinstance(shell_id, str):
            raise TypeError("shell_id must be a string")
        try:
            return self._shells_by_id[shell_id]
        except KeyError as error:
            raise KeyError(f"PhysicalShell not found: {shell_id}") from error

    def get_ground_station(self, node_id: NodeId) -> PhysicalGroundStation:
        _validate_query_node_id(node_id, _GROUND_STATION_KIND)
        try:
            return self._ground_stations_by_node_id[node_id]
        except KeyError as error:
            raise KeyError(f"PhysicalGroundStation not found: {node_id.value}") from error


def _validate_node_identity(
    node_id: NodeId, expected_kind: str, entity_id: str | None = None
) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError("node_id must be a NodeId")
    if node_id.entity_kind != expected_kind:
        raise ValueError(f"node_id kind must be {expected_kind}")
    if entity_id is not None and node_id.entity_id != entity_id:
        raise ValueError("node_id entity ID must match the input entity ID")


def _validate_optional_positive_integer(
    value: int | None, field_name: str
) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _validate_query_node_id(
    node_id: NodeId, expected_kind: str | None = None
) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError("node_id must be a NodeId")
    if expected_kind is not None and node_id.entity_kind != expected_kind:
        raise TypeError(f"node_id must identify a {expected_kind}")


def _validate_asn(asn: int) -> None:
    if isinstance(asn, bool) or not isinstance(asn, int) or asn <= 0:
        raise ValueError("asn must be a positive integer")


def _finite_float(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite number") from error
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted


def _validate_angle(value: float, field_name: str) -> float:
    converted = _finite_float(value, field_name)
    if not 0.0 <= converted < 360.0:
        raise ValueError(f"{field_name} must be between 0 inclusive and 360 exclusive")
    return converted


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_optional_text(
    value: str | None, field_name: str
) -> str | None:
    if value is None:
        return None
    _validate_text(value, field_name)
    return value.strip()
