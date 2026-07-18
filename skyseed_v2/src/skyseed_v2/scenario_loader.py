"""Strict loader for versioned, v2-owned static Emulator scenario files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yaml import YAMLError
from yaml.constructor import ConstructorError
from yaml.loader import SafeLoader
from yaml.nodes import MappingNode

from skyseed_v2.specifications import (
    EbgpPeeringSpecification,
    EbgpRouterReferenceSpecification,
    GroundStationSpecification,
    InOrbitPhaseDistributionSpecification,
    InternetExchangeAttachmentSpecification,
    InternetExchangeSpecification,
    LocalNetworkAttachmentSpecification,
    OrbitGenerationSpecification,
    OrbitSpecification,
    OrdinaryAutonomousSystemSpecification,
    OrdinaryNetworkSpecification,
    OrdinaryRouterSpecification,
    RaanDistributionSpecification,
    SatelliteSpecification,
    ShellSpecification,
    SkyseedAutonomousSystemSpecification,
    StaticEmulatorScenarioSpecification,
)


SCENARIO_SCHEMA_VERSION = "skyseed-static-emulator/v1"
GROUND_STATIONS_SCHEMA_VERSION = "skyseed-ground-stations/v1"

__all__ = [
    "GROUND_STATIONS_SCHEMA_VERSION",
    "SCENARIO_SCHEMA_VERSION",
    "StaticEmulatorScenarioLoadError",
    "StaticEmulatorScenarioLoader",
]


class StaticEmulatorScenarioLoadError(ValueError):
    """Report fail-closed file parsing or specification validation failure.

    Layer: serialization adapter error. The Loader creates it and file-input
    callers catch it. It is not a domain error, specification or runtime state;
    it is public from this submodule as the stable Loader failure boundary.
    """


class _UniqueKeySafeLoader(SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
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


_UniqueKeySafeLoader.add_constructor(
    "tag:yaml.org,2002:map",
    _construct_unique_mapping,
)


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise StaticEmulatorScenarioLoadError(
            f"{context} must be a mapping with string keys"
        )
    return value


def _sequence(value: Any, context: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise StaticEmulatorScenarioLoadError(f"{context} must be a sequence")
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
        raise StaticEmulatorScenarioLoadError(
            f"{context} is missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise StaticEmulatorScenarioLoadError(
            f"{context} contains unknown fields: {sorted(unknown)}"
        )


class StaticEmulatorScenarioLoader:
    """Parse one static v2 file into immutable validated specifications.

    Layer: serialization adapter. File-driven callers create it and receive a
    `StaticEmulatorScenarioSpecification`; the specification Applier is its
    long-term downstream consumer. It is not an entity factory, Applier,
    Registry adapter or Compiler. It is public from this submodule because
    file input needs a stable fail-closed boundary.
    """

    def load(self, scenario_file: Path) -> StaticEmulatorScenarioSpecification:
        path = Path(scenario_file).resolve()
        root = path.parent
        payload = self._read_document(path, "scenario")
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
            raise StaticEmulatorScenarioLoadError(
                "unsupported scenario schema_version: "
                f"{payload['schema_version']!r}"
            )
        try:
            return StaticEmulatorScenarioSpecification(
                skyseed_autonomous_systems=tuple(
                    self._parse_skyseed_as(value, root, index)
                    for index, value in enumerate(
                        _sequence(
                            payload["skyseed_autonomous_systems"],
                            "skyseed_autonomous_systems",
                        )
                    )
                ),
                internet_exchanges=tuple(
                    self._parse_internet_exchange(value, index)
                    for index, value in enumerate(
                        _sequence(
                            payload.get("internet_exchanges", []),
                            "internet_exchanges",
                        )
                    )
                ),
                ordinary_autonomous_systems=tuple(
                    self._parse_ordinary_as(value, index)
                    for index, value in enumerate(
                        _sequence(
                            payload.get("ordinary_autonomous_systems", []),
                            "ordinary_autonomous_systems",
                        )
                    )
                ),
                ebgp_peerings=tuple(
                    self._parse_ebgp_peering(value, index)
                    for index, value in enumerate(
                        _sequence(payload.get("ebgp_peerings", []), "ebgp_peerings")
                    )
                ),
            )
        except StaticEmulatorScenarioLoadError:
            raise
        except (TypeError, ValueError) as error:
            raise StaticEmulatorScenarioLoadError(
                f"scenario validation failed: {error}"
            ) from error

    @staticmethod
    def _read_document(path: Path, context: str) -> dict[str, Any]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise StaticEmulatorScenarioLoadError(
                f"cannot read {context} file: {path}"
            ) from error
        try:
            payload = _UniqueKeySafeLoader(content).get_single_data()
        except YAMLError as error:
            raise StaticEmulatorScenarioLoadError(
                f"{context} file is not valid YAML: {path}"
            ) from error
        return _mapping(payload, context)

    def _parse_skyseed_as(
        self,
        value: Any,
        root: Path,
        index: int,
    ) -> SkyseedAutonomousSystemSpecification:
        context = f"skyseed_autonomous_systems[{index}]"
        mapping = _mapping(value, context)
        _fields(
            mapping,
            required={"asn"},
            optional={"shells", "ground_stations", "ground_stations_file"},
            context=context,
        )
        if "ground_stations" in mapping and "ground_stations_file" in mapping:
            raise StaticEmulatorScenarioLoadError(
                f"{context} must not specify both ground_stations and "
                "ground_stations_file"
            )
        stations: tuple[GroundStationSpecification, ...]
        if "ground_stations_file" in mapping:
            stations = self._load_ground_stations_file(
                mapping["ground_stations_file"], root, context
            )
        else:
            stations = tuple(
                self._parse_ground_station(item, station_index, context)
                for station_index, item in enumerate(
                    _sequence(
                        mapping.get("ground_stations", []),
                        f"{context}.ground_stations",
                    )
                )
            )
        return SkyseedAutonomousSystemSpecification(
            asn=mapping["asn"],
            shells=tuple(
                self._parse_shell(item, shell_index, context)
                for shell_index, item in enumerate(
                    _sequence(mapping.get("shells", []), f"{context}.shells")
                )
            ),
            ground_stations=stations,
        )

    def _load_ground_stations_file(
        self,
        raw_reference: Any,
        root: Path,
        context: str,
    ) -> tuple[GroundStationSpecification, ...]:
        if not isinstance(raw_reference, str) or not raw_reference.strip():
            raise StaticEmulatorScenarioLoadError(
                f"{context}.ground_stations_file must be a relative path"
            )
        reference = Path(raw_reference)
        if reference.is_absolute() or ".." in reference.parts:
            raise StaticEmulatorScenarioLoadError(
                f"{context}.ground_stations_file must stay within the example root"
            )
        path = (root / reference).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise StaticEmulatorScenarioLoadError(
                f"{context}.ground_stations_file resolves outside the example root"
            ) from error
        payload = self._read_document(path, "ground station data")
        _fields(
            payload,
            required={"schema_version", "ground_stations"},
            context="ground station data",
        )
        if payload["schema_version"] != GROUND_STATIONS_SCHEMA_VERSION:
            raise StaticEmulatorScenarioLoadError(
                "unsupported ground station schema_version: "
                f"{payload['schema_version']!r}"
            )
        return tuple(
            self._parse_ground_station(item, index, "ground station data")
            for index, item in enumerate(
                _sequence(payload["ground_stations"], "ground_stations")
            )
        )

    def _parse_shell(
        self,
        value: Any,
        index: int,
        parent: str,
    ) -> ShellSpecification:
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
            raise StaticEmulatorScenarioLoadError(
                f"{context} must specify exactly one of generation or orbits"
            )
        return ShellSpecification(
            shell_id=mapping["shell_id"],
            altitude_km=mapping["altitude_km"],
            inclination_deg=mapping["inclination_deg"],
            generation=(
                self._parse_generation(mapping["generation"], context)
                if has_generation
                else None
            ),
            orbits=(
                tuple(
                    self._parse_orbit(item, orbit_index, context)
                    for orbit_index, item in enumerate(
                        _sequence(mapping["orbits"], f"{context}.orbits")
                    )
                )
                if has_orbits
                else ()
            ),
        )

    @staticmethod
    def _parse_generation(value: Any, parent: str) -> OrbitGenerationSpecification:
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
        return OrbitGenerationSpecification(
            orbit_count=mapping["orbit_count"],
            satellites_per_orbit=mapping["satellites_per_orbit"],
            raan=RaanDistributionSpecification(
                start_deg=raan["start_deg"],
                step_deg=raan["step_deg"],
            ),
            phase=InOrbitPhaseDistributionSpecification(
                start_deg=phase["start_deg"],
                step_deg=phase["step_deg"],
                alternating_orbit_offset_deg=phase[
                    "alternating_orbit_offset_deg"
                ],
            ),
        )

    def _parse_orbit(
        self,
        value: Any,
        index: int,
        parent: str,
    ) -> OrbitSpecification:
        context = f"{parent}.orbits[{index}]"
        mapping = _mapping(value, context)
        _fields(
            mapping,
            required={"orbit_id", "right_ascension_deg", "satellites"},
            context=context,
        )
        return OrbitSpecification(
            orbit_id=mapping["orbit_id"],
            right_ascension_deg=mapping["right_ascension_deg"],
            satellites=tuple(
                self._parse_satellite(item, satellite_index, context)
                for satellite_index, item in enumerate(
                    _sequence(mapping["satellites"], f"{context}.satellites")
                )
            ),
        )

    @staticmethod
    def _parse_satellite(
        value: Any,
        index: int,
        parent: str,
    ) -> SatelliteSpecification:
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
            raise StaticEmulatorScenarioLoadError(
                f"{context}.payload must contain only string keys and values"
            )
        return SatelliteSpecification(
            satellite_id=mapping["satellite_id"],
            index_in_orbit=mapping["index_in_orbit"],
            mean_anomaly_deg=mapping["mean_anomaly_deg"],
            payload_items=tuple(payload.items()),
        )

    def _parse_ground_station(
        self,
        value: Any,
        index: int,
        parent: str,
    ) -> GroundStationSpecification:
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
        return GroundStationSpecification(
            display_name=mapping["display_name"],
            latitude_deg=mapping["latitude_deg"],
            longitude_deg=mapping["longitude_deg"],
            elevation_m=mapping["elevation_m"],
            operator=mapping["operator"],
            station_type=mapping["station_type"],
            ground_station_id=mapping.get("ground_station_id"),
            identity_key=mapping.get("identity_key"),
            ix_attachments=tuple(
                self._parse_ix_attachment(item, attachment_index, context)
                for attachment_index, item in enumerate(
                    _sequence(
                        mapping.get("ix_attachments", []),
                        f"{context}.ix_attachments",
                    )
                )
            ),
        )

    @staticmethod
    def _parse_ix_attachment(
        value: Any,
        index: int,
        parent: str,
    ) -> InternetExchangeAttachmentSpecification:
        context = f"{parent}.ix_attachments[{index}]"
        mapping = _mapping(value, context)
        _fields(mapping, required={"ix_id", "address"}, context=context)
        return InternetExchangeAttachmentSpecification(
            ix_id=mapping["ix_id"],
            address=mapping["address"],
        )

    @staticmethod
    def _parse_internet_exchange(
        value: Any,
        index: int,
    ) -> InternetExchangeSpecification:
        context = f"internet_exchanges[{index}]"
        mapping = _mapping(value, context)
        _fields(
            mapping,
            required={"ix_id", "prefix"},
            optional={"route_server"},
            context=context,
        )
        return InternetExchangeSpecification(
            ix_id=mapping["ix_id"],
            prefix=mapping["prefix"],
            route_server=mapping.get("route_server", False),
        )

    def _parse_ordinary_as(
        self,
        value: Any,
        index: int,
    ) -> OrdinaryAutonomousSystemSpecification:
        context = f"ordinary_autonomous_systems[{index}]"
        mapping = _mapping(value, context)
        _fields(
            mapping,
            required={"asn", "networks", "routers"},
            context=context,
        )
        return OrdinaryAutonomousSystemSpecification(
            asn=mapping["asn"],
            networks=tuple(
                self._parse_ordinary_network(item, network_index, context)
                for network_index, item in enumerate(
                    _sequence(mapping["networks"], f"{context}.networks")
                )
            ),
            routers=tuple(
                self._parse_ordinary_router(item, router_index, context)
                for router_index, item in enumerate(
                    _sequence(mapping["routers"], f"{context}.routers")
                )
            ),
        )

    @staticmethod
    def _parse_ordinary_network(
        value: Any,
        index: int,
        parent: str,
    ) -> OrdinaryNetworkSpecification:
        context = f"{parent}.networks[{index}]"
        mapping = _mapping(value, context)
        _fields(mapping, required={"network_id", "prefix"}, context=context)
        return OrdinaryNetworkSpecification(
            network_id=mapping["network_id"],
            prefix=mapping["prefix"],
        )

    def _parse_ordinary_router(
        self,
        value: Any,
        index: int,
        parent: str,
    ) -> OrdinaryRouterSpecification:
        context = f"{parent}.routers[{index}]"
        mapping = _mapping(value, context)
        _fields(
            mapping,
            required={"router_id"},
            optional={"network_attachments", "ix_attachments"},
            context=context,
        )
        return OrdinaryRouterSpecification(
            router_id=mapping["router_id"],
            network_attachments=tuple(
                self._parse_local_attachment(item, attachment_index, context)
                for attachment_index, item in enumerate(
                    _sequence(
                        mapping.get("network_attachments", []),
                        f"{context}.network_attachments",
                    )
                )
            ),
            ix_attachments=tuple(
                self._parse_ix_attachment(item, attachment_index, context)
                for attachment_index, item in enumerate(
                    _sequence(
                        mapping.get("ix_attachments", []),
                        f"{context}.ix_attachments",
                    )
                )
            ),
        )

    @staticmethod
    def _parse_local_attachment(
        value: Any,
        index: int,
        parent: str,
    ) -> LocalNetworkAttachmentSpecification:
        context = f"{parent}.network_attachments[{index}]"
        mapping = _mapping(value, context)
        _fields(mapping, required={"network_id", "address"}, context=context)
        return LocalNetworkAttachmentSpecification(
            network_id=mapping["network_id"],
            address=mapping["address"],
        )

    def _parse_ebgp_peering(
        self,
        value: Any,
        index: int,
    ) -> EbgpPeeringSpecification:
        context = f"ebgp_peerings[{index}]"
        mapping = _mapping(value, context)
        _fields(
            mapping,
            required={"ix_id", "first", "second"},
            optional={"relationship"},
            context=context,
        )
        return EbgpPeeringSpecification(
            ix_id=mapping["ix_id"],
            first=self._parse_ebgp_peer(mapping["first"], f"{context}.first"),
            second=self._parse_ebgp_peer(mapping["second"], f"{context}.second"),
            relationship=mapping.get("relationship", "unfiltered"),
        )

    @staticmethod
    def _parse_ebgp_peer(
        value: Any,
        context: str,
    ) -> EbgpRouterReferenceSpecification:
        mapping = _mapping(value, context)
        _fields(mapping, required={"asn", "router_id"}, context=context)
        return EbgpRouterReferenceSpecification(
            asn=mapping["asn"],
            router_id=mapping["router_id"],
        )
