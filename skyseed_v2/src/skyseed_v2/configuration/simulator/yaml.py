"""Read YAML into immutable Simulator configuration values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._yaml import (
    _ConfigurationYamlError,
    fields as _fields,
    mapping as _mapping,
    read_document as _read_document,
    sequence as _sequence,
)
from .zone import (
    LatitudeLongitudeRectangleDefinition,
    ZoneDefinition,
    ZoneLayoutDefinition,
)


class ZoneLayoutDefinitionLoadError(ValueError):
    """Report fail-closed Simulator Zone-layout loading failure.

    Layer: Simulator configuration-read error. The Zone-layout YAML loader
    creates it and its file-input callers catch it. It is not an Emulator
    configuration error, runtime Zone lookup failure, or planning state
    and is public from ``skyseed_v2.configuration.simulator``.
    """


def load_zone_layout_definition(path: Path) -> ZoneLayoutDefinition:
    """Read one Zone layout YAML file into its immutable Definition."""

    try:
        return _load_zone_layout_definition(path)
    except ZoneLayoutDefinitionLoadError:
        raise
    except _ConfigurationYamlError as error:
        raise ZoneLayoutDefinitionLoadError(str(error)) from error


def _load_zone_layout_definition(path: Path) -> ZoneLayoutDefinition:
    payload = _read_document(Path(path).resolve(), "zone layout")
    _fields(
        payload,
        required={"allow_overlap", "zones"},
        context="zone layout",
    )
    try:
        zones = tuple(
            _parse_zone_definition(value, index)
            for index, value in enumerate(
                _sequence(payload["zones"], "zone layout.zones")
            )
        )
        return ZoneLayoutDefinition(
            zones=zones,
            allow_overlap=payload["allow_overlap"],
        )
    except ZoneLayoutDefinitionLoadError:
        raise
    except (TypeError, ValueError) as error:
        raise ZoneLayoutDefinitionLoadError(
            f"zone layout validation failed: {error}"
        ) from error


def _parse_zone_definition(value: Any, index: int) -> ZoneDefinition:
    context = f"zone layout.zones[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"zone_id", "geometry"},
        optional={"priority"},
        context=context,
    )
    geometry_context = f"{context}.geometry"
    geometry = _mapping(mapping["geometry"], geometry_context)
    _fields(
        geometry,
        required={
            "kind",
            "south_latitude_deg",
            "north_latitude_deg",
            "west_longitude_deg",
            "east_longitude_deg",
        },
        context=geometry_context,
    )
    if geometry["kind"] != "latitude-longitude-rectangle":
        raise ZoneLayoutDefinitionLoadError(
            f"{geometry_context}.kind is unsupported: {geometry['kind']!r}"
        )
    return ZoneDefinition(
        zone_id=mapping["zone_id"],
        priority=mapping.get("priority", 0),
        geometry=LatitudeLongitudeRectangleDefinition(
            south_latitude_deg=geometry["south_latitude_deg"],
            north_latitude_deg=geometry["north_latitude_deg"],
            west_longitude_deg=geometry["west_longitude_deg"],
            east_longitude_deg=geometry["east_longitude_deg"],
        ),
    )


__all__ = [
    "ZoneLayoutDefinitionLoadError",
    "load_zone_layout_definition",
]
