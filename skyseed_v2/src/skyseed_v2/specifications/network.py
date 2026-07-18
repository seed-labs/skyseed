"""Immutable Network, Router, IX, AS, and eBGP specifications."""

from __future__ import annotations

from dataclasses import dataclass

from ._validation import (
    _asn,
    _identifier,
    _ipv4_address,
    _ipv4_network,
    _positive_integer,
)


@dataclass(frozen=True, slots=True)
class InternetExchangeAttachmentSpecification:
    """Describe one static node-to-IX attachment input.

    Layer: immutable input specification. It is nested in node specifications
    and consumed by the Applier; it is not a network interface or SEED object.
    It is public from specifications for programmatic input.
    """

    ix_id: int
    address: str

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


@dataclass(frozen=True, slots=True)
class LocalNetworkAttachmentSpecification:
    """Describe one ordinary Router attachment to an AS-owned network.

    Layer: immutable input specification. Input callers create it and the
    Applier passes it to public SEED join methods. It is not an interface or
    Network entity and is public from specifications.
    """

    network_id: str
    address: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "network_id", _identifier(self.network_id, "network_id")
        )
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


@dataclass(frozen=True, slots=True)
class InternetExchangeSpecification:
    """Describe one static Internet exchange construction input.

    Layer: immutable input specification. Loader or programmatic callers create
    it and the Applier calls the SEED Base API. It is not an IX entity or
    lifecycle owner and is public from specifications.
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
class OrdinaryNetworkSpecification:
    """Describe one ordinary-AS static IPv4 Network input.

    Layer: immutable input specification. Input callers create it and the
    Applier consumes it through public SEED calls. It is not a SEED Network and
    is public from specifications.
    """

    network_id: str
    prefix: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "network_id", _identifier(self.network_id, "network_id")
        )
        object.__setattr__(self, "prefix", _ipv4_network(self.prefix, "prefix"))


@dataclass(frozen=True, slots=True)
class OrdinaryRouterSpecification:
    """Describe one ordinary SEED Router construction input.

    Layer: immutable input specification. Input callers create it and the
    Applier calls ordinary AS/Router methods. It is not a Router or integration
    adapter and is public from specifications.
    """

    router_id: str
    network_attachments: tuple[LocalNetworkAttachmentSpecification, ...] = ()
    ix_attachments: tuple[InternetExchangeAttachmentSpecification, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "router_id", _identifier(self.router_id, "router_id")
        )
        network_attachments = tuple(self.network_attachments)
        ix_attachments = tuple(self.ix_attachments)
        if any(
            not isinstance(item, LocalNetworkAttachmentSpecification)
            for item in network_attachments
        ):
            raise TypeError(
                "network_attachments must contain "
                "LocalNetworkAttachmentSpecification values"
            )
        if any(
            not isinstance(item, InternetExchangeAttachmentSpecification)
            for item in ix_attachments
        ):
            raise TypeError(
                "ix_attachments must contain "
                "InternetExchangeAttachmentSpecification values"
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
class OrdinaryAutonomousSystemSpecification:
    """Describe one ordinary ground AS with Network and Router inputs.

    Layer: immutable input specification. Loader or programmatic callers create
    it and the Applier uses public SEED APIs. It is not an AutonomousSystem or
    entity owner and is public from specifications.
    """

    asn: int
    networks: tuple[OrdinaryNetworkSpecification, ...]
    routers: tuple[OrdinaryRouterSpecification, ...]

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        networks = tuple(self.networks)
        routers = tuple(self.routers)
        if not networks or any(
            not isinstance(item, OrdinaryNetworkSpecification)
            for item in networks
        ):
            raise ValueError(
                "networks must contain at least one OrdinaryNetworkSpecification"
            )
        if not routers or any(
            not isinstance(item, OrdinaryRouterSpecification)
            for item in routers
        ):
            raise ValueError(
                "routers must contain at least one OrdinaryRouterSpecification"
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
class EbgpRouterReferenceSpecification:
    """Identify one statically declared Router-like peering endpoint.

    Layer: immutable input specification. Input callers create it and nest it
    in an eBGP specification; it is not a Router, runtime locator or Simulator
    identity and is public from specifications.
    """

    asn: int
    router_id: str

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        object.__setattr__(
            self, "router_id", _identifier(self.router_id, "router_id")
        )


@dataclass(frozen=True, slots=True)
class EbgpPeeringSpecification:
    """Describe one explicit static eBGP peering input on an IX.

    Layer: immutable input specification. Input callers create it and the
    Applier configures the SEED Ebgp Layer. It is not a session entity or
    dynamic route and is public from specifications.
    """

    ix_id: int
    first: EbgpRouterReferenceSpecification
    second: EbgpRouterReferenceSpecification
    relationship: str = "unfiltered"

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        if not isinstance(
            self.first, EbgpRouterReferenceSpecification
        ) or not isinstance(self.second, EbgpRouterReferenceSpecification):
            raise TypeError(
                "first and second must be "
                "EbgpRouterReferenceSpecification values"
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
