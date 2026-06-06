#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_OBSERVE_SELECTORS = ("ut1:as160-host1",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe Iteration2 offline validation and summarize the result."
    )
    default_repo_root = Path(__file__).resolve().parents[3]
    parser.add_argument(
        "--repo-root",
        default=str(default_repo_root),
        help="Repository root. Defaults to the current skyseed repo root.",
    )
    parser.add_argument(
        "--summary-out",
        default="",
        help="Optional path to write the summarized JSON report.",
    )
    parser.add_argument(
        "--raw-out",
        default="",
        help="Optional path to write the raw JSON emitted by the experiment app.",
    )
    parser.add_argument(
        "--installer",
        default="linux-mpls-dry-run",
        choices=("generic-dry-run", "linux-forwarding-dry-run", "linux-mpls-dry-run"),
        help="Installer backend passed to iteration2_satellite_experiment.py.",
    )
    parser.add_argument(
        "--scenario",
        default="small-scale-semi-real-internet",
        choices=("single-ut-direct", "small-scale-semi-real-internet"),
        help="Scenario passed to iteration2_satellite_experiment.py.",
    )
    parser.add_argument(
        "--input-profile",
        default="semi-real-orbital-elements",
        choices=("semi-real-orbital-elements", "synthetic-position"),
        help="Input profile passed to iteration2_satellite_experiment.py.",
    )
    parser.add_argument(
        "--observe-flow",
        action="append",
        default=[],
        help="Flow selector, e.g. ut1:as160-host1. Can be repeated.",
    )
    return parser.parse_args()


def run_experiment(
    repo_root: Path,
    *,
    installer: str,
    scenario: str,
    input_profile: str,
    observe_flows: List[str],
) -> Dict[str, Any]:
    app_path = repo_root / "satellite" / "apps" / "iteration2_satellite_experiment.py"
    command = [
        "python3",
        str(app_path),
        "--format",
        "json",
        "--installer",
        installer,
        "--scenario",
        scenario,
        "--input-profile",
        input_profile,
    ]
    for selector in observe_flows:
        command.extend(["--observe-flow", selector])

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "iteration2_satellite_experiment.py failed with exit code {code}\n"
            "stderr:\n{stderr}".format(
                code=completed.returncode,
                stderr=completed.stderr.strip(),
            )
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Experiment output was not valid JSON: {error}".format(error=str(exc))
        ) from exc
    payload["_command"] = command
    payload["_stderr"] = completed.stderr
    return payload


def _find_observed_flow(slice_report: Dict[str, Any], selector: str) -> Dict[str, Any]:
    ut_id, _, target = selector.partition(":")
    for entry in slice_report.get("flow_path_summaries", []):
        if str(entry.get("ut_id")) != ut_id:
            continue
        if str(entry.get("target_host_node_id")) == target:
            return dict(entry)
    return {}


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "detail": detail,
    }


