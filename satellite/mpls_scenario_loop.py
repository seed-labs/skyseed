#!/usr/bin/env python3
# encoding: utf-8

"""Long-running serial loop runner for explicit MPLS-4 scenario transitions."""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from controller import SCENARIO_DEFINITIONS
from mpls_scenario_smoke import run_scenario_cycle
from mpls_ut_egress_smoke import load_link_state, run_command
DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
DEFAULT_LINK_STATE = DEFAULT_CONFIG_DIR / "link_state.json"
DEFAULT_TOPOLOGY = DEFAULT_CONFIG_DIR / "topology.json"
DEFAULT_CONTROLLER = DEFAULT_DIR / "controller.py"
FALLBACK_FORWARDING_CLEANUP_COMMANDS: Sequence[tuple[str, List[str], str]] = (
    (
        "ut1",
        ["ip", "route", "del", "10.154.0.129/32"],
        "delete UT destination route on ut1",
    ),
    (
        "sat-a1",
        ["ip", "route", "del", "10.154.0.129/32"],
        "delete ingress route on sat-a1",
    ),
    (
        "sat-a2",
        ["ip", "route", "del", "10.154.0.129/32"],
        "delete ingress route on sat-a2",
    ),
    (
        "gw1",
        ["ip", "-f", "mpls", "route", "del", "101"],
        "delete forward pop entry on gw1",
    ),
    (
        "gw2",
        ["ip", "-f", "mpls", "route", "del", "101"],
        "delete forward pop entry on gw2",
    ),
    (
        "sat-b1",
        ["ip", "-f", "mpls", "route", "del", "100"],
        "delete forward LFIB on sat-b1",
    ),
    (
        "sat-b2",
        ["ip", "-f", "mpls", "route", "del", "100"],
        "delete forward LFIB on sat-b2",
    ),
    (
        "gw1",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete reverse ingress route on gw1",
    ),
    (
        "gw2",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete reverse ingress route on gw2",
    ),
    (
        "sat-a1",
        ["ip", "-f", "mpls", "route", "del", "201"],
        "delete reverse pop entry on sat-a1",
    ),
    (
        "sat-a2",
        ["ip", "-f", "mpls", "route", "del", "201"],
        "delete reverse pop entry on sat-a2",
    ),
    (
        "sat-a1",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete UT delivery route on sat-a1",
    ),
    (
        "sat-a2",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete UT delivery route on sat-a2",
    ),
    (
        "sat-b1",
        ["ip", "-f", "mpls", "route", "del", "200"],
        "delete reverse LFIB on sat-b1",
    ),
    (
        "sat-b2",
        ["ip", "-f", "mpls", "route", "del", "200"],
        "delete reverse LFIB on sat-b2",
    ),
    (
        "as154-router0",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete return route on as154-router0",
    ),
    (
        "as2-r102",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete return route on as2-r102",
    ),
    (
        "as2-r101",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete return route on as2-r101",
    ),
    (
        "as2-r100",
        ["ip", "route", "del", "10.90.11.2/32"],
        "delete return route on as2-r100",
    ),
    (
        "sat-a1",
        ["sysctl", "-w", "net.mpls.conf.xp0a.input=0"],
        "disable MPLS input on sat-a1 xp0a",
    ),
    (
        "sat-a2",
        ["sysctl", "-w", "net.mpls.conf.xp0a.input=0"],
        "disable MPLS input on sat-a2 xp0a",
    ),
    (
        "sat-b1",
        ["sysctl", "-w", "net.mpls.conf.xp0b.input=0"],
        "disable MPLS input on sat-b1 xp0b",
    ),
    (
        "sat-b2",
        ["sysctl", "-w", "net.mpls.conf.xp0b.input=0"],
        "disable MPLS input on sat-b2 xp0b",
    ),
    (
        "sat-b1",
        ["sysctl", "-w", "net.mpls.conf.ga-g1s1.input=0"],
        "disable MPLS input on sat-b1 ga-g1s1",
    ),
    (
        "gw1",
        ["sysctl", "-w", "net.mpls.conf.ga-g1s1.input=0"],
        "disable MPLS input on gw1 ga-g1s1",
    ),
    (
        "sat-b2",
        ["sysctl", "-w", "net.mpls.conf.ga-g2s0.input=0"],
        "disable MPLS input on sat-b2 ga-g2s0",
    ),
    (
        "gw2",
        ["sysctl", "-w", "net.mpls.conf.ga-g2s0.input=0"],
        "disable MPLS input on gw2 ga-g2s0",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a long-lived serial t0<->t1 scenario loop with MPLS cleanup, "
            "install, verify, and cleanup on every round."
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
        "--sequence",
        default="t0,t1",
        help="Comma-separated scenario sequence to repeat. Default: t0,t1.",
    )
    parser.add_argument(
        "--max-rounds",
        "--rounds",
        dest="max_rounds",
        type=int,
        default=0,
        help=(
            "Total scenario rounds to run. Use 0 for an indefinite loop. "
            "One round means one scenario apply/verify/install/verify/cleanup cycle."
        ),
    )
    parser.add_argument(
        "--interval",
        "--sleep-seconds",
        dest="interval",
        type=float,
        default=5.0,
        help="Sleep interval between successful rounds. Default: 5 seconds.",
    )
    parser.add_argument(
        "--all-ut",
        action="store_true",
        help=(
            "Run each scenario round with all supported attached UT egress "
            "MPLS intents instead of only the scenario's default verify_ut."
        ),
    )
    return parser.parse_args()


def parse_sequence(sequence_text: str) -> List[str]:
    scenarios = [item.strip() for item in sequence_text.split(",") if item.strip()]
    if not scenarios:
        raise SystemExit("sequence must contain at least one scenario")
    for scenario_name in scenarios:
        if scenario_name not in SCENARIO_DEFINITIONS:
            supported = ", ".join(sorted(SCENARIO_DEFINITIONS))
            raise SystemExit(
                f"unsupported scenario: {scenario_name}. Supported scenarios: {supported}"
            )
    return scenarios


def render_summary(round_index: int, summary: dict) -> None:
    print(f"[round {round_index}] scenario: {summary['scenario']}")
    ut_summaries = summary.get("ut_summaries")
    if ut_summaries:
        for ut_summary in ut_summaries:
            print(f"  ut: {ut_summary['ut_id']}")
            print(f"    gateway: {ut_summary['selected_gateway']}")
            print("    selected path: " + " -> ".join(ut_summary["forward_mpls_nodes"]))
            print(
                "    forward labels: "
                + " -> ".join(str(label) for label in ut_summary["forward_labels"])
            )
            print(
                "    reverse labels: "
                + " -> ".join(str(label) for label in ut_summary["reverse_labels"])
            )
    else:
        print(f"  gateway: {summary['selected_gateway']}")
        print("  selected path: " + " -> ".join(summary["forward_mpls_nodes"]))
        print(
            "  forward labels: "
            + " -> ".join(str(label) for label in summary["forward_labels"])
        )
        print(
            "  reverse labels: "
            + " -> ".join(str(label) for label in summary["reverse_labels"])
        )
    print("  verify: PASS")
    print("  residue observation: PASS")


def best_effort_controller_action(
    controller_path: Path,
    link_state_path: Path,
    *args: str,
) -> None:
    run_command(
        [
            "python3",
            str(controller_path),
            "--link-state",
            str(link_state_path),
            *args,
        ],
        failure_stage=f"best effort controller action {' '.join(args)}",
        allow_failure=True,
    )


def _best_effort_controller_cleanup(
    controller_path: Path,
    link_state_path: Path,
    ut_id: str,
) -> bool:
    result = run_command(
        [
            "python3",
            str(controller_path),
            "--link-state",
            str(link_state_path),
            "--cleanup-ut-egress-mpls",
            ut_id,
        ],
        failure_stage=f"best effort controller cleanup for {ut_id}",
        allow_failure=True,
    )
    return result.returncode == 0


def _best_effort_controller_cleanup_all_attached(
    controller_path: Path,
    link_state_path: Path,
) -> bool:
    result = run_command(
        [
            "python3",
            str(controller_path),
            "--link-state",
            str(link_state_path),
            "--cleanup-all-attached-ut-egress-mpls",
        ],
        failure_stage="best effort controller cleanup for all attached UTs",
        allow_failure=True,
    )
    return result.returncode == 0


def _fallback_forwarding_cleanup() -> None:
    print(
        "[mpls-scenario-loop] controller cleanup could not resolve the current "
        "UT egress plan; running fallback forwarding cleanup for the known "
        "t0/t1 demo path"
    )
    for node_id, command, description in FALLBACK_FORWARDING_CLEANUP_COMMANDS:
        run_command(
            ["docker", "exec", node_id, *command],
            failure_stage=f"fallback forwarding cleanup on {node_id}: {description}",
            allow_failure=True,
        )


def cleanup_known_forwarding(
    controller_path: Path,
    link_state_path: Path,
    *,
    all_ut: bool = False,
) -> None:
    if all_ut:
        if not _best_effort_controller_cleanup_all_attached(
            controller_path,
            link_state_path,
        ):
            _fallback_forwarding_cleanup()
        return
    cleaned_ut_ids: Set[str] = set()
    fallback_required = False
    for definition in SCENARIO_DEFINITIONS.values():
        verify_ut_id = str(definition.get("verify_ut_id"))
        if verify_ut_id in cleaned_ut_ids:
            continue
        cleaned_ut_ids.add(verify_ut_id)
        if not _best_effort_controller_cleanup(controller_path, link_state_path, verify_ut_id):
            fallback_required = True
    if fallback_required:
        _fallback_forwarding_cleanup()


def scenario_participant_nodes(scenario_name: str) -> List[str]:
    definition = SCENARIO_DEFINITIONS[scenario_name]
    managed_slots = dict(definition.get("managed_slots", {}))
    node_ids: Set[str] = set()
    for target in managed_slots.values():
        if not target:
            continue
        for value in target.get("binding", {}).values():
            if isinstance(value, str) and value.startswith(("sat-", "gw", "ut")):
                node_ids.add(value)
    return sorted(node_ids)


def expected_network_container_names(slot_entry: Dict[str, object]) -> List[str]:
    expected_names: List[str] = []
    for endpoint in slot_entry.get("preconnected_endpoints", []):
        node_id = str(endpoint.get("node_id"))
        if node_id:
            expected_names.append(node_id)
    for endpoint in slot_entry.get("dynamic_endpoints", []):
        node_id = str(endpoint.get("node_id"))
        if node_id:
            expected_names.append(node_id)
    return sorted(set(expected_names))


def inspect_network_container_names(network_name: str) -> List[str]:
    result = subprocess.run(
        ["docker", "network", "inspect", network_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"inspect docker network {network_name}: failed with exit status {result.returncode}"
        )
    payload = json.loads(result.stdout)
    if not payload:
        return []
    network = payload[0]
    containers = network.get("Containers") or {}
    names = [
        str(container.get("Name"))
        for container in containers.values()
        if container.get("Name")
    ]
    return sorted(set(names))


def expected_satellite_runtime_interfaces(
    state: Dict[str, object],
) -> Dict[str, Set[str]]:
    expected: Dict[str, Set[str]] = {}
    for slot_entry in state.get("slots", []):
        if not slot_entry.get("occupied", False):
            continue
        for endpoint in slot_entry.get("dynamic_endpoints", []):
            node_id = str(endpoint.get("node_id"))
            interface_name = str(endpoint.get("interface_name"))
            if not node_id.startswith("sat-"):
                continue
            expected.setdefault(node_id, set()).add(interface_name)
    return expected


def inspect_satellite_runtime_interfaces(node_id: str) -> Set[str]:
    result = subprocess.run(
        ["docker", "exec", node_id, "ip", "-o", "link", "show"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"inspect runtime interfaces on {node_id}: failed with exit status {result.returncode}"
        )
    runtime_interfaces: Set[str] = set()
    for line in result.stdout.splitlines():
        if ": " not in line:
            continue
        interface_name = line.split(": ", 1)[1].split("@", 1)[0].split(":", 1)[0].strip()
        if interface_name.startswith(("ua-", "ga-", "xp")):
            runtime_interfaces.add(interface_name)
    return runtime_interfaces


def observe_runtime_residue(
    *,
    link_state_path: Path,
    scenario_name: str,
) -> List[str]:
    state = load_link_state(link_state_path)
    definition = SCENARIO_DEFINITIONS[scenario_name]
    issues: List[str] = []

    managed_slots = dict(definition.get("managed_slots", {}))
    slot_map = {
        str(entry.get("slot_name")): entry
        for entry in state.get("slots", [])
    }
    for slot_name in managed_slots:
        slot_entry = slot_map.get(slot_name)
        if slot_entry is None:
            issues.append(f"missing_slot:{slot_name}")
            continue
        network_name = str(slot_entry.get("network_name") or slot_name)
        expected_names = expected_network_container_names(slot_entry)
        actual_names = inspect_network_container_names(network_name)
        if actual_names != expected_names:
            issues.append(
                f"bridge_occupancy_mismatch:{slot_name}:expected={expected_names}:actual={actual_names}"
            )

    expected_interfaces = expected_satellite_runtime_interfaces(state)
    for node_id in scenario_participant_nodes(scenario_name):
        if not node_id.startswith("sat-"):
            continue
        actual = inspect_satellite_runtime_interfaces(node_id)
        expected = expected_interfaces.get(node_id, set())
        extras = sorted(actual - expected)
        missing = sorted(expected - actual)
        if extras:
            issues.append(f"stale_interface:{node_id}:{','.join(extras)}")
        if missing:
            issues.append(f"missing_interface:{node_id}:{','.join(missing)}")

    return issues


def abort_current_round(
    *,
    controller_path: Path,
    link_state_path: Path,
    scenario_name: str,
    error: Exception,
    all_ut: bool = False,
) -> int:
    print(f"[mpls-scenario-loop] round aborted on scenario {scenario_name}: {error}")
    print(
        "[mpls-scenario-loop] attempting best-effort MPLS forwarding cleanup and "
        "printing current link_state before exit"
    )
    cleanup_known_forwarding(controller_path, link_state_path, all_ut=all_ut)
    best_effort_controller_action(
        controller_path,
        link_state_path,
        "--show-link-state",
    )
    return 1


def main() -> int:
    args = parse_args()
    controller_path = Path(args.controller).resolve()
    link_state_path = Path(args.link_state).resolve()
    topology_path = Path(args.topology_config).resolve()
    scenarios = parse_sequence(str(args.sequence))
    rounds_limit: Optional[int] = None if args.max_rounds <= 0 else args.max_rounds

    print("[mpls-scenario-loop] starting serial scenario loop")
    print("  sequence: " + ",".join(scenarios))
    print(
        "  max_rounds: "
        + ("infinite" if rounds_limit is None else str(rounds_limit))
    )
    print(f"  interval_seconds: {args.interval}")
    print(f"  all_ut: {args.all_ut}")
    if args.all_ut:
        best_effort_controller_action(
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
            best_effort_controller_action(
                controller_path,
                link_state_path,
                "--prepare-ut-egress-policy",
                ut_id,
            )

    round_index = 0
    try:
        for scenario_name in itertools.cycle(scenarios):
            if rounds_limit is not None and round_index >= rounds_limit:
                break
            round_index += 1
            print(f"[round {round_index}] applying scenario: {scenario_name}")
            started_at = time.monotonic()
            try:
                summary = run_scenario_cycle(
                    controller_path=controller_path,
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    scenario_name=scenario_name,
                    all_ut=args.all_ut,
                )
                observation_issues = observe_runtime_residue(
                    link_state_path=link_state_path,
                    scenario_name=scenario_name,
                )
                if observation_issues:
                    raise RuntimeError(
                        "runtime residue observation failed: "
                        + ", ".join(observation_issues)
                    )
            except Exception as error:
                return abort_current_round(
                    controller_path=controller_path,
                    link_state_path=link_state_path,
                    scenario_name=scenario_name,
                    error=error,
                    all_ut=args.all_ut,
                )
            elapsed = time.monotonic() - started_at
            render_summary(round_index, summary)
            print(f"  elapsed_seconds: {elapsed:.2f}")

            if args.interval > 0 and (
                rounds_limit is None or round_index < rounds_limit
            ):
                print(
                    f"[mpls-scenario-loop] sleeping {args.interval:.2f}s "
                    "before next round"
                )
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[mpls-scenario-loop] interrupted by user; cleaning up forwarding and stopping")
        cleanup_known_forwarding(controller_path, link_state_path, all_ut=args.all_ut)
        best_effort_controller_action(
            controller_path,
            link_state_path,
            "--show-link-state",
        )
        return 130

    best_effort_controller_action(
        controller_path,
        link_state_path,
        "--show-link-state",
    )
    print(
        "[mpls-scenario-loop] loop completed after "
        f"{round_index} successful rounds"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
