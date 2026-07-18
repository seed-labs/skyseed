"""Immutable, backend-independent specifications for static construction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from ipaddress import IPv4Address, IPv4Network
from math import isfinite


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if not value[0].isalnum() or any(
        not (character.isalnum() or character in "_.-") for character in value
    ):
        raise ValueError(
            f"{field_name} must start with an alphanumeric character and contain "
            "only alphanumeric characters, '_', '.', or '-'"
        )
    return value


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _angle(value: float, field_name: str) -> float:
    normalized = float(value)
    if not isfinite(normalized) or not 0.0 <= normalized < 360.0:
        raise ValueError(f"{field_name} must be between 0 and 360 (exclusive)")
    return normalized


def _positive_integer(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _asn(value: int, field_name: str) -> int:
    normalized = _positive_integer(value, field_name)
    if normalized > 4_294_967_295:
        raise ValueError(f"{field_name} must be a valid 32-bit ASN")
    return normalized


def _ipv4_address(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an IPv4 address")
    try:
        return str(IPv4Address(value))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an IPv4 address") from error


def _ipv4_network(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an IPv4 network prefix")
    try:
        return str(IPv4Network(value, strict=True))
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be a canonical IPv4 network prefix"
        ) from error


@dataclass(frozen=True, slots=True)
class RaanDistributionSpecification:
    """Uniform right-ascension generation parameters for a Shell."""

    start_deg: float = 0.0
    step_deg: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_deg", _angle(self.start_deg, "start_deg"))
        if self.step_deg is not None:
            step = float(self.step_deg)
            if not isfinite(step) or step <= 0.0:
                raise ValueError("step_deg must be a positive finite number")
            object.__setattr__(self, "step_deg", step)


@dataclass(frozen=True, slots=True)
class InOrbitPhaseDistributionSpecification:
    """Uniform in-orbit phase and alternating plane offset parameters."""

    start_deg: float = 0.0
    step_deg: float | None = None
    alternating_orbit_offset_deg: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_deg", _angle(self.start_deg, "start_deg"))
        object.__setattr__(
            self,
            "alternating_orbit_offset_deg",
            _angle(
                self.alternating_orbit_offset_deg,
                "alternating_orbit_offset_deg",
            ),
        )
        if self.step_deg is not None:
            step = float(self.step_deg)
            if not isfinite(step) or step <= 0.0:
                raise ValueError("step_deg must be a positive finite number")
            object.__setattr__(self, "step_deg", step)


@dataclass(frozen=True, slots=True)
class OrbitGenerationSpecification:
    """Rules for generating every Orbit and Satellite in one Shell."""

    orbit_count: int
    satellites_per_orbit: int
    raan: RaanDistributionSpecification = RaanDistributionSpecification()
    phase: InOrbitPhaseDistributionSpecification = (
        InOrbitPhaseDistributionSpecification()
    )

    def __post_init__(self) -> None:
        _positive_integer(self.orbit_count, "orbit_count")
        _positive_integer(self.satellites_per_orbit, "satellites_per_orbit")
        if not isinstance(self.raan, RaanDistributionSpecification):
            raise TypeError("raan must be a RaanDistributionSpecification")
        if not isinstance(self.phase, InOrbitPhaseDistributionSpecification):
            raise TypeError(
                "phase must be an InOrbitPhaseDistributionSpecification"
            )


@dataclass(frozen=True, slots=True)
class SatelliteSpecification:
    """One explicitly specified Satellite within an Orbit."""

    satellite_id: str
    index_in_orbit: int
    mean_anomaly_deg: float
    payload_items: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "satellite_id", _identifier(self.satellite_id, "satellite_id")
        )
        if (
            not isinstance(self.index_in_orbit, int)
            or isinstance(self.index_in_orbit, bool)
            or self.index_in_orbit < 0
        ):
            raise ValueError("index_in_orbit must be a non-negative integer")
        object.__setattr__(
            self,
            "mean_anomaly_deg",
            _angle(self.mean_anomaly_deg, "mean_anomaly_deg"),
        )
        payload_items = tuple(self.payload_items)
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            for key, value in payload_items
        ):
            raise TypeError("payload_items must contain non-empty string keys and values")
        if len({key for key, _ in payload_items}) != len(payload_items):
            raise ValueError("payload_items contains a duplicate key")
        object.__setattr__(self, "payload_items", tuple(sorted(payload_items)))


@dataclass(frozen=True, slots=True)
class OrbitSpecification:
    """One explicitly specified Orbit and its explicit Satellites."""

    orbit_id: str
    right_ascension_deg: float
    satellites: tuple[SatelliteSpecification, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "orbit_id", _identifier(self.orbit_id, "orbit_id"))
        object.__setattr__(
            self,
            "right_ascension_deg",
            _angle(self.right_ascension_deg, "right_ascension_deg"),
        )
        satellites = tuple(self.satellites)
        if not satellites:
            raise ValueError("satellites must contain at least one specification")
        if any(not isinstance(item, SatelliteSpecification) for item in satellites):
            raise TypeError("satellites must contain SatelliteSpecification values")
        ids = [item.satellite_id for item in satellites]
        indices = [item.index_in_orbit for item in satellites]
        if len(set(ids)) != len(ids):
            raise ValueError("satellites contains a duplicate satellite_id")
        if len(set(indices)) != len(indices):
            raise ValueError("satellites contains a duplicate index_in_orbit")
        object.__setattr__(
            self, "satellites", tuple(sorted(satellites, key=lambda item: item.satellite_id))
        )


@dataclass(frozen=True, slots=True)
class ShellSpecification:
    """One static Shell, expressed by generation rules or explicit Orbits."""

    shell_id: str
    altitude_km: float
    inclination_deg: float
    generation: OrbitGenerationSpecification | None = None
    orbits: tuple[OrbitSpecification, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "shell_id", _identifier(self.shell_id, "shell_id"))
        altitude = float(self.altitude_km)
        inclination = float(self.inclination_deg)
        if not isfinite(altitude) or altitude <= 0.0:
            raise ValueError("altitude_km must be a positive finite number")
        if not isfinite(inclination) or not 0.0 <= inclination <= 180.0:
            raise ValueError("inclination_deg must be between 0 and 180")
        object.__setattr__(self, "altitude_km", altitude)
        object.__setattr__(self, "inclination_deg", inclination)

        orbits = tuple(self.orbits)
        if self.generation is not None and not isinstance(
            self.generation, OrbitGenerationSpecification
        ):
            raise TypeError("generation must be an OrbitGenerationSpecification")
        if any(not isinstance(item, OrbitSpecification) for item in orbits):
            raise TypeError("orbits must contain OrbitSpecification values")
        if (self.generation is None) == (not orbits):
            raise ValueError("specify exactly one of generation or explicit orbits")
        ids = [item.orbit_id for item in orbits]
        if len(set(ids)) != len(ids):
            raise ValueError("orbits contains a duplicate orbit_id")
        object.__setattr__(
            self, "orbits", tuple(sorted(orbits, key=lambda item: item.orbit_id))
        )


@dataclass(frozen=True, slots=True)
class GroundStationSpecification:
    """Static GroundStation data with explicit or generated logical identity."""

    display_name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None
    operator: str
    station_type: str
    ground_station_id: str | None = None
    identity_key: str | None = None
    ix_attachments: tuple["InternetExchangeAttachmentSpecification", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name"))
        object.__setattr__(self, "operator", _text(self.operator, "operator"))
        object.__setattr__(
            self, "station_type", _text(self.station_type, "station_type")
        )
        latitude = float(self.latitude_deg)
        longitude = float(self.longitude_deg)
        elevation = None if self.elevation_m is None else float(self.elevation_m)
        if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        if not isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")
        if elevation is not None and not isfinite(elevation):
            raise ValueError("elevation_m must be finite when provided")
        object.__setattr__(self, "latitude_deg", latitude)
        object.__setattr__(self, "longitude_deg", longitude)
        object.__setattr__(self, "elevation_m", elevation)

        has_explicit_id = self.ground_station_id is not None
        has_identity_key = self.identity_key is not None
        if has_explicit_id == has_identity_key:
            raise ValueError(
                "specify exactly one of ground_station_id or identity_key"
            )
        if has_explicit_id:
            object.__setattr__(
                self,
                "ground_station_id",
                _identifier(self.ground_station_id, "ground_station_id"),
            )
        else:
            object.__setattr__(
                self, "identity_key", _text(self.identity_key, "identity_key")
            )

        attachments = tuple(self.ix_attachments)
        if any(
            not isinstance(item, InternetExchangeAttachmentSpecification)
            for item in attachments
        ):
            raise TypeError(
                "ix_attachments must contain "
                "InternetExchangeAttachmentSpecification values"
            )
        if len({item.ix_id for item in attachments}) != len(attachments):
            raise ValueError("ix_attachments contains a duplicate ix_id")
        object.__setattr__(
            self,
            "ix_attachments",
            tuple(sorted(attachments, key=lambda item: item.ix_id)),
        )


def ground_station_id_for(specification: GroundStationSpecification) -> str:
    """Resolve one v2-native stable ID without using input order or SEED state."""

    if not isinstance(specification, GroundStationSpecification):
        raise TypeError("specification must be a GroundStationSpecification")
    if specification.ground_station_id is not None:
        return specification.ground_station_id
    assert specification.identity_key is not None
    digest = sha256(specification.identity_key.encode("utf-8")).hexdigest()[:20]
    return f"ground-station-{digest}"


@dataclass(frozen=True, slots=True)
class InternetExchangeAttachmentSpecification:
    """One static node attachment to an explicitly identified IX."""

    ix_id: int
    address: str

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


@dataclass(frozen=True, slots=True)
class LocalNetworkAttachmentSpecification:
    """One static ordinary-Router attachment to an AS-owned network."""

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
    """One static Internet exchange and its explicit peering prefix."""

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
    """One ordinary-AS static IPv4 network."""

    network_id: str
    prefix: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "network_id", _identifier(self.network_id, "network_id")
        )
        object.__setattr__(self, "prefix", _ipv4_network(self.prefix, "prefix"))


@dataclass(frozen=True, slots=True)
class OrdinaryRouterSpecification:
    """One ordinary SEED Router and its static network attachments."""

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
            raise ValueError("network_attachments contains a duplicate network_id")
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
    """One ordinary ground AS with networks and Routers."""

    asn: int
    networks: tuple[OrdinaryNetworkSpecification, ...]
    routers: tuple[OrdinaryRouterSpecification, ...]

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        networks = tuple(self.networks)
        routers = tuple(self.routers)
        if not networks or any(
            not isinstance(item, OrdinaryNetworkSpecification) for item in networks
        ):
            raise ValueError(
                "networks must contain at least one OrdinaryNetworkSpecification"
            )
        if not routers or any(
            not isinstance(item, OrdinaryRouterSpecification) for item in routers
        ):
            raise ValueError(
                "routers must contain at least one OrdinaryRouterSpecification"
            )
        if len({item.network_id for item in networks}) != len(networks):
            raise ValueError("networks contains a duplicate network_id")
        if len({item.router_id for item in routers}) != len(routers):
            raise ValueError("routers contains a duplicate router_id")
        object.__setattr__(
            self, "networks", tuple(sorted(networks, key=lambda item: item.network_id))
        )
        object.__setattr__(
            self, "routers", tuple(sorted(routers, key=lambda item: item.router_id))
        )


@dataclass(frozen=True, slots=True)
class SkyseedAutonomousSystemSpecification:
    """Static Shell and GroundStation inputs owned by one Skyseed AS."""

    asn: int
    shells: tuple[ShellSpecification, ...] = ()
    ground_stations: tuple[GroundStationSpecification, ...] = ()

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        shells = tuple(self.shells)
        stations = tuple(self.ground_stations)
        if any(not isinstance(item, ShellSpecification) for item in shells):
            raise TypeError("shells must contain ShellSpecification values")
        if any(not isinstance(item, GroundStationSpecification) for item in stations):
            raise TypeError(
                "ground_stations must contain GroundStationSpecification values"
            )
        if not shells and not stations:
            raise ValueError(
                "Skyseed AS must contain at least one Shell or GroundStation"
            )
        if len({item.shell_id for item in shells}) != len(shells):
            raise ValueError("shells contains a duplicate shell_id")
        station_ids = [ground_station_id_for(item) for item in stations]
        if len(set(station_ids)) != len(station_ids):
            raise ValueError("ground_stations contains a duplicate logical ID")
        explicit_satellite_ids = [
            satellite.satellite_id
            for shell in shells
            for orbit in shell.orbits
            for satellite in orbit.satellites
        ]
        if len(set(explicit_satellite_ids)) != len(explicit_satellite_ids):
            raise ValueError("shells contains a duplicate satellite_id")
        object.__setattr__(
            self, "shells", tuple(sorted(shells, key=lambda item: item.shell_id))
        )
        object.__setattr__(
            self,
            "ground_stations",
            tuple(sorted(stations, key=ground_station_id_for)),
        )


@dataclass(frozen=True, slots=True)
class EbgpRouterReferenceSpecification:
    """Logical reference to one statically declared Router-like node."""

    asn: int
    router_id: str

    def __post_init__(self) -> None:
        _asn(self.asn, "asn")
        object.__setattr__(
            self, "router_id", _identifier(self.router_id, "router_id")
        )


@dataclass(frozen=True, slots=True)
class EbgpPeeringSpecification:
    """One explicit static eBGP peering on an IX."""

    ix_id: int
    first: EbgpRouterReferenceSpecification
    second: EbgpRouterReferenceSpecification
    relationship: str = "unfiltered"

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        if not isinstance(self.first, EbgpRouterReferenceSpecification) or not isinstance(
            self.second, EbgpRouterReferenceSpecification
        ):
            raise TypeError(
                "first and second must be EbgpRouterReferenceSpecification values"
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


@dataclass(frozen=True, slots=True)
class StaticEmulatorScenarioSpecification:
    """Immutable v2-owned input for one approved static Emulator scenario."""

    skyseed_autonomous_systems: tuple[SkyseedAutonomousSystemSpecification, ...]
    internet_exchanges: tuple[InternetExchangeSpecification, ...] = ()
    ordinary_autonomous_systems: tuple[
        OrdinaryAutonomousSystemSpecification, ...
    ] = ()
    ebgp_peerings: tuple[EbgpPeeringSpecification, ...] = ()

    def __post_init__(self) -> None:
        skyseed_ases = tuple(self.skyseed_autonomous_systems)
        exchanges = tuple(self.internet_exchanges)
        ordinary_ases = tuple(self.ordinary_autonomous_systems)
        peerings = tuple(self.ebgp_peerings)
        if not skyseed_ases or any(
            not isinstance(item, SkyseedAutonomousSystemSpecification)
            for item in skyseed_ases
        ):
            raise ValueError(
                "skyseed_autonomous_systems must contain at least one "
                "SkyseedAutonomousSystemSpecification"
            )
        if any(not isinstance(item, InternetExchangeSpecification) for item in exchanges):
            raise TypeError(
                "internet_exchanges must contain InternetExchangeSpecification values"
            )
        if any(
            not isinstance(item, OrdinaryAutonomousSystemSpecification)
            for item in ordinary_ases
        ):
            raise TypeError(
                "ordinary_autonomous_systems must contain "
                "OrdinaryAutonomousSystemSpecification values"
            )
        if any(not isinstance(item, EbgpPeeringSpecification) for item in peerings):
            raise TypeError("ebgp_peerings must contain EbgpPeeringSpecification values")

        asns = [item.asn for item in (*skyseed_ases, *ordinary_ases)]
        if len(set(asns)) != len(asns):
            raise ValueError("scenario contains a duplicate ASN")
        if len({item.ix_id for item in exchanges}) != len(exchanges):
            raise ValueError("scenario contains a duplicate ix_id")
        peering_keys = [item.sort_key for item in peerings]
        if len(set(peering_keys)) != len(peering_keys):
            raise ValueError("scenario contains a duplicate eBGP peering")

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
            self, "ebgp_peerings", tuple(sorted(peerings, key=lambda item: item.sort_key))
        )
        self._validate_references()

    def _validate_references(self) -> None:
        exchanges = {item.ix_id: IPv4Network(item.prefix) for item in self.internet_exchanges}
        routers_by_asn: dict[int, dict[str, set[int]]] = {}
        ix_addresses: dict[int, set[IPv4Address]] = {
            ix_id: set() for ix_id in exchanges
        }

        for autonomous_system in self.skyseed_autonomous_systems:
            router_attachments: dict[str, set[int]] = {}
            for station in autonomous_system.ground_stations:
                station_id = ground_station_id_for(station)
                router_attachments[station_id] = {
                    attachment.ix_id for attachment in station.ix_attachments
                }
                for attachment in station.ix_attachments:
                    self._validate_ix_attachment(
                        attachment, exchanges, ix_addresses, station_id
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
                            f"Router {router.router_id!r} references unknown local "
                            f"network {attachment.network_id!r}"
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
                        attachment, exchanges, ix_addresses, router.router_id
                    )
            routers_by_asn[autonomous_system.asn] = router_attachments

        for peering in self.ebgp_peerings:
            if peering.ix_id not in exchanges:
                raise ValueError(
                    f"eBGP peering references unknown IX {peering.ix_id}"
                )
            for peer in (peering.first, peering.second):
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
        attachment: InternetExchangeAttachmentSpecification,
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
            address, exchanges[attachment.ix_id], f"node {node_id!r}"
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
