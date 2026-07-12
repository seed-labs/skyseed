#!/usr/bin/env python3
# encoding: utf-8

"""Export combined ground/satellite link slices for Skyseed consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

try:
    from .export_ground_links import (
        DEFAULT_MAX_GROUND_LINK_DISTANCE_KM,
        DEFAULT_START_TIMESTAMP_MS,
        PLANNED_SHELL_CHOICES,
        _derive_steps,
        _parse_interval_seconds,
        build_ground_links_computation,
        post_trigger,
    )
    from .export_satellite_links import (
        DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW,
        DEFAULT_CROSS_PLANE_HYSTERESIS_KM,
        DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM,
        build_satellite_links_payload,
    )
except ImportError:
    from export_ground_links import (
        DEFAULT_MAX_GROUND_LINK_DISTANCE_KM,
        DEFAULT_START_TIMESTAMP_MS,
        PLANNED_SHELL_CHOICES,
        _derive_steps,
        _parse_interval_seconds,
        build_ground_links_computation,
        post_trigger,
    )
    from export_satellite_links import (
        DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW,
        DEFAULT_CROSS_PLANE_HYSTERESIS_KM,
        DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM,
        build_satellite_links_payload,
    )


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def build_links_payload(
    *,
    planned_shell: str | None = "S5",
    tle_path: Path | None = None,
    planned_orbit_data: Path | None = None,
    ground_station_catalog: Path | None = None,
    ground_station_ids: str | None = None,
    max_ground_stations: int | None = None,
    start_timestamp: str | None = DEFAULT_START_TIMESTAMP_MS,
    interval: str = "1s",
    steps: int | None = 3,
    duration: str | None = None,
    max_satellites: int = 0,
    min_elevation_deg: float = 5.0,
    max_distance_km: float | None = DEFAULT_MAX_GROUND_LINK_DISTANCE_KM,
    max_satellites_per_ground_station: int | None = None,
    hysteresis_elevation_deg: float = 2.0,
    earth_clearance_km: float = 0.0,
    satellite_id_format: str = "norad",
    include_ground_links: bool = True,
    include_satellite_links: bool = True,
    include_same_plane: bool = True,
    include_cross_plane: bool = True,
    cross_plane_strategy: str = "nearest-adjacent-plane",
    cross_plane_candidate_window: int = DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW,
    max_cross_plane_distance_km: float = DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM,
    cross_plane_hysteresis_km: float = DEFAULT_CROSS_PLANE_HYSTERESIS_KM,
) -> Dict[str, Any]:
    interval_seconds = _parse_interval_seconds(interval)
    effective_steps = _derive_steps(
        steps=steps,
        duration=duration,
        interval_seconds=interval_seconds,
    )

    ground_samples: List[List[Dict[str, str]]]
    if include_ground_links:
        ground_computation = build_ground_links_computation(
            planned_shell=planned_shell,
            tle_path=tle_path,
            planned_orbit_data=planned_orbit_data,
            ground_station_catalog=ground_station_catalog,
            ground_station_ids=ground_station_ids,
            max_ground_stations=max_ground_stations,
            start_timestamp=start_timestamp,
            interval=interval,
            steps=effective_steps,
            duration=None,
            max_satellites=max_satellites,
            min_elevation_deg=min_elevation_deg,
            satellite_id_format=satellite_id_format,
            max_satellites_per_ground_station=max_satellites_per_ground_station,
            hysteresis_elevation_deg=hysteresis_elevation_deg,
            earth_clearance_km=earth_clearance_km,
            max_distance_km=max_distance_km,
        )
        timestamp_ms = ground_computation.timestamp_ms
        ground_samples = list(ground_computation.samples)
    else:
        timestamp_ms = str(start_timestamp or "")
        ground_samples = [[] for _ in range(effective_steps)]

    if include_satellite_links:
        satellite_payload = build_satellite_links_payload(
            planned_shell=planned_shell,
            tle_path=tle_path,
            planned_orbit_data=planned_orbit_data,
            start_timestamp=timestamp_ms,
            interval=interval,
            steps=effective_steps,
            duration=None,
            max_satellites=max_satellites,
            satellite_id_format=satellite_id_format,
            include_same_plane=include_same_plane,
            include_cross_plane=include_cross_plane,
            cross_plane_strategy=cross_plane_strategy,
            cross_plane_candidate_window=cross_plane_candidate_window,
            max_cross_plane_distance_km=max_cross_plane_distance_km,
            cross_plane_hysteresis_km=cross_plane_hysteresis_km,
        )
        timestamp_ms = str(satellite_payload["timestamp"])
        satellite_samples = [
            list(frame.get("satelliteLinks", []))
            for frame in satellite_payload["links"]
        ]
    else:
        satellite_samples = [[] for _ in range(effective_steps)]

    links = [
        {
            "groundLinks": ground_samples[index] if index < len(ground_samples) else [],
            "satelliteLinks": satellite_samples[index] if index < len(satellite_samples) else [],
        }
        for index in range(effective_steps)
    ]
    return {
        "timestamp": str(timestamp_ms),
        "interval": str(interval),
        "links": links,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate combined groundLinks/satelliteLinks slices.",
    )
    orbit = parser.add_mutually_exclusive_group()
    orbit.add_argument("--planned-shell", choices=PLANNED_SHELL_CHOICES, default=None)
    orbit.add_argument("--tle", type=Path, default=None)
    parser.add_argument("--planned-orbit-data", type=Path, default=None)
    parser.add_argument("--ground-station-catalog", type=Path, default=None)
    parser.add_argument("--ground-station-ids", default=None)
    parser.add_argument("--max-ground-stations", type=int, default=None)
    parser.add_argument("--start-timestamp", default=DEFAULT_START_TIMESTAMP_MS)
    parser.add_argument("--interval", default="1s")
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--duration", default=None)
    parser.add_argument("--max-satellites", type=int, default=0)
    parser.add_argument("--satellite-id-format", choices=("norad", "node-id"), default="norad")
    parser.add_argument("--min-elevation-deg", type=float, default=5.0)
    parser.add_argument("--max-distance-km", type=float, default=DEFAULT_MAX_GROUND_LINK_DISTANCE_KM)
    parser.add_argument("--max-satellites-per-ground-station", type=int, default=None)
    parser.add_argument("--hysteresis-elevation-deg", type=float, default=2.0)
    parser.add_argument("--earth-clearance-km", type=float, default=0.0)
    parser.add_argument("--no-ground-links", action="store_true")
    parser.add_argument("--no-satellite-links", action="store_true")
    parser.add_argument("--no-same-plane", action="store_true")
    parser.add_argument("--no-cross-plane", action="store_true")
    parser.add_argument(
        "--cross-plane-strategy",
        choices=("nearest-adjacent-plane", "same-index"),
        default="nearest-adjacent-plane",
    )
    parser.add_argument(
        "--cross-plane-candidate-window",
        type=int,
        default=DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW,
    )
    parser.add_argument(
        "--max-cross-plane-distance-km",
        type=float,
        default=DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM,
    )
    parser.add_argument(
        "--cross-plane-hysteresis-km",
        type=float,
        default=DEFAULT_CROSS_PLANE_HYSTERESIS_KM,
    )
    parser.add_argument(
        "--dry-run-json",
        nargs="?",
        const="-",
        default="-",
        help="Print JSON to stdout or write it to the optional path.",
    )
    parser.add_argument("--post-url", default=None)
    parser.add_argument("--post-timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_links_payload(
        planned_shell=(None if args.tle is not None else (args.planned_shell or "S5")),
        tle_path=args.tle,
        planned_orbit_data=args.planned_orbit_data,
        ground_station_catalog=args.ground_station_catalog,
        ground_station_ids=args.ground_station_ids,
        max_ground_stations=args.max_ground_stations,
        start_timestamp=args.start_timestamp,
        interval=args.interval,
        steps=args.steps,
        duration=args.duration,
        max_satellites=args.max_satellites,
        min_elevation_deg=args.min_elevation_deg,
        max_distance_km=args.max_distance_km,
        max_satellites_per_ground_station=args.max_satellites_per_ground_station,
        hysteresis_elevation_deg=args.hysteresis_elevation_deg,
        earth_clearance_km=args.earth_clearance_km,
        satellite_id_format=args.satellite_id_format,
        include_ground_links=not args.no_ground_links,
        include_satellite_links=not args.no_satellite_links,
        include_same_plane=not args.no_same_plane,
        include_cross_plane=not args.no_cross_plane,
        cross_plane_strategy=args.cross_plane_strategy,
        cross_plane_candidate_window=args.cross_plane_candidate_window,
        max_cross_plane_distance_km=args.max_cross_plane_distance_km,
        cross_plane_hysteresis_km=args.cross_plane_hysteresis_km,
    )
    if args.dry_run_json == "-":
        if not args.post_url:
            print(json.dumps(payload, separators=(",", ":")))
    else:
        _write_json(Path(args.dry_run_json), payload)
    if args.post_url:
        return post_trigger(str(args.post_url), timeout_seconds=float(args.post_timeout_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
