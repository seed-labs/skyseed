"""Define the Simulator-owned immutable PhysicalWorld aggregate."""

from __future__ import annotations

from collections.abc import Iterable, Mapping as MappingABC
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from threading import RLock
from time import perf_counter
from types import MappingProxyType
from typing import Mapping
from weakref import WeakValueDictionary

from skyseed_v2.configuration.simulator import ZoneLayoutDefinition
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.shared import NodeId

from ..input import (
    SimulatorStaticInput,
    GroundStationSimulationInput,
    SatelliteSimulationInput,
)
from ..geometry import EarthFixedPosition
from .capability import InterSatelliteLinkCapability
from .physical_frame import PhysicalFrame
from .position import PositionModel
from .zone import SphericalCap, Zone, ZoneLayout, ZoneMembershipModel


_SATELLITE_KIND = "satellite"
_GROUND_STATION_KIND = "ground-station"
_OrbitKey = tuple[int, str, str]
_ShellKey = tuple[int, str]
_POSITION_MODEL_OWNERS: WeakValueDictionary[int, PhysicalWorld] = (
    WeakValueDictionary()
)
_POSITION_MODEL_OWNER_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class Satellite:
    """Represent one Simulator-owned static Satellite.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and Orbit owns it. It is not an Emulator entity,
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
class GroundStation:
    """Represent one Simulator-owned static GroundStation.

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
class Orbit:
    """Own one Simulator-side Orbit's parameters and Satellites.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and one Shell owns it. It is not an Emulator
    Orbit, trajectory service or
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
    _satellites: tuple[Satellite, ...]
    _satellites_by_node_id: Mapping[NodeId, Satellite] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError("Orbit values are created by PhysicalWorld materialization")

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
        satellites: Iterable[Satellite],
    ) -> Orbit:
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
            raise ValueError("Orbit must contain at least one Satellite")
        by_node_id: dict[NodeId, Satellite] = {}
        indices: set[int] = set()
        for satellite in satellite_values:
            if not isinstance(satellite, Satellite):
                raise TypeError("satellites must contain Satellite values")
            if satellite.node_id.asn != asn:
                raise ValueError("Satellite ASN must match its owning Orbit")
            if satellite.node_id in by_node_id:
                raise ValueError(f"duplicate NodeId: {satellite.node_id.value}")
            if satellite.orbit_index in indices:
                raise ValueError(
                    f"duplicate orbit_index {satellite.orbit_index} in Orbit"
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
    def satellites(self) -> tuple[Satellite, ...]:
        return self._satellites

    def get_satellite(self, node_id: NodeId) -> Satellite:
        _validate_query_node_id(node_id, _SATELLITE_KIND)
        try:
            return self._satellites_by_node_id[node_id]
        except KeyError as error:
            raise KeyError(f"Satellite not found: {node_id.value}") from error


@dataclass(frozen=True, slots=True, init=False)
class Shell:
    """Own Simulator-side Orbits within one AS-qualified Shell.

    Layer: immutable Simulator physical-domain entity. PhysicalWorld
    materialization creates it and one Simulator-side AS owns it. It is not an
    Emulator Shell, public
    Shell-ID value type or population helper. It is public from
    ``skyseed_v2.simulator`` for navigation through PhysicalWorld results.
    """

    _asn: int
    _shell_id: str
    _orbits: tuple[Orbit, ...]
    _orbits_by_id: Mapping[str, Orbit] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError("Shell values are created by PhysicalWorld materialization")

    @classmethod
    def _create(
        cls, *, asn: int, shell_id: str, orbits: Iterable[Orbit]
    ) -> Shell:
        _validate_asn(asn)
        shell_id = validate_identifier(shell_id, "shell_id")
        orbit_values = tuple(orbits)
        if not orbit_values:
            raise ValueError("Shell must contain at least one Orbit")
        by_id: dict[str, Orbit] = {}
        for orbit in orbit_values:
            if not isinstance(orbit, Orbit):
                raise TypeError("orbits must contain Orbit values")
            if orbit.asn != asn or orbit.shell_id != shell_id:
                raise ValueError("Orbit identity must match its owning Shell")
            if orbit.orbit_id in by_id:
                raise ValueError(f"duplicate Orbit ID: {orbit.orbit_id}")
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
    def orbits(self) -> tuple[Orbit, ...]:
        return self._orbits

    def get_orbit(self, orbit_id: str) -> Orbit:
        if not isinstance(orbit_id, str):
            raise TypeError("orbit_id must be a string")
        try:
            return self._orbits_by_id[orbit_id]
        except KeyError as error:
            raise KeyError(f"Orbit not found: {orbit_id}") from error


@dataclass(frozen=True, slots=True, init=False)
class SkyseedAutonomousSystem:
    """Own Simulator-side Shells and GroundStations for one ASN.

    Layer: immutable Simulator physical-domain entity and static owner.
    PhysicalWorld materialization creates it and PhysicalWorld owns it. It is not a seedemu
    AutonomousSystem, Emulator entity or runtime resource registry. It is public
    from ``skyseed_v2.simulator`` for facade-returned hierarchy navigation.
    """

    _asn: int
    _shells: tuple[Shell, ...]
    _ground_stations: tuple[GroundStation, ...]
    _shells_by_id: Mapping[str, Shell] = field(
        repr=False, compare=False, hash=False
    )
    _ground_stations_by_node_id: Mapping[NodeId, GroundStation] = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError(
            "SkyseedAutonomousSystem values are created by PhysicalWorld materialization"
        )

    @classmethod
    def _create(
        cls,
        *,
        asn: int,
        shells: Iterable[Shell],
        ground_stations: Iterable[GroundStation],
    ) -> SkyseedAutonomousSystem:
        _validate_asn(asn)
        shell_values = tuple(shells)
        ground_station_values = tuple(ground_stations)
        shells_by_id: dict[str, Shell] = {}
        ground_stations_by_node_id: dict[NodeId, GroundStation] = {}

        for shell in shell_values:
            if not isinstance(shell, Shell):
                raise TypeError("shells must contain Shell values")
            if shell.asn != asn:
                raise ValueError("Shell ASN must match its owning AS")
            if shell.shell_id in shells_by_id:
                raise ValueError(f"duplicate Shell ID: {shell.shell_id}")
            shells_by_id[shell.shell_id] = shell

        for ground_station in ground_station_values:
            if not isinstance(ground_station, GroundStation):
                raise TypeError(
                    "ground_stations must contain GroundStation values"
                )
            if ground_station.node_id.asn != asn:
                raise ValueError("GroundStation ASN must match its owning AS")
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
    def shells(self) -> tuple[Shell, ...]:
        return self._shells

    @property
    def ground_stations(self) -> tuple[GroundStation, ...]:
        return self._ground_stations

    def get_shell(self, shell_id: str) -> Shell:
        if not isinstance(shell_id, str):
            raise TypeError("shell_id must be a string")
        try:
            return self._shells_by_id[shell_id]
        except KeyError as error:
            raise KeyError(f"Shell not found: {shell_id}") from error

    def get_ground_station(self, node_id: NodeId) -> GroundStation:
        _validate_query_node_id(node_id, _GROUND_STATION_KIND)
        try:
            return self._ground_stations_by_node_id[node_id]
        except KeyError as error:
            raise KeyError(f"GroundStation not found: {node_id.value}") from error


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class PhysicalWorld:
    """Own static physical facts and project timestamped physical frames.

    Layer: Simulator physical aggregate root and query/projection facade.
    ``Simulator`` creates it explicitly from ``SimulatorStaticInput`` plus one
    ``PositionModel`` and then owns it. It is not boundary input, a time cursor,
    global resource registry, operational state, or planning service. It is
    public from ``skyseed_v2.simulator`` because callers consume its stable
    read-only facts and ``PhysicalFrame`` results.
    """

    _autonomous_systems: tuple[SkyseedAutonomousSystem, ...]
    _autonomous_systems_by_asn: Mapping[int, SkyseedAutonomousSystem] = field(
        repr=False, compare=False, hash=False
    )
    _nodes_by_id: Mapping[NodeId, Satellite | GroundStation] = field(
        repr=False, compare=False, hash=False
    )
    _zone_layout: ZoneLayout | None = field(
        repr=False, compare=False, hash=False
    )
    _position_model: PositionModel = field(
        repr=False, compare=False, hash=False
    )
    _execution_observer: _ExecutionObserver | None = field(
        repr=False, compare=False, hash=False
    )
    _semantic_fingerprint: str = field(
        repr=False, compare=False, hash=False
    )

    def __init__(self) -> None:
        raise TypeError("PhysicalWorld is created explicitly by Simulator")

    @classmethod
    def _materialize(
        cls,
        static_input: SimulatorStaticInput,
        position_model: PositionModel,
        zone_layout_definition: ZoneLayoutDefinition | None = None,
        zone_membership_model: ZoneMembershipModel | None = None,
        execution_observer: _ExecutionObserver | None = None,
    ) -> PhysicalWorld:
        if not isinstance(static_input, SimulatorStaticInput):
            raise TypeError("static_input must be a SimulatorStaticInput")
        if not isinstance(position_model, PositionModel):
            raise TypeError("position_model must implement PositionModel")
        if execution_observer is not None and not isinstance(
            execution_observer, _ExecutionObserver
        ):
            raise TypeError(
                "execution_observer must be an _ExecutionObserver or None"
            )
        if zone_layout_definition is None and zone_membership_model is not None:
            raise ValueError(
                "zone_membership_model requires zone_layout_definition"
            )
        zone_layout = (
            None
            if zone_layout_definition is None
            else ZoneLayout._materialize(
                zone_layout_definition, zone_membership_model
            )
        )

        satellite_inputs_by_orbit: dict[
            _OrbitKey, list[SatelliteSimulationInput]
        ] = {}
        orbit_parameters: dict[
            _OrbitKey, tuple[float, float, float, datetime]
        ] = {}
        orbit_indices: dict[_OrbitKey, set[int]] = {}
        satellite_parents: dict[NodeId, _OrbitKey] = {}
        ground_station_inputs_by_asn: dict[
            int, list[GroundStationSimulationInput]
        ] = {}
        seen_node_ids: set[NodeId] = set()

        for simulation_input in static_input.list_nodes():
            if simulation_input.node_id in seen_node_ids:
                raise ValueError(
                    f"duplicate NodeId: {simulation_input.node_id.value}"
                )
            seen_node_ids.add(simulation_input.node_id)

            if isinstance(simulation_input, SatelliteSimulationInput):
                _validate_satellite_input(simulation_input)
                parent = (
                    simulation_input.node_id.asn,
                    simulation_input.shell_id,
                    simulation_input.orbit_id,
                )
                previous_parent = satellite_parents.get(simulation_input.node_id)
                if previous_parent is not None and previous_parent != parent:
                    raise ValueError(
                        f"Satellite appears in multiple Orbits: "
                        f"{simulation_input.node_id.value}"
                    )
                satellite_parents[simulation_input.node_id] = parent

                parameters = _validated_orbit_parameters(simulation_input)
                previous_parameters = orbit_parameters.setdefault(
                    parent, parameters
                )
                if previous_parameters != parameters:
                    raise ValueError(
                        "conflicting Orbit parameters for "
                        f"{parent[0]}/{parent[1]}/{parent[2]}"
                    )
                indices = orbit_indices.setdefault(parent, set())
                if simulation_input.orbit_index in indices:
                    raise ValueError(
                        f"duplicate orbit_index {simulation_input.orbit_index} "
                        f"in Orbit {parent[0]}/{parent[1]}/{parent[2]}"
                    )
                indices.add(simulation_input.orbit_index)
                satellite_inputs_by_orbit.setdefault(parent, []).append(
                    simulation_input
                )
            elif isinstance(simulation_input, GroundStationSimulationInput):
                _validate_ground_station_input(simulation_input)
                ground_station_inputs_by_asn.setdefault(
                    simulation_input.node_id.asn, []
                ).append(simulation_input)
            else:  # pragma: no cover - SimulatorStaticInput already fails closed
                raise TypeError("unsupported Simulator static input value")

        orbits_by_shell: dict[_ShellKey, list[Orbit]] = {}
        for orbit_key in sorted(satellite_inputs_by_orbit):
            asn, shell_id, orbit_id = orbit_key
            parameters = orbit_parameters[orbit_key]
            satellites = tuple(
                Satellite(
                    node_id=simulation_input.node_id,
                    orbit_index=simulation_input.orbit_index,
                    mean_anomaly_deg=simulation_input.mean_anomaly_deg,
                    max_simultaneous_ground_station_accesses=(
                        simulation_input.max_simultaneous_ground_station_accesses
                    ),
                    inter_satellite_link_capability=_isl_capability(
                        simulation_input
                        .maximum_simultaneous_inter_satellite_links
                    ),
                )
                for simulation_input in sorted(
                    satellite_inputs_by_orbit[orbit_key],
                    key=lambda value: (value.orbit_index, value.node_id.value),
                )
            )
            orbit = Orbit._create(
                asn=asn,
                shell_id=shell_id,
                orbit_id=orbit_id,
                altitude_km=parameters[0],
                inclination_deg=parameters[1],
                right_ascension_deg=parameters[2],
                epoch_utc=parameters[3],
                satellites=satellites,
            )
            orbits_by_shell.setdefault((asn, shell_id), []).append(orbit)

        shells_by_asn: dict[int, list[Shell]] = {}
        for shell_key in sorted(orbits_by_shell):
            asn, shell_id = shell_key
            shell = Shell._create(
                asn=asn,
                shell_id=shell_id,
                orbits=tuple(
                    sorted(
                        orbits_by_shell[shell_key],
                        key=lambda value: value.orbit_id,
                    )
                ),
            )
            shells_by_asn.setdefault(asn, []).append(shell)

        asns = sorted(set(shells_by_asn) | set(ground_station_inputs_by_asn))
        autonomous_systems = []
        for asn in asns:
            ground_stations = tuple(
                GroundStation(
                    node_id=simulation_input.node_id,
                    latitude_deg=simulation_input.latitude_deg,
                    longitude_deg=simulation_input.longitude_deg,
                    elevation_m=simulation_input.elevation_m,
                    station_type=simulation_input.station_type,
                    operator=simulation_input.operator,
                    max_simultaneous_satellite_accesses=(
                        simulation_input.max_simultaneous_satellite_accesses
                    ),
                )
                for simulation_input in sorted(
                    ground_station_inputs_by_asn.get(asn, ()),
                    key=lambda value: value.node_id.value,
                )
            )
            autonomous_systems.append(
                SkyseedAutonomousSystem._create(
                    asn=asn,
                    shells=tuple(
                        sorted(
                            shells_by_asn.get(asn, ()),
                            key=lambda value: value.shell_id,
                        )
                    ),
                    ground_stations=ground_stations,
                )
            )
        return cls._create(
            autonomous_systems,
            position_model=position_model,
            zone_layout=zone_layout,
            execution_observer=execution_observer,
        )

    @classmethod
    def _create(
        cls,
        autonomous_systems: Iterable[SkyseedAutonomousSystem],
        *,
        position_model: PositionModel,
        zone_layout: ZoneLayout | None = None,
        execution_observer: _ExecutionObserver | None = None,
    ) -> PhysicalWorld:
        if not isinstance(position_model, PositionModel):
            raise TypeError("position_model must implement PositionModel")
        if zone_layout is not None and not isinstance(zone_layout, ZoneLayout):
            raise TypeError("zone_layout must be a ZoneLayout or None")
        autonomous_system_values = tuple(autonomous_systems)
        by_asn: dict[int, SkyseedAutonomousSystem] = {}
        nodes: dict[NodeId, Satellite | GroundStation] = {}

        for autonomous_system in autonomous_system_values:
            if not isinstance(autonomous_system, SkyseedAutonomousSystem):
                raise TypeError(
                    "autonomous_systems must contain SkyseedAutonomousSystem values"
                )
            if autonomous_system.asn in by_asn:
                raise ValueError(f"duplicate ASN: {autonomous_system.asn}")
            by_asn[autonomous_system.asn] = autonomous_system

            for shell in autonomous_system.shells:
                for orbit in shell.orbits:
                    for satellite in orbit.satellites:
                        if satellite.node_id in nodes:
                            raise ValueError(
                                f"duplicate NodeId: {satellite.node_id.value}"
                            )
                        nodes[satellite.node_id] = satellite

            for ground_station in autonomous_system.ground_stations:
                if ground_station.node_id in nodes:
                    raise ValueError(
                        f"duplicate NodeId: {ground_station.node_id.value}"
                    )
                nodes[ground_station.node_id] = ground_station

        instance = object.__new__(cls)
        object.__setattr__(instance, "_autonomous_systems", autonomous_system_values)
        object.__setattr__(
            instance, "_autonomous_systems_by_asn", MappingProxyType(by_asn)
        )
        object.__setattr__(instance, "_nodes_by_id", MappingProxyType(nodes))
        object.__setattr__(instance, "_zone_layout", zone_layout)
        object.__setattr__(instance, "_position_model", position_model)
        object.__setattr__(instance, "_execution_observer", execution_observer)
        object.__setattr__(
            instance,
            "_semantic_fingerprint",
            _physical_world_semantic_fingerprint(
                autonomous_system_values,
                zone_layout,
                position_model,
            ),
        )
        _claim_position_model_owner(position_model, instance)
        return instance

    def _set_execution_observer(self, observer: _ExecutionObserver) -> None:
        if not isinstance(observer, _ExecutionObserver):
            raise TypeError("observer must be an _ExecutionObserver")
        if self._execution_observer is not None:
            raise RuntimeError("PhysicalWorld execution observation is already enabled")
        object.__setattr__(self, "_execution_observer", observer)

    def _get_execution_observer(self) -> _ExecutionObserver | None:
        """Return the private recorder shared by physical execution owners."""

        return self._execution_observer

    def frame_at(self, timestamp: datetime) -> PhysicalFrame:
        """Project one immutable physical frame without advancing time."""

        utc = normalize_utc_datetime(timestamp, "timestamp")
        observer = self._execution_observer
        scope = {"timestamp": utc.isoformat()} if observer is not None else {}
        total_started = perf_counter() if observer is not None else 0.0

        stage_started = perf_counter() if observer is not None else 0.0
        raw_positions = self._position_model.compute_positions(self, utc)
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        _record_physical_world_observation(
            observer,
            "position-compute",
            stage_elapsed,
            stage_started,
            scope,
            {
                "emitted_positions": (
                    len(raw_positions)
                    if isinstance(raw_positions, Mapping)
                    else 0
                )
            },
        )

        stage_started = perf_counter() if observer is not None else 0.0
        positions = _validated_positions(self, raw_positions)
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        _record_physical_world_observation(
            observer,
            "position-validation",
            stage_elapsed,
            stage_started,
            scope,
            {"validated_positions": len(positions)},
        )

        stage_started = perf_counter() if observer is not None else 0.0
        frame = PhysicalFrame._create(timestamp=utc, positions=positions)
        stage_elapsed = (
            perf_counter() - stage_started if observer is not None else 0.0
        )
        work = {"published_positions": len(frame.positions)}
        _record_physical_world_observation(
            observer,
            "physical-frame-publication",
            stage_elapsed,
            stage_started,
            scope,
            work,
        )
        if observer is not None:
            _record_physical_world_observation(
                observer,
                "physical-projection",
                perf_counter() - total_started,
                total_started,
                scope,
                work,
                duration_role="rollup",
            )
        return frame

    def get_position_model(self) -> PositionModel:
        return self._position_model

    def semantic_fingerprint(self) -> str:
        """Return the cached versioned fingerprint of all static semantics."""

        return self._semantic_fingerprint

    @property
    def autonomous_systems(self) -> tuple[SkyseedAutonomousSystem, ...]:
        return self._autonomous_systems

    def get_autonomous_system(self, asn: int) -> SkyseedAutonomousSystem:
        _validate_asn(asn)
        try:
            return self._autonomous_systems_by_asn[asn]
        except KeyError as error:
            raise KeyError(f"Autonomous system not found: {asn}") from error

    def get_node(self, node_id: NodeId) -> Satellite | GroundStation:
        _validate_query_node_id(node_id)
        try:
            return self._nodes_by_id[node_id]
        except KeyError as error:
            raise KeyError(f"Node not found: {node_id.value}") from error

    def get_satellite(self, node_id: NodeId) -> Satellite:
        _validate_query_node_id(node_id, _SATELLITE_KIND)
        try:
            node = self.get_node(node_id)
        except KeyError as error:
            raise KeyError(f"Satellite not found: {node_id.value}") from error
        if not isinstance(node, Satellite):  # pragma: no cover - identity invariant
            raise TypeError(f"{node_id.value} does not identify a Satellite")
        return node

    def get_ground_station(self, node_id: NodeId) -> GroundStation:
        _validate_query_node_id(node_id, _GROUND_STATION_KIND)
        try:
            node = self.get_node(node_id)
        except KeyError as error:
            raise KeyError(f"GroundStation not found: {node_id.value}") from error
        if not isinstance(node, GroundStation):  # pragma: no cover - identity invariant
            raise TypeError(f"{node_id.value} does not identify a GroundStation")
        return node

    def get_shell(self, asn: int, shell_id: str) -> Shell:
        autonomous_system = self.get_autonomous_system(asn)
        return autonomous_system.get_shell(shell_id)

    def get_orbit(self, asn: int, shell_id: str, orbit_id: str) -> Orbit:
        shell = self.get_shell(asn, shell_id)
        return shell.get_orbit(orbit_id)

    def get_zone_layout(self) -> ZoneLayout:
        if self._zone_layout is None:
            raise RuntimeError("PhysicalWorld has no ZoneLayout")
        return self._zone_layout

    def get_zone(self, zone_id: str) -> Zone:
        return self.get_zone_layout().get_zone(zone_id)

    def find_zones(
        self, latitude_deg: float, longitude_deg: float
    ) -> tuple[Zone, ...]:
        return self.get_zone_layout().find_zones(
            latitude_deg, longitude_deg
        )

    def find_intersecting_zones(
        self, spherical_cap: SphericalCap
    ) -> tuple[Zone, ...]:
        return self.get_zone_layout().find_intersecting_zones(spherical_cap)

    def resolve_zone(self, latitude_deg: float, longitude_deg: float) -> Zone:
        return self.get_zone_layout().resolve_zone(
            latitude_deg, longitude_deg
        )


def _validate_satellite_input(value: SatelliteSimulationInput) -> None:
    _validate_node_identity(value.node_id, _SATELLITE_KIND, value.satellite_id)
    validate_identifier(value.shell_id, "shell_id")
    validate_identifier(value.orbit_id, "orbit_id")
    if (
        isinstance(value.orbit_index, bool)
        or not isinstance(value.orbit_index, int)
        or value.orbit_index < 0
    ):
        raise ValueError("orbit_index must be a non-negative integer")
    _validate_angle(value.mean_anomaly_deg, "mean_anomaly_deg")


def _isl_capability(
    maximum_simultaneous_links: int | None,
) -> InterSatelliteLinkCapability | None:
    if maximum_simultaneous_links is None:
        return None
    return InterSatelliteLinkCapability(
        maximum_simultaneous_links=maximum_simultaneous_links
    )


def _validated_orbit_parameters(
    value: SatelliteSimulationInput,
) -> tuple[float, float, float, datetime]:
    altitude = _finite_float(value.altitude_km, "altitude_km")
    inclination = _finite_float(value.inclination_deg, "inclination_deg")
    right_ascension = _validate_angle(
        value.right_ascension_deg, "right_ascension_deg"
    )
    if altitude <= 0.0:
        raise ValueError("altitude_km must be positive")
    if not 0.0 <= inclination <= 180.0:
        raise ValueError("inclination_deg must be between 0 and 180")
    epoch = normalize_utc_datetime(value.epoch_utc, "epoch_utc")
    return altitude, inclination, right_ascension, epoch


def _validate_ground_station_input(
    value: GroundStationSimulationInput,
) -> None:
    _validate_node_identity(
        value.node_id, _GROUND_STATION_KIND, value.ground_station_id
    )
    GroundStation(
        node_id=value.node_id,
        latitude_deg=value.latitude_deg,
        longitude_deg=value.longitude_deg,
        elevation_m=value.elevation_m,
        station_type=value.station_type,
        operator=value.operator,
        max_simultaneous_satellite_accesses=(
            value.max_simultaneous_satellite_accesses
        ),
    )


def _validated_positions(
    physical_world: PhysicalWorld,
    positions: Mapping[NodeId, EarthFixedPosition],
) -> Mapping[NodeId, EarthFixedPosition]:
    if not isinstance(positions, Mapping):
        raise TypeError("PositionModel must return a Mapping")
    if set(positions) != set(physical_world._nodes_by_id):
        raise ValueError(
            "PositionModel must return exactly every PhysicalWorld physical node"
        )
    copied: dict[NodeId, EarthFixedPosition] = {}
    for node_id, position in positions.items():
        if not isinstance(node_id, NodeId):
            raise TypeError("PositionModel keys must be NodeId values")
        if not isinstance(position, EarthFixedPosition):
            raise TypeError(
                "PositionModel values must be EarthFixedPosition values"
            )
        copied[node_id] = position
    return MappingProxyType(copied)


def _claim_position_model_owner(
    position_model: PositionModel,
    physical_world: PhysicalWorld,
) -> None:
    with _POSITION_MODEL_OWNER_LOCK:
        if id(position_model) in _POSITION_MODEL_OWNERS:
            raise ValueError(
                "PositionModel instance already belongs to another PhysicalWorld"
            )
        _POSITION_MODEL_OWNERS[id(position_model)] = physical_world


def _record_physical_world_observation(
    observer: _ExecutionObserver | None,
    stage: str,
    duration_seconds: float,
    started_monotonic: float,
    scope: Mapping[str, object],
    work: Mapping[str, int | float],
    *,
    duration_role: str = "stage",
) -> None:
    if observer is None:
        return
    observer.record(
        component="physical-world",
        stage=stage,
        phase="one-shot",
        duration_seconds=duration_seconds,
        duration_role=duration_role,
        scope=scope,
        work=work,
        started_monotonic=started_monotonic,
    )


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


def _physical_world_semantic_fingerprint(
    autonomous_systems: tuple[SkyseedAutonomousSystem, ...],
    zone_layout: ZoneLayout | None,
    position_model: PositionModel,
) -> str:
    """Hash the sole canonical material for immutable PhysicalWorld identity."""

    material = {
        "schema": "skyseed-v2-physical-world-v1",
        "autonomous_systems": [
            {
                "asn": autonomous_system.asn,
                "shells": [
                    {
                        "shell_id": shell.shell_id,
                        "orbits": [
                            {
                                "orbit_id": orbit.orbit_id,
                                "altitude_km": _float_material(
                                    orbit.altitude_km
                                ),
                                "inclination_deg": _float_material(
                                    orbit.inclination_deg
                                ),
                                "right_ascension_deg": _float_material(
                                    orbit.right_ascension_deg
                                ),
                                "epoch_utc": orbit.epoch_utc.isoformat(),
                                "satellites": [
                                    _satellite_fingerprint_material(satellite)
                                    for satellite in sorted(
                                        orbit.satellites,
                                        key=lambda value: _node_sort_key(
                                            value.node_id
                                        ),
                                    )
                                ],
                            }
                            for orbit in sorted(
                                shell.orbits,
                                key=lambda value: value.orbit_id,
                            )
                        ],
                    }
                    for shell in sorted(
                        autonomous_system.shells,
                        key=lambda value: value.shell_id,
                    )
                ],
                "ground_stations": [
                    _ground_station_fingerprint_material(value)
                    for value in sorted(
                        autonomous_system.ground_stations,
                        key=lambda value: _node_sort_key(value.node_id),
                    )
                ],
            }
            for autonomous_system in sorted(
                autonomous_systems, key=lambda value: value.asn
            )
        ],
        "zone_layout": _zone_layout_fingerprint_material(zone_layout),
        "position_model": _model_fingerprint_material(position_model),
    }
    payload = json.dumps(
        material,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _satellite_fingerprint_material(value: Satellite) -> dict[str, object]:
    capability = value.inter_satellite_link_capability
    return {
        "node_id": _node_fingerprint_material(value.node_id),
        "orbit_index": value.orbit_index,
        "mean_anomaly_deg": _float_material(value.mean_anomaly_deg),
        "max_simultaneous_ground_station_accesses": (
            value.max_simultaneous_ground_station_accesses
        ),
        "maximum_simultaneous_inter_satellite_links": (
            None
            if capability is None
            else capability.maximum_simultaneous_links
        ),
    }


def _ground_station_fingerprint_material(
    value: GroundStation,
) -> dict[str, object]:
    return {
        "node_id": _node_fingerprint_material(value.node_id),
        "latitude_deg": _float_material(value.latitude_deg),
        "longitude_deg": _float_material(value.longitude_deg),
        "elevation_m": _float_material(value.elevation_m),
        "station_type": value.station_type,
        "operator": value.operator,
        "max_simultaneous_satellite_accesses": (
            value.max_simultaneous_satellite_accesses
        ),
    }


def _zone_layout_fingerprint_material(
    value: ZoneLayout | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "allow_overlap": value.allow_overlap,
        "zones": [
            {
                "zone_id": zone.zone_id,
                "priority": zone.priority,
                "geometry": {
                    "south_latitude_deg": _float_material(
                        zone.geometry.south_latitude_deg
                    ),
                    "north_latitude_deg": _float_material(
                        zone.geometry.north_latitude_deg
                    ),
                    "west_longitude_deg": _float_material(
                        zone.geometry.west_longitude_deg
                    ),
                    "east_longitude_deg": _float_material(
                        zone.geometry.east_longitude_deg
                    ),
                },
            }
            for zone in sorted(value.zones, key=lambda item: item.zone_id)
        ],
        "membership_model": _model_fingerprint_material(
            value.get_membership_model()
        ),
    }


def _model_fingerprint_material(value: object) -> dict[str, object]:
    active: set[int] = set()
    identity = id(value)
    active.add(identity)
    try:
        return _active_model_fingerprint_material(value, active)
    finally:
        active.remove(identity)


def _active_model_fingerprint_material(
    value: object, active: set[int]
) -> dict[str, object]:
    model_type = type(value)
    identity = f"{model_type.__module__}.{model_type.__qualname__}"
    explicit = getattr(value, "semantic_fingerprint_material", None)
    if explicit is not None:
        if not callable(explicit):
            raise TypeError(
                "semantic_fingerprint_material must be callable when present"
            )
        raw_state = explicit()
        return {
            "type": identity,
            "state": _semantic_model_value(raw_state, active),
        }
    state: dict[str, object] = {}
    dictionary = getattr(value, "__dict__", None)
    if dictionary is not None:
        if not isinstance(dictionary, dict):
            raise TypeError("Model __dict__ must be a dictionary")
        state.update(dictionary)
    for cls in reversed(model_type.__mro__):
        slots = cls.__dict__.get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in {"__dict__", "__weakref__"} or name in state:
                continue
            if hasattr(value, name):
                state[name] = getattr(value, name)
    return {
        "type": identity,
        "state": _semantic_model_value(state, active),
    }


def _semantic_model_value(value: object, active: set[int]) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Model fingerprint values must be finite")
        return _float_material(value)
    if isinstance(value, datetime):
        return {"datetime": normalize_utc_datetime(value, "value").isoformat()}
    if isinstance(value, timedelta):
        return {
            "timedelta": {
                "days": value.days,
                "seconds": value.seconds,
                "microseconds": value.microseconds,
            }
        }
    if isinstance(value, NodeId):
        return {"node_id": _node_fingerprint_material(value)}
    if isinstance(value, Enum):
        enum_type = type(value)
        return {
            "enum": f"{enum_type.__module__}.{enum_type.__qualname__}",
            "value": _semantic_model_value(value.value, active),
        }
    identity = id(value)
    if identity in active:
        raise ValueError("Model fingerprint configuration must be acyclic")
    active.add(identity)
    try:
        if isinstance(value, MappingABC):
            pairs = [
                (
                    _semantic_model_value(key, active),
                    _semantic_model_value(item, active),
                )
                for key, item in value.items()
            ]
            pairs.sort(key=lambda pair: _canonical_json(pair[0]))
            return {
                "mapping": [
                    {"key": key, "value": item} for key, item in pairs
                ]
            }
        if isinstance(value, (tuple, list)):
            return {
                "sequence": [
                    _semantic_model_value(item, active) for item in value
                ]
            }
        if isinstance(value, (set, frozenset)):
            items = [
                _semantic_model_value(item, active) for item in value
            ]
            items.sort(key=_canonical_json)
            return {"set": items}
        if is_dataclass(value) and not isinstance(value, type):
            value_type = type(value)
            return {
                "dataclass": (
                    f"{value_type.__module__}.{value_type.__qualname__}"
                ),
                "fields": {
                    item.name: _semantic_model_value(
                        getattr(value, item.name), active
                    )
                    for item in fields(value)
                },
            }
        if callable(value):
            raise TypeError(
                "Model fingerprint configuration cannot contain callables"
            )
        return _active_model_fingerprint_material(value, active)
    finally:
        active.remove(identity)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _float_material(value: float) -> str:
    converted = float(value)
    if not isfinite(converted):
        raise ValueError("PhysicalWorld fingerprint values must be finite")
    return converted.hex()


def _node_fingerprint_material(value: NodeId) -> dict[str, object]:
    return {
        "asn": value.asn,
        "entity_kind": value.entity_kind,
        "entity_id": value.entity_id,
    }


def _node_sort_key(value: NodeId) -> tuple[int, str, str]:
    return value.asn, value.entity_kind, value.entity_id


__all__ = [
    "GroundStation",
    "Orbit",
    "Satellite",
    "Shell",
    "SkyseedAutonomousSystem",
    "PhysicalWorld",
]
