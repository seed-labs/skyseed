"""Immutable aggregate of static facts projected from the Emulator."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from skyseed_v2.node_id import NodeId

from .ground_station import GroundStationSimulationInput
from .satellite import SatelliteSimulationInput


_SATELLITE_ENTITY_KIND = "satellite"
_GROUND_STATION_ENTITY_KIND = "ground-station"


SimulationEntityInput = (
    SatelliteSimulationInput | GroundStationSimulationInput
)


@dataclass(frozen=True, slots=True, init=False)
class SimulatorStaticInput:
    """Hold one deterministic static Emulator-to-Simulator snapshot.

    The snapshot contains only immutable input values and a logical-ID index.
    It does not retain Emulator entities, SEED registries, containers, Compose
    settings, persistence objects or Simulator runtime state.

    Layer: immutable Emulator-to-Simulator boundary aggregate. The external
    input-generation adapter or direct data callers create it; Simulator
    construction reads it. It is not a lookup service, builder, Registry view
    or long-lived Simulator model. It is public from `simulator.static_input`
    because it is the complete static boundary contract projected from the
    Emulator.
    """

    _satellites: tuple[SatelliteSimulationInput, ...]
    _ground_stations: tuple[GroundStationSimulationInput, ...]
    _nodes: tuple[SimulationEntityInput, ...]
    _by_node_id: Mapping[NodeId, SimulationEntityInput] = field(
        repr=False,
        compare=False,
        hash=False,
    )

    def __init__(
        self,
        satellites: Iterable[SatelliteSimulationInput] = (),
        ground_stations: Iterable[GroundStationSimulationInput] = (),
    ) -> None:
        satellite_values = tuple(satellites)
        ground_station_values = tuple(ground_stations)
        self._validate_input_types(satellite_values, ground_station_values)

        ordered_nodes = tuple(
            sorted(
                satellite_values + ground_station_values,
                key=lambda simulation_input: simulation_input.node_id.value,
            )
        )
        by_node_id: dict[NodeId, SimulationEntityInput] = {}
        for simulation_input in ordered_nodes:
            if simulation_input.node_id in by_node_id:
                raise ValueError(
                    "duplicate NodeId: "
                    f"{simulation_input.node_id.value}"
                )
            by_node_id[simulation_input.node_id] = simulation_input

        object.__setattr__(
            self,
            "_satellites",
            tuple(
                simulation_input
                for simulation_input in ordered_nodes
                if isinstance(simulation_input, SatelliteSimulationInput)
            ),
        )
        object.__setattr__(
            self,
            "_ground_stations",
            tuple(
                simulation_input
                for simulation_input in ordered_nodes
                if isinstance(simulation_input, GroundStationSimulationInput)
            ),
        )
        object.__setattr__(self, "_nodes", ordered_nodes)
        object.__setattr__(
            self,
            "_by_node_id",
            MappingProxyType(by_node_id),
        )

    @staticmethod
    def _validate_input_types(
        satellites: tuple[SatelliteSimulationInput, ...],
        ground_stations: tuple[GroundStationSimulationInput, ...],
    ) -> None:
        if any(
            not isinstance(simulation_input, SatelliteSimulationInput)
            for simulation_input in satellites
        ):
            raise TypeError(
                "satellites must contain SatelliteSimulationInput values"
            )
        if any(
            not isinstance(simulation_input, GroundStationSimulationInput)
            for simulation_input in ground_stations
        ):
            raise TypeError(
                "ground_stations must contain "
                "GroundStationSimulationInput values"
            )

    def get(self, node_id: NodeId) -> SimulationEntityInput:
        """Return one static entity input by its complete logical identity."""

        if not isinstance(node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        try:
            return self._by_node_id[node_id]
        except KeyError as error:
            raise KeyError(f"NodeId not found: {node_id.value}") from error

    def get_satellite(
        self,
        node_id: NodeId,
    ) -> SatelliteSimulationInput:
        """Return one Satellite input by its complete logical identity."""

        return self._get_typed(
            node_id,
            entity_kind=_SATELLITE_ENTITY_KIND,
            input_type=SatelliteSimulationInput,
        )

    def get_ground_station(
        self,
        node_id: NodeId,
    ) -> GroundStationSimulationInput:
        """Return one GroundStation input by its complete logical identity."""

        return self._get_typed(
            node_id,
            entity_kind=_GROUND_STATION_ENTITY_KIND,
            input_type=GroundStationSimulationInput,
        )

    def _get_typed(
        self,
        node_id: NodeId,
        *,
        entity_kind: str,
        input_type: type[SatelliteSimulationInput]
        | type[GroundStationSimulationInput],
    ) -> SimulationEntityInput:
        if not isinstance(node_id, NodeId):
            raise TypeError("node_id must be a NodeId")
        if node_id.entity_kind != entity_kind:
            raise TypeError(f"{node_id.value} is not a {entity_kind} identity")
        simulation_input = self.get(node_id)
        if not isinstance(simulation_input, input_type):
            raise TypeError(f"{node_id.value} does not identify a {entity_kind}")
        return simulation_input

    def list_satellites(self) -> tuple[SatelliteSimulationInput, ...]:
        return self._satellites

    def list_ground_stations(
        self,
    ) -> tuple[GroundStationSimulationInput, ...]:
        return self._ground_stations

    def list_nodes(self) -> tuple[SimulationEntityInput, ...]:
        return self._nodes
