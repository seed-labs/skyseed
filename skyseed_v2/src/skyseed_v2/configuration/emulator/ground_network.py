"""Define the Config adapter for upstream SEED ground-network objects."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.shared.identifier import validate_identifier

from ._validation import _asn, _ipv4_network, _positive_integer
from .attachments import InternetExchangeAttachment, LocalNetworkAttachment


@dataclass(frozen=True, slots=True)
class InternetExchangeConfig:
    """Describe one upstream SEED Internet exchange construction input.

    Layer: immutable Config in the thin ground-network adapter. File or
    programmatic callers create it and static Emulator construction consumes
    it. It is not an IX entity and is public from configuration.
    """

    ix_id: int
    prefix: str
    route_server: bool = False

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        object.__setattr__(self, "prefix", _ipv4_network(self.prefix, "prefix"))
        if not isinstance(self.route_server, bool):
            raise TypeError("route_server must be a boolean")


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Describe one ordinary-AS upstream SEED IPv4 Network input.

    Layer: immutable Config in the thin ground-network adapter. File or
    programmatic callers create it and static Emulator construction consumes
    it. It is not a SEED Network and is public from configuration.
    """

    network_id: str
    prefix: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "network_id",
            validate_identifier(self.network_id, "network_id"),
        )
        object.__setattr__(self, "prefix", _ipv4_network(self.prefix, "prefix"))


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Describe one ordinary upstream SEED Router construction input.

    Layer: immutable Config in the thin ground-network adapter. It groups
    separately typed attachment relations for static construction. It is not a
    Router or attachment and is public from configuration.
    """

    router_id: str
    network_attachments: tuple[LocalNetworkAttachment, ...] = ()
    ix_attachments: tuple[InternetExchangeAttachment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "router_id", validate_identifier(self.router_id, "router_id")
        )
        network_attachments = tuple(self.network_attachments)
        ix_attachments = tuple(self.ix_attachments)
        if any(
            not isinstance(item, LocalNetworkAttachment)
            for item in network_attachments
        ):
            raise TypeError(
                "network_attachments must contain LocalNetworkAttachment values"
            )
        if any(
            not isinstance(item, InternetExchangeAttachment)
            for item in ix_attachments
        ):
            raise TypeError(
                "ix_attachments must contain InternetExchangeAttachment values"
            )
        if len({item.network_id for item in network_attachments}) != len(
            network_attachments
        ):
            raise ValueError(
                "network_attachments contains a duplicate network_id"
            )
        if len({item.ix_id for item in ix_attachments}) != len(ix_attachments):
            raise ValueError("ix_attachments contains a duplicate ix_id")
        object.__setattr__(
            self,
            "network_attachments",
            tuple(sorted(network_attachments, key=lambda item: item.network_id)),
        )
        object.__setattr__(
            self,
            "ix_attachments",
            tuple(sorted(ix_attachments, key=lambda item: item.ix_id)),
        )
        if not network_attachments and not ix_attachments:
            raise ValueError("ordinary Router must have at least one attachment")


@dataclass(frozen=True, slots=True)
class OrdinaryAutonomousSystemConfig:
    """Describe one ordinary ground AS with Network and Router inputs.

    Layer: immutable Config in the thin ground-network adapter. It exists only
    to assemble upstream objects in the complete static example. It is not an
    AutonomousSystem or general SEED schema and is public from configuration.
    """

    asn: int
    networks: tuple[NetworkConfig, ...]
    routers: tuple[RouterConfig, ...]

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        networks = tuple(self.networks)
        routers = tuple(self.routers)
        if not networks or any(
            not isinstance(item, NetworkConfig) for item in networks
        ):
            raise ValueError("networks must contain at least one NetworkConfig")
        if not routers or any(
            not isinstance(item, RouterConfig) for item in routers
        ):
            raise ValueError("routers must contain at least one RouterConfig")
        if len({item.network_id for item in networks}) != len(networks):
            raise ValueError("networks contains a duplicate network_id")
        if len({item.router_id for item in routers}) != len(routers):
            raise ValueError("routers contains a duplicate router_id")
        object.__setattr__(
            self,
            "networks",
            tuple(sorted(networks, key=lambda item: item.network_id)),
        )
        object.__setattr__(
            self,
            "routers",
            tuple(sorted(routers, key=lambda item: item.router_id)),
        )


@dataclass(frozen=True, slots=True)
class EbgpRouterReference:
    """Identify one statically declared Router-like peering endpoint.

    Layer: immutable configuration auxiliary value. EbgpPeeringConfig contains
    it and validation resolves it against declared owners. It is not a Router,
    global locator or Simulator identity and is public from configuration.
    """

    asn: int
    router_id: str

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        object.__setattr__(
            self, "router_id", validate_identifier(self.router_id, "router_id")
        )


@dataclass(frozen=True, slots=True)
class EbgpPeeringConfig:
    """Describe one explicit symmetric unfiltered eBGP peering on an IX.

    Layer: immutable Config in the thin ground-network adapter. Static Emulator
    construction passes it to the upstream Ebgp layer. It is not a routing
    session entity or dynamic route and is public from configuration.
    """

    ix_id: int
    peer_routers: tuple[EbgpRouterReference, EbgpRouterReference]
    relationship: str = "unfiltered"

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        peer_routers = tuple(self.peer_routers)
        if len(peer_routers) != 2 or any(
            not isinstance(peer, EbgpRouterReference) for peer in peer_routers
        ):
            raise TypeError(
                "peer_routers must contain exactly two EbgpRouterReference values"
            )
        if peer_routers[0].asn == peer_routers[1].asn:
            raise ValueError("eBGP peers must belong to different ASNs")
        if self.relationship != "unfiltered":
            raise ValueError("relationship must be 'unfiltered'")
        object.__setattr__(
            self,
            "peer_routers",
            tuple(
                sorted(
                    peer_routers,
                    key=lambda peer: (peer.asn, peer.router_id),
                )
            ),
        )

    @property
    def sort_key(self) -> tuple[int, int, str, int, str]:
        first, second = self.peer_routers
        return (
            self.ix_id,
            first.asn,
            first.router_id,
            second.asn,
            second.router_id,
        )


__all__ = [
    "EbgpPeeringConfig",
    "EbgpRouterReference",
    "InternetExchangeConfig",
    "NetworkConfig",
    "OrdinaryAutonomousSystemConfig",
    "RouterConfig",
]
