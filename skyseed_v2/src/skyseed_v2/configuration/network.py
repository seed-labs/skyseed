"""Immutable Network, Router, IX, AS, and eBGP Config values."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.identifier import validate_identifier

from ._validation import (
    _asn,
    _ipv4_address,
    _ipv4_network,
    _positive_integer,
)


@dataclass(frozen=True, slots=True)
class InternetExchangeAttachmentConfig:
    """Describe one static node-to-IX attachment input.

    Layer: immutable Config. It is nested in node Config values and consumed
    by `create_static_emulator`; it is not a network interface or SEED object.
    It is public from configuration for programmatic input.
    """

    ix_id: int
    address: str

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


@dataclass(frozen=True, slots=True)
class LocalNetworkAttachmentConfig:
    """Describe one ordinary Router attachment to an AS-owned network.

    Layer: immutable Config. Input callers create it and
    `create_static_emulator` passes it to public SEED join methods. It is not
    an interface or Network entity and is public from configuration.
    """

    network_id: str
    address: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "network_id",
            validate_identifier(self.network_id, "network_id"),
        )
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


@dataclass(frozen=True, slots=True)
class InternetExchangeConfig:
    """Describe one static Internet exchange construction input.

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it and `create_static_emulator` calls the SEED Base API. It
    is not an IX entity or lifecycle owner and is public from configuration.
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
    """Describe one ordinary-AS static IPv4 Network input.

    Layer: immutable Config. Input callers create it and
    `create_static_emulator` consumes it through public SEED calls. It is not
    a SEED Network and is public from configuration.
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
    """Describe one ordinary SEED Router construction input.

    Layer: immutable Config. Input callers create it and
    `create_static_emulator` calls ordinary AS/Router methods. It is not a
    Router or integration adapter and is public from configuration.
    """

    router_id: str
    network_attachments: tuple[LocalNetworkAttachmentConfig, ...] = ()
    ix_attachments: tuple[InternetExchangeAttachmentConfig, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "router_id", validate_identifier(self.router_id, "router_id")
        )
        network_attachments = tuple(self.network_attachments)
        ix_attachments = tuple(self.ix_attachments)
        if any(
            not isinstance(item, LocalNetworkAttachmentConfig)
            for item in network_attachments
        ):
            raise TypeError(
                "network_attachments must contain "
                "LocalNetworkAttachmentConfig values"
            )
        if any(
            not isinstance(item, InternetExchangeAttachmentConfig)
            for item in ix_attachments
        ):
            raise TypeError(
                "ix_attachments must contain "
                "InternetExchangeAttachmentConfig values"
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

    Layer: immutable Config. `load_static_emulator_config` or programmatic
    callers create it and `create_static_emulator` uses public SEED APIs. It is
    not an AutonomousSystem or entity owner and is public from configuration.
    """

    asn: int
    networks: tuple[NetworkConfig, ...]
    routers: tuple[RouterConfig, ...]

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        networks = tuple(self.networks)
        routers = tuple(self.routers)
        if not networks or any(
            not isinstance(item, NetworkConfig)
            for item in networks
        ):
            raise ValueError(
                "networks must contain at least one NetworkConfig"
            )
        if not routers or any(
            not isinstance(item, RouterConfig)
            for item in routers
        ):
            raise ValueError(
                "routers must contain at least one RouterConfig"
            )
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
class EbgpRouterReferenceConfig:
    """Identify one statically declared Router-like peering endpoint.

    Layer: immutable Config. Input callers create it and nest it
    in an eBGP config; it is not a Router, runtime locator or Simulator
    identity and is public from configuration.
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
    """Describe one explicit static eBGP peering input on an IX.

    Layer: immutable Config. Input callers create it and
    `create_static_emulator` configures the SEED Ebgp Layer. It is not a
    session entity or dynamic route and is public from configuration.
    """

    ix_id: int
    first: EbgpRouterReferenceConfig
    second: EbgpRouterReferenceConfig
    relationship: str = "unfiltered"

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        if not isinstance(
            self.first, EbgpRouterReferenceConfig
        ) or not isinstance(self.second, EbgpRouterReferenceConfig):
            raise TypeError(
                "first and second must be "
                "EbgpRouterReferenceConfig values"
            )
        if self.first.asn == self.second.asn:
            raise ValueError("eBGP peers must belong to different ASNs")
        if self.relationship != "unfiltered":
            raise ValueError("relationship must be 'unfiltered'")

    @property
    def sort_key(self) -> tuple[int, int, str, int, str]:
        peers = sorted(
            (
                (self.first.asn, self.first.router_id),
                (self.second.asn, self.second.router_id),
            )
        )
        return (self.ix_id, *peers[0], *peers[1])
