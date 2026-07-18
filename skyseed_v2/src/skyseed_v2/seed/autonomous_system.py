"""Unified AutonomousSystem ownership for static Skyseed entities."""

from __future__ import annotations

from typing import Mapping

from seedemu.core import AutonomousSystem, Emulator, Router

from skyseed_v2.domain import (
    GROUND_STATION_REGISTRY_TYPE,
    SATELLITE_REGISTRY_TYPE,
    GroundStation,
    GroundStationLocation,
    Satellite,
    Shell,
)
from skyseed_v2.domain.validation import validate_identifier


class SkyseedAutonomousSystem(AutonomousSystem):
    """Own static Skyseed entities and integrate them with one SEED AS.

    Layer: approved domain owner plus SEED integration. Handwritten callers or
    the specification Applier create it; Base, routing layers and callers use
    it. It is not a scenario facade or Descriptor factory. It is public from
    ``skyseed_v2.seed`` because ownership and SEED lifecycle meet here.
    """

    def __init__(self, asn: int, subnet_template: str = "10.{}.0.0/16") -> None:
        super().__init__(asn, subnet_template)
        self._shells: dict[str, Shell] = {}
        self._satellite_ids: set[str] = set()
        self._ground_stations: dict[str, GroundStation] = {}

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
        if (
            ground_station_id in self._ground_stations
            or ground_station_id in super().getRouters()
        ):
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
        self._ground_stations[ground_station_id] = ground_station
        return ground_station

    def ground_stations(self) -> tuple[GroundStation, ...]:
        return tuple(self._ground_stations.values())

    def get_ground_station(self, ground_station_id: str) -> GroundStation:
        try:
            return self._ground_stations[ground_station_id]
        except KeyError as error:
            raise KeyError(
                f"ground station {ground_station_id!r} does not exist"
            ) from error

    def getRouters(self) -> list[str]:
        return super().getRouters() + list(self._ground_stations)

    def createRouter(self, name: str, routingBackend: str = "bird") -> Router:
        if name in self._ground_stations:
            raise ValueError(f"Router name {name!r} already exists")
        return super().createRouter(name, routingBackend)

    def getRouter(self, name: str) -> Router:
        if name in self._ground_stations:
            return self._ground_stations[name]
        return super().getRouter(name)

    def getBorderRouters(self) -> list[Router]:
        return super().getBorderRouters() + [
            station
            for station in self._ground_stations.values()
            if station.isBorderRouter()
        ]

    def registerNodes(self, emulator: Emulator) -> None:
        super().registerNodes(emulator)
        registry = emulator.getRegistry()
        scope = str(self.getAsn())
        for ground_station in self.ground_stations():
            registry.register(
                scope,
                GROUND_STATION_REGISTRY_TYPE,
                ground_station.ground_station_id,
                ground_station,
            )
        for satellite in self.satellites():
            registry.register(
                scope,
                SATELLITE_REGISTRY_TYPE,
                satellite.satellite_id,
                satellite,
            )

    def inheritOptions(self, emulator: Emulator) -> None:
        # Upstream discovers GroundStation through its standard rnode entry.
        super().inheritOptions(emulator)
        for satellite in self.satellites():
            self.handDown(satellite)

    def configure(self, emulator: Emulator) -> None:
        # Upstream configures ordinary Routers and Hosts first.
        super().configure(emulator)
        registry = emulator.getRegistry()
        for ground_station in self.ground_stations():
            if not ground_station.getNameServers():
                ground_station.setNameServers(self.getNameServers())
            ground_station.configure(emulator)
            if ground_station.isBorderRouter():
                registry.register(
                    str(self.getAsn()),
                    "brdnode",
                    ground_station.getName(),
                    ground_station,
                )
        for satellite in self.satellites():
            satellite.configure(emulator)
