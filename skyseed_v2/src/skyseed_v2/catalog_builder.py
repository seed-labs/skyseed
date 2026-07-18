"""Application service for collecting static Simulator-facing contracts."""

from __future__ import annotations

from collections.abc import Iterable

from seedemu.core import AutonomousSystem

from skyseed_v2.contracts import SimulationEntityCatalog
from skyseed_v2.projection import (
    GroundStationDescriptorProjector,
    SatelliteDescriptorProjector,
)
from skyseed_v2.seed.autonomous_system import SkyseedAutonomousSystem


__all__ = ["SimulationEntityCatalogBuilder"]


class SimulationEntityCatalogBuilder:
    """Project Emulator-owned entities into one immutable Catalog.

    Layer: application projection service. Handwritten callers or the
    specification Applier create it; Catalog consumers use its output. It
    reads existing Skyseed AS entities, delegates to Descriptor Projectors and
    lets `SimulationEntityCatalog` enforce logical-ID uniqueness. It is not an
    entity factory, Registry reader, Compiler or serializer. It remains public
    from this submodule because no entity or Projector owns multi-AS assembly.
    """

    def __init__(
        self,
        *,
        satellite_projector: SatelliteDescriptorProjector | None = None,
        ground_station_projector: GroundStationDescriptorProjector | None = None,
    ) -> None:
        self._satellite_projector = (
            satellite_projector or SatelliteDescriptorProjector()
        )
        self._ground_station_projector = (
            ground_station_projector or GroundStationDescriptorProjector()
        )

    def build(
        self,
        autonomous_systems: AutonomousSystem | Iterable[AutonomousSystem],
    ) -> SimulationEntityCatalog:
        """Project Skyseed entities without consulting Registry or Compiler output."""

        candidates = self._snapshot_inputs(autonomous_systems)
        satellites = []
        ground_stations = []
        for autonomous_system in candidates:
            if not isinstance(autonomous_system, AutonomousSystem):
                raise TypeError(
                    "autonomous_systems must contain AutonomousSystem values"
                )
            if not isinstance(autonomous_system, SkyseedAutonomousSystem):
                continue
            satellites.extend(
                self._satellite_projector.project(satellite)
                for satellite in autonomous_system.satellites()
            )
            ground_stations.extend(
                self._ground_station_projector.project(ground_station)
                for ground_station in autonomous_system.ground_stations()
            )
        return SimulationEntityCatalog(satellites, ground_stations)

    @staticmethod
    def _snapshot_inputs(
        autonomous_systems: AutonomousSystem | Iterable[AutonomousSystem],
    ) -> tuple[AutonomousSystem, ...]:
        if isinstance(autonomous_systems, AutonomousSystem):
            return (autonomous_systems,)
        try:
            return tuple(autonomous_systems)
        except TypeError as error:
            raise TypeError(
                "autonomous_systems must be an AutonomousSystem or iterable"
            ) from error
