#!/usr/bin/env python3
# encoding: utf-8

"""Build a minimal Starlink-shell preview pipeline from real GP/TLE input."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from contracts import (
    validate_network_mutation_plan,
    validate_runtime_update_plan,
    validate_topology_snapshot,
)
from ground_model import (
    load_gateway_catalog,
    load_zone_catalog,
    load_ut_catalog,
    select_gateway_entries,
    select_ut_entries,
    summarize_ground_model,
)
from network_mutation import (
    build_network_mutation_plan,
    dynamic_slot_state_from_link_state,
    dynamic_slot_state_from_snapshot,
    make_runtime_parameter_mutation_policy,
)
from orbit_data import (
    CelesTrakGpTleOrbitDataSource,
    DEFAULT_STARLINK_GP_TLE_URL,
    LocalOrbitDataSource,
    StarlinkCatalogTableRecord,
    fetch_starlink_catalog_table_html,
    fetch_tle_for_catnr,
    parse_tle_catalog,
    parse_starlink_catalog_table,
    write_orbit_cache_artifact,
)
from planner import UtInternetExperimentPlanner
from seed_integration import resolve_platform
from seedemu.compiler import Docker
from starlink_shell import (
    PLANNED_STARLINK_PHASE1_SHELL_PRESETS,
    generate_planned_shell_records,
    planned_shell_preset,
    propagate_planned_shell_records,
    propagate_starlink_records,
    select_starlink_shell,
)
from starlink_shell_topology import (
    SKYSEED_STARTUP_PROFILE_UT_PLACEHOLDER,
    _shift_gateway_definitions_for_ix_offset,
    build_starlink_preview_scenario,
    build_starlink_shell_emulator,
    build_starlink_shell_initial_link_state,
    build_starlink_shell_layout,
    build_starlink_shell_topology_inventory,
    build_starlink_topology_snapshot_pipeline,
    derive_starlink_coordinate_consistent_ground_model,
    derive_starlink_preview_ground_locations,
    write_starlink_shell_artifacts,
)
from temporal_planning import (
    build_gz_planning_artifacts,
    build_sample_boundary_temporal_plan,
    build_temporal_plan,
)


DEFAULT_DATA_DIR = SATELLITE_DIR / "data" / "tle"
DEFAULT_TLE_PATH = DEFAULT_DATA_DIR / "starlink_latest.tle"
DEFAULT_TLE_MANIFEST_PATH = DEFAULT_DATA_DIR / "starlink_latest.manifest.json"
DEFAULT_CONFIG_DIR = SATELLITE_DIR / "configs" / "starlink_shell_preview"
DEFAULT_OUTPUT_DIR = SATELLITE_DIR / "output" / "starlink_shell_preview"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dockerfile_copy_source_for_target(dockerfile_text: str, target_path: str) -> str | None:
    pattern = re.compile(
        rf"^COPY\s+(\S+)\s+{re.escape(target_path)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(dockerfile_text)
    if match is None:
        return None
    return str(match.group(1))


def _strip_router_bird_startup(start_script_text: str) -> tuple[str, bool]:
    lines = start_script_text.splitlines()
    kept_lines: List[str] = []
    removed = False
    for line in lines:
        stripped = line.strip()
        if stripped in ("[ ! -d /run/bird ] && mkdir /run/bird", "bird -d &"):
            removed = True
            continue
        kept_lines.append(line)
    updated = "\n".join(kept_lines)
    if start_script_text.endswith("\n"):
        updated += "\n"
    return updated, removed


def _apply_skyseed_lightweight_startup_profiles(
    *,
    output_dir: Path,
    topology_inventory: Dict[str, Any],
) -> Dict[str, Any]:
    patched_nodes: List[Dict[str, Any]] = []

    def patch_service_dir(service_dir_name: str, *, startup_profile: str) -> None:
        service_dir = output_dir / service_dir_name
        dockerfile_path = service_dir / "Dockerfile"
        if not dockerfile_path.exists():
            return
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
        start_source = _dockerfile_copy_source_for_target(dockerfile_text, "/start.sh")
        if start_source is None:
            return
        start_path = service_dir / start_source
        if not start_path.exists():
            return
        original = start_path.read_text(encoding="utf-8")
        updated, removed = _strip_router_bird_startup(original)
        if not removed:
            return
        start_path.write_text(updated, encoding="utf-8")
        patched_nodes.append(
            {
                "service_dir": service_dir_name,
                "startup_profile": startup_profile,
                "removed_router_daemon_startup": True,
            }
        )

    for ut in topology_inventory.get("user_terminals", []):
        ut_id = str(ut.get("ut_id", "")).strip()
        if not ut_id:
            continue
        patch_service_dir(
            f"rnode_{250}_{ut_id}-placeholder-rtr",
            startup_profile=SKYSEED_STARTUP_PROFILE_UT_PLACEHOLDER,
        )

    return {
        "patched_node_count": len(patched_nodes),
        "patched_nodes": patched_nodes,
    }


def _parse_start_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _records_summary(records: Sequence[Any]) -> Dict[str, Any]:
    epochs = [
        datetime.fromisoformat(str(record.epoch_utc).replace("Z", "+00:00"))
        for record in records
    ]
    inclinations = [float(record.inclination_deg) for record in records]
    mean_motions = [float(record.mean_motion_rev_per_day) for record in records]
    norad_ids = [int(record.norad_id) for record in records]
    return {
        "record_count": len(records),
        "norad_id_min": min(norad_ids) if norad_ids else None,
        "norad_id_max": max(norad_ids) if norad_ids else None,
        "inclination_deg_min": round(min(inclinations), 6) if inclinations else None,
        "inclination_deg_max": round(max(inclinations), 6) if inclinations else None,
        "mean_motion_rev_per_day_min": round(min(mean_motions), 9) if mean_motions else None,
        "mean_motion_rev_per_day_max": round(max(mean_motions), 9) if mean_motions else None,
        "epoch_utc_min": min(epochs).isoformat().replace("+00:00", "Z") if epochs else None,
        "epoch_utc_max": max(epochs).isoformat().replace("+00:00", "Z") if epochs else None,
    }


def _gateway_exit_summary(runtime_update_plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    per_ut: Dict[str, Dict[str, Any]] = {}
    for subplan in dict(runtime_update_plan.get("node_subplans", {})).values():
        for action in list(subplan.get("actions", [])):
            flow_context = dict(
                action.get("intent", {}).get("outcome", {}).get("flow_context", {})
            )
            ut_id = str(flow_context.get("ut_id", "")).strip()
            if not ut_id:
                continue
            selected_gateway_id = str(flow_context.get("selected_gateway_id", "")).strip()
            if not selected_gateway_id:
                continue
            per_ut[ut_id] = {
                "selected_gateway_id": selected_gateway_id,
                "selected_gateway_satellite_id": flow_context.get("selected_gateway_satellite_id"),
                "serving_satellite_id": flow_context.get("serving_satellite_id"),
                "target_host_node_id": flow_context.get("target_host_node_id"),
            }
    return per_ut


def _change_events(
    *,
    field_name: str,
    slice_records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    previous: Dict[str, Any] | None = None
    for record in slice_records:
        current = dict(record.get(field_name, {}))
        if previous is None:
            previous = current
            continue
        changes = {
            key: {
                "from": previous.get(key),
                "to": current.get(key),
            }
            for key in sorted(set(previous) | set(current))
            if previous.get(key) != current.get(key)
        }
        if changes:
            events.append(
                {
                    "slice_id": record["slice_id"],
                    "changes": changes,
                }
            )
        previous = current
    return events


def _time_slices(
    *,
    start_time: datetime,
    step_seconds: float,
    slice_count: int,
) -> List[Any]:
    from experiment_topology import ExperimentTimeSlice

    slices = []
    for index in range(slice_count):
        slices.append(
            ExperimentTimeSlice(
                slice_id=f"starlink-shell-slice-{index + 1:04d}",
                relative_time_seconds=round(index * step_seconds, 6),
                duration_seconds=round(step_seconds, 6),
            )
        )
    return slices


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


def _effective_sampling(
    *,
    sample_interval: float | None,
    step_seconds: float,
    slice_count: int,
    horizon_seconds: float | None,
) -> tuple[float, int, float]:
    effective_interval = (
        float(sample_interval)
        if sample_interval is not None
        else float(step_seconds)
    )
    if effective_interval <= 0.0:
        raise ValueError("sample interval must be positive")
    if horizon_seconds is not None:
        if float(horizon_seconds) < 0.0:
            raise ValueError("horizon seconds must be non-negative")
        effective_slice_count = int(math.floor(float(horizon_seconds) / effective_interval)) + 1
        return effective_interval, max(1, effective_slice_count), float(horizon_seconds)
    effective_slice_count = max(1, int(slice_count))
    effective_horizon = effective_interval * max(0, effective_slice_count - 1)
    return effective_interval, effective_slice_count, effective_horizon


def _effective_event_evaluation_interval(
    *,
    sample_interval_seconds: float,
    event_evaluation_interval_seconds: float | None,
) -> float:
    if event_evaluation_interval_seconds is None:
        return float(sample_interval_seconds)
    effective_interval = float(event_evaluation_interval_seconds)
    if effective_interval <= 0.0:
        raise ValueError("event evaluation interval must be positive")
    return min(float(sample_interval_seconds), effective_interval)


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
    max_workers: int = 4,
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
        ordered_ids = ", ".join(
            str(rows[index].norad_id)
            for index in sorted(ordered_records)
        )
        error_text = "; ".join(fetch_errors) if fetch_errors else "unknown fetch failure"
        raise RuntimeError(
            "failed to fetch one or more sampled CATNR TLE records; "
            f"successful_catnr_ids=[{ordered_ids}] errors={error_text}"
        )
    return [ordered_records[index] for index in range(len(rows))]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a minimal Starlink-shell preview pipeline from planned-shell or real-catalog orbit input."
        )
    )
    parser.add_argument(
        "--orbit-input-mode",
        choices=("planned-shell", "real-catalog"),
        default="real-catalog",
        help="Use a deterministic planned-shell preset or a real cached/live catalog input.",
    )
    parser.add_argument(
        "--planned-shell-id",
        choices=tuple(sorted(PLANNED_STARLINK_PHASE1_SHELL_PRESETS)),
        default="S5",
        help="Planned-shell preset to use when --orbit-input-mode planned-shell is selected.",
    )
    parser.add_argument(
        "--source-mode",
        choices=("cache", "refresh", "local"),
        default="cache",
        help="Load local cache, force refresh from CelesTrak, or use a specific local file.",
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_STARLINK_GP_TLE_URL,
        help="CelesTrak Starlink GP/TLE source URL.",
    )
    parser.add_argument(
        "--tle-path",
        default=str(DEFAULT_TLE_PATH),
        help="Cached or local TLE path.",
    )
    parser.add_argument(
        "--orbit-data",
        default=None,
        help="Explicit local orbit data file for reproducible real-catalog runs.",
    )
    parser.add_argument(
        "--manifest-path",
        default=str(DEFAULT_TLE_MANIFEST_PATH),
        help="Cached metadata manifest path.",
    )
    parser.add_argument(
        "--max-satellites",
        type=int,
        default=24,
        help="Balanced preview shell size. Common values are 24, 48, 72, and 172 for S5 full shell.",
    )
    parser.add_argument(
        "--ut-catalog",
        default=None,
        help="Optional JSON file describing the UT catalog.",
    )
    parser.add_argument(
        "--gateway-catalog",
        default=None,
        help="Optional JSON file describing the gateway catalog.",
    )
    parser.add_argument(
        "--zone-catalog",
        default=None,
        help="Optional JSON file describing explicit geographic zone partitions.",
    )
    parser.add_argument(
        "--ut-ids",
        default=None,
        help="Comma-separated selected UT ids to emulate.",
    )
    parser.add_argument(
        "--gateway-ids",
        default=None,
        help="Comma-separated selected gateway ids to emulate.",
    )
    parser.add_argument(
        "--max-uts",
        type=int,
        default=2,
        help="Maximum emulated UT count when --ut-ids is not provided.",
    )
    parser.add_argument(
        "--max-gateways",
        type=int,
        default=3,
        help="Maximum emulated gateway count when --gateway-ids is not provided.",
    )
    parser.add_argument(
        "--ground-placement",
        choices=("catalog", "shell-aware"),
        default="shell-aware",
        help="Use catalog coordinates directly or derive shell-aware preview positions from the first propagated slice.",
    )
    parser.add_argument(
        "--coordinate-policy",
        choices=("real_geographic", "shell_aware_consistent", "synthetic_shell_aware"),
        default=None,
        help=(
            "Coordinate semantics for UT/GW/zone catalogs. Defaults to "
            "real_geographic for --ground-placement catalog and "
            "shell_aware_consistent for --ground-placement shell-aware."
        ),
    )
    parser.add_argument(
        "--start-time",
        default=None,
        help="Propagation start time in ISO-8601 UTC. Default is current UTC time.",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=None,
        help="Observation interval used for coarse reporting and queue comparison.",
    )
    parser.add_argument(
        "--event-evaluation-interval",
        type=float,
        default=None,
        help="Optional finer dense evaluation interval used to generate event-time runtime updates.",
    )
    parser.add_argument(
        "--horizon-seconds",
        type=float,
        default=None,
        help="Optional temporal planning horizon. If set, derives sample count from horizon/sample interval.",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=120.0,
        help="Propagation step between preview slices.",
    )
    parser.add_argument(
        "--slice-count",
        type=int,
        default=5,
        help="Number of preview slices to generate.",
    )
    parser.add_argument(
        "--parameter-max-interval",
        type=float,
        default=240.0,
        help="Maximum time between lightweight ParameterEpochs when parameter drift is present.",
    )
    parser.add_argument(
        "--delay-threshold-ms",
        type=float,
        default=0.5,
        help="Absolute delay drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--loss-threshold-pct",
        type=float,
        default=0.02,
        help="Absolute packet loss drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--bandwidth-threshold-pct",
        type=float,
        default=5.0,
        help="Relative bandwidth drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--routing-cost-threshold",
        type=int,
        default=5,
        help="Absolute routing-cost drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--ut-slot-margin",
        type=int,
        default=1,
        help="Extra UT access slots to budget above the observed simultaneous active degree.",
    )
    parser.add_argument(
        "--gateway-slot-margin",
        type=int,
        default=1,
        help="Extra gateway access slots to budget above the observed simultaneous active degree.",
    )
    parser.add_argument(
        "--satellite-isl-slot-margin",
        type=int,
        default=1,
        help="Extra per-satellite ISL degree margin above observed simultaneous activity.",
    )
    parser.add_argument(
        "--config-dir",
        default=str(DEFAULT_CONFIG_DIR),
        help="Directory for generated topology/link-state/snapshot preview artifacts.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Optional Docker compiler output directory if --compile-seed-output is enabled.",
    )
    parser.add_argument(
        "--mini-internet-node-prefix",
        default="",
        help="Optional container/node-name prefix for compiled Mini Internet routers and hosts.",
    )
    parser.add_argument(
        "--mini-internet-ix-offset",
        type=int,
        default=0,
        help="Optional IX id offset for compiled Mini Internet route-server/container coexistence.",
    )
    parser.add_argument(
        "--platform",
        choices=("amd", "arm"),
        default="amd",
        help="Target Docker platform when compiling SEED output.",
    )
    parser.add_argument(
        "--skip-seed-render",
        action="store_true",
        help="Skip seedemu render validation and only build the topology objects.",
    )
    parser.add_argument(
        "--compile-seed-output",
        action="store_true",
        help="Compile SEED Docker output after render. This remains optional for preview runs.",
    )
    parser.add_argument(
        "--docker-dummy-network-pool",
        default="10.128.0.0/9",
        help="SeedEmu self-managed Docker dummy network pool for compiled output.",
    )
    parser.add_argument(
        "--docker-dummy-network-mask",
        type=int,
        default=24,
        help="SeedEmu self-managed Docker dummy network mask for compiled output.",
    )
    parser.add_argument(
        "--disable-seed-internet-map",
        action="store_true",
        help="Disable SeedEmu internet-map exposure in compiled Docker output.",
    )
    parser.add_argument(
        "--seed-internet-map-port",
        type=int,
        default=8080,
        help="SeedEmu internet-map port when compiled output keeps it enabled.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render the preview summary as text or JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tle_path = Path(args.orbit_data or args.tle_path).resolve()
    manifest_path = Path(args.manifest_path).resolve()
    config_dir = Path(args.config_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    topology_json_path = config_dir / "topology.json"
    link_state_json_path = config_dir / "link_state.json"
    shell_manifest_json_path = config_dir / "shell_manifest.json"
    propagated_positions_json_path = config_dir / "propagated_positions.json"
    snapshot_preview_json_path = config_dir / "snapshot_preview.json"
    planner_preview_json_path = config_dir / "planner_preview.json"
    temporal_plan_json_path = config_dir / "temporal_plan.json"
    zone_model_json_path = config_dir / "zone_model.json"
    zone_anchor_schedule_json_path = config_dir / "zone_anchor_schedule.json"
    gz_fec_plan_json_path = config_dir / "gz_fec_plan.json"
    gz_update_diff_summary_json_path = config_dir / "gz_update_diff_summary.json"
    zone_anchor_quality_summary_json_path = config_dir / "zone_anchor_quality_summary.json"
    zone_availability_analysis_json_path = config_dir / "zone_availability_analysis.json"
    effective_workload_catalog_json_path = config_dir / "effective_workload_catalog.json"

    ut_catalog = load_ut_catalog(args.ut_catalog)
    selected_uts = select_ut_entries(
        ut_catalog,
        selected_ids=args.ut_ids,
        max_uts=int(args.max_uts) if args.max_uts is not None else None,
    )
    gateway_catalog = load_gateway_catalog(args.gateway_catalog)
    zone_catalog = load_zone_catalog(args.zone_catalog)
    selected_gateways = select_gateway_entries(
        gateway_catalog,
        selected_ids=args.gateway_ids,
        max_gateways=int(args.max_gateways) if args.max_gateways is not None else None,
    )
    original_selected_uts = [dict(entry) for entry in selected_uts]
    original_selected_gateways = [dict(entry) for entry in selected_gateways]
    original_zone_catalog = [dict(entry) for entry in zone_catalog]
    effective_gateway_definitions = _shift_gateway_definitions_for_ix_offset(
        selected_gateways,
        ix_id_offset=int(args.mini_internet_ix_offset),
    )
    if not selected_uts:
        raise ValueError("at least one UT must be selected")
    if not selected_gateways:
        raise ValueError("at least one gateway must be selected")
    if zone_catalog:
        known_zone_ids = {
            str(entry["zone_id"])
            for entry in zone_catalog
        }
        unknown_zone_ids = sorted(
            {
                str(entry["zone_id"])
                for entry in selected_uts
                if str(entry["zone_id"]) not in known_zone_ids
            }
        )
        if unknown_zone_ids:
            raise ValueError(
                f"selected UTs reference unknown zone ids not present in zone catalog: "
                f"{unknown_zone_ids}"
            )

    artifact = None
    parse_result = None
    catalog_parse_result = None
    catalog_selection_mode = "group_gp_tle"
    orbit_input_kind = "real_tle_sgp4"
    orbit_source = "celestrak_starlink_gp_tle"

    if args.orbit_input_mode == "planned-shell":
        preset = planned_shell_preset(str(args.planned_shell_id))
        if int(args.max_satellites) > preset.satellite_count:
            raise ValueError(
                f"--max-satellites {args.max_satellites} exceeds planned shell "
                f"{preset.shell_id} capacity {preset.satellite_count}"
            )
        full_records = generate_planned_shell_records(shell_id=preset.shell_id)
        parse_result = None
        selection = select_starlink_shell(
            full_records,
            max_satellites=int(args.max_satellites),
        )
        catalog_selection_mode = "planned_shell_preset"
        orbit_input_kind = "planned_shell_circular_orbit"
        orbit_source = f"starlink_phase1_{preset.shell_id}"
        artifact = {
            "source_name": "planned_starlink_phase1_shell_preset",
            "source_url": None,
            "cache_path": None,
            "manifest_path": None,
            "fetched_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "orbit_format": "planned_shell_preset",
            "sha256": None,
            "metadata": {
                "planned_shell_preset": preset.to_dict(),
                "object_count": len(full_records),
            },
        }
        record_summary = _records_summary(full_records)
    else:
        local_mode = args.source_mode == "local" or args.orbit_data is not None
        if local_mode:
            data_source = LocalOrbitDataSource(
                orbit_path=tle_path,
                orbit_format="tle",
                manifest_path=manifest_path if manifest_path.exists() else None,
            )
            refresh = False
        else:
            data_source = CelesTrakGpTleOrbitDataSource(
                source_url=str(args.source_url),
                cache_path=tle_path,
                manifest_path=manifest_path,
            )
            refresh = args.source_mode == "refresh"
        try:
            artifact = data_source.materialize(refresh=refresh)
            if getattr(artifact, "source_name", "") == "celestrak_starlink_catalog_table_fallback":
                catalog_selection_mode = "catalog_table_plus_catnr_gp_tle_fallback"
            parse_result = parse_tle_catalog(artifact.raw_text)
            selection = select_starlink_shell(
                parse_result.records,
                max_satellites=int(args.max_satellites),
            )
        except Exception as exc:
            if local_mode:
                raise
            catalog_selection_mode = "catalog_table_plus_catnr_gp_tle_fallback"
            table_html = fetch_starlink_catalog_table_html()
            catalog_parse_result = parse_starlink_catalog_table(table_html)
            clustered_rows = _table_shell_cluster(catalog_parse_result.records)
            sampled_rows = _sample_evenly(
                clustered_rows,
                min(len(clustered_rows), max(int(args.max_satellites) * 2, 96)),
            )
            sampled_tle_records = _fetch_sampled_tle_records(sampled_rows)
            fallback_raw_text = (
                "\n".join(
                    line
                    for record in sampled_tle_records
                    for line in (record.satellite_name, record.line1, record.line2)
                )
                + "\n"
            )
            parse_result = parse_tle_catalog(fallback_raw_text)
            selection = select_starlink_shell(
                parse_result.records,
                max_satellites=int(args.max_satellites),
            )
            artifact = write_orbit_cache_artifact(
                cache_path=tle_path,
                manifest_path=manifest_path,
                source_name="celestrak_starlink_catalog_table_fallback",
                source_url=str(args.source_url),
                orbit_format="tle_subset_via_catnr",
                raw_text=fallback_raw_text,
                extra_metadata={
                    "fallback_reason": str(exc),
                    "fallback_table_url": "https://celestrak.org/NORAD/ELEMENTS/table.php?GROUP=starlink",
                    "catalog_row_count": len(catalog_parse_result.records),
                    "sampled_catnr_count": len(sampled_rows),
                    "sampled_catnr_ids": [row.norad_id for row in sampled_rows],
                    "current_as_of_utc": catalog_parse_result.current_as_of_utc,
                },
            )
        record_summary = dict(parse_result.summary)
    start_time = _parse_start_time(args.start_time)
    effective_sample_interval, effective_slice_count, effective_horizon_seconds = _effective_sampling(
        sample_interval=args.sample_interval,
        step_seconds=float(args.step_seconds),
        slice_count=int(args.slice_count),
        horizon_seconds=args.horizon_seconds,
    )
    effective_event_evaluation_interval = _effective_event_evaluation_interval(
        sample_interval_seconds=effective_sample_interval,
        event_evaluation_interval_seconds=args.event_evaluation_interval,
    )
    evaluation_slice_count = (
        int(math.floor(float(effective_horizon_seconds) / effective_event_evaluation_interval)) + 1
    )
    time_slices = _time_slices(
        start_time=start_time,
        step_seconds=effective_event_evaluation_interval,
        slice_count=evaluation_slice_count,
    )
    if args.orbit_input_mode == "planned-shell":
        propagated_slices = propagate_planned_shell_records(
            selection.selected_records,
            start_time=start_time,
            step_seconds=effective_event_evaluation_interval,
            slice_count=evaluation_slice_count,
        )
    else:
        propagated_slices = propagate_starlink_records(
            selection.selected_records,
            start_time=start_time,
            step_seconds=effective_event_evaluation_interval,
            slice_count=evaluation_slice_count,
        )
    layout = build_starlink_shell_layout(selection)
    coordinate_policy = (
        str(args.coordinate_policy)
        if args.coordinate_policy is not None
        else (
            "real_geographic"
            if str(args.ground_placement) == "catalog"
            else "shell_aware_consistent"
        )
    )
    effective_ut_definitions = [dict(entry) for entry in selected_uts]
    effective_zone_catalog = [dict(entry) for entry in zone_catalog]
    coordinate_semantics = {
        "mode": "real_geographic",
        "original_catalog_coordinates": "latitude_deg/longitude_deg in input catalogs",
        "effective_runtime_coordinates": "same as catalog coordinates",
        "coverage_coordinates": "effective_runtime_coordinates",
        "zone_anchor_coordinates": "effective_runtime_coordinates",
        "ut_attach_coordinates": "effective_runtime_coordinates",
        "gw_selection_coordinates": "effective_runtime_coordinates",
        "gz_fec_coordinates": "effective_runtime_coordinates",
        "mixing_relocated_ground_with_fixed_zone_centers": False,
    }
    if coordinate_policy == "real_geographic":
        effective_ground_model = derive_starlink_coordinate_consistent_ground_model(
            propagated_slices[0],
            ut_definitions=selected_uts,
            gateway_definitions=effective_gateway_definitions,
            zone_definitions=zone_catalog,
            coordinate_policy=coordinate_policy,
        )
        preview_ground_locations = None
        effective_ut_definitions = [
            dict(entry)
            for entry in list(effective_ground_model["ut_definitions"])
        ]
        effective_gateway_definitions = [
            dict(entry)
            for entry in list(effective_ground_model["gateway_definitions"])
        ]
        effective_zone_catalog = [
            dict(entry)
            for entry in list(effective_ground_model["zone_definitions"])
        ]
        coordinate_semantics = dict(effective_ground_model["coordinate_semantics"])
    elif coordinate_policy in {"shell_aware_consistent", "synthetic_shell_aware"}:
        if zone_catalog:
            effective_ground_model = derive_starlink_coordinate_consistent_ground_model(
                propagated_slices[0],
                ut_definitions=selected_uts,
                gateway_definitions=effective_gateway_definitions,
                zone_definitions=zone_catalog,
                coordinate_policy=coordinate_policy,
            )
            preview_ground_locations = dict(effective_ground_model["ground_locations"])
            effective_ut_definitions = [
                dict(entry)
                for entry in list(effective_ground_model["ut_definitions"])
            ]
            effective_gateway_definitions = [
                dict(entry)
                for entry in list(effective_ground_model["gateway_definitions"])
            ]
            effective_zone_catalog = [
                dict(entry)
                for entry in list(effective_ground_model["zone_definitions"])
            ]
            coordinate_semantics = dict(effective_ground_model["coordinate_semantics"])
        else:
            preview_ground_locations = derive_starlink_preview_ground_locations(
                propagated_slices[0],
                ut_ids=[str(entry["ut_id"]) for entry in selected_uts],
                gateway_ids=[
                    str(entry["gateway_id"]) for entry in effective_gateway_definitions
                ],
            )
            coordinate_semantics = {
                "mode": coordinate_policy,
                "original_catalog_coordinates": "preserved in selected UT/GW catalogs",
                "effective_runtime_coordinates": "derived from first propagated shell slice",
                "coverage_coordinates": "effective_runtime_coordinates",
                "zone_anchor_coordinates": "derived_from_ut_membership",
                "ut_attach_coordinates": "effective_runtime_coordinates",
                "gw_selection_coordinates": "effective_runtime_coordinates",
                "gz_fec_coordinates": "effective_runtime_coordinates",
                "mixing_relocated_ground_with_fixed_zone_centers": False,
            }
    else:
        raise ValueError(f"unsupported coordinate policy: {coordinate_policy}")
    scenario = build_starlink_preview_scenario(
        layout=layout,
        time_slices=time_slices,
        ut_definitions=effective_ut_definitions,
        gateway_definitions=effective_gateway_definitions,
        zone_definitions=effective_zone_catalog,
        ground_locations=preview_ground_locations,
        mini_internet_ix_offset=int(args.mini_internet_ix_offset),
    )
    effective_ground_locations = scenario.ground_locations
    emu = build_starlink_shell_emulator(
        layout,
        ut_definitions=effective_ut_definitions,
        gateway_definitions=effective_gateway_definitions,
        mini_internet_node_prefix=str(args.mini_internet_node_prefix),
        mini_internet_ix_offset=int(args.mini_internet_ix_offset),
    )
    seed_rendered = False
    seed_compiled = False
    seed_render_wall_seconds = 0.0
    seed_compile_output_wall_seconds = 0.0
    startup_profile_patch_summary = {
        "patched_node_count": 0,
        "patched_nodes": [],
    }
    if not bool(args.skip_seed_render):
        render_started_at = time.perf_counter()
        emu.render()
        seed_render_wall_seconds = time.perf_counter() - render_started_at
        seed_rendered = True
        if bool(args.compile_seed_output):
            compile_started_at = time.perf_counter()
            emu.compile(
                Docker(
                    selfManagedNetwork=True,
                    platform=resolve_platform(str(args.platform)),
                    namingScheme="{name}",
                    dummyNetworksPool=str(args.docker_dummy_network_pool),
                    dummyNetworksMask=int(args.docker_dummy_network_mask),
                    internetMapEnabled=not bool(args.disable_seed_internet_map),
                    internetMapPort=int(args.seed_internet_map_port),
                ),
                str(output_dir),
                override=True,
            )
            seed_compile_output_wall_seconds = time.perf_counter() - compile_started_at
            seed_compiled = True

    topology_inventory = build_starlink_shell_topology_inventory(
        layout,
        ut_definitions=effective_ut_definitions,
        gateway_definitions=effective_gateway_definitions,
        orbit_input_kind=orbit_input_kind,
        orbit_source=orbit_source,
        mini_internet_ix_offset=int(args.mini_internet_ix_offset),
        ut_slots_per_terminal=1 + max(0, int(args.ut_slot_margin)),
        gateway_slots_per_gateway=1 + max(0, int(args.gateway_slot_margin)),
        cross_plane_slot_model="candidate_pair",
    )
    if seed_compiled:
        startup_profile_patch_summary = _apply_skyseed_lightweight_startup_profiles(
            output_dir=output_dir,
            topology_inventory=topology_inventory,
        )
    link_state = build_starlink_shell_initial_link_state(topology_inventory)
    write_starlink_shell_artifacts(
        topology_json_path=topology_json_path,
        link_state_json_path=link_state_json_path,
        topology_inventory=topology_inventory,
        link_state=link_state,
    )
    pipeline = build_starlink_topology_snapshot_pipeline(
        topology_path=topology_json_path,
        scenario=scenario,
        propagated_slices=propagated_slices,
    )
    snapshots = pipeline.produce_snapshots()
    observation_index_set: set[int] = set()
    observation_tolerance = max(0.001, float(effective_sample_interval) * 0.001)
    for index, orbit_slice in enumerate(propagated_slices):
        relative_time = float(orbit_slice.relative_time_seconds)
        multiple = round(relative_time / float(effective_sample_interval))
        if abs(relative_time - (multiple * float(effective_sample_interval))) <= observation_tolerance:
            observation_index_set.add(index)
    observation_index_set.add(0)
    observation_index_set.add(max(0, len(propagated_slices) - 1))
    observation_slice_ids = [
        str(propagated_slices[index].slice_id)
        for index in sorted(observation_index_set)
    ]

    shell_manifest_payload = {
        "orbit_input_mode": args.orbit_input_mode,
        "orbit_data_source": {
            "source_name": (
                artifact.source_name
                if hasattr(artifact, "source_name")
                else artifact["source_name"]
            ),
            "source_url": (
                artifact.source_url
                if hasattr(artifact, "source_url")
                else artifact["source_url"]
            ),
            "cache_path": (
                artifact.cache_path
                if hasattr(artifact, "cache_path")
                else artifact["cache_path"]
            ),
            "manifest_path": (
                artifact.manifest_path
                if hasattr(artifact, "manifest_path")
                else artifact["manifest_path"]
            ),
            "download_timestamp_utc": (
                artifact.fetched_at_utc
                if hasattr(artifact, "fetched_at_utc")
                else artifact["fetched_at_utc"]
            ),
            "format": (
                artifact.orbit_format
                if hasattr(artifact, "orbit_format")
                else artifact["orbit_format"]
            ),
            "sha256": (
                artifact.sha256
                if hasattr(artifact, "sha256")
                else artifact["sha256"]
            ),
        },
        "orbit_input_kind": orbit_input_kind,
        "orbit_source": orbit_source,
        "selection_input_mode": catalog_selection_mode,
        "parse_summary": record_summary,
        "catalog_parse_summary": (
            {
                "record_count": len(catalog_parse_result.records),
                "current_as_of_utc": catalog_parse_result.current_as_of_utc,
            }
            if catalog_parse_result is not None
            else None
        ),
        "ground_model": {
            "ut_catalog": [dict(entry) for entry in ut_catalog],
            "selected_uts": [dict(entry) for entry in original_selected_uts],
            "effective_uts": [dict(entry) for entry in effective_ut_definitions],
            "gateway_catalog": [dict(entry) for entry in gateway_catalog],
            "selected_gateways": [dict(entry) for entry in original_selected_gateways],
            "effective_gateways": [dict(entry) for entry in effective_gateway_definitions],
            "zone_catalog": [dict(entry) for entry in original_zone_catalog],
            "effective_zone_catalog": [dict(entry) for entry in effective_zone_catalog],
            "coordinate_semantics": dict(coordinate_semantics),
        },
        "shell_selection": selection.manifest,
        "layout": layout.to_dict(),
        "effective_ground_locations": {
            node_id: {
                "latitude_deg": location.latitude_deg,
                "longitude_deg": location.longitude_deg,
                "altitude_km": location.altitude_km,
            }
            for node_id, location in sorted(effective_ground_locations.items())
        },
        "temporal_policy": {
            "sample_interval_seconds": effective_sample_interval,
            "event_evaluation_interval_seconds": effective_event_evaluation_interval,
            "horizon_seconds": effective_horizon_seconds,
            "parameter_max_interval": (
                float(args.parameter_max_interval)
                if args.parameter_max_interval is not None
                else None
            ),
            "delay_threshold_ms": float(args.delay_threshold_ms),
            "loss_threshold_pct": float(args.loss_threshold_pct),
            "bandwidth_threshold_pct": float(args.bandwidth_threshold_pct),
            "routing_cost_threshold": int(args.routing_cost_threshold),
            "ut_slot_margin": int(args.ut_slot_margin),
            "gateway_slot_margin": int(args.gateway_slot_margin),
            "satellite_isl_slot_margin": int(args.satellite_isl_slot_margin),
        },
        "data_provenance": {
            "orbit": {
                "orbit_mode": args.orbit_input_mode,
                "shell_preset": str(args.planned_shell_id) if args.orbit_input_mode == "planned-shell" else None,
                "position_generation_model": orbit_input_kind,
                "source_name": (
                    artifact.source_name if hasattr(artifact, "source_name") else artifact["source_name"]
                ),
                "source_url": (
                    artifact.source_url if hasattr(artifact, "source_url") else artifact["source_url"]
                ),
                "source_path": (
                    artifact.cache_path if hasattr(artifact, "cache_path") else artifact["cache_path"]
                ),
                "source_checksum": (
                    artifact.sha256 if hasattr(artifact, "sha256") else artifact["sha256"]
                ),
                "epoch_range_utc": {
                    "min": record_summary.get("epoch_utc_min"),
                    "max": record_summary.get("epoch_utc_max"),
                },
            },
            "ground": {
                "coordinate_semantics": dict(coordinate_semantics),
                "ut_coordinates": [
                    {
                        "ut_id": str(entry["ut_id"]),
                        "source": entry.get("source"),
                        "type": entry.get("type"),
                        "catalog_coordinates": {
                            "latitude_deg": entry.get("latitude_deg"),
                            "longitude_deg": entry.get("longitude_deg"),
                            "altitude_km": entry.get("altitude_km", 0.0),
                        },
                        "effective_coordinates": {
                            "latitude_deg": effective_ground_locations[str(entry["ut_id"])].latitude_deg,
                            "longitude_deg": effective_ground_locations[str(entry["ut_id"])].longitude_deg,
                            "altitude_km": effective_ground_locations[str(entry["ut_id"])].altitude_km,
                        },
                        "user_provided": bool(args.ut_ids),
                    }
                    for entry in original_selected_uts
                ],
                "gateway_coordinates": [
                    {
                        "gateway_id": str(entry["gateway_id"]),
                        "source": entry.get("source"),
                        "type": entry.get("type"),
                        "confidence": entry.get("confidence"),
                        "catalog_coordinates": {
                            "latitude_deg": entry.get("latitude_deg"),
                            "longitude_deg": entry.get("longitude_deg"),
                            "altitude_km": entry.get("altitude_km", 0.0),
                        },
                        "effective_coordinates": {
                            "latitude_deg": effective_ground_locations[str(entry["gateway_id"])].latitude_deg,
                            "longitude_deg": effective_ground_locations[str(entry["gateway_id"])].longitude_deg,
                            "altitude_km": effective_ground_locations[str(entry["gateway_id"])].altitude_km,
                        },
                    }
                    for entry in original_selected_gateways
                ],
                "zone_coordinates": [
                    {
                        "zone_id": str(original["zone_id"]),
                        "source": original.get("source"),
                        "service_role": original.get("service_role"),
                        "active_service": original.get("active_service"),
                        "required_for_runtime": original.get("required_for_runtime"),
                        "catalog_coordinates": {
                            "latitude_deg": original.get("latitude_deg"),
                            "longitude_deg": original.get("longitude_deg"),
                            "altitude_km": original.get("altitude_km", 0.0),
                        },
                        "effective_coordinates": {
                            "latitude_deg": effective.get("latitude_deg"),
                            "longitude_deg": effective.get("longitude_deg"),
                            "altitude_km": effective.get("altitude_km", 0.0),
                        },
                        "ut_ids": list(original.get("ut_ids", [])),
                        "default_gateway": original.get("default_gateway"),
                        "secondary_gateway": original.get("secondary_gateway"),
                        "preferred_gateway_ids": list(
                            original.get("preferred_gateway_ids", [])
                        ),
                    }
                    for original in original_zone_catalog
                    for effective in [
                        next(
                            (
                                dict(entry)
                                for entry in effective_zone_catalog
                                if str(entry.get("zone_id")) == str(original.get("zone_id"))
                            ),
                            dict(original),
                        )
                    ]
                ],
            },
            "mini_internet": {
                "mode": "seed_generated_mini_internet_subset",
                "is_seed_generated": True,
                "gateway_to_ix_bindings": [
                    {
                        "gateway_id": str(entry["gateway_id"]),
                        "ix_id": int(entry["ix_id"]),
                        "ix_ip_address": str(entry["ix_ip_address"]),
                    }
                    for entry in effective_gateway_definitions
                ],
                "retained_ping_test_hosts": [
                    {
                        "asn": 154,
                        "node_id": "as154-host-new",
                        "ip_address": "10.154.0.129",
                    },
                    {
                        "asn": 160,
                        "node_id": "as160-host1",
                        "ip_address": "10.160.0.72",
                    },
                ],
            },
        },
    }
    _write_json(shell_manifest_json_path, shell_manifest_payload)
    _write_json(
        effective_workload_catalog_json_path,
        {
            "schema": "skyseed_effective_workload_catalog_v1",
            "ground_placement": str(args.ground_placement),
            "coordinate_policy": str(coordinate_policy),
            "coordinate_semantics": dict(coordinate_semantics),
            "original_catalogs": {
                "uts": [dict(entry) for entry in original_selected_uts],
                "gateways": [dict(entry) for entry in original_selected_gateways],
                "zones": [dict(entry) for entry in original_zone_catalog],
            },
            "effective_catalogs": {
                "uts": [dict(entry) for entry in effective_ut_definitions],
                "gateways": [dict(entry) for entry in effective_gateway_definitions],
                "zones": [dict(entry) for entry in effective_zone_catalog],
            },
            "effective_ground_locations": {
                node_id: {
                    "latitude_deg": location.latitude_deg,
                    "longitude_deg": location.longitude_deg,
                    "altitude_km": location.altitude_km,
                }
                for node_id, location in sorted(effective_ground_locations.items())
            },
        },
    )
    _write_json(
        propagated_positions_json_path,
        {
            "start_time_utc": start_time.isoformat().replace("+00:00", "Z"),
            "step_seconds": effective_event_evaluation_interval,
            "slice_count": evaluation_slice_count,
            "horizon_seconds": effective_horizon_seconds,
            "slices": [orbit_slice.to_dict() for orbit_slice in propagated_slices],
            "observation_interval_seconds": effective_sample_interval,
            "observation_slice_ids": observation_slice_ids,
        },
    )
    _write_json(
        snapshot_preview_json_path,
        {
            "pipeline": pipeline.metadata(),
            "snapshots": [snapshot.to_dict() for snapshot in snapshots],
        },
    )

    planner = UtInternetExperimentPlanner()
    current_slot_state = dynamic_slot_state_from_link_state(link_state)
    current_state_id = "starlink_shell_preview_link_state"
    runtime_parameter_mutation_policy = make_runtime_parameter_mutation_policy(
        delay_threshold_ms=float(args.delay_threshold_ms),
        loss_threshold_pct=float(args.loss_threshold_pct),
        bandwidth_threshold_pct=float(args.bandwidth_threshold_pct),
        routing_cost_threshold=int(args.routing_cost_threshold),
    )
    planner_preview_records: List[Dict[str, Any]] = []
    runtime_update_plans: List[Any] = []
    planner_preview_all_valid = True
    for snapshot in snapshots:
        topology_validation = validate_topology_snapshot(snapshot)
        runtime_update_plan = planner.plan(snapshot)
        runtime_update_plans.append(runtime_update_plan)
        runtime_update_validation = validate_runtime_update_plan(runtime_update_plan)
        planner_flow_validation = planner.validate_flow(snapshot, runtime_update_plan)
        network_mutation_plan = build_network_mutation_plan(
            source_state=current_slot_state,
            source_state_id=current_state_id,
            target_snapshot=snapshot,
            transition_name=f"{current_state_id}_to_{snapshot.time_slice.get('slice_id')}",
            runtime_parameter_mutation_policy=runtime_parameter_mutation_policy,
        )
        network_mutation_validation = validate_network_mutation_plan(network_mutation_plan)
        snapshot_record = {
            "slice_id": snapshot.time_slice.get("slice_id"),
            "absolute_time_utc": snapshot.time_slice.get("absolute_time_utc"),
            "node_count": len(snapshot.nodes),
            "active_link_count": len(snapshot.active_links),
            "zone_count": len(snapshot.zones),
            "active_serving_satellite_ids": {
                zone_id: zone.get("active_serving_satellite_id")
                for zone_id, zone in sorted(snapshot.zones.items())
            },
            "gateway_exit_summary": _gateway_exit_summary(runtime_update_plan.to_dict()),
            "topology_snapshot_validation": topology_validation.to_dict(),
            "runtime_update_plan_validation": runtime_update_validation.to_dict(),
            "planner_flow_validation": planner_flow_validation.to_dict(),
            "network_mutation_plan_validation": network_mutation_validation.to_dict(),
            "runtime_update_plan": runtime_update_plan.to_dict(),
            "network_mutation_plan_summary": {
                "mutation_id": network_mutation_plan.mutation_id,
                "operation_count": len(network_mutation_plan.operations),
                "slot_names": [
                    str(operation.get("slot_name"))
                    for operation in network_mutation_plan.operations
                ],
            },
        }
        planner_preview_all_valid = planner_preview_all_valid and all(
            (
                topology_validation.is_valid,
                runtime_update_validation.is_valid,
                planner_flow_validation.is_valid,
                network_mutation_validation.is_valid,
            )
        )
        planner_preview_records.append(snapshot_record)
        current_slot_state = dynamic_slot_state_from_snapshot(snapshot)
        current_state_id = snapshot.snapshot_id

    gz_planning_artifacts = build_gz_planning_artifacts(
        snapshots=snapshots,
        runtime_update_plans=runtime_update_plans,
    )
    for index, snapshot_record in enumerate(planner_preview_records):
        fec_plan = dict(gz_planning_artifacts["gz_fec_plan"]["plans"][index])
        diff_summary = dict(gz_planning_artifacts["gz_update_diff_summary"]["diffs"][index])
        snapshot_record["gz_fec_plan_summary"] = {
            "model": fec_plan.get("model"),
            "flow_count": fec_plan.get("flow_count"),
            "forward_fec_count": fec_plan.get("forward_fec_count"),
            "return_fec_count": fec_plan.get("return_fec_count"),
            "backbone_state_scales_with_ut_count": dict(
                fec_plan.get("planning_scope", {})
            ).get("backbone_state_scales_with_ut_count"),
        }
        snapshot_record["gz_update_diff_summary"] = {
            "model": diff_summary.get("model"),
            "changed_zones": list(diff_summary.get("changed_zones", [])),
            "changed_fec_group_count": diff_summary.get("changed_fec_group_count"),
            "changed_gz_return_group_count": diff_summary.get(
                "changed_gz_return_group_count"
            ),
            "skipped_unchanged_fec_group_count": diff_summary.get(
                "skipped_unchanged_fec_group_count"
            ),
            "affected_node_count": diff_summary.get("affected_node_count"),
            "estimated_lfib_route_operation_items": diff_summary.get(
                "estimated_lfib_route_operation_items"
            ),
            "diff_scope": diff_summary.get("diff_scope"),
        }

    planner_preview_payload = {
        "planner": planner.metadata(),
        "all_valid": planner_preview_all_valid,
        "snapshots": planner_preview_records,
    }
    _write_json(planner_preview_json_path, planner_preview_payload)
    _write_json(zone_model_json_path, gz_planning_artifacts["zone_model"])
    _write_json(zone_anchor_schedule_json_path, gz_planning_artifacts["zone_anchor_schedule"])
    _write_json(gz_fec_plan_json_path, gz_planning_artifacts["gz_fec_plan"])
    _write_json(
        gz_update_diff_summary_json_path,
        gz_planning_artifacts["gz_update_diff_summary"],
    )
    _write_json(
        zone_anchor_quality_summary_json_path,
        gz_planning_artifacts["zone_anchor_quality_summary"],
    )
    _write_json(
        zone_availability_analysis_json_path,
        gz_planning_artifacts["zone_availability_analysis"],
    )

    temporal_plan = build_temporal_plan(
        topology_inventory=topology_inventory,
        link_state_path=link_state_json_path,
        propagated_slices=propagated_slices,
        snapshots=snapshots,
        runtime_update_plans=runtime_update_plans,
        parameter_max_interval=(
            float(args.parameter_max_interval)
            if args.parameter_max_interval is not None
            else None
        ),
        delay_threshold_ms=float(args.delay_threshold_ms),
        loss_threshold_pct=float(args.loss_threshold_pct),
        bandwidth_threshold_pct=float(args.bandwidth_threshold_pct),
        routing_cost_threshold=int(args.routing_cost_threshold),
        ut_slot_margin=int(args.ut_slot_margin),
        gateway_slot_margin=int(args.gateway_slot_margin),
        satellite_isl_slot_margin=int(args.satellite_isl_slot_margin),
        observation_interval_seconds=effective_sample_interval,
        event_evaluation_interval_seconds=effective_event_evaluation_interval,
    )
    coarse_indices = sorted(observation_index_set)
    coarse_temporal_plan = build_sample_boundary_temporal_plan(
        topology_inventory=topology_inventory,
        link_state_path=link_state_json_path,
        propagated_slices=[propagated_slices[index] for index in coarse_indices],
        snapshots=[snapshots[index] for index in coarse_indices],
        runtime_update_plans=[runtime_update_plans[index] for index in coarse_indices],
        parameter_max_interval=(
            float(args.parameter_max_interval)
            if args.parameter_max_interval is not None
            else None
        ),
        delay_threshold_ms=float(args.delay_threshold_ms),
        loss_threshold_pct=float(args.loss_threshold_pct),
        bandwidth_threshold_pct=float(args.bandwidth_threshold_pct),
        routing_cost_threshold=int(args.routing_cost_threshold),
        ut_slot_margin=int(args.ut_slot_margin),
        gateway_slot_margin=int(args.gateway_slot_margin),
        satellite_isl_slot_margin=int(args.satellite_isl_slot_margin),
    )
    temporal_plan_payload = temporal_plan.to_dict()
    temporal_plan_payload["policy"] = {
        "sample_interval_seconds": effective_sample_interval,
        "event_evaluation_interval_seconds": effective_event_evaluation_interval,
        "horizon_seconds": effective_horizon_seconds,
        "parameter_max_interval": (
            float(args.parameter_max_interval)
            if args.parameter_max_interval is not None
            else None
        ),
        "delay_threshold_ms": float(args.delay_threshold_ms),
        "loss_threshold_pct": float(args.loss_threshold_pct),
        "bandwidth_threshold_pct": float(args.bandwidth_threshold_pct),
        "routing_cost_threshold": int(args.routing_cost_threshold),
    }
    coarse_delay_rows: List[Dict[str, Any]] = []
    for event in list(temporal_plan_payload.get("event_queue", [])):
        event_type = str(event.get("event_type"))
        if event_type not in {"runtime_epoch", "parameter_epoch"}:
            continue
        activation_relative = float(event.get("activation_relative_time_seconds", 0.0))
        coarse_activation_relative = math.ceil(
            activation_relative / float(effective_sample_interval)
        ) * float(effective_sample_interval)
        coarse_delay_rows.append(
            {
                "event_id": event.get("event_id"),
                "event_type": event_type,
                "slice_id": event.get("slice_id"),
                "event_time_seconds": round(activation_relative, 6),
                "coarse_queue_time_seconds": round(coarse_activation_relative, 6),
                "coarse_queue_delay_seconds": round(
                    max(0.0, coarse_activation_relative - activation_relative),
                    6,
                ),
            }
        )
    temporal_plan_payload["queue_model_comparison"] = {
        "coarse_sample_boundary_queue": {
            "runtime_epoch_count": len(coarse_temporal_plan.runtime_epochs),
            "parameter_epoch_count": len(coarse_temporal_plan.parameter_epochs),
            "observation_event_count": len(coarse_temporal_plan.observation_frames),
            "evaluation_sample_count": len(coarse_temporal_plan.orbit_samples),
        },
        "event_time_queue": {
            "runtime_epoch_count": len(temporal_plan.runtime_epochs),
            "parameter_epoch_count": len(temporal_plan.parameter_epochs),
            "observation_event_count": len(temporal_plan.observation_frames),
            "evaluation_sample_count": len(temporal_plan.orbit_samples),
        },
        "delay_relative_to_detected_event_time": {
            "maximum_seconds": round(
                max(
                    (
                        float(row.get("coarse_queue_delay_seconds", 0.0))
                        for row in coarse_delay_rows
                    ),
                    default=0.0,
                ),
                6,
            ),
            "average_seconds": round(
                (
                    sum(float(row.get("coarse_queue_delay_seconds", 0.0)) for row in coarse_delay_rows)
                    / float(len(coarse_delay_rows))
                )
                if coarse_delay_rows
                else 0.0,
                6,
            ),
            "per_event": coarse_delay_rows,
        },
    }
    _write_json(temporal_plan_json_path, temporal_plan_payload)
    preview_update_counters = dict(temporal_plan_payload.get("preview_update_counters", {}))

    risky_coverage_margin_zones: List[Dict[str, Any]] = []
    zone_quality_summary = dict(gz_planning_artifacts.get("zone_anchor_quality_summary", {}))
    for zone in list(zone_quality_summary.get("zones", [])):
        min_margin = zone.get("coverage_margin_deg_min")
        if min_margin is None:
            continue
        if float(min_margin) <= 3.0:
            risky_coverage_margin_zones.append(
                {
                    "zone_id": zone.get("zone_id"),
                    "coverage_margin_deg_min": min_margin,
                    "handover_count": zone.get("handover_count"),
                    "unresolved_count": zone.get("unresolved_count"),
                }
            )
    selected_ut_roles: Dict[str, int] = {}
    for ut in effective_ut_definitions:
        role = str(
            ut.get("role")
            or ut.get("workload_role")
            or ut.get("placement_role")
            or "unspecified"
        )
        selected_ut_roles[role] = selected_ut_roles.get(role, 0) + 1
    boundary_ut_count = sum(
        count
        for role, count in selected_ut_roles.items()
        if "boundary" in role or "zone-boundary" in role
    )
    remote_return_ut_count = sum(
        count
        for role, count in selected_ut_roles.items()
        if "remote" in role or "return" in role
    )
    s4_preview_readiness = {
        "schema_version": "skyseed-s4-preview-readiness-v1",
        "dry_run_preview_only": True,
        "real_runtime_executed": False,
        "planned_shell_id": args.planned_shell_id if args.orbit_input_mode == "planned-shell" else None,
        "is_s4_preview": (
            args.orbit_input_mode == "planned-shell"
            and str(args.planned_shell_id).upper() == "S4"
        ),
        "expected_runtime_epoch_density": (
            len(temporal_plan.runtime_epochs) / len(temporal_plan.orbit_samples)
            if temporal_plan.orbit_samples
            else 0.0
        ),
        "expected_parameter_epoch_density": (
            len(temporal_plan.parameter_epochs) / len(temporal_plan.orbit_samples)
            if temporal_plan.orbit_samples
            else 0.0
        ),
        "expected_gz_fec_count": {
            "forward_fec_count": sum(
                len(list(plan.get("forward_fecs", [])))
                for plan in list(gz_planning_artifacts.get("gz_fec_plan", {}).get("plans", []))
            ),
            "return_fec_count": sum(
                len(list(plan.get("return_fecs", [])))
                for plan in list(gz_planning_artifacts.get("gz_fec_plan", {}).get("plans", []))
            ),
        },
        "expected_bpf_owned_scope_count": preview_update_counters.get(
            "expected_bpf_owned_scope_count"
        ),
        "ut_gw_zone_coverage_summary": {
            "selected_ut_count": len(effective_ut_definitions),
            "selected_gateway_count": len(effective_gateway_definitions),
            "selected_zone_count": len(effective_zone_catalog),
            "ut_role_counts": selected_ut_roles,
        },
        "coordinate_semantics": dict(coordinate_semantics),
        "boundary_ut_count": boundary_ut_count,
        "remote_return_ut_count": remote_return_ut_count,
        "risky_coverage_margin_zones": risky_coverage_margin_zones,
        "recommendation": (
            "s4_preview_suitable_for_bounded_real_runtime"
            if (
                args.orbit_input_mode == "planned-shell"
                and str(args.planned_shell_id).upper() == "S4"
                and not risky_coverage_margin_zones
            )
            else "review_preview_or_consider_s3_720_fallback_before_real_runtime"
        ),
    }

    serving_change_events = _change_events(
        field_name="active_serving_satellite_ids",
        slice_records=planner_preview_records,
    )
    gateway_exit_change_events = _change_events(
        field_name="gateway_exit_summary",
        slice_records=planner_preview_records,
    )

    summary = {
        "orbit_input_mode": args.orbit_input_mode,
        "orbit_data_source": {
            "source_name": (
                artifact.source_name
                if hasattr(artifact, "source_name")
                else artifact["source_name"]
            ),
            "source_url": (
                artifact.source_url
                if hasattr(artifact, "source_url")
                else artifact["source_url"]
            ),
            "cache_path": (
                artifact.cache_path
                if hasattr(artifact, "cache_path")
                else artifact["cache_path"]
            ),
            "manifest_path": (
                artifact.manifest_path
                if hasattr(artifact, "manifest_path")
                else artifact["manifest_path"]
            ),
            "download_timestamp_utc": (
                artifact.fetched_at_utc
                if hasattr(artifact, "fetched_at_utc")
                else artifact["fetched_at_utc"]
            ),
            "format": (
                artifact.orbit_format
                if hasattr(artifact, "orbit_format")
                else artifact["orbit_format"]
            ),
            "sha256": (
                artifact.sha256
                if hasattr(artifact, "sha256")
                else artifact["sha256"]
            ),
        },
        "orbit_input_kind": orbit_input_kind,
        "orbit_source": orbit_source,
        "selection_input_mode": catalog_selection_mode,
        "parsed_starlink_record_count": (
            len(catalog_parse_result.records)
            if catalog_parse_result is not None
            else int(record_summary.get("record_count", 0))
        ),
        "parsed_tle_record_count": int(record_summary.get("record_count", 0)),
        "malformed_record_count": (
            len(parse_result.malformed_records)
            if parse_result is not None
            else 0
        ),
        "record_summary": record_summary,
        "shell_selection": selection.manifest,
        "selected_satellite_count": len(selection.selected_records),
        "ground_model": summarize_ground_model(
            ut_catalog=ut_catalog,
            selected_uts=effective_ut_definitions,
            gateway_catalog=gateway_catalog,
            selected_gateways=effective_gateway_definitions,
            zone_catalog=effective_zone_catalog,
        ),
        "coordinate_semantics": dict(coordinate_semantics),
        "time_slices": {
            "start_time_utc": start_time.isoformat().replace("+00:00", "Z"),
            "step_seconds": effective_event_evaluation_interval,
            "slice_count": evaluation_slice_count,
            "horizon_seconds": effective_horizon_seconds,
            "slice_ids": [orbit_slice.slice_id for orbit_slice in propagated_slices],
            "observation_interval_seconds": effective_sample_interval,
            "observation_slice_ids": observation_slice_ids,
        },
        "snapshot_preview": {
            "pipeline_metadata": pipeline.metadata(),
            "snapshot_count": len(snapshots),
            "serving_satellite_changes": serving_change_events,
            "gateway_exit_changes": gateway_exit_change_events,
            "snapshot_summaries": [
                {
                    "slice_id": record["slice_id"],
                    "absolute_time_utc": record["absolute_time_utc"],
                    "node_count": record["node_count"],
                    "active_link_count": record["active_link_count"],
                    "active_serving_satellite_ids": record["active_serving_satellite_ids"],
                    "gateway_exit_summary": record["gateway_exit_summary"],
                    "network_mutation_operation_count": record[
                        "network_mutation_plan_summary"
                    ]["operation_count"],
                }
                for record in planner_preview_records
            ],
        },
        "effective_ground_locations": {
            node_id: {
                "latitude_deg": location.latitude_deg,
                "longitude_deg": location.longitude_deg,
                "altitude_km": location.altitude_km,
            }
            for node_id, location in sorted(effective_ground_locations.items())
        },
        "planner_preview": {
            "planner_name": planner.metadata()["planner_name"],
            "all_valid": planner_preview_all_valid,
        },
        "temporal_plan": {
            "orbit_sample_count": len(temporal_plan.orbit_samples),
            "topology_snapshot_count": len(temporal_plan.orbit_samples),
            "observation_frame_count": len(temporal_plan.observation_frames),
            "event_queue_count": len(temporal_plan.event_queue),
            "runtime_epoch_count": len(temporal_plan.runtime_epochs),
            "parameter_epoch_count": len(temporal_plan.parameter_epochs),
            "no_op_sample_count": temporal_plan.no_op_sample_count,
            "event_time_summary": temporal_plan.event_time_summary,
            "preview_update_counters": preview_update_counters,
            "s4_preview_readiness": s4_preview_readiness,
            "queue_model_comparison": temporal_plan_payload.get("queue_model_comparison", {}),
            "runtime_epoch_summaries": [
                {
                    "slice_id": epoch.slice_id,
                    "trigger_reasons": list(epoch.trigger_reasons),
                    "active_link_count": epoch.active_link_count,
                    "changed_link_count": len(epoch.changed_link_ids),
                    "network_mutation_operation_count": epoch.network_mutation_operation_count,
                }
                for epoch in temporal_plan.runtime_epochs
            ],
            "parameter_epoch_summaries": [
                {
                    "slice_id": epoch.slice_id,
                    "trigger_reasons": list(epoch.trigger_reasons),
                    "active_link_count": epoch.active_link_count,
                    "changed_link_count": len(epoch.changed_link_ids),
                    "changed_parameter_counts": dict(epoch.changed_parameter_counts),
                }
                for epoch in temporal_plan.parameter_epochs
            ],
            "resource_budget": temporal_plan.resource_budget,
            "preparation_plan": temporal_plan.preparation_plan,
        },
        "topology_artifacts": {
            "topology_json": str(topology_json_path),
            "link_state_json": str(link_state_json_path),
            "shell_manifest_json": str(shell_manifest_json_path),
            "propagated_positions_json": str(propagated_positions_json_path),
            "snapshot_preview_json": str(snapshot_preview_json_path),
            "planner_preview_json": str(planner_preview_json_path),
            "temporal_plan_json": str(temporal_plan_json_path),
            "zone_model_json": str(zone_model_json_path),
            "zone_anchor_schedule_json": str(zone_anchor_schedule_json_path),
            "gz_fec_plan_json": str(gz_fec_plan_json_path),
            "gz_update_diff_summary_json": str(gz_update_diff_summary_json_path),
            "zone_anchor_quality_summary_json": str(
                zone_anchor_quality_summary_json_path
            ),
            "zone_availability_analysis_json": str(zone_availability_analysis_json_path),
            "effective_workload_catalog_json": str(effective_workload_catalog_json_path),
        },
        "seed_topology": {
            "emulator_constructed": True,
            "rendered": seed_rendered,
            "compiled": seed_compiled,
            "output_dir": str(output_dir),
            "startup_profile_patch": startup_profile_patch_summary,
            "timing": {
                "render_wall_seconds": round(seed_render_wall_seconds, 6),
                "compile_output_wall_seconds": round(
                    seed_compile_output_wall_seconds,
                    6,
                ),
            },
        },
    }

    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print("Minimal Starlink-shell preview built successfully.")
    print(
        "Source URL: "
        f"{artifact.source_url if hasattr(artifact, 'source_url') else artifact['source_url']}"
    )
    print(
        "Cache: "
        f"{artifact.cache_path if hasattr(artifact, 'cache_path') else artifact['cache_path']}"
    )
    print(
        "Manifest: "
        f"{artifact.manifest_path if hasattr(artifact, 'manifest_path') else artifact['manifest_path']}"
    )
    print(
        "Parsed records: "
        f"{summary['parsed_starlink_record_count']} input rows, "
        f"{summary['parsed_tle_record_count']} selected-orbit records, "
        f"{summary['malformed_record_count']} malformed"
    )
    print(
        "Shell selection: "
        f"{selection.manifest['selected_satellite_count']} satellites from "
        f"{selection.manifest['selected_plane_count']} planes"
    )
    print(
        "Ground model: "
        f"{summary['ground_model']['selected_ut_count']} UTs, "
        f"{summary['ground_model']['selected_gateway_count']} gateways"
    )
    print(
        "Time slices: "
        f"{len(propagated_slices)} slices, "
        f"step={float(args.step_seconds):.1f}s, "
        f"start={start_time.isoformat().replace('+00:00', 'Z')}"
    )
    print(
        "Planner preview: "
        f"{'valid' if planner_preview_all_valid else 'invalid'} across "
        f"{len(snapshots)} snapshots"
    )
    print(f"Topology inventory: {topology_json_path}")
    print(f"Link state: {link_state_json_path}")
    print(f"Shell manifest: {shell_manifest_json_path}")
    print(f"Snapshot preview: {snapshot_preview_json_path}")
    print(f"Planner preview: {planner_preview_json_path}")
    print(f"Zone model: {zone_model_json_path}")
    print(f"G/Z FEC plan: {gz_fec_plan_json_path}")
    print(f"G/Z update diff: {gz_update_diff_summary_json_path}")
    print(
        "SEED topology: "
        f"constructed={True} rendered={seed_rendered} compiled={seed_compiled}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
