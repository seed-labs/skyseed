#!/usr/bin/env python3
# encoding: utf-8

"""UT and gateway catalog helpers for Starlink shell previews."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence


DEFAULT_UT_CATALOG: Sequence[Dict[str, Any]] = (
    {
        "ut_id": "ut1",
        "ut_index": 1,
        "display_name": "Northern Test UT",
        "zone_id": "zone-north",
        "region": "north",
        "latitude_deg": 64.1466,
        "longitude_deg": -21.9426,
        "altitude_km": 0.0,
        "source": "builtin_preview_catalog",
        "type": "user_defined",
    },
    {
        "ut_id": "ut2",
        "ut_index": 2,
        "display_name": "Southern Test UT",
        "zone_id": "zone-south",
        "region": "south",
        "latitude_deg": -41.2866,
        "longitude_deg": 174.7756,
        "altitude_km": 0.0,
        "source": "builtin_preview_catalog",
        "type": "user_defined",
    },
    {
        "ut_id": "ut3",
        "ut_index": 3,
        "display_name": "Atlantic Test UT",
        "zone_id": "zone-atlantic",
        "region": "atlantic",
        "latitude_deg": 32.3078,
        "longitude_deg": -64.7505,
        "altitude_km": 0.0,
        "source": "builtin_preview_catalog",
        "type": "user_defined",
    },
    {
        "ut_id": "ut4",
        "ut_index": 4,
        "display_name": "Mediterranean Test UT",
        "zone_id": "zone-mediterranean",
        "region": "mediterranean",
        "latitude_deg": 35.8989,
        "longitude_deg": 14.5146,
        "altitude_km": 0.0,
        "source": "builtin_preview_catalog",
        "type": "user_defined",
    },
)


DEFAULT_GATEWAY_CATALOG: Sequence[Dict[str, Any]] = (
    {
        "gateway_id": "gw1",
        "gateway_index": 1,
        "name": "Reykjavik Gateway",
        "region": "north",
        "latitude_deg": 64.1466,
        "longitude_deg": -21.9426,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 100,
        "ix_ip_address": "10.100.0.250",
        "upstream_asn": 3,
    },
    {
        "gateway_id": "gw2",
        "gateway_index": 2,
        "name": "Wellington Gateway",
        "region": "south",
        "latitude_deg": -41.2866,
        "longitude_deg": 174.7756,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 101,
        "ix_ip_address": "10.101.0.250",
        "upstream_asn": 12,
    },
    {
        "gateway_id": "gw3",
        "gateway_index": 3,
        "name": "Hamilton Gateway",
        "region": "atlantic-west",
        "latitude_deg": 32.2948,
        "longitude_deg": -64.7814,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 102,
        "ix_ip_address": "10.102.0.250",
        "upstream_asn": 11,
    },
    {
        "gateway_id": "gw4",
        "gateway_index": 4,
        "name": "Valletta Gateway",
        "region": "mediterranean",
        "latitude_deg": 35.8989,
        "longitude_deg": 14.5146,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 103,
        "ix_ip_address": "10.103.0.250",
        "upstream_asn": 3,
    },
    {
        "gateway_id": "gw5",
        "gateway_index": 5,
        "name": "Nuuk Gateway",
        "region": "arctic",
        "latitude_deg": 64.1835,
        "longitude_deg": -51.7216,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 104,
        "ix_ip_address": "10.104.0.250",
        "upstream_asn": 4,
    },
    {
        "gateway_id": "gw6",
        "gateway_index": 6,
        "name": "Ushuaia Gateway",
        "region": "antarctic",
        "latitude_deg": -54.8019,
        "longitude_deg": -68.3030,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 105,
        "ix_ip_address": "10.105.0.250",
        "upstream_asn": 2,
    },
    {
        "gateway_id": "gw7",
        "gateway_index": 7,
        "name": "Seattle Gateway",
        "region": "pacific-northwest",
        "latitude_deg": 47.6062,
        "longitude_deg": -122.3321,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 101,
        "ix_ip_address": "10.101.0.251",
        "upstream_asn": 2,
    },
    {
        "gateway_id": "gw8",
        "gateway_index": 8,
        "name": "Lisbon Gateway",
        "region": "atlantic-east",
        "latitude_deg": 38.7223,
        "longitude_deg": -9.1393,
        "altitude_km": 0.0,
        "type": "manual_backhaul_gateway",
        "source": "builtin_preview_catalog",
        "confidence": "unofficial",
        "unofficial": True,
        "ix_id": 100,
        "ix_ip_address": "10.100.0.251",
        "upstream_asn": 2,
    },
)


DEFAULT_ZONE_CATALOG: Sequence[Dict[str, Any]] = ()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_entries(entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(entry) for entry in entries]


def _validate_required_fields(
    *,
    entries: Sequence[Dict[str, Any]],
    required_fields: Sequence[str],
    entry_label: str,
) -> List[Dict[str, Any]]:
    normalized = _normalize_entries(entries)
    for entry in normalized:
        missing = [field for field in required_fields if field not in entry]
        if missing:
            raise ValueError(
                f"{entry_label} entry is missing required fields {missing}: {entry}"
            )
    return normalized


def load_ut_catalog(path: str | None = None) -> List[Dict[str, Any]]:
    if path is None:
        return _normalize_entries(DEFAULT_UT_CATALOG)
    payload = _load_json(Path(path).resolve())
    if not isinstance(payload, list):
        raise ValueError("UT catalog JSON must be a list of entries")
    return _validate_required_fields(
        entries=payload,
        required_fields=(
            "ut_id",
            "ut_index",
            "display_name",
            "zone_id",
            "region",
            "latitude_deg",
            "longitude_deg",
        ),
        entry_label="UT catalog",
    )


def load_gateway_catalog(path: str | None = None) -> List[Dict[str, Any]]:
    if path is None:
        return _normalize_entries(DEFAULT_GATEWAY_CATALOG)
    payload = _load_json(Path(path).resolve())
    if not isinstance(payload, list):
        raise ValueError("gateway catalog JSON must be a list of entries")
    return _validate_required_fields(
        entries=payload,
        required_fields=(
            "gateway_id",
            "gateway_index",
            "name",
            "region",
            "latitude_deg",
            "longitude_deg",
            "type",
            "source",
            "confidence",
            "ix_id",
            "ix_ip_address",
            "upstream_asn",
        ),
        entry_label="gateway catalog",
    )


def load_zone_catalog(path: str | None = None) -> List[Dict[str, Any]]:
    if path is None:
        return _normalize_entries(DEFAULT_ZONE_CATALOG)
    payload = _load_json(Path(path).resolve())
    if not isinstance(payload, list):
        raise ValueError("zone catalog JSON must be a list of entries")
    return _validate_required_fields(
        entries=payload,
        required_fields=(
            "zone_id",
            "display_name",
            "region",
            "latitude_deg",
            "longitude_deg",
        ),
        entry_label="zone catalog",
    )


def _parse_selected_ids(value: str | None) -> List[str]:
    if value is None or not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _select_entries(
    *,
    entries: Sequence[Dict[str, Any]],
    id_field: str,
    selected_ids: Sequence[str],
    max_count: int | None,
) -> List[Dict[str, Any]]:
    entry_by_id = {str(entry[id_field]): dict(entry) for entry in entries}
    if selected_ids:
        missing = [entry_id for entry_id in selected_ids if entry_id not in entry_by_id]
        if missing:
            raise ValueError(f"unknown selected ids for {id_field}: {missing}")
        selected = [entry_by_id[entry_id] for entry_id in selected_ids]
        # Explicit caller-provided selections should not be silently truncated by
        # default preview caps such as max_uts=2 or max_gateways=3.
        return selected
    else:
        selected = sorted(
            _normalize_entries(entries),
            key=lambda entry: (int(entry.get(f"{id_field.split('_')[0]}_index", 0)), str(entry[id_field])),
        )
    if max_count is not None:
        selected = selected[: max(0, int(max_count))]
    return selected


def select_ut_entries(
    entries: Sequence[Dict[str, Any]],
    *,
    selected_ids: str | None = None,
    max_uts: int | None = None,
) -> List[Dict[str, Any]]:
    return _select_entries(
        entries=entries,
        id_field="ut_id",
        selected_ids=_parse_selected_ids(selected_ids),
        max_count=max_uts,
    )


def select_gateway_entries(
    entries: Sequence[Dict[str, Any]],
    *,
    selected_ids: str | None = None,
    max_gateways: int | None = None,
) -> List[Dict[str, Any]]:
    selected = _select_entries(
        entries=entries,
        id_field="gateway_id",
        selected_ids=_parse_selected_ids(selected_ids),
        max_count=max_gateways,
    )
    ix_to_gateways: Dict[int, List[str]] = {}
    for entry in selected:
        ix_id = int(entry["ix_id"])
        ix_to_gateways.setdefault(ix_id, []).append(str(entry["gateway_id"]))
    duplicate_ix_assignments = {
        ix_id: gateway_ids
        for ix_id, gateway_ids in sorted(ix_to_gateways.items())
        if len(gateway_ids) > 1
    }
    if duplicate_ix_assignments:
        details = ", ".join(
            f"ix{ix_id}: {gateway_ids}"
            for ix_id, gateway_ids in duplicate_ix_assignments.items()
        )
        raise ValueError(
            "selected gateway set is not currently supported: each Skyseed gateway must use "
            f"a unique Mini Internet IX for SEED eBGP startup readiness; duplicates found: {details}"
        )
    return selected


def summarize_ground_model(
    *,
    ut_catalog: Sequence[Dict[str, Any]],
    selected_uts: Sequence[Dict[str, Any]],
    gateway_catalog: Sequence[Dict[str, Any]],
    selected_gateways: Sequence[Dict[str, Any]],
    zone_catalog: Sequence[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    return {
        "ut_catalog_count": len(ut_catalog),
        "selected_ut_count": len(selected_uts),
        "selected_ut_ids": [str(entry["ut_id"]) for entry in selected_uts],
        "gateway_catalog_count": len(gateway_catalog),
        "selected_gateway_count": len(selected_gateways),
        "selected_gateway_ids": [str(entry["gateway_id"]) for entry in selected_gateways],
        "zone_catalog_count": len(zone_catalog or []),
        "selected_zone_count": len(zone_catalog or []),
        "selected_zone_ids": [str(entry["zone_id"]) for entry in (zone_catalog or [])],
    }
