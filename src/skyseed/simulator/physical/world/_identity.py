"""Canonical static-world identity material, evaluated once at publication."""
from __future__ import annotations
from collections.abc import Mapping as MappingABC
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import TYPE_CHECKING
from skyseed.shared import NodeId
from skyseed.shared.time import normalize_utc_datetime
if TYPE_CHECKING:
    from .entities import PhysicalSatellite, PhysicalGroundStation, PhysicalAutonomousSystem
    from .zone import ZoneLayout
    from ..models.position import PositionModel

def _physical_world_semantic_fingerprint(
    autonomous_systems: tuple[PhysicalAutonomousSystem, ...],
    zone_layout: ZoneLayout | None,
    position_model: PositionModel,
) -> str:
    """Hash the sole canonical material for immutable PhysicalWorld identity."""

    material = {
        "schema": "skyseed-v2-physical-world-v1",
        "autonomous_systems": [
            {
                "asn": autonomous_system.asn,
                "shells": [
                    {
                        "shell_id": shell.shell_id,
                        "orbits": [
                            {
                                "orbit_id": orbit.orbit_id,
                                "altitude_km": _float_material(
                                    orbit.altitude_km
                                ),
                                "inclination_deg": _float_material(
                                    orbit.inclination_deg
                                ),
                                "right_ascension_deg": _float_material(
                                    orbit.right_ascension_deg
                                ),
                                "epoch_utc": orbit.epoch_utc.isoformat(),
                                "satellites": [
                                    _satellite_fingerprint_material(satellite)
                                    for satellite in sorted(
                                        orbit.satellites,
                                        key=lambda value: _node_sort_key(
                                            value.node_id
                                        ),
                                    )
                                ],
                            }
                            for orbit in sorted(
                                shell.orbits,
                                key=lambda value: value.orbit_id,
                            )
                        ],
                    }
                    for shell in sorted(
                        autonomous_system.shells,
                        key=lambda value: value.shell_id,
                    )
                ],
                "ground_stations": [
                    _ground_station_fingerprint_material(value)
                    for value in sorted(
                        autonomous_system.ground_stations,
                        key=lambda value: _node_sort_key(value.node_id),
                    )
                ],
            }
            for autonomous_system in sorted(
                autonomous_systems, key=lambda value: value.asn
            )
        ],
        "zone_layout": _zone_layout_fingerprint_material(zone_layout),
        "position_model": _model_fingerprint_material(position_model),
    }
    payload = json.dumps(
        material,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _satellite_fingerprint_material(value: PhysicalSatellite) -> dict[str, object]:
    capability = value.inter_satellite_link_capability
    return {
        "node_id": _node_fingerprint_material(value.node_id),
        "orbit_index": value.orbit_index,
        "mean_anomaly_deg": _float_material(value.mean_anomaly_deg),
        "max_simultaneous_ground_station_accesses": (
            value.max_simultaneous_ground_station_accesses
        ),
        "maximum_simultaneous_inter_satellite_links": (
            None
            if capability is None
            else capability.maximum_simultaneous_links
        ),
    }


def _ground_station_fingerprint_material(
    value: PhysicalGroundStation,
) -> dict[str, object]:
    return {
        "node_id": _node_fingerprint_material(value.node_id),
        "latitude_deg": _float_material(value.latitude_deg),
        "longitude_deg": _float_material(value.longitude_deg),
        "elevation_m": _float_material(value.elevation_m),
        "station_type": value.station_type,
        "operator": value.operator,
        "max_simultaneous_satellite_accesses": (
            value.max_simultaneous_satellite_accesses
        ),
    }


def _zone_layout_fingerprint_material(
    value: ZoneLayout | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "allow_overlap": value.allow_overlap,
        "zones": [
            {
                "zone_id": zone.zone_id,
                "priority": zone.priority,
                "geometry": {
                    "south_latitude_deg": _float_material(
                        zone.geometry.south_latitude_deg
                    ),
                    "north_latitude_deg": _float_material(
                        zone.geometry.north_latitude_deg
                    ),
                    "west_longitude_deg": _float_material(
                        zone.geometry.west_longitude_deg
                    ),
                    "east_longitude_deg": _float_material(
                        zone.geometry.east_longitude_deg
                    ),
                },
            }
            for zone in sorted(value.zones, key=lambda item: item.zone_id)
        ],
        "membership_model": _model_fingerprint_material(
            value.get_membership_model()
        ),
    }


def _model_fingerprint_material(value: object) -> dict[str, object]:
    """Use only type identity and explicitly declared semantic data.

    Same-type Models without this optional capability have indistinguishable
    configuration. Private fields and provenance descriptions are not inputs.
    """
    model_type = type(value)
    identity = f"{model_type.__module__}.{model_type.__qualname__}"
    explicit = getattr(value, "semantic_fingerprint_material", None)
    raw_state: object = {}
    if explicit is not None:
        if not callable(explicit):
            raise TypeError(
                "semantic_fingerprint_material must be callable when present"
            )
        raw_state = explicit()
    return {
        "type": identity,
        "state": _semantic_model_value(raw_state, {id(value)}),
    }


def _semantic_model_value(value: object, active: set[int]) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Model fingerprint values must be finite")
        return _float_material(value)
    if isinstance(value, datetime):
        return {"datetime": normalize_utc_datetime(value, "value").isoformat()}
    if isinstance(value, timedelta):
        return {
            "timedelta": {
                "days": value.days,
                "seconds": value.seconds,
                "microseconds": value.microseconds,
            }
        }
    if isinstance(value, NodeId):
        return {"node_id": _node_fingerprint_material(value)}
    if isinstance(value, Enum):
        enum_type = type(value)
        return {
            "enum": f"{enum_type.__module__}.{enum_type.__qualname__}",
            "value": _semantic_model_value(value.value, active),
        }
    identity = id(value)
    if identity in active:
        raise ValueError("Model fingerprint configuration must be acyclic")
    active.add(identity)
    try:
        if isinstance(value, MappingABC):
            pairs = [
                (
                    _semantic_model_value(key, active),
                    _semantic_model_value(item, active),
                )
                for key, item in value.items()
            ]
            pairs.sort(key=lambda pair: _canonical_json(pair[0]))
            return {
                "mapping": [
                    {"key": key, "value": item} for key, item in pairs
                ]
            }
        if isinstance(value, (tuple, list)):
            return {
                "sequence": [
                    _semantic_model_value(item, active) for item in value
                ]
            }
        if isinstance(value, (set, frozenset)):
            items = [
                _semantic_model_value(item, active) for item in value
            ]
            items.sort(key=_canonical_json)
            return {"set": items}
        if is_dataclass(value) and not isinstance(value, type):
            value_type = type(value)
            return {
                "dataclass": (
                    f"{value_type.__module__}.{value_type.__qualname__}"
                ),
                "fields": {
                    item.name: _semantic_model_value(
                        getattr(value, item.name), active
                    )
                    for item in fields(value)
                },
            }
        if callable(value):
            raise TypeError(
                "Model fingerprint configuration cannot contain callables"
            )
        value_type = type(value)
        raise TypeError(
            "Model fingerprint configuration contains unsupported value type "
            f"{value_type.__module__}.{value_type.__qualname__}; "
            "supply explicit data values"
        )
    finally:
        active.remove(identity)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _float_material(value: float) -> str:
    converted = float(value)
    if not isfinite(converted):
        raise ValueError("PhysicalWorld fingerprint values must be finite")
    return converted.hex()


def _node_fingerprint_material(value: NodeId) -> dict[str, object]:
    return {
        "asn": value.asn,
        "entity_kind": value.entity_kind,
        "entity_id": value.entity_id,
    }


def _node_sort_key(value: NodeId) -> tuple[int, str, str]:
    return value.asn, value.entity_kind, value.entity_id
