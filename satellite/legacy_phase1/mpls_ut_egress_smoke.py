#!/usr/bin/env python3
# encoding: utf-8

"""Regression wrapper for the controller-managed MPLS-3 UT egress path."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from _path_setup import bootstrap_legacy_import_path

SATELLITE_DIR = bootstrap_legacy_import_path()

from mpls_control import compute_ut_egress_plan, get_ut_egress_intent


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = SATELLITE_DIR / "legacy_phase1" / "configs"
DEFAULT_LINK_STATE = DEFAULT_CONFIG_DIR / "link_state.json"
DEFAULT_TOPOLOGY = DEFAULT_CONFIG_DIR / "topology.json"
DEFAULT_CONTROLLER = SATELLITE_DIR / "controller.py"
BRIDGE_NETFILTER_SYSCTLS = (
    "net.bridge.bridge-nf-call-iptables",
    "net.bridge.bridge-nf-call-ip6tables",
    "net.bridge.bridge-nf-call-arptables",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a repeatable MPLS-3 regression for the controller-managed "
            "UT egress dataplane."
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
        "--ut",
        default=get_ut_egress_intent("ut1").ut_id,
        help="UT id to test.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the UT egress MPLS forwarding state only.",
    )
    parser.add_argument(
        "--ping-target",
        help="Optional non-local Internet target IP to use for verification.",
    )
    return parser.parse_args()


def run_command(
    command: List[str],
    *,
    failure_stage: str,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"[mpls-ut-egress-smoke] {' '.join(command)}")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip())
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(
            f"{failure_stage}: command failed with exit status {result.returncode}"
        )
    return result


def controller_action(
    controller_path: Path,
    link_state_path: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        [
            "python3",
            str(controller_path),
            "--link-state",
            str(link_state_path),
            *args,
        ],
        failure_stage=f"controller action {' '.join(args)}",
    )


def load_link_state(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_topology(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def slot_state_by_name(state: Dict[str, object], slot_name: str) -> Dict[str, object]:
    for entry in state.get("slots", []):
        if entry.get("slot_name") == slot_name:
            return entry
    raise KeyError(f"slot not found: {slot_name}")


def bridge_netfilter_ready() -> bool:
    for key in BRIDGE_NETFILTER_SYSCTLS:
        result = run_command(
            ["sysctl", key],
            failure_stage=f"probe {key}",
        )
        if "=" not in result.stdout:
            return False
        value_text = result.stdout.split("=", 1)[1].strip()
        if value_text != "0":
            return False
    return True


def ensure_slot_runtime(controller_path: Path, link_state_path: Path) -> Dict[str, object]:
    state = load_link_state(link_state_path)
    if not state.get("runtime_initialized", False) or not bridge_netfilter_ready():
        print(
            "[mpls-ut-egress-smoke] runtime or host bridge prerequisite missing; "
            "rerunning --init-slot-runtime"
        )
        controller_action(controller_path, link_state_path, "--init-slot-runtime")
        state = load_link_state(link_state_path)
    return state


def ensure_binding(
    *,
    controller_path: Path,
    link_state_path: Path,
    slot_name: str,
    expected_binding: Dict[str, str],
    create_args: List[str],
) -> None:
    state = load_link_state(link_state_path)
    entry = slot_state_by_name(state, slot_name)
    binding = entry.get("binding")
    if binding == expected_binding:
        return
    if entry.get("occupied", False):
        raise RuntimeError(
            f"{slot_name} is already occupied by unexpected binding: {binding}"
        )
    controller_action(controller_path, link_state_path, *create_args)
    state = load_link_state(link_state_path)
    entry = slot_state_by_name(state, slot_name)
    if entry.get("binding") != expected_binding:
        raise RuntimeError(
            f"{slot_name} binding mismatch after controller action: {entry.get('binding')}"
        )


def ensure_runtime_topology(
    controller_path: Path,
    link_state_path: Path,
    ut_id: str,
) -> Dict[str, object]:
    intent = get_ut_egress_intent(ut_id)
    required_bindings = (
        (
            "ut1-access-slot0",
            {"ut_id": intent.ut_id, "satellite_id": "sat-a1"},
            ["--attach-ut", f"{intent.ut_id}:sat-a1", "--slot-name", "ut1-access-slot0"],
        ),
        (
            "ab-cross-slot0",
            {
                "satellite_a_id": "sat-a1",
                "satellite_b_id": "sat-b1",
                "satellite_a_direction": "left",
                "satellite_b_direction": "right",
            },
            [
                "--connect-cross",
                "sat-a1:sat-b1:left:right",
                "--slot-name",
                "ab-cross-slot0",
            ],
        ),
        (
            "gw1-gateway-slot1",
            {"gateway_id": "gw1", "satellite_id": "sat-b1"},
            ["--attach-gw", "gw1:sat-b1", "--slot-name", "gw1-gateway-slot1"],
        ),
    )

    def ensure_once() -> Dict[str, object]:
        ensure_slot_runtime(controller_path, link_state_path)
        for slot_name, expected_binding, create_args in required_bindings:
            ensure_binding(
                controller_path=controller_path,
                link_state_path=link_state_path,
                slot_name=slot_name,
                expected_binding=expected_binding,
                create_args=create_args,
            )
        return load_link_state(link_state_path)

    try:
        return ensure_once()
    except RuntimeError as error:
        print(f"[mpls-ut-egress-smoke] runtime recovery: {error}")
        print("[mpls-ut-egress-smoke] rerunning --init-slot-runtime once before retry")
        controller_action(controller_path, link_state_path, "--init-slot-runtime")
        return ensure_once()


def prepare_ut_egress_policy(
    controller_path: Path,
    link_state_path: Path,
    ut_id: str,
) -> None:
    controller_action(
        controller_path,
        link_state_path,
        "--prepare-ut-egress-policy",
        ut_id,
    )


def resolve_plan_or_raise(topology_path: Path, link_state_path: Path, ut_id: str):
    topology = load_topology(topology_path)
    state = load_link_state(link_state_path)
    plan, errors = compute_ut_egress_plan(topology, state, ut_id)
    if plan is None:
        raise RuntimeError(
            f"failed to resolve UT egress plan for {ut_id}: {', '.join(errors)}"
        )
    return plan


def topology_snapshot(state: Dict[str, object], slot_names: Iterable[str]) -> Dict[str, Dict[str, object]]:
    snapshot: Dict[str, Dict[str, object]] = {}
    for slot_name in slot_names:
        entry = slot_state_by_name(state, slot_name)
        snapshot[slot_name] = {
            "occupied": bool(entry.get("occupied", False)),
            "binding": entry.get("binding"),
        }
    return snapshot


def assert_topology_snapshot(
    *,
    before: Dict[str, Dict[str, object]],
    after: Dict[str, Dict[str, object]],
) -> None:
    if before != after:
        raise RuntimeError(
            "runtime topology changed unexpectedly across MPLS smoke cycle: "
            f"before={before}, after={after}"
        )


def assert_output_absent(
    *,
    node_id: str,
    command: List[str],
    forbidden_texts: Iterable[str],
    description: str,
) -> None:
    result = run_command(
        ["docker", "exec", node_id, *command],
        failure_stage=description,
    )
    output = result.stdout.strip()
    for forbidden_text in forbidden_texts:
        if forbidden_text and forbidden_text in output:
            raise RuntimeError(
                f"{description}: unexpected forwarding state still present: {forbidden_text}"
            )


def verify_forwarding_cleanup(
    topology_path: Path,
    link_state_path: Path,
    ut_id: str,
) -> None:
    plan = resolve_plan_or_raise(topology_path, link_state_path, ut_id)
    assert_output_absent(
        node_id=plan.ut_id,
        command=["ip", "route", "show", plan.default_egress_prefix],
        forbidden_texts=[plan.default_egress_prefix],
        description=f"verify UT default route cleanup on {plan.ut_id}",
    )
    table_forbidden = ["default"]
    assert_output_absent(
        node_id=plan.serving_satellite_id,
        command=["ip", "route", "show", "table", str(plan.policy_table_id)],
        forbidden_texts=table_forbidden,
        description=f"verify source-policy table cleanup on {plan.serving_satellite_id}",
    )
    assert_output_absent(
        node_id=plan.serving_satellite_id,
        command=["ip", "-f", "mpls", "route", "show"],
        forbidden_texts=[f"{plan.reverse_labels[-1]} dev lo"],
        description=f"verify reverse pop cleanup on {plan.serving_satellite_id}",
    )
    assert_output_absent(
        node_id=plan.selected_gateway_id,
        command=["ip", "-f", "mpls", "route", "show"],
        forbidden_texts=[f"{plan.forward_labels[-1]} dev lo"],
        description=f"verify forward pop cleanup on {plan.selected_gateway_id}",
    )
    assert_output_absent(
        node_id=plan.selected_gateway_id,
        command=["ip", "route", "show", plan.return_target_prefix],
        forbidden_texts=[plan.return_target_prefix],
        description=f"verify reverse ingress route cleanup on {plan.selected_gateway_id}",
    )
    assert_output_absent(
        node_id=plan.selected_gateway_id,
        command=["sh", "-lc", "iptables -t nat -S"],
        forbidden_texts=[
            (
                f"-A POSTROUTING -s {plan.active_access_ip}/32 "
                f"-o {plan.gateway_nat_interface} "
                f"-j SNAT --to-source {plan.gateway_nat_source_ip}"
            )
        ],
        description=f"verify SNAT cleanup on {plan.selected_gateway_id}",
    )
    assert_output_absent(
        node_id=plan.selected_gateway_id,
        command=["sh", "-lc", "iptables -S FORWARD"],
        forbidden_texts=[
            (
                f"-A FORWARD -i {plan.gateway_endpoint.interface_name} "
                f"-o {plan.gateway_nat_interface} "
                f"-s {plan.active_access_ip}/32 -j ACCEPT"
            ),
            (
                f"-A FORWARD -i {plan.gateway_nat_interface} "
                f"-o {plan.gateway_endpoint.interface_name} "
                f"-d {plan.active_access_ip}/32 "
                "-m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
            ),
            (
                f"-A FORWARD -i {plan.gateway_nat_interface} "
                f"-o {plan.gateway_endpoint.interface_name} "
                f"-d {plan.active_access_ip}/32 "
                "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
            ),
        ],
        description=f"verify FORWARD cleanup on {plan.selected_gateway_id}",
    )
    if plan.swap_nodes:
        swap_node = plan.swap_nodes[0]
        assert_output_absent(
            node_id=swap_node,
            command=["ip", "-f", "mpls", "route", "show"],
            forbidden_texts=[
                f"{plan.forward_labels[0]} as to {plan.forward_labels[1]}",
                f"{plan.reverse_labels[0]} as to {plan.reverse_labels[1]}",
            ],
            description=f"verify transit LFIB cleanup on {swap_node}",
        )
    assert_output_absent(
        node_id=plan.serving_satellite_id,
        command=["ip", "route", "show", plan.return_target_prefix],
        forbidden_texts=[plan.return_target_prefix],
        description=(
            f"verify UT delivery route cleanup on {plan.serving_satellite_id}"
        ),
    )


def main() -> int:
    args = parse_args()
    controller_path = Path(args.controller).resolve()
    link_state_path = Path(args.link_state).resolve()
    topology_path = Path(args.topology_config).resolve()

    if args.cleanup:
        controller_action(
            controller_path,
            link_state_path,
            "--cleanup-ut-egress-mpls",
            args.ut,
        )
        verify_forwarding_cleanup(topology_path, link_state_path, args.ut)
        controller_action(controller_path, link_state_path, "--show-link-state")
        print("[mpls-ut-egress-smoke] cleanup completed")
        return 0

    ensure_runtime_topology(controller_path, link_state_path, args.ut)
    prepare_ut_egress_policy(controller_path, link_state_path, args.ut)
    plan = resolve_plan_or_raise(topology_path, link_state_path, args.ut)
    before_state = load_link_state(link_state_path)
    before_snapshot = topology_snapshot(before_state, plan.required_slot_names)

    controller_action(
        controller_path,
        link_state_path,
        "--cleanup-ut-egress-mpls",
        args.ut,
    )
    verify_forwarding_cleanup(topology_path, link_state_path, args.ut)
    controller_action(
        controller_path,
        link_state_path,
        "--install-ut-egress-mpls",
        args.ut,
    )
    controller_action(
        controller_path,
        link_state_path,
        "--verify-ut-egress-mpls",
        args.ut,
        *(["--ping-target", args.ping_target] if args.ping_target else []),
    )
    controller_action(
        controller_path,
        link_state_path,
        "--cleanup-ut-egress-mpls",
        args.ut,
    )
    verify_forwarding_cleanup(topology_path, link_state_path, args.ut)

    after_state = load_link_state(link_state_path)
    after_snapshot = topology_snapshot(after_state, plan.required_slot_names)
    assert_topology_snapshot(before=before_snapshot, after=after_snapshot)
    controller_action(controller_path, link_state_path, "--show-link-state")
    print("[mpls-ut-egress-smoke] cleanup verified: forwarding cleared, topology unchanged")
    print("[mpls-ut-egress-smoke] install + verify + cleanup completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