def summarize_report(report: Dict[str, Any], observe_selectors: List[str]) -> Dict[str, Any]:
    experiment = dict(report.get("experiment", {}))
    validation = dict(report.get("validation", {}))
    slices = [dict(item) for item in report.get("slices", [])]
    transitions = [dict(item) for item in report.get("transitions", [])]

    checks: List[Dict[str, Any]] = []
    checks.append(
        _check(
            "top_level_validation_all_valid",
            bool(validation.get("all_valid")),
            "report.validation.all_valid={0}".format(validation.get("all_valid")),
        )
    )
    checks.append(
        _check(
            "slice_count_expected",
            int(validation.get("slice_count", 0)) >= 5 and len(slices) >= 5,
            "reported slice_count={0}, actual slices={1}".format(
                validation.get("slice_count"),
                len(slices),
            ),
        )
    )
    checks.append(
        _check(
            "experiment_all_valid",
            bool(experiment.get("all_valid")),
            "experiment.all_valid={0}".format(experiment.get("all_valid")),
        )
    )
    checks.append(
        _check(
            "transition_count_matches",
            len(transitions) == max(len(slices) - 1, 0),
            "transition_count={0}, expected={1}".format(
                len(transitions),
                max(len(slices) - 1, 0),
            ),
        )
    )

    targets = {
        str(item.get("node_id"))
        for item in experiment.get("mini_internet_targets_discovered", [])
    }
    checks.append(
        _check(
            "target_inventory_contains_hosts",
            {"as154-host-new", "as160-host1"}.issubset(targets),
            "discovered targets={0}".format(sorted(targets)),
        )
    )

    slice_summaries: List[Dict[str, Any]] = []
    observed_multi_hop = False
    observed_presence = {selector: 0 for selector in observe_selectors}
    observed_missing_slices = {selector: [] for selector in observe_selectors}
    for slice_report in slices:
        slice_validation = dict(slice_report.get("validation", {}))
        observed = {
            selector: _find_observed_flow(slice_report, selector)
            for selector in observe_selectors
        }
        for selector, flow in observed.items():
            if not flow:
                observed_missing_slices[selector].append(slice_report.get("time_slice_id"))
                continue
            observed_presence[selector] += 1
            if bool(flow.get("contains_multi_hop")):
                observed_multi_hop = True

        slice_summaries.append(
            {
                "slice_id": slice_report.get("time_slice_id"),
                "snapshot_id": slice_report.get("snapshot_id"),
                "all_valid": bool(slice_validation.get("all_valid")),
                "zone_serving": dict(slice_report.get("zone_serving", {})),
                "observed_flows": {
                    selector: {
                        "path_string": flow.get("path_string"),
                        "selected_gateway_id": flow.get("selected_gateway_id"),
                        "contains_multi_hop": flow.get("contains_multi_hop"),
                        "path_hop_count": flow.get("path_hop_count"),
                    }
                    for selector, flow in observed.items()
                    if flow
                },
            }
        )

    checks.append(
        _check(
            "slice_validations_all_pass",
            all(item["all_valid"] for item in slice_summaries),
            "slice validation states={0}".format(
                [item["all_valid"] for item in slice_summaries]
            ),
        )
    )
    checks.append(
        _check(
            "observed_flows_found_in_sequence",
            all(count > 0 for count in observed_presence.values()),
            "presence={0}, missing_slices={1}".format(
                observed_presence,
                observed_missing_slices,
            ),
        )
    )
    checks.append(
        _check(
            "observed_flow_contains_multi_hop_case",
            observed_multi_hop,
            "at least one observed slice should include a multi-hop satellite path",
        )
    )

    transition_summaries = []
    for item in transitions:
        transition_summaries.append(
            {
                "from_slice_id": item.get("from_slice_id"),
                "to_slice_id": item.get("to_slice_id"),
                "changed_link_ids": [
                    change.get("link_id") for change in item.get("changed_links", [])
                ],
                "mutation_operation_count": item.get("network_mutation_summary", {}).get(
                    "operation_count"
                ),
            }
        )

    overall_passed = all(check["passed"] for check in checks)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": "pass" if overall_passed else "fail",
        "checks": checks,
        "command": report.get("_command", []),
        "stderr": report.get("_stderr", ""),
        "experiment": {
            "experiment_name": experiment.get("experiment_name"),
            "scenario_name": experiment.get("scenario_name"),
            "installer_backend": experiment.get("installer_backend", {}),
            "real_execution_performed": experiment.get("real_execution_performed"),
            "target_slice_id": experiment.get("target_slice_id"),
        },
        "observe_selectors": list(observe_selectors),
        "observation_notes": {
            "observed_presence": observed_presence,
            "observed_missing_slices": observed_missing_slices,
            "note": (
                "A selector may be absent in some slices when the special-prefix "
                "override collapses onto the same direct gateway path as the default flow."
            ),
        },
        "slice_summaries": slice_summaries,
        "transition_summaries": transition_summaries,
    }
    return summary


def write_json(path_text: str, payload: Dict[str, Any]) -> None:
    if not path_text:
        return
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    observe_selectors = list(args.observe_flow or DEFAULT_OBSERVE_SELECTORS)

    report = run_experiment(
        repo_root,
        installer=args.installer,
        scenario=args.scenario,
        input_profile=args.input_profile,
        observe_flows=observe_selectors,
    )
    summary = summarize_report(report, observe_selectors)

    write_json(args.summary_out, summary)
    if args.raw_out:
        write_json(args.raw_out, report)

    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if summary.get("overall_status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
