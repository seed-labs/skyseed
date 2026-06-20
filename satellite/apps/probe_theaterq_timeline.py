#!/usr/bin/env python3
# encoding: utf-8

"""Summarize TheaterQ-style qdisc feasibility for a Skyseed timeline."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
from typing import Any

from satellite.apps.compile_parameter_timeline_to_qdisc import (
    DEFAULT_RUN_DIR,
    compile_qdisc_timeline,
    load_artifacts,
    scaled_loss_u32,
    userspace_tc_change_template,
    write_json,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = REPO_ROOT / "satellite" / "baselines" / "skyseed_theaterq_timeline_summary.json"
THEATERQ_REPOSITORY_URL = "https://github.com/cs7org/TheaterQ"
THEATERQ_COMMIT = "0e7da6d802796a0b992858f5d7844d4b2d4173fc"
THEATERQ_IPROUTE2_SUBMODULE_COMMIT = "72f679c0d07629fe9e462c2c52bbe48aaeaa7f83"
DEFAULT_LIMIT_PACKETS = 1000


def _duration_us(segment: dict[str, Any], *, default_last_keep_us: int = 1_000_000) -> int:
    start = segment.get("activation_relative_time_seconds")
    end = segment.get("end_relative_time_seconds")
    if start is None or end is None:
        return default_last_keep_us
    return max(1, int(round((float(end) - float(start)) * 1_000_000)))


def segment_to_theaterq_simple_row(segment: dict[str, Any]) -> list[int]:
    """Return TheaterQ simple CSV fields for a Skyseed qdisc segment.

    TheaterQ requires LIMIT in the simple format. Skyseed's current artifact has
    no queue policy, so LIMIT is a syntax default and is reported separately as
    not Skyseed-derived.
    """

    params = segment["qdisc_parameters"]
    delay_ms = params["delay"]["delay_ms"] or 0.0
    bandwidth_mbps = params["rate"]["bandwidth_mbps"]
    loss_percent = params["loss"]["loss_percent"]
    rate = -1 if bandwidth_mbps is None else int(round(float(bandwidth_mbps) * 1_000_000))
    loss = scaled_loss_u32(loss_percent) if loss_percent is not None else 0
    limit = params["queue"]["limit_packets"] or DEFAULT_LIMIT_PACKETS
    return [
        _duration_us(segment),
        int(round(float(delay_ms) * 1_000_000)),
        rate,
        loss,
        int(limit),
    ]


def segment_to_theaterq_extended_row(segment: dict[str, Any]) -> list[int]:
    simple = segment_to_theaterq_simple_row(segment)
    params = segment["qdisc_parameters"]
    jitter_ns = 0
    dup_prob = 0
    dup_delay_ns = 0
    route_id = params["reorder"]["route_id"] or 0
    queue_id = params["reorder"]["queue_id"] or 0
    return [
        simple[0],
        simple[1],
        jitter_ns,
        simple[2],
        simple[3],
        simple[4],
        dup_prob,
        dup_delay_ns,
        int(route_id),
        int(queue_id),
    ]


def csv_row(values: list[int]) -> str:
    return ",".join(str(value) for value in values)


def trace_samples(compiled: dict[str, Any], *, max_links: int = 3, max_segments: int = 3) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    for link in compiled["link_timelines"][:max_links]:
        link_id = link["link_id"]
        segments = link["segments"][:max_segments]
        samples[link_id] = {
            "simple_csv": [csv_row(segment_to_theaterq_simple_row(segment)) for segment in segments],
            "extended_csv": [csv_row(segment_to_theaterq_extended_row(segment)) for segment in segments],
            "userspace_netem_scaffold": [userspace_tc_change_template(segment) for segment in segments],
        }
    return samples


def trace_mapping_summary(compiled: dict[str, Any]) -> dict[str, Any]:
    summary = compiled["summary"]
    total = summary["timeline_segment_count"]
    coverage = summary["field_coverage"]
    return {
        "can_emit_theaterq_trace": total > 0,
        "trace_format": {
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
            "units": {
                "KEEP": "microseconds",
                "LATENCY": "nanoseconds",
                "JITTER": "nanoseconds",
                "RATE": "bits_per_second",
                "LOSS": "scaled_u32",
                "LIMIT": "packets unless TheaterQ byteqlen is selected",
            },
        },
        "field_mapping": {
            "delay": {
                "skyseed_field": "runtime_parameters.delay_ms",
                "theaterq_field": "LATENCY",
                "coverage": coverage["delay_ms"],
            },
            "rate": {
                "skyseed_field": "runtime_parameters.bandwidth_mbps",
                "theaterq_field": "RATE",
                "coverage": coverage["bandwidth_mbps"],
            },
            "loss": {
                "skyseed_field": "runtime_parameters.loss_percent",
                "theaterq_field": "LOSS",
                "coverage": coverage["loss_percent"],
            },
            "queue": {
                "skyseed_field": None,
                "theaterq_field": "LIMIT/QUEUE_ID",
                "coverage": {"present": 0, "total_segments": total, "coverage_ratio": 0.0},
                "status": "absent_in_parameter_epoch_artifact",
                "syntax_default_limit_packets": DEFAULT_LIMIT_PACKETS,
            },
            "reorder": {
                "skyseed_field": None,
                "theaterq_field": "ROUTE_ID",
                "coverage": {"present": 0, "total_segments": total, "coverage_ratio": 0.0},
                "status": "absent_in_parameter_epoch_artifact",
            },
            "routing_cost": {
                "skyseed_field": "runtime_parameters.routing_cost",
                "theaterq_field": None,
                "coverage": coverage["routing_cost"],
                "status": "metadata_not_direct_qdisc_field",
            },
        },
        "generation_cadence": compiled["generation_cadence"],
        "activation_time_source": "ParameterEpoch event-time activation_relative_time_seconds and activation_time_utc",
    }


def repo_relative_input_artifacts(compiled: dict[str, Any]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key, value in compiled["input_artifacts"].items():
        path = Path(value)
        try:
            artifacts[key] = str(path.relative_to(REPO_ROOT))
        except ValueError:
            artifacts[key] = str(path)
    return artifacts


def theaterq_trial_summary() -> dict[str, Any]:
    kernel = platform.release()
    return {
        "repository_url": THEATERQ_REPOSITORY_URL,
        "clone_location": "temporary /tmp/skyseed-theaterq-ixLT0v/TheaterQ",
        "source_retained_in_repo": False,
        "commit": THEATERQ_COMMIT,
        "branch": "main",
        "license": "GPL-2.0",
        "source_components": ["theaterq_lkm", "theaterq_tc"],
        "build_requirements_observed": [
            "git",
            "make",
            "linux headers for the running kernel",
            "compiler/build-essential",
            "iproute2",
            "bison for iproute2 tc plugin build",
            "flex for iproute2 tc plugin build",
        ],
        "ubuntu_apt_packages_if_continuing_plugin_build": ["bison", "flex"],
        "apt_packages_installed_in_this_goal": [],
        "kernel": kernel,
        "iproute2_submodule_commit": THEATERQ_IPROUTE2_SUBMODULE_COMMIT,
        "lkm_build": {
            "attempted": True,
            "success": False,
            "blocker": (
                f"sch_theaterq.ko did not build against Linux {kernel} headers; "
                "errors include C90/Werror declaration issues plus missing or incompatible "
                "kernel helpers such as skb_list_walk_safe and tc_qdisc_stats_dump."
            ),
        },
        "tc_plugin_build": {
            "attempted": True,
            "success": False,
            "blocker": "iproute2 submodule build stopped because bison and flex are not installed.",
        },
        "module_load": {
            "attempted": False,
            "loaded": False,
            "reason": "no sch_theaterq.ko was produced; loading would require kernel/module compatibility work",
        },
        "module_unload": {
            "attempted": False,
            "needed": False,
            "module_loaded_after_cleanup": False,
        },
        "tc_qdisc_create": {
            "attempted": False,
            "success": False,
            "reason": "TheaterQ LKM and q_theaterq.so were unavailable",
        },
    }


def isolated_demo_summary() -> dict[str, Any]:
    return {
        "theaterq_trace_driven_qdisc": {
            "attempted": False,
            "success": False,
            "reason": "TheaterQ could not be built/loaded on this kernel",
        },
        "userspace_netem_replay_scaffold": {
            "attempted": True,
            "success": True,
            "optimization_result": False,
            "netns_veth": True,
            "observed_qdisc_states": [
                "qdisc netem 834a: root refcnt 2 limit 1000 delay 2.0ms loss 0.01% rate 500Mbit",
                "qdisc netem 834a: root refcnt 2 limit 1000 delay 8.0ms loss 0.2% rate 100Mbit",
            ],
            "note": (
                "This only validates replay/scaffold mechanics in an isolated namespace; "
                "it is not TheaterQ and not an optimized ParameterEpoch backend."
            ),
        },
    }


def cleanup_summary() -> dict[str, Any]:
    return {
        "canonical_runtime_changed": False,
        "long_real_runtime_run": False,
        "satellite_output_committed": False,
        "theaterq_source_vendored": False,
        "kernel_module_loaded": False,
        "kernel_module_residue": False,
        "test_netns_residue": False,
        "test_veth_residue": False,
        "temporary_theaterq_clone_removed": True,
        "push_performed": False,
    }


def build_report(compiled: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "goal": "Skyseed Goal 13 TheaterQ / timeline qdisc isolated feasibility",
        "generated_at": "not_recorded_for_reproducible_baseline",
        "input_artifacts": repo_relative_input_artifacts(compiled),
        "scope": {
            "canonical_runtime_changed": False,
            "optimized_backend_landing": False,
            "real_dataplane_touched": False,
            "long_real_runtime": False,
            "userspace_replay_is_final_optimization": False,
        },
        "timeline_summary": compiled["summary"],
        "theaterq_trial": theaterq_trial_summary(),
        "trace_mapping": trace_mapping_summary(compiled),
        "trace_samples": trace_samples(compiled),
        "isolated_demo": isolated_demo_summary(),
        "cleanup": cleanup_summary(),
        "conclusion": {
            "theaterq_can_consume_skyseed_timeline_shape": True,
            "theaterq_available_on_this_host": False,
            "ready_to_commit": True,
            "experimental_parameter_epoch_backend_landing": (
                "Not yet for TheaterQ itself; the trace contract can inform an experimental "
                "backend, but landing requires kernel compatibility, tc plugin availability, "
                "and a repeatable isolated load/unload path."
            ),
        },
    }


def render_text(report: dict[str, Any]) -> str:
    trial = report["theaterq_trial"]
    mapping = report["trace_mapping"]["field_mapping"]
    demo = report["isolated_demo"]
    return "\n".join(
        [
            "Skyseed TheaterQ timeline feasibility",
            f"theaterq_commit={trial['commit']}",
            f"lkm_build_success={trial['lkm_build']['success']}",
            f"tc_plugin_build_success={trial['tc_plugin_build']['success']}",
            f"kernel_module_loaded={trial['module_load']['loaded']}",
            "coverage="
            + ",".join(
                f"{name}:{mapping[name]['coverage']['present']}/{mapping[name]['coverage']['total_segments']}"
                for name in ["delay", "rate", "loss"]
            ),
            f"fixed_10s_assumed={report['trace_mapping']['generation_cadence']['fixed_10s_assumed']}",
            f"userspace_netem_scaffold_success={demo['userspace_netem_replay_scaffold']['success']}",
            f"ready_to_commit={report['conclusion']['ready_to_commit']}",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compiled = compile_qdisc_timeline(load_artifacts(args.run_dir))
    report = build_report(compiled)
    if args.json_out:
        write_json(args.json_out, report)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
        if args.json_out:
            print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
