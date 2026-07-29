"""Extend SEED AutonomousSystem ownership for Skyseed entities."""

from __future__ import annotations

from typing import Mapping

from seedemu.core import AutonomousSystem
from seedemu.layers import Base

from skyseed_v2.emulator.entities import (
    GroundStation,
    GroundStationLocation,
    Satellite,
    Shell,
)
from skyseed_v2.shared.identifier import validate_identifier


class SkyseedAutonomousSystem(AutonomousSystem):
    """Own static Skyseed entities and integrate them with one SEED AS.

    Layer: approved entity owner plus SEED integration. Handwritten callers or
    static Emulator creation build it; Base, routing layers and callers use
    it. It is not a scenario facade or Simulator input factory. It is public
    from ``skyseed_v2.emulator.seedemu`` because ownership and SEED
    lifecycle meet here.
    """

    def __init__(self, asn: int, subnet_template: str = "10.{}.0.0/16") -> None:
        super().__init__(asn, subnet_template)
        self._shells: dict[str, Shell] = {}
        self._satellite_ids: set[str] = set()

    @property
    def shells(self) -> tuple[Shell, ...]:
        return tuple(self._shells.values())

    def create_shell(self, shell_id: str) -> Shell:
        """Create and own one Shell through the public domain entry point."""

        shell_id = validate_identifier(shell_id, "shell_id")
        if shell_id in self._shells:
            raise ValueError(f"shell_id {shell_id!r} already exists")
        shell = Shell(
            shell_id,
            asn=self.getAsn(),
            satellite_ids=self._satellite_ids,
            _on_satellite_created=self.addExtensionNode,
        )
        self._shells[shell_id] = shell
        return shell

    def get_shell(self, shell_id: str) -> Shell:
        try:
            return self._shells[shell_id]
        except KeyError as error:
            raise KeyError(f"shell {shell_id!r} does not exist") from error

    def satellites(self) -> tuple[Satellite, ...]:
        return tuple(
            satellite
            for shell in self._shells.values()
            for satellite in shell.iter_satellites()
        )

    def get_satellite(self, satellite_id: str) -> Satellite:
        for satellite in self.satellites():
            if satellite.satellite_id == satellite_id:
                return satellite
        raise KeyError(f"satellite {satellite_id!r} does not exist")

    def create_ground_station(
        self,
        ground_station_id: str,
        *,
        location: GroundStationLocation,
        station_type: str,
        operator: str,
        display_name: str | None = None,
        service_config: Mapping[str, str] | None = None,
        routing_backend: str = "bird",
    ) -> GroundStation:
        """Create and own one GroundStation through the public domain entry point."""

        ground_station_id = validate_identifier(
            ground_station_id, "ground_station_id"
        )
        if ground_station_id in self.getRouters():
            raise ValueError(
                f"ground_station_id {ground_station_id!r} already exists"
            )
        ground_station = GroundStation(
            ground_station_id=ground_station_id,
            asn=self.getAsn(),
            location=location,
            station_type=station_type,
            operator=operator,
            display_name=display_name,
            service_config=service_config,
            routing_backend=routing_backend,
        )
        return self._addRouter(ground_station)

    def ground_stations(self) -> tuple[GroundStation, ...]:
        return tuple(
            router
            for name in self.getRouters()
            if isinstance((router := self.getRouter(name)), GroundStation)
        )

    def get_ground_station(self, ground_station_id: str) -> GroundStation:
        try:
            ground_station = self.getRouter(ground_station_id)
        except KeyError as error:
            raise KeyError(
                f"ground station {ground_station_id!r} does not exist"
            ) from error
        if not isinstance(ground_station, GroundStation):
            raise KeyError(
                f"ground station {ground_station_id!r} does not exist"
            )
        return ground_station


def get_skyseed_autonomous_systems(
    base: Base,
) -> tuple[SkyseedAutonomousSystem, ...]:
    """Query Skyseed AS values from an upstream Base without owning lifecycle."""

    if not isinstance(base, Base):
        raise TypeError("base must be an upstream Base layer")
    return tuple(
        autonomous_system
        for asn in sorted(base.getAsns())
        if isinstance(
            (autonomous_system := base.getAutonomousSystem(asn)),
            SkyseedAutonomousSystem,
        )
    )
