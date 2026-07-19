"""Read versioned YAML into immutable static Emulator Config values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yaml import YAMLError
from yaml.constructor import ConstructorError
from yaml.loader import SafeLoader
from yaml.nodes import MappingNode

from .attachments import (
    InternetExchangeAttachment,
    LocalNetworkAttachment,
)
from .ground_network import (
    EbgpPeeringConfig,
    EbgpRouterReference,
    InternetExchangeConfig,
    NetworkConfig,
    OrdinaryAutonomousSystemConfig,
    RouterConfig,
)
from .ground_station import GroundStationConfig
from .satellite import (
    GeneratedOrbitLayout,
    InOrbitPhaseDistribution,
    OrbitConfig,
    RightAscensionDistribution,
    SatelliteConfig,
    ShellConfig,
)
from .static_emulator import (
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
)


SCENARIO_SCHEMA_VERSION = "skyseed-static-emulator/v1"
GROUND_STATIONS_SCHEMA_VERSION = "skyseed-ground-stations/v1"

__all__ = [
    "GROUND_STATIONS_SCHEMA_VERSION",
    "SCENARIO_SCHEMA_VERSION",
    "StaticEmulatorConfigLoadError",
    "load_static_emulator_config",
]


class StaticEmulatorConfigLoadError(ValueError):
    """Report fail-closed file parsing or Config validation failure.

    Layer: configuration-read error. `load_static_emulator_config` creates it
    and file-input callers catch it. It is not an entity error, Config value or
    runtime state; it is public from configuration as the stable file boundary.
    """


class _UniqueKeyYamlReader(SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyYamlReader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyYamlReader.add_constructor(
    "tag:yaml.org,2002:map",
    _construct_unique_mapping,
)


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise StaticEmulatorConfigLoadError(
            f"{context} must be a mapping with string keys"
        )
    return value


def _sequence(value: Any, context: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise StaticEmulatorConfigLoadError(f"{context} must be a sequence")
    return tuple(value)


def _fields(
    mapping: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    context: str,
) -> None:
    missing = required - set(mapping)
    unknown = set(mapping) - required - optional
    if missing:
        raise StaticEmulatorConfigLoadError(
            f"{context} is missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise StaticEmulatorConfigLoadError(
            f"{context} contains unknown fields: {sorted(unknown)}"
        )


def load_static_emulator_config(path: Path) -> StaticEmulatorConfig:
    """Read YAML, validate its schema/fields/paths and return one Config."""

    resolved_path = Path(path).resolve()
    root = resolved_path.parent
    payload = _read_document(resolved_path, "scenario")
    _fields(
        payload,
        required={"schema_version", "skyseed_autonomous_systems"},
        optional={
            "internet_exchanges",
            "ordinary_autonomous_systems",
            "ebgp_peerings",
        },
        context="scenario",
    )
    if payload["schema_version"] != SCENARIO_SCHEMA_VERSION:
        raise StaticEmulatorConfigLoadError(
            "unsupported scenario schema_version: "
            f"{payload['schema_version']!r}"
        )
    try:
        return StaticEmulatorConfig(
            skyseed_autonomous_systems=tuple(
                _parse_skyseed_as(value, root, index)
                for index, value in enumerate(
                    _sequence(
                        payload["skyseed_autonomous_systems"],
                        "skyseed_autonomous_systems",
                    )
                )
            ),
            internet_exchanges=tuple(
                _parse_internet_exchange(value, index)
                for index, value in enumerate(
                    _sequence(
                        payload.get("internet_exchanges", []),
                        "internet_exchanges",
                    )
                )
            ),
            ordinary_autonomous_systems=tuple(
                _parse_ordinary_as(value, index)
                for index, value in enumerate(
                    _sequence(
                        payload.get("ordinary_autonomous_systems", []),
                        "ordinary_autonomous_systems",
                    )
                )
            ),
            ebgp_peerings=tuple(
                _parse_ebgp_peering(value, index)
                for index, value in enumerate(
                    _sequence(payload.get("ebgp_peerings", []), "ebgp_peerings")
                )
            ),
        )
    except StaticEmulatorConfigLoadError:
        raise
    except (TypeError, ValueError) as error:
        raise StaticEmulatorConfigLoadError(
            f"scenario validation failed: {error}"
        ) from error

def _read_document(path: Path, context: str) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise StaticEmulatorConfigLoadError(
            f"cannot read {context} file: {path}"
        ) from error
    try:
        payload = _UniqueKeyYamlReader(content).get_single_data()
    except YAMLError as error:
        raise StaticEmulatorConfigLoadError(
            f"{context} file is not valid YAML: {path}"
        ) from error
    return _mapping(payload, context)

def _parse_skyseed_as(
    value: Any,
    root: Path,
    index: int,
) -> SkyseedAutonomousSystemConfig:
    context = f"skyseed_autonomous_systems[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"asn"},
        optional={"shells", "ground_stations", "ground_stations_file"},
        context=context,
    )
    if "ground_stations" in mapping and "ground_stations_file" in mapping:
        raise StaticEmulatorConfigLoadError(
            f"{context} must not specify both ground_stations and "
            "ground_stations_file"
        )
    stations: tuple[GroundStationConfig, ...]
    if "ground_stations_file" in mapping:
        stations = _load_ground_stations_file(
            mapping["ground_stations_file"], root, context
        )
    else:
        stations = tuple(
            _parse_ground_station(item, station_index, context)
            for station_index, item in enumerate(
                _sequence(
                    mapping.get("ground_stations", []),
                    f"{context}.ground_stations",
                )
            )
        )
    return SkyseedAutonomousSystemConfig(
        asn=mapping["asn"],
        shells=tuple(
            _parse_shell(item, shell_index, context)
            for shell_index, item in enumerate(
                _sequence(mapping.get("shells", []), f"{context}.shells")
            )
        ),
        ground_stations=stations,
    )

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
    if reference.is_absolute() or ".." in reference.parts:
        raise StaticEmulatorConfigLoadError(
            f"{context}.ground_stations_file must stay within the example root"
        )
    path = (root / reference).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise StaticEmulatorConfigLoadError(
            f"{context}.ground_stations_file resolves outside the example root"
        ) from error
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

def _parse_shell(
    value: Any,
    index: int,
    parent: str,
) -> ShellConfig:
    context = f"{parent}.shells[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"shell_id", "altitude_km", "inclination_deg"},
        optional={"generation", "orbits"},
        context=context,
    )
    has_generation = "generation" in mapping
    has_orbits = "orbits" in mapping
    if has_generation == has_orbits:
        raise StaticEmulatorConfigLoadError(
            f"{context} must specify exactly one of generation or orbits"
        )
    return ShellConfig(
        shell_id=mapping["shell_id"],
        altitude_km=mapping["altitude_km"],
        inclination_deg=mapping["inclination_deg"],
        generated_orbit_layout=(
            _parse_generation(mapping["generation"], context)
            if has_generation
            else None
        ),
        explicit_orbits=(
            tuple(
                _parse_orbit(item, orbit_index, context)
                for orbit_index, item in enumerate(
                    _sequence(mapping["orbits"], f"{context}.orbits")
                )
            )
            if has_orbits
            else ()
        ),
    )

def _parse_generation(value: Any, parent: str) -> GeneratedOrbitLayout:
    context = f"{parent}.generation"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"orbit_count", "satellites_per_orbit", "raan", "phase"},
        context=context,
    )
    raan = _mapping(mapping["raan"], f"{context}.raan")
    _fields(
        raan,
        required={"start_deg", "step_deg"},
        context=f"{context}.raan",
    )
    phase = _mapping(mapping["phase"], f"{context}.phase")
    _fields(
        phase,
        required={
            "start_deg",
            "step_deg",
            "alternating_orbit_offset_deg",
        },
        context=f"{context}.phase",
    )
    return GeneratedOrbitLayout(
        orbit_count=mapping["orbit_count"],
        satellites_per_orbit=mapping["satellites_per_orbit"],
        right_ascension_distribution=RightAscensionDistribution(
            start_deg=raan["start_deg"],
            step_deg=raan["step_deg"],
        ),
        in_orbit_phase_distribution=InOrbitPhaseDistribution(
            start_deg=phase["start_deg"],
            step_deg=phase["step_deg"],
            alternating_orbit_offset_deg=phase[
                "alternating_orbit_offset_deg"
            ],
        ),
    )

def _parse_orbit(
    value: Any,
    index: int,
    parent: str,
) -> OrbitConfig:
    context = f"{parent}.orbits[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"orbit_id", "right_ascension_deg", "satellites"},
        context=context,
    )
    return OrbitConfig(
        orbit_id=mapping["orbit_id"],
        right_ascension_deg=mapping["right_ascension_deg"],
        satellites=tuple(
            _parse_satellite(item, satellite_index, context)
            for satellite_index, item in enumerate(
                _sequence(mapping["satellites"], f"{context}.satellites")
            )
        ),
    )

def _parse_satellite(
    value: Any,
    index: int,
    parent: str,
) -> SatelliteConfig:
    context = f"{parent}.satellites[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"satellite_id", "index_in_orbit", "mean_anomaly_deg"},
        optional={"payload"},
        context=context,
    )
    payload = _mapping(mapping.get("payload", {}), f"{context}.payload")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in payload.items()
    ):
        raise StaticEmulatorConfigLoadError(
            f"{context}.payload must contain only string keys and values"
        )
    return SatelliteConfig(
        satellite_id=mapping["satellite_id"],
        index_in_orbit=mapping["index_in_orbit"],
        mean_anomaly_deg=mapping["mean_anomaly_deg"],
        payload_items=tuple(payload.items()),
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
        optional={"ground_station_id", "identity_key", "ix_attachments"},
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
