"""Parse ground stations, ordinary networks, attachments and peerings."""
from __future__ import annotations
from pathlib import Path
from typing import Any

from ..._yaml import (
    _ConfigurationYamlError,
    fields as _fields,
    mapping as _mapping,
    read_document as _read_document,
    sequence as _sequence,
)
from ..attachments import InternetExchangeAttachment, LocalNetworkAttachment
from ..ground_network import (EbgpPeeringConfig, EbgpRouterReference, InternetExchangeConfig,
    NetworkConfig, OrdinaryAutonomousSystemConfig, RouterConfig)
from ..ground_station import GroundStationConfig
from ._errors import StaticEmulatorConfigLoadError

GROUND_STATIONS_SCHEMA_VERSION = "skyseed-ground-stations/v1"

def _load_ground_stations_file(
    raw_reference: Any,
    root: Path,
    context: str,
) -> tuple[GroundStationConfig, ...]:
    if not isinstance(raw_reference, str) or not raw_reference.strip():
        raise StaticEmulatorConfigLoadError(
            f"{context}.ground_stations_file must be a relative path"
        )
    reference = Path(raw_reference)
    if reference.is_absolute():
        raise StaticEmulatorConfigLoadError(
            f"{context}.ground_stations_file must be a relative path"
        )
    return load_ground_station_configs(root / reference)


def load_ground_station_configs(path: Path) -> tuple[GroundStationConfig, ...]:
    """Read independent ground station YAML from a relative or absolute path."""
    try:
        return _read_ground_station_configs(path)
    except StaticEmulatorConfigLoadError:
        raise
    except (_ConfigurationYamlError, TypeError, ValueError) as error:
        raise StaticEmulatorConfigLoadError(str(error)) from error


def _read_ground_station_configs(path: Path) -> tuple[GroundStationConfig, ...]:
    path = Path(path).resolve()
    payload = _read_document(path, "ground station data")
    _fields(
        payload,
        required={"schema_version", "ground_stations"},
        context="ground station data",
    )
    if payload["schema_version"] != GROUND_STATIONS_SCHEMA_VERSION:
        raise StaticEmulatorConfigLoadError(
            "unsupported ground station schema_version: "
            f"{payload['schema_version']!r}"
        )
    return tuple(
        _parse_ground_station(item, index, "ground station data")
        for index, item in enumerate(
            _sequence(payload["ground_stations"], "ground_stations")
        )
    )


def _parse_ground_station(
    value: Any,
    index: int,
    parent: str,
) -> GroundStationConfig:
    context = f"{parent}.ground_stations[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={
            "display_name",
            "latitude_deg",
            "longitude_deg",
            "elevation_m",
            "operator",
            "station_type",
        },
        optional={
            "ground_station_id",
            "identity_key",
            "ix_attachments",
            "max_simultaneous_satellite_accesses",
        },
        context=context,
    )
    return GroundStationConfig(
        display_name=mapping["display_name"],
        latitude_deg=mapping["latitude_deg"],
        longitude_deg=mapping["longitude_deg"],
        elevation_m=mapping["elevation_m"],
        operator=mapping["operator"],
        station_type=mapping["station_type"],
        ground_station_id=mapping.get("ground_station_id"),
        identity_key=mapping.get("identity_key"),
        max_simultaneous_satellite_accesses=mapping.get(
            "max_simultaneous_satellite_accesses"
        ),
        ix_attachments=tuple(
            _parse_ix_attachment(item, attachment_index, context)
            for attachment_index, item in enumerate(
                _sequence(
                    mapping.get("ix_attachments", []),
                    f"{context}.ix_attachments",
                )
            )
        ),
    )


def _parse_ix_attachment(
    value: Any,
    index: int,
    parent: str,
) -> InternetExchangeAttachment:
    context = f"{parent}.ix_attachments[{index}]"
    mapping = _mapping(value, context)
    _fields(mapping, required={"ix_id", "address"}, context=context)
    return InternetExchangeAttachment(
        ix_id=mapping["ix_id"],
        address=mapping["address"],
    )


def _parse_internet_exchange(
    value: Any,
    index: int,
) -> InternetExchangeConfig:
    context = f"internet_exchanges[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"ix_id", "prefix"},
        optional={"route_server"},
        context=context,
    )
    return InternetExchangeConfig(
        ix_id=mapping["ix_id"],
        prefix=mapping["prefix"],
        route_server=mapping.get("route_server", False),
    )


def _parse_ordinary_as(
    value: Any,
    index: int,
) -> OrdinaryAutonomousSystemConfig:
    context = f"ordinary_autonomous_systems[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"asn", "networks", "routers"},
        context=context,
    )
    return OrdinaryAutonomousSystemConfig(
        asn=mapping["asn"],
        networks=tuple(
            _parse_ordinary_network(item, network_index, context)
            for network_index, item in enumerate(
                _sequence(mapping["networks"], f"{context}.networks")
            )
        ),
        routers=tuple(
            _parse_ordinary_router(item, router_index, context)
            for router_index, item in enumerate(
                _sequence(mapping["routers"], f"{context}.routers")
            )
        ),
    )


def _parse_ordinary_network(
    value: Any,
    index: int,
    parent: str,
) -> NetworkConfig:
    context = f"{parent}.networks[{index}]"
    mapping = _mapping(value, context)
    _fields(mapping, required={"network_id", "prefix"}, context=context)
    return NetworkConfig(
        network_id=mapping["network_id"],
        prefix=mapping["prefix"],
    )


def _parse_ordinary_router(
    value: Any,
    index: int,
    parent: str,
) -> RouterConfig:
    context = f"{parent}.routers[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"router_id"},
        optional={"network_attachments", "ix_attachments"},
        context=context,
    )
    return RouterConfig(
        router_id=mapping["router_id"],
        network_attachments=tuple(
            _parse_local_attachment(item, attachment_index, context)
            for attachment_index, item in enumerate(
                _sequence(
                    mapping.get("network_attachments", []),
                    f"{context}.network_attachments",
                )
            )
        ),
        ix_attachments=tuple(
            _parse_ix_attachment(item, attachment_index, context)
            for attachment_index, item in enumerate(
                _sequence(
                    mapping.get("ix_attachments", []),
                    f"{context}.ix_attachments",
                )
            )
        ),
    )


def _parse_local_attachment(
    value: Any,
    index: int,
    parent: str,
) -> LocalNetworkAttachment:
    context = f"{parent}.network_attachments[{index}]"
    mapping = _mapping(value, context)
    _fields(mapping, required={"network_id", "address"}, context=context)
    return LocalNetworkAttachment(
        network_id=mapping["network_id"],
        address=mapping["address"],
    )


def _parse_ebgp_peering(
    value: Any,
    index: int,
) -> EbgpPeeringConfig:
    context = f"ebgp_peerings[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"ix_id", "first", "second"},
        optional={"relationship"},
        context=context,
    )
    return EbgpPeeringConfig(
        ix_id=mapping["ix_id"],
        peer_routers=(
            _parse_ebgp_peer(mapping["first"], f"{context}.first"),
            _parse_ebgp_peer(mapping["second"], f"{context}.second"),
        ),
        relationship=mapping.get("relationship", "unfiltered"),
    )


def _parse_ebgp_peer(
    value: Any,
    context: str,
) -> EbgpRouterReference:
    mapping = _mapping(value, context)
    _fields(mapping, required={"asn", "router_id"}, context=context)
    return EbgpRouterReference(
        asn=mapping["asn"],
        router_id=mapping["router_id"],
    )
