#!/usr/bin/env python3
# encoding: utf-8

"""Compile Skyseed ParameterEpoch link parameters into a qdisc timeline view."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_DIR = (
    REPO_ROOT
    / "satellite"
    / "output"
    / "starlink_shell_experiments"
    / "gz-z6metadata-2h-gated-20260611-0824"
)
DEFAULT_JSON = REPO_ROOT / "satellite" / "baselines" / "skyseed_parameter_qdisc_timeline_summary.json"

PARAMETER_FIELDS = ("delay_ms", "bandwidth_mbps", "loss_percent", "routing_cost")
THEATERQ_U32_MAX = 0xFFFFFFFF


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value


def load_artifacts(run_dir: Path) -> dict[str, Any]:
    temporal_plan_path = run_dir / "temporal_plan.json"
    if not temporal_plan_path.exists():
        temporal_plan_path = run_dir / "config" / "temporal_plan.json"
    parameter_summary_path = run_dir / "parameter_epoch_summary.json"
    details_path = run_dir / "epoch_execution_details.jsonl"
    missing = [
        str(path)
        for path in [temporal_plan_path, parameter_summary_path, details_path]
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError("missing required ParameterEpoch artifact(s): " + ", ".join(missing))
    return {
        "source_root": str(run_dir),
        "temporal_plan_path": str(temporal_plan_path),
        "parameter_epoch_summary_path": str(parameter_summary_path),
        "epoch_execution_details_path": str(details_path),
        "temporal_plan": read_json(temporal_plan_path),
        "parameter_epoch_summary": read_json(parameter_summary_path),
        "epoch_execution_details": list(iter_jsonl(details_path)),
    }


def parameter_epoch_index(temporal_plan: dict[str, Any], summary: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for source_name, records in [
        ("temporal_plan.parameter_epochs", temporal_plan.get("parameter_epochs", []) or []),
        ("parameter_epoch_summary", summary or []),
    ]:
        for record in records:
            if not isinstance(record, dict):
                continue
            epoch_id = record.get("epoch_id")
            if not isinstance(epoch_id, str):
                continue
            merged = dict(index.get(epoch_id, {}))
            merged.update(record)
            merged.setdefault("metadata_sources", [])
            merged["metadata_sources"].append(source_name)
            index[epoch_id] = merged
    return index


def is_parameter_epoch(record: dict[str, Any]) -> bool:
    return (
        record.get("epoch_type") == "parameter_epoch"
        or record.get("original_epoch_type") == "parameter_epoch"
        or str(record.get("epoch_id") or "").startswith("parameterepoch-")
    )


def scaled_loss_u32(loss_percent: float | int | None) -> int | None:
    if loss_percent is None:
        return None
    bounded = min(100.0, max(0.0, float(loss_percent)))
    return int(round((bounded / 100.0) * THEATERQ_U32_MAX))


def rate_bps(bandwidth_mbps: float | int | None) -> int | None:
    if bandwidth_mbps is None:
        return None
    return int(round(float(bandwidth_mbps) * 1_000_000))


def compact_binding(binding: Any) -> Any:
    if not isinstance(binding, dict):
        return binding
    return {key: binding[key] for key in sorted(binding)}


def build_segment(
    *,
    record: dict[str, Any],
    result: dict[str, Any],
    parameter_generation: int,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    state = result.get("resulting_state") or {}
    params = state.get("runtime_parameters") or {}
    slot_name = result.get("slot_name")
    if not isinstance(slot_name, str) or not isinstance(params, dict):
        return None

    bandwidth = params.get("bandwidth_mbps")
    delay = params.get("delay_ms")
    loss = params.get("loss_percent")
    routing_cost = params.get("routing_cost")
    return {
        "link_id": slot_name,
        "slot_type": result.get("slot_type") or params.get("slot_type"),
        "parameter_generation": parameter_generation,
        "epoch_id": record.get("epoch_id"),
        "activation_group_id": record.get("activation_group_id"),
        "activation_relative_time_seconds": record.get("activation_relative_time_seconds"),
        "activation_time_utc": record.get("activation_time_utc"),
        "end_relative_time_seconds": None,
        "end_time_utc": None,
        "slice_id": (metadata or {}).get("slice_id") or record.get("slice_id"),
        "sample_id": (metadata or {}).get("sample_id") or record.get("sample_id"),
        "snapshot_id": (metadata or {}).get("snapshot_id") or record.get("snapshot_id"),
        "trigger_reasons": (metadata or {}).get("trigger_reasons") or record.get("trigger_reasons"),
        "event_time_metadata": {
            "source": "epoch_execution_details.parameter_epoch",
            "metadata_sources": (metadata or {}).get("metadata_sources", []),
        },
        "binding": compact_binding(state.get("binding")),
        "qdisc_parameters": {
            "delay": {
                "delay_ms": delay,
                "jitter_ms": None,
                "source_field": "runtime_parameters.delay_ms",
            },
            "rate": {
                "bandwidth_mbps": bandwidth,
                "rate_bps": rate_bps(bandwidth),
                "source_field": "runtime_parameters.bandwidth_mbps",
            },
            "loss": {
                "loss_percent": loss,
                "loss_scaled_u32": scaled_loss_u32(loss),
                "source_field": "runtime_parameters.loss_percent",
            },
            "queue": {
                "limit_packets": None,
                "limit_bytes": None,
                "source": "not_present_in_parameter_epoch_artifact",
            },
            "reorder": {
                "enabled": False,
                "route_id": None,
                "queue_id": None,
                "source": "not_present_in_parameter_epoch_artifact",
            },
            "routing_cost": {
                "value": routing_cost,
                "source_field": "runtime_parameters.routing_cost",
                "qdisc_direct_field": False,
            },
        },
    }


def apply_segment_end_times(link_timelines: dict[str, list[dict[str, Any]]]) -> None:
    for segments in link_timelines.values():
        segments.sort(
            key=lambda item: (
                float(item.get("activation_relative_time_seconds") or 0.0),
                int(item.get("parameter_generation") or 0),
            )
        )
        for index, segment in enumerate(segments):
            if index + 1 >= len(segments):
                continue
            next_segment = segments[index + 1]
            segment["end_relative_time_seconds"] = next_segment.get("activation_relative_time_seconds")
            segment["end_time_utc"] = next_segment.get("activation_time_utc")


def theaterq_trace_line(segment: dict[str, Any], *, default_last_keep_us: int = 1_000_000) -> str:
    end_time = segment.get("end_relative_time_seconds")
    start_time = segment.get("activation_relative_time_seconds")
    if end_time is None or start_time is None:
        keep_us = default_last_keep_us
    else:
        keep_us = max(1, int(round((float(end_time) - float(start_time)) * 1_000_000)))
    params = segment["qdisc_parameters"]
    latency_ns = int(round(float(params["delay"]["delay_ms"] or 0.0) * 1_000_000))
    jitter_ns = 0
    rate = params["rate"]["rate_bps"] if params["rate"]["rate_bps"] is not None else -1
    loss = params["loss"]["loss_scaled_u32"] if params["loss"]["loss_scaled_u32"] is not None else 0
    limit = params["queue"]["limit_packets"] or 1000
    duplicate_probability = 0
    duplicate_delay = 0
    route_id = params["reorder"]["route_id"] or 0
    queue_id = params["reorder"]["queue_id"] or 0
    return ",".join(
        str(value)
        for value in [
            keep_us,
            latency_ns,
            jitter_ns,
            rate,
            loss,
            limit,
            duplicate_probability,
            duplicate_delay,
            route_id,
            queue_id,
        ]
    )


def userspace_tc_change_template(segment: dict[str, Any]) -> str:
    params = segment["qdisc_parameters"]
    delay = params["delay"]["delay_ms"]
    bandwidth = params["rate"]["bandwidth_mbps"]
    loss = params["loss"]["loss_percent"]
    return (
        "tc qdisc change dev <ifname> root netem "
        f"delay {delay}ms rate {bandwidth}mbit loss {loss}%"
    )


def compile_qdisc_timeline(artifacts: dict[str, Any]) -> dict[str, Any]:
    metadata_by_epoch = parameter_epoch_index(
        artifacts["temporal_plan"],
        artifacts["parameter_epoch_summary"],
    )
    link_timelines: dict[str, list[dict[str, Any]]] = {}
    parameter_records = 0
    parameter_generation = 0
    skipped_records = 0
    for record in artifacts["epoch_execution_details"]:
        if not is_parameter_epoch(record):
            continue
        parameter_records += 1
        metadata = metadata_by_epoch.get(str(record.get("epoch_id") or ""))
        apply_results = ((record.get("host_mutation_execution") or {}).get("apply_results")) or []
        segment_count = 0
        for result in apply_results:
            if not isinstance(result, dict) or result.get("operation") != "update_slot_parameters":
                continue
            segment = build_segment(
                record=record,
                result=result,
                parameter_generation=parameter_generation,
                metadata=metadata,
            )
            if segment is None:
                continue
            link_timelines.setdefault(segment["link_id"], []).append(segment)
            segment_count += 1
        if segment_count == 0:
            skipped_records += 1
        parameter_generation += 1

    apply_segment_end_times(link_timelines)
    sorted_links = [
        {
            "link_id": link_id,
            "slot_type": (segments[0].get("slot_type") if segments else None),
            "segments": segments,
        }
        for link_id, segments in sorted(link_timelines.items())
    ]
    activation_times = sorted(
        {
            float(segment["activation_relative_time_seconds"])
            for segments in link_timelines.values()
            for segment in segments
            if segment.get("activation_relative_time_seconds") is not None
        }
    )
    observed_intervals = [
        round(activation_times[index + 1] - activation_times[index], 6)
        for index in range(len(activation_times) - 1)
    ]
    field_counts = {field: 0 for field in PARAMETER_FIELDS}
    for segments in link_timelines.values():
        for segment in segments:
            params = segment["qdisc_parameters"]
            if params["delay"]["delay_ms"] is not None:
                field_counts["delay_ms"] += 1
            if params["rate"]["bandwidth_mbps"] is not None:
                field_counts["bandwidth_mbps"] += 1
            if params["loss"]["loss_percent"] is not None:
                field_counts["loss_percent"] += 1
            if params["routing_cost"]["value"] is not None:
                field_counts["routing_cost"] += 1

    total_segments = sum(len(link["segments"]) for link in sorted_links)
    return {
        "schema_version": 1,
        "goal": "Skyseed Goal 12 ParameterEpoch qdisc timeline compiler",
        "generated_at": "not_recorded_for_reproducible_baseline",
        "input_artifacts": {
            "source_root": artifacts["source_root"],
            "temporal_plan_path": artifacts["temporal_plan_path"],
            "parameter_epoch_summary_path": artifacts["parameter_epoch_summary_path"],
            "epoch_execution_details_path": artifacts["epoch_execution_details_path"],
        },
        "scope": {
            "canonical_runtime_changed": False,
            "runtime_epoch_parameter_epoch_scheduler_changed": False,
            "gz_semantics_changed": False,
            "real_dataplane_touched": False,
            "long_real_runtime": False,
            "external_kernel_module_loaded": False,
            "optimized_backend_landing": False,
        },
        "division_of_labor": {
            "bpf": ["active_gate", "loss_drop", "counter", "mark", "fail_closed"],
            "qdisc": ["delay", "rate", "bandwidth", "queueing", "reordering"],
        },
        "generation_cadence": {
            "fixed_10s_assumed": False,
            "cadence_source": "artifact ParameterEpoch event-time activation records",
            "observed_activation_interval_seconds": {
                "count": len(observed_intervals),
                "min": min(observed_intervals) if observed_intervals else None,
                "max": max(observed_intervals) if observed_intervals else None,
                "unique_sample": sorted(set(observed_intervals))[:10],
            },
        },
        "qdisc_timeline_model": {
            "backend_neutral": True,
            "per_link_segments": True,
            "segment_time_fields": [
                "activation_relative_time_seconds",
                "activation_time_utc",
                "end_relative_time_seconds",
                "end_time_utc",
            ],
            "parameter_fields": [
                "delay.delay_ms",
                "rate.bandwidth_mbps",
                "rate.rate_bps",
                "loss.loss_percent",
                "loss.loss_scaled_u32",
                "queue.limit_packets",
                "reorder.route_id",
                "routing_cost.value",
            ],
            "full_timeline_available_from_compiler": True,
        },
        "link_timelines": sorted_links,
        "summary": {
            "parameter_epoch_detail_record_count": parameter_records,
            "parameter_epoch_summary_record_count": len(artifacts["parameter_epoch_summary"]),
            "skipped_parameter_records_without_qdisc_updates": skipped_records,
            "link_timeline_count": len(sorted_links),
            "timeline_segment_count": total_segments,
            "field_coverage": {
                field: {
                    "present": field_counts[field],
                    "total_segments": total_segments,
                    "coverage_ratio": field_counts[field] / total_segments if total_segments else 0.0,
                }
                for field in PARAMETER_FIELDS
            },
            "slot_type_counts": slot_type_counts(sorted_links),
        },
    }


def slot_type_counts(link_timelines: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for link in link_timelines:
        slot_type = str(link.get("slot_type") or "unknown")
        counts[slot_type] = counts.get(slot_type, 0) + 1
    return counts


def theaterq_feasibility() -> dict[str, Any]:
    return {
        "source_accessible": True,
        "repository_url": "https://github.com/cs7org/TheaterQ",
        "paper_url": "https://arxiv.org/abs/2510.27057",
        "license": "GPL-2.0",
        "source_components": ["theaterq_lkm", "theaterq_tc"],
        "build_requirements": [
            "linux kernel headers",
            "build-essential/make",
            "iproute2",
            "git submodule initialization for tc plugin",
        ],
        "kernel_module_required": True,
        "tc_plugin_required": True,
        "loaded_in_this_goal": False,
        "trace_format_fit": {
            "simple_csv": ["KEEP", "LATENCY", "RATE", "LOSS", "LIMIT"],
            "extended_csv": [
                "KEEP",
                "LATENCY",
                "JITTER",
                "RATE",
                "LOSS",
                "LIMIT",
                "DUP_PROB",
                "DUP_DELAY",
                "ROUTE_ID",
                "QUEUE_ID",
            ],
            "skyseed_delay_rate_loss_fit": True,
            "skyseed_routing_cost_direct_fit": False,
            "routing_cost_note": "routing_cost is Skyseed path metadata, not a direct qdisc field",
            "skyseed_reorder_fit": "possible with extended ROUTE_ID/QUEUE_ID if a policy maps route changes to IDs",
            "skyseed_queue_fit": "possible with LIMIT/QUEUE_ID when queue policy exists",
        },
        "per_link_timeline": True,
        "multi_qdisc_sync": {
            "supported": True,
            "mechanism": "syncgroup",
            "default_capacity_from_readme": {"syncgroups": 8, "members_per_group": 8},
            "skyseed_note": "large topologies may need module parameter sizing before an isolated demo",
        },
        "conclusion": (
            "TheaterQ is suitable to investigate in a future isolated demo, but it is "
            "an out-of-tree kernel module plus tc plugin and is not appropriate for "
            "this no-module Goal 12 run or for canonical runtime landing."
        ),
    }


def segment_sample(segment: dict[str, Any] | None) -> dict[str, Any] | None:
    if segment is None:
        return None
    fields = [
        "link_id",
        "slot_type",
        "parameter_generation",
        "epoch_id",
        "activation_relative_time_seconds",
        "activation_time_utc",
        "end_relative_time_seconds",
        "end_time_utc",
        "slice_id",
        "sample_id",
        "snapshot_id",
        "trigger_reasons",
        "binding",
        "qdisc_parameters",
    ]
    return {field: copy.deepcopy(segment.get(field)) for field in fields}


def compact_for_baseline(compiled: dict[str, Any]) -> dict[str, Any]:
    link_summaries: list[dict[str, Any]] = []
    theaterq_samples: dict[str, list[str]] = {}
    tc_change_samples: dict[str, list[str]] = {}
    for link in compiled["link_timelines"]:
        segments = link["segments"]
        first = segments[0] if segments else None
        last = segments[-1] if segments else None
        link_id = link["link_id"]
        link_summaries.append(
            {
                "link_id": link_id,
                "slot_type": link.get("slot_type"),
                "segment_count": len(segments),
                "first_segment": segment_sample(first),
                "last_segment": segment_sample(last),
            }
        )
        if len(theaterq_samples) < 3:
            theaterq_samples[link_id] = [theaterq_trace_line(segment) for segment in segments[:3]]
            tc_change_samples[link_id] = [userspace_tc_change_template(segment) for segment in segments[:3]]

    compact = {
        key: copy.deepcopy(compiled[key])
        for key in [
            "schema_version",
            "goal",
            "generated_at",
            "input_artifacts",
            "scope",
            "division_of_labor",
            "generation_cadence",
            "qdisc_timeline_model",
            "summary",
        ]
    }
    compact["link_timeline_summaries"] = link_summaries
    compact["theaterq_feasibility"] = theaterq_feasibility()
    compact["isolated_fallback_demo_scaffold"] = {
        "runs_kernel_qdisc": False,
        "loads_external_kernel_module": False,
        "purpose": "format and semantic validation before any optimized backend landing",
        "theaterq_extended_trace_samples": theaterq_samples,
        "userspace_tc_change_replay_samples": tc_change_samples,
        "note": (
            "The fallback scaffold is a trace generator and userspace tc-change "
            "replay plan only; it is not a custom kernel qdisc and does not claim "
            "TheaterQ timing fidelity."
        ),
    }
    return compact


def render_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    coverage = summary["field_coverage"]
    theaterq = report["theaterq_feasibility"]
    cadence = report["generation_cadence"]
    return "\n".join(
        [
            "Skyseed ParameterEpoch -> qdisc timeline compiler",
            f"input={report['input_artifacts']['source_root']}",
            f"parameter_epoch_detail_records={summary['parameter_epoch_detail_record_count']}",
            f"link_timelines={summary['link_timeline_count']}",
            f"timeline_segments={summary['timeline_segment_count']}",
            "field_coverage="
            + ",".join(
                f"{field}:{coverage[field]['present']}/{coverage[field]['total_segments']}"
                for field in PARAMETER_FIELDS
            ),
            f"fixed_10s_assumed={cadence['fixed_10s_assumed']}",
            f"theaterq_source_accessible={theaterq['source_accessible']}",
            f"theaterq_kernel_module_required={theaterq['kernel_module_required']}",
            f"theaterq_loaded_in_this_goal={theaterq['loaded_in_this_goal']}",
            f"fallback_scaffold_only={not report['isolated_fallback_demo_scaffold']['runs_kernel_qdisc']}",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--full-json-out", type=Path, default=None)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifacts = load_artifacts(args.run_dir)
    compiled = compile_qdisc_timeline(artifacts)
    report = compact_for_baseline(compiled)
    if args.json_out:
        write_json(args.json_out, report)
    if args.full_json_out:
        full_report = copy.deepcopy(compiled)
        full_report["theaterq_feasibility"] = theaterq_feasibility()
        write_json(args.full_json_out, full_report)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
        if args.json_out:
            print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
