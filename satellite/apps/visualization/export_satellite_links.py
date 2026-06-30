#!/usr/bin/env python3
# encoding: utf-8

"""Standalone satellite-to-satellite link prototype exporter.

Package this script together with export_ground_links.py. It reuses the
standalone planned-shell/TLE propagation helpers from that sibling file and
emits only the external payload:
{"timestamp": "...", "interval": "...", "satelliteLinks": [[...]]}.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Sequence
from urllib import error, request

try:
    from export_ground_links import (
        DEFAULT_START_TIMESTAMP_MS,
        PLANNED_STARLINK_PHASE1_SHELL_PRESETS,
        NodePosition,
        OrbitRecord,
        PropagatedOrbitSlice,
        _derive_steps,
        _distance_km,
        _load_orbit_records,
        _parse_interval_seconds,
        _parse_start_timestamp_ms,
        _propagate_records,
        _render_satellite_id,
        satellite_node_id,
    )
except ModuleNotFoundError:
    from apps.visualization.export_ground_links import (
        DEFAULT_START_TIMESTAMP_MS,
        PLANNED_STARLINK_PHASE1_SHELL_PRESETS,
        NodePosition,
        OrbitRecord,
        PropagatedOrbitSlice,
        _derive_steps,
        _distance_km,
        _load_orbit_records,
        _parse_interval_seconds,
        _parse_start_timestamp_ms,
        _propagate_records,
        _render_satellite_id,
        satellite_node_id,
    )


DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM = 3300.0
DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW = 5
DEFAULT_CROSS_PLANE_HYSTERESIS_KM = 150.0


@dataclass(frozen=True)
class SatelliteLinkCandidate:
    satellite_a_id: str
    satellite_b_id: str
    link_type: str
    distance_km: float | None = None
    plane_a: int | None = None
    plane_b: int | None = None


def _record_sort_key(record: OrbitRecord) -> tuple[int, float, int]:
    return (
        int(record.satellite_index_in_plane or 0),
        float(record.mean_anomaly_deg),
        int(record.norad_id),
    )


def _records_by_plane(records: Sequence[OrbitRecord]) -> Dict[int, List[OrbitRecord]]:
    planes: Dict[int, List[OrbitRecord]] = {}
    fallback_plane = 1
    for record in records:
        plane_index = int(record.plane_index or fallback_plane)
        planes.setdefault(plane_index, []).append(record)
    return {
        plane_index: sorted(plane_records, key=_record_sort_key)
        for plane_index, plane_records in sorted(planes.items())
    }


def _same_plane_ring_candidates(records: Sequence[OrbitRecord]) -> List[SatelliteLinkCandidate]:
    candidates: List[SatelliteLinkCandidate] = []
    for plane_index, plane_records in _records_by_plane(records).items():
        if len(plane_records) < 2:
            continue
        for index, left in enumerate(plane_records):
            right = plane_records[(index + 1) % len(plane_records)]
            left_id = satellite_node_id(left.norad_id)
            right_id = satellite_node_id(right.norad_id)
            if left_id == right_id:
                continue
            satellite_a_id, satellite_b_id = sorted((left_id, right_id))
            candidates.append(
                SatelliteLinkCandidate(
                    satellite_a_id=satellite_a_id,
                    satellite_b_id=satellite_b_id,
                    link_type="same_plane_isl",
                    plane_a=plane_index,
                    plane_b=plane_index,
                )
            )
    return sorted(
        candidates,
        key=lambda item: (item.plane_a or 0, item.satellite_a_id, item.satellite_b_id),
    )


def _cross_plane_candidates(records: Sequence[OrbitRecord]) -> List[SatelliteLinkCandidate]:
    planes = _records_by_plane(records)
    plane_ids = sorted(planes)
    candidates: List[SatelliteLinkCandidate] = []
    for left_plane, right_plane in zip(plane_ids, plane_ids[1:]):
        left_records = planes[left_plane]
        right_records = planes[right_plane]
        pair_count = min(len(left_records), len(right_records))
        for index in range(pair_count):
            left_id = satellite_node_id(left_records[index].norad_id)
            right_id = satellite_node_id(right_records[index].norad_id)
            satellite_a_id, satellite_b_id = sorted((left_id, right_id))
            candidates.append(
                SatelliteLinkCandidate(
                    satellite_a_id=satellite_a_id,
                    satellite_b_id=satellite_b_id,
                    link_type="cross_plane_isl",
                    plane_a=left_plane,
                    plane_b=right_plane,
                )
            )
    return sorted(
        candidates,
        key=lambda item: (
            item.plane_a or 0,
            item.plane_b or 0,
            item.satellite_a_id,
            item.satellite_b_id,
        ),
    )


def _nearest_cross_plane_candidates(
    records: Sequence[OrbitRecord],
    positions: Dict[str, NodePosition],
    *,
    candidate_window: int,
    max_cross_plane_distance_km: float,
    previous_cross_links: Dict[tuple[int, int], set[tuple[str, str]]],
    cross_plane_hysteresis_km: float,
) -> List[SatelliteLinkCandidate]:
    planes = _records_by_plane(records)
    plane_ids = sorted(planes)
    active: List[SatelliteLinkCandidate] = []
    window = max(0, int(candidate_window))
    for left_plane, right_plane in zip(plane_ids, plane_ids[1:]):
        left_records = planes[left_plane]
        right_records = planes[right_plane]
        pair_key = (left_plane, right_plane)
        candidate_rows: List[tuple[float, int, str, str, int, int]] = []
        for left_index, left_record in enumerate(left_records):
            left_id = satellite_node_id(left_record.norad_id)
            left_position = positions.get(left_id)
            if left_position is None:
                continue
            for offset in range(-window, window + 1):
                right_index = (left_index + offset) % len(right_records)
                right_record = right_records[right_index]
                right_id = satellite_node_id(right_record.norad_id)
                right_position = positions.get(right_id)
                if right_position is None:
                    continue
                distance_km = _distance_km(left_position, right_position)
                satellite_a_id, satellite_b_id = sorted((left_id, right_id))
                previous_bonus = (
                    1
                    if (satellite_a_id, satellite_b_id) in previous_cross_links.get(pair_key, set())
                    and distance_km <= max_cross_plane_distance_km + cross_plane_hysteresis_km
                    else 0
                )
                if distance_km > max_cross_plane_distance_km and not previous_bonus:
                    continue
                candidate_rows.append(
                    (
                        distance_km,
                        -previous_bonus,
                        satellite_a_id,
                        satellite_b_id,
                        left_index,
                        right_index,
                    )
                )
        used_left: set[int] = set()
        used_right: set[int] = set()
        for distance_km, _, satellite_a_id, satellite_b_id, left_index, right_index in sorted(candidate_rows):
            if left_index in used_left or right_index in used_right:
                continue
            used_left.add(left_index)
            used_right.add(right_index)
            active.append(
                SatelliteLinkCandidate(
                    satellite_a_id=satellite_a_id,
                    satellite_b_id=satellite_b_id,
                    link_type="cross_plane_isl",
                    distance_km=round(distance_km, 3),
                    plane_a=left_plane,
                    plane_b=right_plane,
                )
            )
    return sorted(
        active,
        key=lambda item: (
            item.plane_a or 0,
            item.plane_b or 0,
            item.satellite_a_id,
            item.satellite_b_id,
        ),
    )


def _position_map(orbit_slice: PropagatedOrbitSlice) -> Dict[str, NodePosition]:
    return {
        satellite_id: NodePosition(state.x_km, state.y_km, state.z_km)
        for satellite_id, state in orbit_slice.satellite_states.items()
    }


def _active_satellite_links(
    orbit_slice: PropagatedOrbitSlice,
    *,
    records: Sequence[OrbitRecord],
    same_plane_candidates: Sequence[SatelliteLinkCandidate],
    cross_plane_candidates: Sequence[SatelliteLinkCandidate],
    include_same_plane: bool,
    include_cross_plane: bool,
    cross_plane_strategy: str,
    cross_plane_candidate_window: int,
    max_cross_plane_distance_km: float,
    previous_cross_links: Dict[tuple[int, int], set[tuple[str, str]]],
    cross_plane_hysteresis_km: float,
) -> List[SatelliteLinkCandidate]:
    positions = _position_map(orbit_slice)
    active: List[SatelliteLinkCandidate] = []
    if include_same_plane:
        for candidate in same_plane_candidates:
            if candidate.satellite_a_id not in positions or candidate.satellite_b_id not in positions:
                continue
            active.append(candidate)
    if include_cross_plane and cross_plane_strategy == "same-index":
        for candidate in cross_plane_candidates:
            first = positions.get(candidate.satellite_a_id)
            second = positions.get(candidate.satellite_b_id)
            if first is None or second is None:
                continue
            distance_km = _distance_km(first, second)
            if distance_km > max_cross_plane_distance_km:
                continue
            active.append(
                SatelliteLinkCandidate(
                    satellite_a_id=candidate.satellite_a_id,
                    satellite_b_id=candidate.satellite_b_id,
                    link_type=candidate.link_type,
                    distance_km=round(distance_km, 3),
                    plane_a=candidate.plane_a,
                    plane_b=candidate.plane_b,
                )
            )
    elif include_cross_plane and cross_plane_strategy == "nearest-adjacent-plane":
        active.extend(
            _nearest_cross_plane_candidates(
                records,
                positions,
                candidate_window=cross_plane_candidate_window,
                max_cross_plane_distance_km=max_cross_plane_distance_km,
                previous_cross_links=previous_cross_links,
                cross_plane_hysteresis_km=cross_plane_hysteresis_km,
            )
        )
    elif include_cross_plane:
        raise ValueError(f"unsupported cross-plane strategy: {cross_plane_strategy}")
    return sorted(
        active,
        key=lambda item: (
            item.link_type,
            item.satellite_a_id,
            item.satellite_b_id,
        ),
    )


def _render_link(
    candidate: SatelliteLinkCandidate,
    *,
    satellite_id_format: str,
) -> Dict[str, str]:
    return {
        "satelliteAId": _render_satellite_id(
            candidate.satellite_a_id,
            satellite_id_format=satellite_id_format,
        ),
        "satelliteBId": _render_satellite_id(
            candidate.satellite_b_id,
            satellite_id_format=satellite_id_format,
        ),
    }


def build_satellite_links_result(
    *,
    planned_shell: str | None = "S5",
    tle_path: Path | None = None,
    start_timestamp: str | None = DEFAULT_START_TIMESTAMP_MS,
    interval: str = "1s",
    steps: int | None = 3,
    duration: str | None = None,
    max_satellites: int = 24,
    satellite_id_format: str = "norad",
    include_same_plane: bool = True,
    include_cross_plane: bool = True,
    cross_plane_strategy: str = "nearest-adjacent-plane",
    cross_plane_candidate_window: int = DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW,
    max_cross_plane_distance_km: float = DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM,
    cross_plane_hysteresis_km: float = DEFAULT_CROSS_PLANE_HYSTERESIS_KM,
) -> Dict[str, Any]:
    timestamp_ms, start_time = _parse_start_timestamp_ms(start_timestamp)
    interval_seconds = _parse_interval_seconds(interval)
    if interval_seconds <= 0.0:
        raise ValueError("interval must be positive")
    effective_steps = _derive_steps(
        steps=steps,
        duration=duration,
        interval_seconds=interval_seconds,
    )
    orbit_kind, records = _load_orbit_records(
        planned_shell=planned_shell,
        tle_path=tle_path,
        max_satellites=max_satellites,
        start_time=start_time,
    )
    propagated_slices = _propagate_records(
        orbit_kind=orbit_kind,
        records=records,
        start_time=start_time,
        interval_seconds=interval_seconds,
        steps=effective_steps,
    )
    same_plane_candidates = _same_plane_ring_candidates(records)
    cross_plane_candidates = _cross_plane_candidates(records)
    satellite_links: List[List[Dict[str, str]]] = []
    previous_cross_links: Dict[tuple[int, int], set[tuple[str, str]]] = {}
    for orbit_slice in propagated_slices:
        active = _active_satellite_links(
            orbit_slice,
            records=records,
            same_plane_candidates=same_plane_candidates,
            cross_plane_candidates=cross_plane_candidates,
            include_same_plane=include_same_plane,
            include_cross_plane=include_cross_plane,
            cross_plane_strategy=cross_plane_strategy,
            cross_plane_candidate_window=cross_plane_candidate_window,
            max_cross_plane_distance_km=max_cross_plane_distance_km,
            previous_cross_links=previous_cross_links,
            cross_plane_hysteresis_km=cross_plane_hysteresis_km,
        )
        next_cross_links: Dict[tuple[int, int], set[tuple[str, str]]] = {}
        for candidate in active:
            if candidate.link_type != "cross_plane_isl":
                continue
            pair_key = (int(candidate.plane_a or 0), int(candidate.plane_b or 0))
            next_cross_links.setdefault(pair_key, set()).add(
                (candidate.satellite_a_id, candidate.satellite_b_id)
            )
        previous_cross_links = next_cross_links
        satellite_links.append(
            [
                _render_link(candidate, satellite_id_format=satellite_id_format)
                for candidate in active
            ]
        )
    return {
        "payload": {
            "timestamp": timestamp_ms,
            "interval": str(interval),
            "satelliteLinks": satellite_links,
        }
    }


def build_satellite_links_payload(**kwargs: Any) -> Dict[str, Any]:
    return build_satellite_links_result(**kwargs)["payload"]


def post_payload(url: str, payload: Dict[str, Any], *, timeout_seconds: float = 15.0) -> int:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            print(f"POST succeeded: status={response.status}")
            if response_body:
                print(response_body)
            return 0
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        print(f"POST failed: status={exc.code}", file=sys.stderr)
        print(response_body, file=sys.stderr)
        return 1
    except error.URLError as exc:
        print(f"POST failed: {exc}", file=sys.stderr)
        return 1


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the satellite-to-satellite satelliteLinks JSON payload for "
            "external consumers. Package this file with export_ground_links.py."
        ),
        epilog=(
            "Minimal dry run:\n"
            "  export_satellite_links.py --planned-shell S4 --steps 3 --interval 1s\n\n"
            "Payload contract:\n"
            "  {\"timestamp\":\"...\",\"interval\":\"1s\",\"satelliteLinks\":[[{\"satelliteAId\":\"...\",\"satelliteBId\":\"...\"}]]}\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    orbit = parser.add_mutually_exclusive_group()
    orbit.add_argument(
        "--planned-shell",
        choices=tuple(sorted(PLANNED_STARLINK_PHASE1_SHELL_PRESETS)),
        default=None,
        help="Use a deterministic planned shell preset. Default: S5.",
    )
    orbit.add_argument("--tle", type=Path, default=None, help="Path to a three-line TLE catalog.")
    parser.add_argument("--start-timestamp", default=DEFAULT_START_TIMESTAMP_MS, help="Start timestamp, epoch milliseconds or ISO-8601.")
    parser.add_argument("--interval", default="1s", help='Sampling interval such as "1s" or "30s".')
    parser.add_argument("--steps", type=int, default=None, help="Number of samples. Defaults to 3 unless --duration is set.")
    parser.add_argument("--duration", default=None, help="Optional duration; derives steps from interval.")
    parser.add_argument("--max-satellites", type=int, default=24, help="Prototype satellite sample cap.")
    parser.add_argument("--satellite-id-format", choices=("norad", "node-id"), default="norad", help="Payload satellite id format.")
    parser.add_argument("--no-same-plane", action="store_true", help="Do not emit same-plane ring links.")
    parser.add_argument("--no-cross-plane", action="store_true", help="Do not emit cross-plane dynamic links.")
    parser.add_argument(
        "--cross-plane-strategy",
        choices=("nearest-adjacent-plane", "same-index"),
        default="nearest-adjacent-plane",
        help="Cross-plane matching strategy. Default uses nearest one-to-one matching between adjacent planes.",
    )
    parser.add_argument(
        "--cross-plane-candidate-window",
        type=int,
        default=DEFAULT_CROSS_PLANE_CANDIDATE_WINDOW,
        help="Index window in the adjacent plane for nearest-adjacent-plane matching.",
    )
    parser.add_argument(
        "--max-cross-plane-distance-km",
        type=float,
        default=DEFAULT_CROSS_PLANE_MAX_DISTANCE_KM,
        help="Cross-plane ISL activation threshold. Default matches Skyseed preview logic.",
    )
    parser.add_argument(
        "--cross-plane-hysteresis-km",
        type=float,
        default=DEFAULT_CROSS_PLANE_HYSTERESIS_KM,
        help="Distance margin for retaining previous cross-plane matches.",
    )
    parser.add_argument(
        "--dry-run-json",
        nargs="?",
        const="-",
        default="-",
        help="Print JSON to stdout or write it to the optional path. This is the default mode.",
    )
    parser.add_argument("--post-url", default=None, help="POST payload URL. Omit for dry-run.")
    parser.add_argument("--post-timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_satellite_links_result(
        planned_shell=(None if args.tle is not None else (args.planned_shell or "S5")),
        tle_path=args.tle,
        start_timestamp=args.start_timestamp,
        interval=args.interval,
        steps=args.steps,
        duration=args.duration,
        max_satellites=args.max_satellites,
        satellite_id_format=args.satellite_id_format,
        include_same_plane=not args.no_same_plane,
        include_cross_plane=not args.no_cross_plane,
        cross_plane_strategy=args.cross_plane_strategy,
        cross_plane_candidate_window=args.cross_plane_candidate_window,
        max_cross_plane_distance_km=args.max_cross_plane_distance_km,
        cross_plane_hysteresis_km=args.cross_plane_hysteresis_km,
    )
    payload = result["payload"]
    if args.post_url:
        return post_payload(
            str(args.post_url),
            payload,
            timeout_seconds=float(args.post_timeout_seconds),
        )
    if args.dry_run_json == "-":
        print(json.dumps(payload, indent=2))
    else:
        _write_json(Path(args.dry_run_json), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
