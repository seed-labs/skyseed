"""Define the complete immutable static Emulator Config graph."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network

from ._validation import _asn
from .attachments import InternetExchangeAttachment
from .ground_network import (
    EbgpPeeringConfig,
    InternetExchangeConfig,
    OrdinaryAutonomousSystemConfig,
)
from .ground_station import GroundStationConfig, _derive_ground_station_id
from .satellite import ShellConfig


@dataclass(frozen=True, slots=True)
class SkyseedAutonomousSystemConfig:
    """Group Shell and GroundStation inputs for one future Skyseed AS.

    Layer: immutable Config. Input callers create it and the
    `create_static_emulator` creates the real AS and owned entities. It is not
    an AS, facade or ownership layer and is public from configuration.
    """

    asn: int
    shells: tuple[ShellConfig, ...] = ()
    ground_stations: tuple[GroundStationConfig, ...] = ()

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        shells = tuple(self.shells)
        stations = tuple(self.ground_stations)
        if any(not isinstance(item, ShellConfig) for item in shells):
            raise TypeError("shells must contain ShellConfig values")
        if any(
            not isinstance(item, GroundStationConfig)
            for item in stations
        ):
            raise TypeError(
                "ground_stations must contain GroundStationConfig values"
            )
        if not shells and not stations:
            raise ValueError(
                "Skyseed AS must contain at least one Shell or GroundStation"
            )
        if len({item.shell_id for item in shells}) != len(shells):
            raise ValueError("shells contains a duplicate shell_id")
        station_ids = [_derive_ground_station_id(item) for item in stations]
        if len(set(station_ids)) != len(station_ids):
            raise ValueError("ground_stations contains a duplicate logical ID")
        explicit_satellite_ids = [
            satellite.satellite_id
            for shell in shells
            for orbit in shell.explicit_orbits
            for satellite in orbit.satellites
        ]
        if len(set(explicit_satellite_ids)) != len(explicit_satellite_ids):
            raise ValueError("shells contains a duplicate satellite_id")
        object.__setattr__(
            self,
            "shells",
            tuple(sorted(shells, key=lambda item: item.shell_id)),
        )
        object.__setattr__(
            self,
            "ground_stations",
            tuple(sorted(stations, key=_derive_ground_station_id)),
        )


@dataclass(frozen=True, slots=True)
class StaticEmulatorConfig:
    """Contain one complete validated static Emulator construction input.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it; `create_static_emulator` consumes it. It is not runtime
    state, an entity parent, an Emulator or deployment. It is public from
    configuration as the complete file/programmatic input.
    """

    skyseed_autonomous_systems: tuple[
        SkyseedAutonomousSystemConfig, ...
    ]
    internet_exchanges: tuple[InternetExchangeConfig, ...] = ()
    ordinary_autonomous_systems: tuple[
        OrdinaryAutonomousSystemConfig, ...
    ] = ()
    ebgp_peerings: tuple[EbgpPeeringConfig, ...] = ()

    def __post_init__(self) -> None:
        skyseed_ases = tuple(self.skyseed_autonomous_systems)
        exchanges = tuple(self.internet_exchanges)
        ordinary_ases = tuple(self.ordinary_autonomous_systems)
        peerings = tuple(self.ebgp_peerings)
        if not skyseed_ases or any(
            not isinstance(item, SkyseedAutonomousSystemConfig)
            for item in skyseed_ases
        ):
            raise ValueError(
                "skyseed_autonomous_systems must contain at least one "
                "SkyseedAutonomousSystemConfig"
            )
        if any(
            not isinstance(item, InternetExchangeConfig)
            for item in exchanges
        ):
            raise TypeError(
                "internet_exchanges must contain "
                "InternetExchangeConfig values"
            )
        if any(
            not isinstance(item, OrdinaryAutonomousSystemConfig)
            for item in ordinary_ases
        ):
            raise TypeError(
                "ordinary_autonomous_systems must contain "
                "OrdinaryAutonomousSystemConfig values"
            )
        if any(
            not isinstance(item, EbgpPeeringConfig)
            for item in peerings
        ):
            raise TypeError(
                "ebgp_peerings must contain EbgpPeeringConfig values"
            )

        asns = [item.asn for item in (*skyseed_ases, *ordinary_ases)]
        if len(set(asns)) != len(asns):
            raise ValueError("config contains a duplicate ASN")
        if len({item.ix_id for item in exchanges}) != len(exchanges):
            raise ValueError("config contains a duplicate ix_id")
        peering_keys = [item.sort_key for item in peerings]
        if len(set(peering_keys)) != len(peering_keys):
            raise ValueError("config contains a duplicate eBGP peering")

        object.__setattr__(
            self,
            "skyseed_autonomous_systems",
            tuple(sorted(skyseed_ases, key=lambda item: item.asn)),
        )
        object.__setattr__(
            self,
            "internet_exchanges",
            tuple(sorted(exchanges, key=lambda item: item.ix_id)),
        )
        object.__setattr__(
            self,
            "ordinary_autonomous_systems",
            tuple(sorted(ordinary_ases, key=lambda item: item.asn)),
        )
        object.__setattr__(
            self,
            "ebgp_peerings",
            tuple(sorted(peerings, key=lambda item: item.sort_key)),
        )
        self._validate_references()

    def _validate_references(self) -> None:
        exchanges = {
            item.ix_id: IPv4Network(item.prefix)
            for item in self.internet_exchanges
        }
        routers_by_asn: dict[int, dict[str, set[int]]] = {}
        ix_addresses: dict[int, set[IPv4Address]] = {
            ix_id: set() for ix_id in exchanges
        }

        for autonomous_system in self.skyseed_autonomous_systems:
            router_attachments: dict[str, set[int]] = {}
            for station in autonomous_system.ground_stations:
                station_id = _derive_ground_station_id(station)
                router_attachments[station_id] = {
                    attachment.ix_id for attachment in station.ix_attachments
                }
                for attachment in station.ix_attachments:
                    self._validate_ix_attachment(
                        attachment,
                        exchanges,
                        ix_addresses,
                        station_id,
                    )
            routers_by_asn[autonomous_system.asn] = router_attachments

        for autonomous_system in self.ordinary_autonomous_systems:
            networks = {
                item.network_id: IPv4Network(item.prefix)
                for item in autonomous_system.networks
            }
            local_addresses: dict[str, set[IPv4Address]] = {
                network_id: set() for network_id in networks
            }
            router_attachments: dict[str, set[int]] = {}
            for router in autonomous_system.routers:
                router_attachments[router.router_id] = {
                    attachment.ix_id for attachment in router.ix_attachments
                }
                for attachment in router.network_attachments:
                    if attachment.network_id not in networks:
                        raise ValueError(
                            f"Router {router.router_id!r} references unknown "
                            f"local network {attachment.network_id!r}"
                        )
                    address = IPv4Address(attachment.address)
                    self._validate_host_address(
                        address,
                        networks[attachment.network_id],
                        f"Router {router.router_id!r}",
                    )
                    if address in local_addresses[attachment.network_id]:
                        raise ValueError(
                            f"duplicate address {address} on local network "
                            f"{attachment.network_id!r}"
                        )
                    local_addresses[attachment.network_id].add(address)
                for attachment in router.ix_attachments:
                    self._validate_ix_attachment(
                        attachment,
                        exchanges,
                        ix_addresses,
                        router.router_id,
                    )
            routers_by_asn[autonomous_system.asn] = router_attachments

        for peering in self.ebgp_peerings:
            if peering.ix_id not in exchanges:
                raise ValueError(
                    f"eBGP peering references unknown IX {peering.ix_id}"
                )
            for peer in peering.peer_routers:
                if peer.asn not in routers_by_asn:
                    raise ValueError(
                        f"eBGP peer references unknown ASN {peer.asn}"
                    )
                if peer.router_id not in routers_by_asn[peer.asn]:
                    raise ValueError(
                        f"eBGP peer references unknown Router {peer.router_id!r} "
                        f"in AS{peer.asn}"
                    )
                if peering.ix_id not in routers_by_asn[peer.asn][peer.router_id]:
                    raise ValueError(
                        f"eBGP peer {peer.router_id!r} is not attached to "
                        f"IX {peering.ix_id}"
                    )

    @classmethod
    def _validate_ix_attachment(
        cls,
        attachment: InternetExchangeAttachment,
        exchanges: dict[int, IPv4Network],
        ix_addresses: dict[int, set[IPv4Address]],
        node_id: str,
    ) -> None:
        if attachment.ix_id not in exchanges:
            raise ValueError(
                f"node {node_id!r} references unknown IX {attachment.ix_id}"
            )
        address = IPv4Address(attachment.address)
        cls._validate_host_address(
            address,
            exchanges[attachment.ix_id],
            f"node {node_id!r}",
        )
        if address in ix_addresses[attachment.ix_id]:
            raise ValueError(
                f"duplicate address {address} on IX {attachment.ix_id}"
            )
        ix_addresses[attachment.ix_id].add(address)

    @staticmethod
    def _validate_host_address(
        address: IPv4Address,
        network: IPv4Network,
        context: str,
    ) -> None:
        if address not in network or address in {
            network.network_address,
            network.broadcast_address,
        }:
            raise ValueError(
                f"{context} address {address} is not a host in {network}"
            )
