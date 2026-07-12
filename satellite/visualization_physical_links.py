#!/usr/bin/env python3
# encoding: utf-8

"""Frozen visualization-link preparation helpers for Skyseed previews."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Sequence


SATELLITE_DIR = Path(__file__).resolve().parent
VISUALIZATION_DIR = SATELLITE_DIR / "apps" / "visualization"
EXPORT_LINKS_PATH = VISUALIZATION_DIR / "export_links.py"


def _load_export_links_module() -> Any:
    visualization_path = str(VISUALIZATION_DIR)
    inserted = False
    if visualization_path not in sys.path:
        sys.path.insert(0, visualization_path)
        inserted = True
    spec = importlib.util.spec_from_file_location(
        "skyseed_visualization_export_links",
        EXPORT_LINKS_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load visualization exporter: {EXPORT_LINKS_PATH}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            try:
                sys.path.remove(visualization_path)
            except ValueError:
                pass
    return module


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def visualization_interval_seconds(value: str) -> float:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("visualization link interval is required")
    if text.endswith("ms"):
        return float(text[:-2]) / 1000.0
    if text.endswith("s"):
        return float(text[:-1])
    return float(text)


def build_visualization_ground_station_catalog(
    *,
    gateway_definitions: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for entry in gateway_definitions:
        station_id = str(entry.get("gateway_id") or "").strip()
        if not station_id:
            raise ValueError(
                f"gateway entry missing gateway_id for visualization links: {entry}"
            )
        visualization_gateway_id = str(
            entry.get("visualization_gateway_id") or ""
        ).strip()
        if not visualization_gateway_id:
            raise ValueError(
                "selected gateway is missing explicit visualization_gateway_id: "
                f"{entry}"
            )
        entries.append(
            {
                "groundStationId": station_id,
                "latitude_deg": float(entry["latitude_deg"]),
                "longitude_deg": float(entry["longitude_deg"]),
                "altitude_km": float(entry.get("altitude_km", 0.0)),
                "skyseed_node_type": "gateway",
                "visualization_gateway_id": visualization_gateway_id,
                "scenario_role": entry.get("scenario_role"),
                "policy_reason": entry.get("policy_reason"),
            }
        )
    return sorted(entries, key=lambda item: str(item["groundStationId"]))


def build_serviceability_report(
    *,
    payload: Mapping[str, Any],
    ground_station_catalog: Sequence[Mapping[str, Any]],
    gateway_definitions: Sequence[Mapping[str, Any]],
    minimum_visible_frames: int = 1,
) -> Dict[str, Any]:
    station_ids = [str(entry["groundStationId"]) for entry in ground_station_catalog]
    visible_frame_indexes: Dict[str, set[int]] = {station_id: set() for station_id in station_ids}
    link_counts: Dict[str, int] = {station_id: 0 for station_id in station_ids}
    satellite_ids: set[str] = set()
    satellite_pair_count = 0
    frames = list(payload.get("links") or [])
    for frame_index, frame in enumerate(frames):
        for link in list(dict(frame).get("groundLinks") or []):
            station_id = str(dict(link).get("groundStationId") or "")
            satellite_id = str(dict(link).get("satelliteId") or "")
            if station_id in visible_frame_indexes:
                visible_frame_indexes[station_id].add(frame_index)
                link_counts[station_id] += 1
            if satellite_id:
                satellite_ids.add(satellite_id)
        satellite_pair_count += len(list(dict(frame).get("satelliteLinks") or []))

    station_reports = []
    blockers = []
    station_type_by_id = {
        str(entry["groundStationId"]): str(entry.get("skyseed_node_type") or "")
        for entry in ground_station_catalog
    }
    for station_id in station_ids:
        visible_count = len(visible_frame_indexes[station_id])
        record = {
            "ground_station_id": station_id,
            "skyseed_node_type": station_type_by_id.get(station_id),
            "visible_frame_count": visible_count,
            "ground_link_count": int(link_counts[station_id]),
            "serviceable": visible_count >= int(minimum_visible_frames),
        }
        station_reports.append(record)
        if not bool(record["serviceable"]):
            blockers.append(
                {
                    "kind": "ground_station_unserviceable",
                    "ground_station_id": station_id,
                    "minimum_visible_frames": int(minimum_visible_frames),
                    "visible_frame_count": visible_count,
                }
            )

    return {
        "schema_version": "skyseed-visualization-serviceability-v1",
        "visibility_model": "visualization_frozen_physical_links_v1",
        "frame_count": len(frames),
        "interval": str(payload.get("interval") or ""),
        "ground_station_count": len(station_ids),
        "ground_link_count": sum(link_counts.values()),
        "satellite_link_count": satellite_pair_count,
        "unique_ground_link_satellite_count": len(satellite_ids),
        "minimum_visible_frames": int(minimum_visible_frames),
        "serviceable": not blockers,
        "blockers": blockers,
        "ground_stations": station_reports,
        "gateway_bindings": [
            {
                "gateway_id": str(entry.get("gateway_id")),
                "visualization_gateway_id": str(entry.get("visualization_gateway_id")),
                "scenario_role": entry.get("scenario_role"),
                "policy_reason": entry.get("policy_reason"),
            }
            for entry in gateway_definitions
        ],
    }


def write_frozen_visualization_link_artifacts(
    *,
    output_dir: Path,
    planned_shell: str,
    planned_orbit_data: Path,
    gateway_definitions: Sequence[Mapping[str, Any]],
    start_timestamp: str,
    interval: str,
    duration_seconds: float,
    max_satellites: int,
    minimum_visible_frames: int = 1,
) -> Dict[str, Any]:
    if not str(start_timestamp or "").strip():
        raise ValueError("visualization link start timestamp must be explicit")
    if float(duration_seconds) <= 0.0:
        raise ValueError("visualization link duration_seconds must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    ground_catalog = build_visualization_ground_station_catalog(
        gateway_definitions=gateway_definitions,
    )
    ground_catalog_path = output_dir / "visualization_ground_stations.json"
    links_path = output_dir / "visualization_physical_links.json"
    report_path = output_dir / "visualization_serviceability_report.json"
    _write_json(ground_catalog_path, ground_catalog)
    exporter = _load_export_links_module()
    payload = exporter.build_links_payload(
        planned_shell=str(planned_shell),
        planned_orbit_data=Path(planned_orbit_data),
        ground_station_catalog=ground_catalog_path,
        ground_station_ids=",".join(
            str(entry["groundStationId"]) for entry in ground_catalog
        ),
        start_timestamp=str(start_timestamp),
        interval=str(interval),
        steps=None,
        duration=f"{float(duration_seconds)}s",
        max_satellites=int(max_satellites),
        satellite_id_format="node-id",
    )
    _write_json(links_path, payload)
    report = build_serviceability_report(
        payload=payload,
        ground_station_catalog=ground_catalog,
        gateway_definitions=gateway_definitions,
        minimum_visible_frames=minimum_visible_frames,
    )
    _write_json(report_path, report)
    if not bool(report["serviceable"]):
        raise ValueError(
            "visualization physical link serviceability failed: "
            f"{report['blockers']}"
        )
    return {
        "ground_station_catalog_path": str(ground_catalog_path),
        "links_path": str(links_path),
        "serviceability_report_path": str(report_path),
        "report": report,
        "payload": payload,
    }
