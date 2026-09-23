"""Assemble scene declarations and resolve their local input references."""
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
from ..static_emulator_config import SkyseedAutonomousSystemConfig, StaticEmulatorConfig
from ..ground_station import GroundStationConfig
from ._errors import StaticEmulatorConfigLoadError
from .satellite_layout import _load_shells_file, _parse_shell
from .ground_network import (_load_ground_stations_file, _parse_ground_station,
    _parse_internet_exchange, _parse_ordinary_as, _parse_ebgp_peering)

SCENARIO_SCHEMA_VERSION = "skyseed-static-emulator/v1"

def load_static_emulator_config(path: Path) -> StaticEmulatorConfig:
    """Read YAML, validate its schema/fields/paths and return one Config."""

    try:
        return _load_static_emulator_config(path)
    except StaticEmulatorConfigLoadError:
        raise
    except _ConfigurationYamlError as error:
        raise StaticEmulatorConfigLoadError(str(error)) from error


def _load_static_emulator_config(path: Path) -> StaticEmulatorConfig:
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
        optional={
            "selected_shell",
            "shells",
            "shells_file",
            "ground_stations",
            "ground_stations_file",
        },
        context=context,
    )
    if "ground_stations" in mapping and "ground_stations_file" in mapping:
        raise StaticEmulatorConfigLoadError(
            f"{context} must not specify both ground_stations and "
            "ground_stations_file"
        )
    if "shells" in mapping and "shells_file" in mapping:
        raise StaticEmulatorConfigLoadError(
            f"{context} must not specify both shells and shells_file"
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
    parsed_shells = (
        _load_shells_file(mapping["shells_file"], root, context)
        if "shells_file" in mapping
        else tuple(
            _parse_shell(item, shell_index, context)
            for shell_index, item in enumerate(
                _sequence(mapping.get("shells", []), f"{context}.shells")
            )
        )
    )
    selected_shell = mapping.get("selected_shell")
    if selected_shell is not None:
        if not isinstance(selected_shell, str) or not selected_shell.strip():
            raise StaticEmulatorConfigLoadError(
                f"{context}.selected_shell must be a non-empty shell_id"
            )
        selected = tuple(
            shell for shell in parsed_shells
            if shell.shell_id == selected_shell
        )
        if len(selected) != 1:
            raise StaticEmulatorConfigLoadError(
                f"{context}.selected_shell must match exactly one configured shell"
            )
        parsed_shells = selected
    return SkyseedAutonomousSystemConfig(
        asn=mapping["asn"],
        shells=parsed_shells,
        ground_stations=stations,
    )
