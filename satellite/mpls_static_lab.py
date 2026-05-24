#!/usr/bin/env python3
# encoding: utf-8

"""Regression wrapper for the minimal static MPLS lab."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, List

from mpls_control import DEFAULT_STATIC_LSP_PROFILE, get_static_mpls_profile


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
DEFAULT_LINK_STATE = DEFAULT_CONFIG_DIR / "link_state.json"
DEFAULT_CONTROLLER = DEFAULT_DIR / "controller.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the minimal static MPLS regression lab via controller-driven "
            "install/verify/cleanup actions."
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
        "--profile",
        default=DEFAULT_STATIC_LSP_PROFILE.profile_id,
        help="Static MPLS profile id to use.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the lab-specific MPLS routes and interface input flags.",
    )
    return parser.parse_args()


def run_command(command: List[str], *, failure_stage: str) -> subprocess.CompletedProcess[str]:
    print(f"[mpls-lab] {' '.join(command)}")
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
    if result.returncode != 0:
        raise RuntimeError(f"{failure_stage}: command failed with exit status {result.returncode}")
    return result


def controller_action(
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
        failure_stage=f"controller action {' '.join(args)}",
    )


def load_link_state(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def slot_state_by_name(state: Dict[str, object], slot_name: str) -> Dict[str, object]:
    for entry in state.get("slots", []):
        if entry.get("slot_name") == slot_name:
            return entry
    raise KeyError(f"slot not found: {slot_name}")


def ensure_slot_runtime(controller_path: Path, link_state_path: Path) -> Dict[str, object]:
    state = load_link_state(link_state_path)
    if not state.get("runtime_initialized", False):
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


def ensure_runtime_links(
    controller_path: Path,
    link_state_path: Path,
    profile_id: str,
) -> None:
    profile = get_static_mpls_profile(profile_id)

    def ensure_once() -> None:
        ensure_slot_runtime(controller_path, link_state_path)
        ensure_binding(
            controller_path=controller_path,
            link_state_path=link_state_path,
            slot_name=profile.gateway_slot,
            expected_binding=profile.gateway_binding,
            create_args=[
                "--attach-gw",
                profile.gateway_attach_request,
                "--slot-name",
                profile.gateway_slot,
            ],
        )
        ensure_binding(
            controller_path=controller_path,
            link_state_path=link_state_path,
            slot_name=profile.cross_slot,
            expected_binding=profile.cross_binding,
            create_args=[
                "--connect-cross",
                profile.cross_connect_request,
                "--slot-name",
                profile.cross_slot,
            ],
        )

    try:
        ensure_once()
    except RuntimeError as error:
        print(f"[mpls-lab] runtime recovery: {error}")
        print("[mpls-lab] rerunning --init-slot-runtime once before retry")
        controller_action(controller_path, link_state_path, "--init-slot-runtime")
        ensure_once()


def main() -> int:
    args = parse_args()
    controller_path = Path(args.controller).resolve()
    link_state_path = Path(args.link_state).resolve()

    if args.cleanup:
        controller_action(
            controller_path,
            link_state_path,
            "--cleanup-static-mpls-lsp",
            args.profile,
        )
        print("[mpls-lab] cleanup completed")
        return 0

    ensure_runtime_links(controller_path, link_state_path, args.profile)
    controller_action(
        controller_path,
        link_state_path,
        "--install-static-mpls-lsp",
        args.profile,
    )
    controller_action(
        controller_path,
        link_state_path,
        "--verify-static-mpls-lsp",
        args.profile,
    )
    print("[mpls-lab] install + verify completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
