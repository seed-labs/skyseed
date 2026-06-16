#!/usr/bin/env python3
# encoding: utf-8

"""Fetch and cache current CelesTrak Starlink GP/TLE data."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from orbit_data import (
    CelesTrakGpTleOrbitDataSource,
    DEFAULT_STARLINK_GP_TLE_URL,
    StarlinkCatalogTableRecord,
    fetch_starlink_catalog_table_html,
    fetch_tle_for_catnr,
    parse_tle_catalog,
    parse_starlink_catalog_table,
    write_orbit_cache_artifact,
)


DEFAULT_TLE_PATH = SATELLITE_DIR / "data" / "tle" / "starlink_latest.tle"
DEFAULT_MANIFEST_PATH = SATELLITE_DIR / "data" / "tle" / "starlink_latest.manifest.json"


def _sample_evenly(items: Sequence[Any], count: int) -> List[Any]:
    if count >= len(items):
        return list(items)
    if count <= 0:
        return []
    sampled: List[Any] = []
    seen_indices: set[int] = set()
    for index in range(count):
        target = round(index * (len(items) - 1) / max(1, count - 1))
        while target in seen_indices and target + 1 < len(items):
            target += 1
        seen_indices.add(target)
        sampled.append(items[target])
    return sampled


def _table_shell_cluster(
    records: Sequence[StarlinkCatalogTableRecord],
) -> Sequence[StarlinkCatalogTableRecord]:
    grouped: Dict[tuple[float, float], List[StarlinkCatalogTableRecord]] = {}
    for record in records:
        altitude_km = (record.apogee_height_km + record.perigee_height_km) / 2.0
        inclination_bucket = round(record.inclination_deg / 0.5) * 0.5
        altitude_bucket = round(altitude_km / 25.0) * 25.0
        grouped.setdefault((inclination_bucket, altitude_bucket), []).append(record)
    if not grouped:
        return []
    cluster_key = max(
        grouped,
        key=lambda key: (len(grouped[key]), key[0], key[1]),
    )
    return tuple(
        sorted(
            grouped[cluster_key],
            key=lambda record: (record.norad_id, record.international_designator),
        )
    )


def _fetch_sampled_tle_records(
    rows: Sequence[StarlinkCatalogTableRecord],
    *,
    max_workers: int,
) -> List[Any]:
    ordered_records: Dict[int, Any] = {}
    fetch_errors: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        future_map = {
            executor.submit(fetch_tle_for_catnr, norad_id=row.norad_id): (index, row)
            for index, row in enumerate(rows)
        }
        for future in as_completed(future_map):
            index, row = future_map[future]
            try:
                ordered_records[index] = future.result()
            except Exception as exc:
                fetch_errors.append(f"CATNR {row.norad_id}: {exc}")
    if len(ordered_records) != len(rows):
        error_text = "; ".join(fetch_errors) if fetch_errors else "unknown fetch failure"
        raise RuntimeError(
            "failed to fetch one or more sampled CATNR TLE records: "
            f"{error_text}"
        )
    return [ordered_records[index] for index in range(len(rows))]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and cache the current CelesTrak Starlink GP/TLE catalog."
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_STARLINK_GP_TLE_URL,
        help="CelesTrak GP/TLE source URL.",
    )
    parser.add_argument(
        "--tle-path",
        default=str(DEFAULT_TLE_PATH),
        help="Local cache path for the TLE catalog.",
    )
    parser.add_argument(
        "--manifest-path",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Metadata manifest path for the cached TLE catalog.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh download even if a local cache already exists.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render the fetch summary as text or JSON.",
    )
    parser.add_argument(
        "--max-fallback-records",
        type=int,
        default=96,
        help="When the bulk GP/TLE endpoint is blocked, cache up to this many fallback CATNR-fetched TLE records.",
    )
    parser.add_argument(
        "--fallback-workers",
        type=int,
        default=4,
        help="Bounded worker count for CATNR fallback fetches.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_source = CelesTrakGpTleOrbitDataSource(
        source_url=str(args.source_url),
        cache_path=Path(args.tle_path).resolve(),
        manifest_path=Path(args.manifest_path).resolve(),
    )
    try:
        artifact = data_source.materialize(refresh=bool(args.refresh))
    except Exception as exc:
        table_html = fetch_starlink_catalog_table_html()
        catalog_parse_result = parse_starlink_catalog_table(table_html)
        clustered_rows = _table_shell_cluster(catalog_parse_result.records)
        sampled_rows = _sample_evenly(
            clustered_rows,
            min(len(clustered_rows), int(args.max_fallback_records)),
        )
        sampled_tle_records = _fetch_sampled_tle_records(
            sampled_rows,
            max_workers=int(args.fallback_workers),
        )
        raw_text = (
            "\n".join(
                line
                for record in sampled_tle_records
                for line in (record.satellite_name, record.line1, record.line2)
            )
            + "\n"
        )
        artifact = write_orbit_cache_artifact(
            cache_path=Path(args.tle_path).resolve(),
            manifest_path=Path(args.manifest_path).resolve(),
            source_name="celestrak_starlink_catalog_table_fallback",
            source_url=str(args.source_url),
            orbit_format="tle_subset_via_catnr",
            raw_text=raw_text,
            extra_metadata={
                "fallback_reason": str(exc),
                "fallback_table_url": "https://celestrak.org/NORAD/ELEMENTS/table.php?GROUP=starlink",
                "catalog_row_count": len(catalog_parse_result.records),
                "sampled_catnr_count": len(sampled_rows),
                "sampled_catnr_ids": [row.norad_id for row in sampled_rows],
                "current_as_of_utc": catalog_parse_result.current_as_of_utc,
            },
        )
    parse_result = parse_tle_catalog(artifact.raw_text)
    summary = {
        "source_name": artifact.source_name,
        "source_url": artifact.source_url,
        "cache_path": artifact.cache_path,
        "manifest_path": artifact.manifest_path,
        "download_timestamp_utc": artifact.metadata.get("download_timestamp_utc"),
        "format": artifact.orbit_format,
        "record_count": len(parse_result.records),
        "malformed_record_count": len(parse_result.malformed_records),
        "sha256": artifact.sha256,
        "epoch_utc_min": parse_result.summary.get("epoch_utc_min"),
        "epoch_utc_max": parse_result.summary.get("epoch_utc_max"),
    }
    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print("Starlink GP/TLE cache updated.")
    print(f"Source URL: {summary['source_url']}")
    print(f"TLE cache: {summary['cache_path']}")
    print(f"Manifest: {summary['manifest_path']}")
    print(f"Download timestamp: {summary['download_timestamp_utc']}")
    print(f"Format: {summary['format']}")
    print(
        "Records: "
        f"{summary['record_count']} valid, "
        f"{summary['malformed_record_count']} malformed"
    )
    print(f"SHA256: {summary['sha256']}")
    print(
        "Epoch range: "
        f"{summary['epoch_utc_min']} -> {summary['epoch_utc_max']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
