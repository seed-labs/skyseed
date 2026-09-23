"""Define the Simulator-owned immutable PhysicalWorld aggregate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from time import perf_counter
from types import MappingProxyType
from typing import Mapping
from weakref import WeakValueDictionary

from skyseed.configuration.simulator import ZoneLayoutDefinition
from skyseed.shared.recording import PerformanceRecorder, record_performance
from skyseed.shared.time import normalize_utc_datetime
from skyseed.shared.identifier import validate_identifier
from skyseed.shared import NodeId
from skyseed.shared._shutdown import _shutdown_owned

from skyseed.simulator.input import (
    SimulatorStaticInput,
    GroundStationSimulationInput,
    SatelliteSimulationInput,
)
from skyseed.simulator.physical.world.capability import InterSatelliteLinkCapability
from skyseed.simulator.physical.queries.position import PhysicalPositionSnapshot
from skyseed.simulator.physical.models.position import PositionModel
from .zone import SphericalCap, Zone, ZoneLayout
from ..models.zone_membership import ZoneMembershipModel


from .entities import (PhysicalSatellite, PhysicalGroundStation, PhysicalOrbit,
    PhysicalShell, PhysicalAutonomousSystem, _validate_node_identity, _validate_asn,
    _validate_query_node_id, _validate_angle, _finite_float)
from ._identity import _physical_world_semantic_fingerprint


_SATELLITE_KIND = "satellite"
_GROUND_STATION_KIND = "ground-station"
_OrbitKey = tuple[int, str, str]
_ShellKey = tuple[int, str]
_POSITION_MODEL_OWNERS: WeakValueDictionary[int, PhysicalWorld] = (
    WeakValueDictionary()
)
_POSITION_MODEL_OWNER_LOCK = RLock()


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class PhysicalWorld:
    """Own static physical facts and project timestamped physical frames.

    Layer: Simulator physical aggregate root and query/projection facade.
    ``Simulator`` creates it explicitly from ``SimulatorStaticInput`` plus one
    ``PositionModel`` and then owns it. It is not boundary input, a time cursor,
    global resource registry, operational state, or planning service. It is
    public from ``skyseed.simulator`` because callers consume its stable
    read-only facts and ``PhysicalPositionSnapshot`` results.
    """

    _autonomous_systems: tuple[PhysicalAutonomousSystem, ...]
    _autonomous_systems_by_asn: Mapping[int, PhysicalAutonomousSystem] = field(
        repr=False, compare=False, hash=False
    )
    _nodes_by_id: Mapping[NodeId, PhysicalSatellite | PhysicalGroundStation] = field(
        repr=False, compare=False, hash=False
    )
    _zone_layout: ZoneLayout | None = field(
        repr=False, compare=False, hash=False
    )
    _position_model: PositionModel = field(
        repr=False, compare=False, hash=False
    )
    _execution_observer: PerformanceRecorder | None = field(
        repr=False, compare=False, hash=False
    )
    _semantic_fingerprint: str = field(
        repr=False, compare=False, hash=False
    )

    _shutdown: bool = field(repr=False, compare=False, hash=False)

    def __init__(self) -> None:
        raise TypeError("PhysicalWorld is created explicitly by Simulator")

    @classmethod
    def _materialize(
        cls,
        static_input: SimulatorStaticInput,
        position_model: PositionModel,
        zone_layout_definition: ZoneLayoutDefinition | None = None,
        zone_membership_model: ZoneMembershipModel | None = None,
        execution_observer: PerformanceRecorder | None = None,
    ) -> PhysicalWorld:
        if not isinstance(static_input, SimulatorStaticInput):
            raise TypeError("static_input must be a SimulatorStaticInput")
        if not isinstance(position_model, PositionModel):
            raise TypeError("position_model must implement PositionModel")
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
        ground_stations_by_asn: dict[
            int, list[PhysicalGroundStation]
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
                        f"PhysicalSatellite appears in multiple Orbits: "
                        f"{simulation_input.node_id.value}"
                    )
                satellite_parents[simulation_input.node_id] = parent

                parameters = _validated_orbit_parameters(simulation_input)
                previous_parameters = orbit_parameters.setdefault(
                    parent, parameters
                )
                if previous_parameters != parameters:
                    raise ValueError(
                        "conflicting PhysicalOrbit parameters for "
                        f"{parent[0]}/{parent[1]}/{parent[2]}"
                    )
                indices = orbit_indices.setdefault(parent, set())
                if simulation_input.orbit_index in indices:
                    raise ValueError(
                        f"duplicate orbit_index {simulation_input.orbit_index} "
                        f"in PhysicalOrbit {parent[0]}/{parent[1]}/{parent[2]}"
                    )
                indices.add(simulation_input.orbit_index)
                satellite_inputs_by_orbit.setdefault(parent, []).append(
                    simulation_input
                )
            elif isinstance(simulation_input, GroundStationSimulationInput):
                station = _create_ground_station(simulation_input)
                ground_stations_by_asn.setdefault(
                    station.node_id.asn, []
                ).append(station)
            else:  # pragma: no cover - SimulatorStaticInput already fails closed
                raise TypeError("unsupported Simulator static input value")

        orbits_by_shell: dict[_ShellKey, list[PhysicalOrbit]] = {}
        for orbit_key in sorted(satellite_inputs_by_orbit):
            asn, shell_id, orbit_id = orbit_key
            parameters = orbit_parameters[orbit_key]
            satellites = tuple(
                PhysicalSatellite(
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
            orbit = PhysicalOrbit._create(
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

        shells_by_asn: dict[int, list[PhysicalShell]] = {}
        for shell_key in sorted(orbits_by_shell):
            asn, shell_id = shell_key
            shell = PhysicalShell._create(
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

        asns = sorted(set(shells_by_asn) | set(ground_stations_by_asn))
        autonomous_systems = []
        for asn in asns:
            ground_stations = tuple(sorted(
                ground_stations_by_asn.get(asn, ()),
                key=lambda value: value.node_id.value,
            ))
            autonomous_systems.append(
                PhysicalAutonomousSystem._create(
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
        autonomous_systems: Iterable[PhysicalAutonomousSystem],
        *,
        position_model: PositionModel,
        zone_layout: ZoneLayout | None = None,
        execution_observer: PerformanceRecorder | None = None,
    ) -> PhysicalWorld:
        if not isinstance(position_model, PositionModel):
            raise TypeError("position_model must implement PositionModel")
        if zone_layout is not None and not isinstance(zone_layout, ZoneLayout):
            raise TypeError("zone_layout must be a ZoneLayout or None")
        autonomous_system_values = tuple(autonomous_systems)
        by_asn: dict[int, PhysicalAutonomousSystem] = {}
        nodes: dict[NodeId, PhysicalSatellite | PhysicalGroundStation] = {}

        for autonomous_system in autonomous_system_values:
            if not isinstance(autonomous_system, PhysicalAutonomousSystem):
                raise TypeError(
                    "autonomous_systems must contain PhysicalAutonomousSystem values"
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
        object.__setattr__(instance, "_shutdown", False)
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

    def _set_execution_observer(self, observer: PerformanceRecorder) -> None:
        if self._execution_observer is not None:
            raise RuntimeError("PhysicalWorld execution observation is already enabled")
        object.__setattr__(self, "_execution_observer", observer)

    def _get_execution_observer(self) -> PerformanceRecorder | None:
        """Return the private recorder shared by physical execution owners."""

        return self._execution_observer

    def frame_at(self, timestamp: datetime) -> PhysicalPositionSnapshot:
        """Project one immutable physical frame without advancing time."""

        if self._shutdown:
            raise RuntimeError("PhysicalWorld is shut down")
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
        frame = PhysicalPositionSnapshot._create(
            timestamp=utc, positions=raw_positions, expected_node_ids=self._nodes_by_id,
        )
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

    def shutdown(self, *, wait: bool = True) -> None:
        """Close optional Position and membership resources; retain immutable facts."""
        if not isinstance(wait, bool):
            raise TypeError("wait must be a bool")
        if self._shutdown:
            return
        object.__setattr__(self, "_shutdown", True)
        membership = None
        if self._zone_layout is not None:
            object.__setattr__(self._zone_layout, "_closed", True)
            membership = self._zone_layout.get_membership_model()
        _shutdown_owned((self._position_model, membership), wait=wait)

    def get_position_model(self) -> PositionModel:
        return self._position_model

    def semantic_fingerprint(self) -> str:
        """Return the cached fingerprint of static facts and declared Model identity."""

        return self._semantic_fingerprint

    @property
    def autonomous_systems(self) -> tuple[PhysicalAutonomousSystem, ...]:
        return self._autonomous_systems

    def get_autonomous_system(self, asn: int) -> PhysicalAutonomousSystem:
        _validate_asn(asn)
        try:
            return self._autonomous_systems_by_asn[asn]
        except KeyError as error:
            raise KeyError(f"Autonomous system not found: {asn}") from error

    def get_node(self, node_id: NodeId) -> PhysicalSatellite | PhysicalGroundStation:
        _validate_query_node_id(node_id)
        try:
            return self._nodes_by_id[node_id]
        except KeyError as error:
            raise KeyError(f"Node not found: {node_id.value}") from error

    def get_satellite(self, node_id: NodeId) -> PhysicalSatellite:
        _validate_query_node_id(node_id, _SATELLITE_KIND)
        try:
            node = self.get_node(node_id)
        except KeyError as error:
            raise KeyError(f"PhysicalSatellite not found: {node_id.value}") from error
        if not isinstance(node, PhysicalSatellite):  # pragma: no cover - identity invariant
            raise TypeError(f"{node_id.value} does not identify a PhysicalSatellite")
        return node

    def get_ground_station(self, node_id: NodeId) -> PhysicalGroundStation:
        _validate_query_node_id(node_id, _GROUND_STATION_KIND)
        try:
            node = self.get_node(node_id)
        except KeyError as error:
            raise KeyError(f"PhysicalGroundStation not found: {node_id.value}") from error
        if not isinstance(node, PhysicalGroundStation):  # pragma: no cover - identity invariant
            raise TypeError(f"{node_id.value} does not identify a PhysicalGroundStation")
        return node

    def get_shell(self, asn: int, shell_id: str) -> PhysicalShell:
        autonomous_system = self.get_autonomous_system(asn)
        return autonomous_system.get_shell(shell_id)

    def get_orbit(self, asn: int, shell_id: str, orbit_id: str) -> PhysicalOrbit:
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


def _create_ground_station(
    value: GroundStationSimulationInput,
) -> PhysicalGroundStation:
    _validate_node_identity(
        value.node_id, _GROUND_STATION_KIND, value.ground_station_id
    )
    return PhysicalGroundStation(
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
    observer: PerformanceRecorder | None,
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
    record_performance(observer,
        component="physical-world",
        stage=stage,
        phase="one-shot",
        duration_seconds=duration_seconds,
        duration_role=duration_role,
        scope=scope,
        work=work,
        started_monotonic=started_monotonic,
    )


__all__ = ["PhysicalWorld"]
