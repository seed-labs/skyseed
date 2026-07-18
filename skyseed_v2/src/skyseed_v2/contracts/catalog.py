"""Immutable aggregate of static descriptors exported by the Emulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from .ground_station import GroundStationDescriptor
from .identity import (
    GROUND_STATION_ENTITY_KIND,
    SATELLITE_ENTITY_KIND,
    SimulationNodeId,
)
from .satellite import SatelliteDescriptor


SimulationDescriptor = SatelliteDescriptor | GroundStationDescriptor


@dataclass(frozen=True, slots=True, init=False)
class SimulationEntityCatalog:
    """Hold the deterministic read-only Emulator-to-Simulator entity snapshot.

    Layer: approved cross-layer contract. CatalogBuilder or explicit contract
    callers create it; future Simulator code and current readers consume it.
    It owns only immutable Descriptors and a logical-ID index, not entities,
    Registry objects, containers, persistence or runtime state. It is a stable
    root public type because it is the approved Emulator output boundary.
    """

    _satellites: tuple[SatelliteDescriptor, ...]
    _ground_stations: tuple[GroundStationDescriptor, ...]
    _nodes: tuple[SimulationDescriptor, ...]
    _by_node_id: Mapping[SimulationNodeId, SimulationDescriptor] = field(
        repr=False,
        compare=False,
        hash=False,
    )

    def __init__(
        self,
        satellites: Iterable[SatelliteDescriptor] = (),
        ground_stations: Iterable[GroundStationDescriptor] = (),
    ) -> None:
        satellite_values = tuple(satellites)
        ground_station_values = tuple(ground_stations)
        self._validate_descriptor_types(
            satellite_values,
            ground_station_values,
        )

        ordered_nodes = tuple(
            sorted(
                satellite_values + ground_station_values,
                key=lambda descriptor: descriptor.node_id.value,
            )
        )
        by_node_id: dict[SimulationNodeId, SimulationDescriptor] = {}
        for descriptor in ordered_nodes:
            if descriptor.node_id in by_node_id:
                raise ValueError(
                    "duplicate SimulationNodeId: "
                    f"{descriptor.node_id.value}"
                )
            by_node_id[descriptor.node_id] = descriptor

        object.__setattr__(
            self,
            "_satellites",
            tuple(
                descriptor
                for descriptor in ordered_nodes
                if isinstance(descriptor, SatelliteDescriptor)
            ),
        )
        object.__setattr__(
            self,
            "_ground_stations",
            tuple(
                descriptor
                for descriptor in ordered_nodes
                if isinstance(descriptor, GroundStationDescriptor)
            ),
        )
        object.__setattr__(self, "_nodes", ordered_nodes)
        object.__setattr__(
            self,
            "_by_node_id",
            MappingProxyType(by_node_id),
        )

    @staticmethod
    def _validate_descriptor_types(
        satellites: tuple[SatelliteDescriptor, ...],
        ground_stations: tuple[GroundStationDescriptor, ...],
    ) -> None:
        if any(
            not isinstance(descriptor, SatelliteDescriptor)
            for descriptor in satellites
        ):
            raise TypeError("satellites must contain SatelliteDescriptor values")
        if any(
            not isinstance(descriptor, GroundStationDescriptor)
            for descriptor in ground_stations
        ):
            raise TypeError(
                "ground_stations must contain GroundStationDescriptor values"
            )

    def get(self, node_id: SimulationNodeId) -> SimulationDescriptor:
        """Return the Descriptor with one complete logical node identity."""

        if not isinstance(node_id, SimulationNodeId):
            raise TypeError("node_id must be a SimulationNodeId")
        try:
            return self._by_node_id[node_id]
        except KeyError as error:
            raise KeyError(f"SimulationNodeId not found: {node_id.value}") from error

    def get_satellite(
        self,
        node_id_or_id: SimulationNodeId | str,
    ) -> SatelliteDescriptor:
        """Return one Satellite Descriptor by logical ID or unambiguous entity ID."""

        return self._get_typed(
            node_id_or_id,
            entity_kind=SATELLITE_ENTITY_KIND,
            descriptors=self._satellites,
            descriptor_type=SatelliteDescriptor,
        )

    def get_ground_station(
        self,
        node_id_or_id: SimulationNodeId | str,
    ) -> GroundStationDescriptor:
        """Return one GS Descriptor by logical ID or unambiguous entity ID."""

        return self._get_typed(
            node_id_or_id,
            entity_kind=GROUND_STATION_ENTITY_KIND,
            descriptors=self._ground_stations,
            descriptor_type=GroundStationDescriptor,
        )

    def _get_typed(
        self,
        node_id_or_id: SimulationNodeId | str,
        *,
        entity_kind: str,
        descriptors: tuple[SimulationDescriptor, ...],
        descriptor_type: type[SatelliteDescriptor] | type[GroundStationDescriptor],
    ) -> SimulationDescriptor:
        if isinstance(node_id_or_id, SimulationNodeId):
            if node_id_or_id.entity_kind != entity_kind:
                raise TypeError(
                    f"{node_id_or_id.value} is not a {entity_kind} identity"
                )
            descriptor = self.get(node_id_or_id)
            if not isinstance(descriptor, descriptor_type):
                raise TypeError(
                    f"{node_id_or_id.value} does not identify a {entity_kind}"
                )
            return descriptor

        if not isinstance(node_id_or_id, str):
            raise TypeError("node_id_or_id must be a SimulationNodeId or string")
        matches = tuple(
            descriptor
            for descriptor in descriptors
            if descriptor.node_id.entity_id == node_id_or_id
        )
        if not matches:
            raise KeyError(f"{entity_kind} not found: {node_id_or_id}")
        if len(matches) > 1:
            raise ValueError(
                f"ambiguous {entity_kind} entity ID {node_id_or_id!r}; "
                "use SimulationNodeId"
            )
        return matches[0]

    def list_satellites(self) -> tuple[SatelliteDescriptor, ...]:
        return self._satellites

    def list_ground_stations(self) -> tuple[GroundStationDescriptor, ...]:
        return self._ground_stations

    def list_nodes(self) -> tuple[SimulationDescriptor, ...]:
        return self._nodes
