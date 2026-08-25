"""Extend SEED AutonomousSystem ownership for Skyseed entities."""

from __future__ import annotations

from typing import Mapping

from seedemu.core import AutonomousSystem, Emulator, Router
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
        operator: str | None,
        display_name: str | None = None,
        service_config: Mapping[str, str] | None = None,
        routing_backend: str = "bird",
        max_simultaneous_satellite_accesses: int | None = None,
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
            max_simultaneous_satellite_accesses=(
                max_simultaneous_satellite_accesses
            ),
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

    def createRouter(
        self, name: str, routingBackend: str = "bird"
    ) -> Router:
        """Create a standard SEED Router without colliding with a station."""

        assert name not in self._ground_stations, (
            f"Router with name {name} already exists."
        )
        return super().createRouter(name, routingBackend)

    def getRouters(self) -> list[str]:
        """Return ordinary Router and GroundStation inventory names."""

        return super().getRouters() + list(self._ground_stations)

    def getBorderRouters(self) -> list[Router]:
        """Return every ordinary or GroundStation border router."""

        return super().getBorderRouters() + [
            station
            for station in self._ground_stations.values()
            if station.isBorderRouter()
        ]

    def getRouter(self, name: str) -> Router:
        """Resolve a GroundStation through the standard Router query path."""

        if name in self._ground_stations:
            return self._ground_stations[name]
        return super().getRouter(name)

    @staticmethod
    def _new_base_option(base: Base, fullname: str):
        matches = [
            option
            for option in base.getAvailableOptions()
            if option.fullname() == fullname
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"SEED Base option {fullname!r} is unavailable or ambiguous"
            )
        return matches[0]

    def registerNodes(self, emulator: Emulator) -> None:
        """Register Skyseed entities through their upstream node contracts."""

        base = emulator.getLayer("Base")
        if not isinstance(base, Base):
            raise RuntimeError("SkyseedAutonomousSystem requires a SEED Base layer")
        for satellite in self.satellites():
            if satellite.getOption("sysctl_netipv4_conf_rp_filter") is None:
                satellite.setOption(
                    self._new_base_option(
                        base, "sysctl_netipv4_conf_rp_filter"
                    )
                )

        super().registerNodes(emulator)
        registry = emulator.getRegistry()
        for name, station in self._ground_stations.items():
            registry.register(str(self.getAsn()), "rnode", name, station)

    def configure(self, emulator: Emulator) -> None:
        """Configure GroundStations with ordinary SEED Router semantics."""

        super().configure(emulator)
        registry = emulator.getRegistry()
        for name, station in self._ground_stations.items():
            if len(station.getNameServers()) == 0:
                station.setNameServers(self.getNameServers())
            station.configure(emulator)
            if station.isBorderRouter():
                registry.register(str(self.getAsn()), "brdnode", name, station)


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
