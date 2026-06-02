#!/usr/bin/env python3
# encoding: utf-8

"""Regression wrapper for explicit MPLS-4 scenario transitions."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List

from controller import SCENARIO_DEFINITIONS
from mpls_control import (
    compute_all_attached_ut_egress_plans,
    resolve_ut_egress_ping_target,
)
from mpls_ut_egress_smoke import (
    controller_action,
    load_link_state,
    load_topology,
    resolve_plan_or_raise,
    topology_snapshot,
    assert_topology_snapshot,
    verify_forwarding_cleanup,
)
DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
DEFAULT_LINK_STATE = DEFAULT_CONFIG_DIR / "link_state.json"
DEFAULT_TOPOLOGY = DEFAULT_CONFIG_DIR / "topology.json"
DEFAULT_CONTROLLER = DEFAULT_DIR / "controller.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run explicit MPLS-4 scenario apply/verify/install/cleanup smoke cycles."
        )
    )
    parser.add_argument(
        "--controller",
        default=str(DEFAULT_CONTROLLER),
        help="Path to controller.py.",
    )
    parser.add_argument(
        "--link-state",
        default=str(DEFAULT_LINK_STATE),
        help="Path to link_state.json.",
    )
    parser.add_argument(
        "--topology-config",
        default=str(DEFAULT_TOPOLOGY),
        help="Path to topology.json.",
    )
    parser.add_argument(
        "--scenario",
        help="Run one supported scenario cycle, such as t0 or t1.",
    )
    parser.add_argument(
        "--sequence",
        help="Run a comma-separated scenario sequence, such as t0,t1.",
    )
    parser.add_argument(
        "--all-ut",
        action="store_true",
        help=(
            "Install/verify/cleanup all supported UT egress intents that are "
            "currently attached in the selected scenario."
        ),
    )
    return parser.parse_args()


def scenario_slot_names(scenario_name: str) -> List[str]:
    definition = SCENARIO_DEFINITIONS[scenario_name]
    return list(dict(definition.get("managed_slots", {})).keys())


def _ut_is_attached(link_state_path: Path, ut_id: str) -> bool:
    state = load_link_state(link_state_path)
    for entry in state.get("slots", []):
        if entry.get("slot_type") != "ut_access":
            continue
        binding = entry.get("binding") or {}
        if entry.get("occupied", False) and binding.get("ut_id") == ut_id:
            return True
    return False


def scenario_plan_summary(
    scenario_name: str,
    topology_path: Path,
    link_state_path: Path,
) -> Dict[str, Any]:
    plan = resolve_plan_or_raise(
        topology_path,
        link_state_path,
        str(SCENARIO_DEFINITIONS[scenario_name].get("verify_ut_id")),
    )
    return {
        "scenario": scenario_name,
        "profile_id": plan.profile_id,
        "ut_id": plan.ut_id,
        "active_access_slot": plan.active_access_slot_name,
        "active_access_interface": plan.active_access_interface,
        "active_access_ip": plan.active_access_ip,
        "serving_satellite": plan.serving_satellite_id,
        "selected_gateway": plan.selected_gateway_id,
        "satellite_path": list(plan.satellite_path),
        "forward_mpls_nodes": list(plan.forward_mpls_nodes),
        "forward_labels": list(plan.forward_labels),
        "reverse_labels": list(plan.reverse_labels),
        "hop_count": max(0, len(plan.forward_mpls_nodes) - 1),
        "ping_target": resolve_ut_egress_ping_target(plan),
        "gateway_nat_interface": plan.gateway_nat_interface,
        "gateway_nat_source_ip": plan.gateway_nat_source_ip,
    }


def scenario_all_ut_plan_summaries(
    topology_path: Path,
    link_state_path: Path,
) -> List[Dict[str, Any]]:
    state = load_link_state(link_state_path)
    plans, issues = compute_all_attached_ut_egress_plans(
        load_topology(topology_path),
        state,
    )
    if issues:
        raise RuntimeError(
            "failed to resolve all-attached UT egress plans: " + ", ".join(issues)
        )
    summaries: List[Dict[str, Any]] = []
    for plan in plans:
        summaries.append(
            {
                "scenario": "all-attached",
                "profile_id": plan.profile_id,
                "ut_id": plan.ut_id,
                "active_access_slot": plan.active_access_slot_name,
                "active_access_interface": plan.active_access_interface,
                "active_access_ip": plan.active_access_ip,
                "serving_satellite": plan.serving_satellite_id,
                "selected_gateway": plan.selected_gateway_id,
                "satellite_path": list(plan.satellite_path),
                "forward_mpls_nodes": list(plan.forward_mpls_nodes),
                "forward_labels": list(plan.forward_labels),
                "reverse_labels": list(plan.reverse_labels),
                "hop_count": max(0, len(plan.forward_mpls_nodes) - 1),
                "ping_target": resolve_ut_egress_ping_target(plan),
                "gateway_nat_interface": plan.gateway_nat_interface,
                "gateway_nat_source_ip": plan.gateway_nat_source_ip,
            }
        )
    return summaries


def run_scenario_cycle(
    *,
    controller_path: Path,
    topology_path: Path,
    link_state_path: Path,
    scenario_name: str,
    all_ut: bool = False,
) -> Dict[str, Any]:
    definition = SCENARIO_DEFINITIONS[scenario_name]
    verify_ut_id = str(definition.get("verify_ut_id"))
    managed_slot_names = scenario_slot_names(scenario_name)

    print(f"[mpls-scenario-smoke] begin scenario cycle: {scenario_name}")
    if all_ut:
        controller_action(
            controller_path,
            link_state_path,
            "--cleanup-all-attached-ut-egress-mpls",
        )
    else:
        if _ut_is_attached(link_state_path, verify_ut_id):
            controller_action(
                controller_path,
                link_state_path,
                "--cleanup-ut-egress-mpls",
                verify_ut_id,
            )
        else:
            print(
                "[mpls-scenario-smoke] cleanup no-op on clean baseline: "
                f"{verify_ut_id} is not attached yet"
            )
    controller_action(
        controller_path,
        link_state_path,
        "--apply-scenario",
        scenario_name,
    )
    controller_action(
        controller_path,
        link_state_path,
        "--verify-scenario",
        scenario_name,
    )
    summaries = (
        scenario_all_ut_plan_summaries(topology_path, link_state_path)
        if all_ut
        else [scenario_plan_summary(scenario_name, topology_path, link_state_path)]
    )
    print("[mpls-scenario-smoke] active MPLS plan(s)")
    for summary in summaries:
        print(f"  ut: {summary['ut_id']}")
        print(
            "    active_access: "
            f"{summary['active_access_slot']} {summary['active_access_interface']} "
            f"{summary['active_access_ip']}"
        )
        print(f"    serving_satellite: {summary['serving_satellite']}")
        print(f"    selected_gateway: {summary['selected_gateway']}")
        print("    forward_path: " + " -> ".join(summary["forward_mpls_nodes"]))
        print(
            "    forward_labels: "
            + " -> ".join(str(label) for label in summary["forward_labels"])
        )
        print(
            "    reverse_labels: "
            + " -> ".join(str(label) for label in summary["reverse_labels"])
        )

    applied_state = load_link_state(link_state_path)
    applied_snapshot = topology_snapshot(applied_state, managed_slot_names)

    if all_ut:
        controller_action(
            controller_path,
            link_state_path,
            "--install-all-attached-ut-egress-mpls",
        )
        controller_action(
            controller_path,
            link_state_path,
            "--verify-all-attached-ut-egress-mpls",
        )
        for summary in summaries:
            controller_action(
                controller_path,
                link_state_path,
                "--verify-ut-egress-mpls",
                str(summary["ut_id"]),
                "--ping-target",
                "10.160.0.71",
            )
        controller_action(
            controller_path,
            link_state_path,
            "--cleanup-all-attached-ut-egress-mpls",
        )
        for summary in summaries:
            verify_forwarding_cleanup(
                topology_path,
                link_state_path,
                str(summary["ut_id"]),
            )
    else:
        controller_action(
            controller_path,
            link_state_path,
            "--install-ut-egress-mpls",
            verify_ut_id,
        )
        controller_action(
            controller_path,
            link_state_path,
            "--verify-ut-egress-mpls",
            verify_ut_id,
        )
        controller_action(
            controller_path,
            link_state_path,
            "--cleanup-ut-egress-mpls",
            verify_ut_id,
        )
        verify_forwarding_cleanup(topology_path, link_state_path, verify_ut_id)

    after_cleanup_state = load_link_state(link_state_path)
    after_cleanup_snapshot = topology_snapshot(after_cleanup_state, managed_slot_names)
    assert_topology_snapshot(before=applied_snapshot, after=after_cleanup_snapshot)

    controller_action(
        controller_path,
        link_state_path,
        "--verify-scenario",
        scenario_name,
    )
    print(
        "[mpls-scenario-smoke] cleanup verified: forwarding cleared, "
        f"scenario topology preserved for {scenario_name}"
    )
    if all_ut:
        return {"scenario": scenario_name, "ut_summaries": summaries}
    return summaries[0]


def main() -> int:
    args = parse_args()
    if bool(args.scenario) == bool(args.sequence):
        raise SystemExit("Specify exactly one of --scenario or --sequence.")

    controller_path = Path(args.controller).resolve()
    link_state_path = Path(args.link_state).resolve()
    topology_path = Path(args.topology_config).resolve()
    scenarios: Iterable[str]
    if args.scenario:
        scenarios = [args.scenario]
    else:
        scenarios = [item.strip() for item in str(args.sequence).split(",") if item.strip()]

    for scenario_name in scenarios:
        if scenario_name not in SCENARIO_DEFINITIONS:
            supported = ", ".join(sorted(SCENARIO_DEFINITIONS))
            raise SystemExit(
                f"unsupported scenario: {scenario_name}. Supported scenarios: {supported}"
            )

    if args.all_ut:
        controller_action(
            controller_path,
            link_state_path,
            "--prepare-all-ut-egress-policies",
        )
    else:
        prepared_ut_ids = sorted(
            {
                str(SCENARIO_DEFINITIONS[scenario_name].get("verify_ut_id"))
                for scenario_name in scenarios
            }
        )
        for ut_id in prepared_ut_ids:
            controller_action(
                controller_path,
                link_state_path,
                "--prepare-ut-egress-policy",
                ut_id,
            )

    for scenario_name in scenarios:
        run_scenario_cycle(
            controller_path=controller_path,
            topology_path=topology_path,
            link_state_path=link_state_path,
            scenario_name=scenario_name,
            all_ut=args.all_ut,
        )

    controller_action(controller_path, link_state_path, "--show-link-state")
    print("[mpls-scenario-smoke] scenario sequence completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
