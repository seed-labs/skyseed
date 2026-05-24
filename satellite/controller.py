#!/usr/bin/env python3
# encoding: utf-8

"""Runtime controller for the slot-based satellite link prototype."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from mpls_control import (
    compute_all_attached_ut_egress_plans,
    compute_ut_egress_plan,
    get_ut_egress_intent,
    get_static_mpls_profile,
    resolve_ut_egress_ping_target,
    static_mpls_cleanup_specs,
    static_mpls_install_specs,
    static_mpls_ping_command,
    static_mpls_pop_capture_command,
    static_mpls_push_capture_command,
    static_mpls_ready_node_ids,
    static_mpls_route_show_specs,
    supported_ut_egress_ut_ids,
    ut_egress_policy_preparation_plan,
    ut_egress_policy_prepare_specs,
    ut_egress_cleanup_specs,
    UtEgressCommandSpec,
    ut_access_probe_command,
    ut_egress_install_specs,
    ut_egress_ping_command,
    ut_egress_ready_node_ids,
    ut_egress_route_show_specs,
    validate_static_mpls_path,
)


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
VALID_CROSS_DIRECTIONS = {"left", "right"}
BRIDGE_NETFILTER_SYSCTLS = (
    "net.bridge.bridge-nf-call-iptables",
    "net.bridge.bridge-nf-call-ip6tables",
    "net.bridge.bridge-nf-call-arptables",
)
RUNTIME_PROTOCOL_VERSION = "satellite-distributed-runtime-update-protocol-v1"
RUNTIME_AGENT_SCHEMA_VERSION = "satellite-runtime-local-agent-v1"
RUNTIME_PAYLOAD_SCHEMA_VERSION = "satellite-runtime-update-payload-v2"
RUNTIME_RESPONSE_ENVELOPE_SCHEMA_VERSION = "satellite-runtime-update-response-envelope-v1"
RUNTIME_NODE_METADATA_SCHEMA_VERSION = "satellite-runtime-node-metadata-v1"
RUNTIME_ACK_STATUSES = [
    "pending",
    "awaiting_transport_completion",
    "acknowledged_success",
    "acknowledged_error",
]
RUNTIME_APPLY_STATUSES = [
    "not_started",
    "in_progress",
    "applied",
    "partially_applied",
    "transport_error",
]
RUNTIME_ERROR_CODES = [
    "none",
    "transport_error",
    "command_failed",
    "validation_failed",
    "replay_conflict",
    "unsupported_command",
    "unsupported_protocol_version",
    "malformed_payload",
    "internal_error",
]
RUNTIME_ERROR_CATEGORIES = [
    "none",
    "transport",
    "execution",
    "validation",
    "replay",
    "protocol",
    "payload",
    "capability",
    "internal",
]
RUNTIME_VALIDATION_STATUSES = [
    "not_run",
    "passed",
    "failed",
    "rejected",
]
SCENARIO_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "t0": {
        "description": "Initial explicit runtime scenario for MPLS-4 transition validation.",
        "managed_slots": {
            "ut1-access-slot0": {
                "action": "attach_ut",
                "request": "ut1:sat-a1",
                "binding": {"ut_id": "ut1", "satellite_id": "sat-a1"},
            },
            "ut1-access-slot1": None,
            "ut2-access-slot0": {
                "action": "attach_ut",
                "request": "ut2:sat-a2",
                "binding": {"ut_id": "ut2", "satellite_id": "sat-a2"},
            },
            "ut2-access-slot1": None,
            "ut3-access-slot0": {
                "action": "attach_ut",
                "request": "ut3:sat-b3",
                "binding": {"ut_id": "ut3", "satellite_id": "sat-b3"},
            },
            "ut3-access-slot1": None,
            "gw1-gateway-slot0": None,
            "gw1-gateway-slot1": {
                "action": "attach_gw",
                "request": "gw1:sat-b1",
                "binding": {"gateway_id": "gw1", "satellite_id": "sat-b1"},
            },
            "gw2-gateway-slot0": {
                "action": "attach_gw",
                "request": "gw2:sat-a3",
                "binding": {"gateway_id": "gw2", "satellite_id": "sat-a3"},
            },
            "gw2-gateway-slot1": None,
            "gw3-gateway-slot0": None,
            "gw3-gateway-slot1": None,
            "ab-cross-slot0": {
                "action": "connect_cross",
                "request": "sat-a1:sat-b1:left:right",
                "binding": {
                    "satellite_a_id": "sat-a1",
                    "satellite_b_id": "sat-b1",
                    "satellite_a_direction": "left",
                    "satellite_b_direction": "right",
                },
            },
            "ab-cross-slot1": {
                "action": "connect_cross",
                "request": "sat-a2:sat-b2:left:right",
                "binding": {
                    "satellite_a_id": "sat-a2",
                    "satellite_b_id": "sat-b2",
                    "satellite_a_direction": "left",
                    "satellite_b_direction": "right",
                },
            },
            "ab-cross-slot2": None,
        },
        "verify_ut_id": "ut1",
        "expected_serving_satellite_id": "sat-a1",
        "expected_selected_gateway_id": "gw1",
        "expected_satellite_path": ["sat-a1", "sat-b1"],
    },
    "t1": {
        "description": "Follow-up explicit runtime scenario for MPLS-4 transition validation.",
        "managed_slots": {
            "ut1-access-slot0": {
                "action": "attach_ut",
                "request": "ut1:sat-a2",
                "binding": {"ut_id": "ut1", "satellite_id": "sat-a2"},
            },
            "ut1-access-slot1": None,
            "ut2-access-slot0": {
                "action": "attach_ut",
                "request": "ut2:sat-a3",
                "binding": {"ut_id": "ut2", "satellite_id": "sat-a3"},
            },
            "ut2-access-slot1": None,
            "ut3-access-slot0": {
                "action": "attach_ut",
                "request": "ut3:sat-b1",
                "binding": {"ut_id": "ut3", "satellite_id": "sat-b1"},
            },
            "ut3-access-slot1": None,
            "gw1-gateway-slot0": None,
            "gw1-gateway-slot1": None,
            "gw2-gateway-slot0": {
                "action": "attach_gw",
                "request": "gw2:sat-b2",
                "binding": {"gateway_id": "gw2", "satellite_id": "sat-b2"},
            },
            "gw2-gateway-slot1": None,
            "gw3-gateway-slot0": None,
            "gw3-gateway-slot1": None,
            "ab-cross-slot0": {
                "action": "connect_cross",
                "request": "sat-a2:sat-b2:left:right",
                "binding": {
                    "satellite_a_id": "sat-a2",
                    "satellite_b_id": "sat-b2",
                    "satellite_a_direction": "left",
                    "satellite_b_direction": "right",
                },
            },
            "ab-cross-slot1": {
                "action": "connect_cross",
                "request": "sat-a3:sat-b3:left:right",
                "binding": {
                    "satellite_a_id": "sat-a3",
                    "satellite_b_id": "sat-b3",
                    "satellite_a_direction": "left",
                    "satellite_b_direction": "right",
                },
            },
            "ab-cross-slot2": None,
        },
        "verify_ut_id": "ut1",
        "expected_serving_satellite_id": "sat-a2",
        "expected_selected_gateway_id": "gw2",
        "expected_satellite_path": ["sat-a2", "sat-b2"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Satellite Phase 2 slot-based dynamic link controller."
    )
    parser.add_argument(
        "--topology-config",
        default=str(DEFAULT_CONFIG_DIR / "topology.json"),
        help="Path to the topology inventory JSON file.",
    )
    parser.add_argument(
        "--link-state",
        default=str(DEFAULT_CONFIG_DIR / "link_state.json"),
        help="Path to the slot/link state JSON file.",
    )
    parser.add_argument(
        "--slot-name",
        help=(
            "Optional explicit slot name for attach/connect or owner-scoped "
            "release operations. If unset, the controller uses the first free "
            "or current occupied slot as appropriate."
        ),
    )
    parser.add_argument(
        "--init-slot-runtime",
        action="store_true",
        help=(
            "Reset and recreate all dynamic slot bridge networks, preconnect "
            "UT/GW fixed endpoints, and rewrite link_state.json."
        ),
    )
    parser.add_argument(
        "--show-link-state",
        action="store_true",
        help="Print the current slot/link state.",
    )
    parser.add_argument(
        "--probe-mpls",
        action="store_true",
        help=(
            "Run a read-only MPLS capability probe against the host and "
            "currently running satellite/gateway containers."
        ),
    )
    parser.add_argument(
        "--probe-report",
        help="Optional path to write the MPLS probe JSON report.",
    )
    parser.add_argument(
        "--install-static-mpls-lsp",
        help=(
            "Install one supported static MPLS LSP profile, including runtime "
            "slot preparation and Linux MPLS route programming."
        ),
    )
    parser.add_argument(
        "--verify-static-mpls-lsp",
        help=(
            "Verify one supported static MPLS LSP profile with LFIB reads, "
            "tcpdump evidence, and end-to-end ping."
        ),
    )
    parser.add_argument(
        "--cleanup-static-mpls-lsp",
        help=(
            "Remove one supported static MPLS LSP profile's Linux MPLS/IP "
            "routes and interface input flags without releasing runtime slots."
        ),
    )
    parser.add_argument(
        "--install-ut-egress-mpls",
        help=(
            "Install one supported UT Internet-egress MPLS intent by reading "
            "the current link_state and programming Linux MPLS/IP routes."
        ),
    )
    parser.add_argument(
        "--verify-ut-egress-mpls",
        help=(
            "Verify one supported UT Internet-egress MPLS intent with "
            "path resolution, LFIB reads, tcpdump evidence, and end-to-end ping."
        ),
    )
    parser.add_argument(
        "--cleanup-ut-egress-mpls",
        help=(
            "Remove one supported UT Internet-egress MPLS intent's Linux "
            "MPLS/IP routes without releasing runtime slots."
        ),
    )
    parser.add_argument(
        "--prepare-ut-egress-policy",
        help=(
            "Install low-frequency UT egress source-policy guardrails on every "
            "candidate serving satellite for one supported UT."
        ),
    )
    parser.add_argument(
        "--prepare-all-ut-egress-policies",
        action="store_true",
        help=(
            "Install low-frequency UT egress source-policy guardrails for all "
            "supported UT intents."
        ),
    )
    parser.add_argument(
        "--install-all-attached-ut-egress-mpls",
        action="store_true",
        help=(
            "Install Linux MPLS/IP routes for all supported UT intents that "
            "currently have exactly one active access attachment."
        ),
    )
    parser.add_argument(
        "--verify-all-attached-ut-egress-mpls",
        action="store_true",
        help=(
            "Verify Linux MPLS/IP routes for all supported UT intents that "
            "currently have exactly one active access attachment."
        ),
    )
    parser.add_argument(
        "--cleanup-all-attached-ut-egress-mpls",
        action="store_true",
        help=(
            "Remove Linux MPLS/IP routes for all supported UT intents that "
            "currently have exactly one active access attachment."
        ),
    )
    parser.add_argument(
        "--ping-target",
        help=(
            "Optional non-local Internet target IP to use for UT egress "
            "verification. If unset, each UT intent uses its default target."
        ),
    )
    parser.add_argument(
        "--apply-scenario",
        help="Apply one supported explicit runtime scenario, such as t0 or t1.",
    )
    parser.add_argument(
        "--verify-scenario",
        help=(
            "Verify one supported explicit runtime scenario against link_state "
            "and the current UT egress path selection."
        ),
    )
    parser.add_argument(
        "--attach-ut",
        help="Attach a UT to a satellite with one free access slot: <ut-id>:<satellite-id>.",
    )
    parser.add_argument(
        "--detach-ut",
        help="Release the currently occupied access slot of a UT: <ut-id>.",
    )
    parser.add_argument(
        "--attach-gw",
        help="Attach a gateway to a satellite with one free gateway slot: <gateway-id>:<satellite-id>.",
    )
    parser.add_argument(
        "--detach-gw",
        help="Release the currently occupied gateway slot of a gateway: <gateway-id>.",
    )
    parser.add_argument(
        "--connect-cross",
        help=(
            "Connect one orbit-a satellite and one orbit-b satellite through a "
            "free cross-plane slot: <sat-a>:<sat-b>:<dir-a>:<dir-b>."
        ),
    )
    parser.add_argument(
        "--release-slot",
        help="Release one occupied dynamic slot by slot name.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Render the per-command execution plan for the selected action "
            "without mutating runtime state."
        ),
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Print action timing and command-category counts after execution.",
    )

    args = parser.parse_args()

    action_count = sum(
        1
        for enabled in (
            args.init_slot_runtime,
            args.show_link_state,
            args.probe_mpls,
            bool(args.install_static_mpls_lsp),
            bool(args.verify_static_mpls_lsp),
            bool(args.cleanup_static_mpls_lsp),
            bool(args.prepare_ut_egress_policy),
            args.prepare_all_ut_egress_policies,
            bool(args.install_ut_egress_mpls),
            bool(args.verify_ut_egress_mpls),
            bool(args.cleanup_ut_egress_mpls),
            args.install_all_attached_ut_egress_mpls,
            args.verify_all_attached_ut_egress_mpls,
            args.cleanup_all_attached_ut_egress_mpls,
            bool(args.apply_scenario),
            bool(args.verify_scenario),
            bool(args.attach_ut),
            bool(args.detach_ut),
            bool(args.attach_gw),
            bool(args.detach_gw),
            bool(args.connect_cross),
            bool(args.release_slot),
        )
        if enabled
    )
    if action_count > 1:
        parser.error("run only one action per command")
    if args.slot_name and not (
        args.attach_ut
        or args.detach_ut
        or args.attach_gw
        or args.detach_gw
        or args.connect_cross
    ):
        parser.error(
            "--slot-name currently applies only to --attach-ut, --detach-ut, "
            "--attach-gw, --detach-gw, or --connect-cross"
        )
    if args.probe_report and not args.probe_mpls:
        parser.error("--probe-report applies only to --probe-mpls")
    if args.ping_target and not (
        args.verify_ut_egress_mpls or args.verify_all_attached_ut_egress_mpls
    ):
        parser.error("--ping-target currently applies only to UT egress verify actions")

    return args


def load_json(path_str: str) -> Dict[str, Any]:
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path_str: str, data: Dict[str, Any]) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class SatelliteController:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.topology = load_json(args.topology_config)
        self.link_state = self._load_link_state(args.link_state)
        self._active_command_observation: Optional[Dict[str, Any]] = None

    def _default_link_state(self) -> Dict[str, Any]:
        return {
            "schema_version": "satellite-phase2-slot-state-v1",
            "runtime_initialized": False,
            "topology_schema_version": self.topology.get("schema_version"),
            "slots": [],
        }

    def _load_link_state(self, path_str: str) -> Dict[str, Any]:
        path = Path(path_str)
        if not path.exists():
            return self._default_link_state()
        return load_json(path_str)

    def _save_link_state(self, state: Dict[str, Any]) -> None:
        write_json(self.args.link_state, state)
        self.link_state = state

    def _utc_timestamp(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00",
            "Z",
        )

    def _runtime_protocol_version(self) -> str:
        return RUNTIME_PROTOCOL_VERSION

    def _runtime_agent_schema_version(self) -> str:
        return RUNTIME_AGENT_SCHEMA_VERSION

    def _runtime_supported_ack_statuses(self) -> List[str]:
        return list(RUNTIME_ACK_STATUSES)

    def _runtime_supported_apply_statuses(self) -> List[str]:
        return list(RUNTIME_APPLY_STATUSES)

    def _runtime_supported_error_codes(self) -> List[str]:
        return list(RUNTIME_ERROR_CODES)

    def _runtime_supported_error_categories(self) -> List[str]:
        return list(RUNTIME_ERROR_CATEGORIES)

    def _runtime_supported_validation_statuses(self) -> List[str]:
        return list(RUNTIME_VALIDATION_STATUSES)

    def _execution_nonce(self, node_plan_id: str) -> str:
        return f"{node_plan_id}:{uuid4().hex}"


    def _slot_inventory(self) -> Dict[str, List[Dict[str, Any]]]:
        inventory = self.topology.get("slot_inventory", {})
        return {
            "ut_access": list(inventory.get("ut_access", [])),
            "gateway_access": list(inventory.get("gateway_access", [])),
            "cross_plane_isl": list(inventory.get("cross_plane_isl", [])),
        }

    def _slot_inventory_by_name(self) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        for group in self._slot_inventory().values():
            for entry in group:
                entries[str(entry.get("slot_name"))] = entry
        return entries

    def _slot_state_by_name(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        source = self.link_state if state is None else state
        return {
            str(entry.get("slot_name")): entry
            for entry in source.get("slots", [])
        }

    def _require_slot_type(
        self,
        *,
        state: Dict[str, Any],
        slot_name: str,
        expected_slot_type: str,
    ) -> Dict[str, Any]:
        slot_state = self._slot_state_by_name(state).get(slot_name)
        if slot_state is None:
            raise ValueError(f"unknown slot name: {slot_name}")
        if slot_state.get("slot_type") != expected_slot_type:
            raise RuntimeError(
                f"{slot_name} is {slot_state.get('slot_type')}, expected {expected_slot_type}"
            )
        return slot_state

    def _fixed_links(self) -> List[Dict[str, Any]]:
        return list(self.topology.get("fixed_links", {}).get("satellite_satellite", []))

    def _satellite_ids(self) -> set[str]:
        return {
            str(entry.get("satellite_id"))
            for entry in self.topology.get("satellites", [])
            if entry.get("satellite_id") is not None
        }

    def _gateway_ids(self) -> set[str]:
        return {
            str(entry.get("gateway_id"))
            for entry in self.topology.get("gateways", [])
            if entry.get("gateway_id") is not None
        }

    def _ut_ids(self) -> set[str]:
        return {
            str(entry.get("ut_id"))
            for entry in self.topology.get("user_terminals", [])
            if entry.get("ut_id") is not None
        }

    def _satellite_topology_entry(self, satellite_id: str) -> Dict[str, Any]:
        for entry in self.topology.get("satellites", []):
            if entry.get("satellite_id") == satellite_id:
                return dict(entry)
        raise ValueError(f"unknown satellite_id: {satellite_id}")

    def _gateway_topology_entry(self, gateway_id: str) -> Dict[str, Any]:
        for entry in self.topology.get("gateways", []):
            if entry.get("gateway_id") == gateway_id:
                return dict(entry)
        raise ValueError(f"unknown gateway_id: {gateway_id}")

    def _ut_topology_entry(self, ut_id: str) -> Dict[str, Any]:
        for entry in self.topology.get("user_terminals", []):
            if entry.get("ut_id") == ut_id:
                return dict(entry)
        raise ValueError(f"unknown ut_id: {ut_id}")

    def _satellite_orbit(self, satellite_id: str) -> str:
        for entry in self.topology.get("satellites", []):
            if entry.get("satellite_id") == satellite_id:
                orbit_id = entry.get("orbit_id")
                if orbit_id is None:
                    break
                return str(orbit_id)
        raise ValueError(f"unknown satellite_id: {satellite_id}")

    def _build_initial_link_state(self) -> Dict[str, Any]:
        slots: List[Dict[str, Any]] = []

        for slot in self._slot_inventory().get("ut_access", []):
            pre = dict(slot.get("preconnected_endpoint", {}))
            slots.append(
                {
                    "slot_name": slot.get("slot_name"),
                    "slot_type": "ut_access",
                    "network_name": slot.get("network_name"),
                    "docker_network_name": slot.get("docker_network_name"),
                    "subnet": slot.get("subnet"),
                    "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                    "owner_node_id": slot.get("ut_id"),
                    "preconnected_endpoints": [pre],
                    "dynamic_endpoints": [],
                    "occupied": False,
                    "binding": None,
                }
            )

        for slot in self._slot_inventory().get("gateway_access", []):
            pre = dict(slot.get("preconnected_endpoint", {}))
            slots.append(
                {
                    "slot_name": slot.get("slot_name"),
                    "slot_type": "gw_access",
                    "network_name": slot.get("network_name"),
                    "docker_network_name": slot.get("docker_network_name"),
                    "subnet": slot.get("subnet"),
                    "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                    "owner_node_id": slot.get("gateway_id"),
                    "preconnected_endpoints": [pre],
                    "dynamic_endpoints": [],
                    "occupied": False,
                    "binding": None,
                }
            )

        for slot in self._slot_inventory().get("cross_plane_isl", []):
            slots.append(
                {
                    "slot_name": slot.get("slot_name"),
                    "slot_type": "cross_plane_isl",
                    "network_name": slot.get("network_name"),
                    "docker_network_name": slot.get("docker_network_name"),
                    "subnet": slot.get("subnet"),
                    "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                    "owner_node_id": None,
                    "preconnected_endpoints": [],
                    "dynamic_endpoints": [],
                    "occupied": False,
                    "binding": None,
                }
            )

        for link in self._fixed_links():
            slots.append(
                {
                    "slot_name": link.get("link_id"),
                    "slot_type": "fixed_isl",
                    "network_name": link.get("network_name"),
                    "docker_network_name": link.get("docker_network_name"),
                    "subnet": link.get("subnet"),
                    "bridge_gateway_ip": None,
                    "owner_node_id": None,
                    "preconnected_endpoints": list(link.get("endpoints", [])),
                    "dynamic_endpoints": [],
                    "occupied": True,
                    "binding": {
                        "satellite_a_id": link.get("endpoints", [{}])[0].get("node_id"),
                        "satellite_b_id": link.get("endpoints", [{}, {}])[1].get("node_id"),
                    },
                }
            )

        return {
            "schema_version": "satellite-phase2-slot-state-v1",
            "runtime_initialized": True,
            "topology_schema_version": self.topology.get("schema_version"),
            "slots": sorted(slots, key=lambda entry: str(entry.get("slot_name", ""))),
        }

    def _run_command(
        self,
        command: List[str],
        *,
        failure_stage: str,
        allow_failure: bool = False,
        observation: Optional[Dict[str, Any]] = None,
    ) -> subprocess.CompletedProcess[str]:
        rendered = shlex.join(command)
        print(f"  {rendered}")
        started_at = time.monotonic()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed_seconds = time.monotonic() - started_at
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip())
        self._record_command_execution(
            command=command,
            failure_stage=failure_stage,
            allow_failure=allow_failure,
            result=result,
            elapsed_seconds=elapsed_seconds,
            observation=observation,
        )
        if result.returncode != 0 and not allow_failure:
            raise RuntimeError(
                f"{failure_stage}: command failed with exit status {result.returncode}: {rendered}"
            )
        return result

    def _begin_command_observation(self, action_name: str) -> None:
        if self._active_command_observation is not None:
            raise RuntimeError(
                "command observation already active for "
                f"{self._active_command_observation.get('action')}"
            )
        self._active_command_observation = {
            "action": action_name,
            "started_at": time.monotonic(),
            "commands": [],
        }

    def _end_command_observation(self) -> Dict[str, Any]:
        if self._active_command_observation is None:
            return {
                "action": "none",
                "total_commands": 0,
                "elapsed_seconds": 0.0,
                "node_counts": {},
                "command_family_counts": {},
                "logical_category_counts": {},
                "logical_kind_counts": {},
                "node_command_family_counts": {},
                "node_logical_category_counts": {},
                "node_logical_kind_counts": {},
            }

        observation = self._active_command_observation
        self._active_command_observation = None
        commands = list(observation.get("commands", []))
        node_counts = Counter(entry["node_id"] for entry in commands)
        family_counts = Counter(entry["command_family"] for entry in commands)
        logical_category_counts = Counter(entry["logical_category"] for entry in commands)
        logical_kind_counts = Counter(entry["logical_kind"] for entry in commands)

        node_family_counts: Dict[str, Counter[str]] = {}
        node_category_counts: Dict[str, Counter[str]] = {}
        node_kind_counts: Dict[str, Counter[str]] = {}
        for entry in commands:
            node_id = str(entry["node_id"])
            node_family_counts.setdefault(node_id, Counter())[entry["command_family"]] += 1
            node_category_counts.setdefault(node_id, Counter())[entry["logical_category"]] += 1
            node_kind_counts.setdefault(node_id, Counter())[entry["logical_kind"]] += 1

        return {
            "action": observation.get("action"),
            "total_commands": len(commands),
            "elapsed_seconds": round(time.monotonic() - observation["started_at"], 3),
            "node_counts": dict(sorted(node_counts.items())),
            "command_family_counts": dict(sorted(family_counts.items())),
            "logical_category_counts": dict(sorted(logical_category_counts.items())),
            "logical_kind_counts": dict(sorted(logical_kind_counts.items())),
            "node_command_family_counts": {
                node_id: dict(sorted(counter.items()))
                for node_id, counter in sorted(node_family_counts.items())
            },
            "node_logical_category_counts": {
                node_id: dict(sorted(counter.items()))
                for node_id, counter in sorted(node_category_counts.items())
            },
            "node_logical_kind_counts": {
                node_id: dict(sorted(counter.items()))
                for node_id, counter in sorted(node_kind_counts.items())
            },
            "commands": commands,
        }

    def _print_command_observation_summary(
        self,
        summary: Dict[str, Any],
        *,
        label: str,
    ) -> None:
        print(f"{label}: {summary.get('action')}")
        print(f"  total_commands={summary.get('total_commands')}")
        print(f"  elapsed_seconds={summary.get('elapsed_seconds')}")
        print(
            "  node_counts="
            + json.dumps(summary.get("node_counts", {}), sort_keys=True)
        )
        print(
            "  command_family_counts="
            + json.dumps(summary.get("command_family_counts", {}), sort_keys=True)
        )
        print(
            "  logical_category_counts="
            + json.dumps(summary.get("logical_category_counts", {}), sort_keys=True)
        )
        print(
            "  logical_kind_counts="
            + json.dumps(summary.get("logical_kind_counts", {}), sort_keys=True)
        )
        print(
            "  node_command_family_counts="
            + json.dumps(summary.get("node_command_family_counts", {}), sort_keys=True)
        )
        print(
            "  node_logical_kind_counts="
            + json.dumps(summary.get("node_logical_kind_counts", {}), sort_keys=True)
        )

    def _command_target_node_and_effective_command(
        self,
        command: Sequence[str],
    ) -> Tuple[str, List[str]]:
        command_list = list(command)
        if len(command_list) >= 3 and command_list[0] == "docker" and command_list[1] == "exec":
            return str(command_list[2]), command_list[3:]
        if (
            len(command_list) >= 5
            and command_list[0] == "docker"
            and command_list[1] == "network"
            and command_list[2] in {"connect", "disconnect"}
        ):
            return str(command_list[-1]), command_list[:3]
        return "host", command_list

    def _infer_command_family(self, command: Sequence[str]) -> str:
        command_list = list(command)
        if not command_list:
            return "unknown"
        if len(command_list) >= 3 and command_list[0] == "docker" and command_list[1] == "network":
            return f"docker_network_{command_list[2]}"
        if command_list[0] == "docker":
            return "docker"
        if command_list[0] == "ip":
            if len(command_list) >= 4 and command_list[1] == "-f" and command_list[2] == "mpls":
                return "ip_mpls_route"
            if len(command_list) >= 2:
                subcommand = command_list[1]
                if subcommand == "route":
                    return "ip_route"
                if subcommand == "rule":
                    return "ip_rule"
                if subcommand == "link":
                    return "ip_link"
                if subcommand == "addr":
                    return "ip_addr"
            return "ip"
        if command_list[0] == "sysctl":
            return "sysctl"
        if command_list[0] == "ping":
            return "ping"
        if command_list[0] == "sh" and len(command_list) >= 3 and command_list[1] == "-lc":
            script = command_list[2]
            if "iptables" in script:
                return "iptables"
            if "ip -f mpls route" in script:
                return "ip_mpls_route"
            if "ip rule" in script:
                return "ip_rule"
            if "ip route" in script:
                return "ip_route"
            if "ping " in script:
                return "ping"
            return "shell"
        return command_list[0]

    def _infer_logical_kind(
        self,
        *,
        effective_command: Sequence[str],
        command_family: str,
        failure_stage: str,
    ) -> str:
        command_list = list(effective_command)
        stage = failure_stage.lower()
        if command_family.startswith("docker_network_"):
            return command_family
        if command_family == "ip_link":
            if " name " in f" {' '.join(command_list)} ":
                return "interface_rename"
            if " up" in f" {' '.join(command_list)} " or " down" in f" {' '.join(command_list)} ":
                return "interface_state"
            if " del " in f" {' '.join(command_list)} ":
                return "interface_delete"
            return "ip_link"
        if command_family == "ip_addr":
            if " flush " in f" {' '.join(command_list)} ":
                return "address_flush"
            if " add " in f" {' '.join(command_list)} ":
                return "address_add"
            return "ip_addr"
        if command_family == "ip_route":
            if "unreachable" in command_list:
                return "guardrail_route"
            if "encap" in command_list and "table" in command_list:
                return "policy_table_default_route"
            if "encap" in command_list:
                return "mpls_encap_route"
            if " show " in f" {' '.join(command_list)} ":
                return "route_show"
            if " replace " in f" {' '.join(command_list)} ":
                return "route_replace"
            if " del " in f" {' '.join(command_list)} ":
                return "route_delete"
            return "ip_route"
        if command_family == "ip_rule":
            if " show " in f" {' '.join(command_list)} ":
                return "rule_show"
            if " add " in f" {' '.join(command_list)} " or "add pref" in stage:
                return "source_policy_rule"
            return "ip_rule"
        if command_family == "ip_mpls_route":
            joined = f" {' '.join(command_list)} "
            if " show " in joined:
                return "lfib_show"
            if " del " in joined:
                return "lfib_delete"
            if " as " in joined:
                return "lfib_swap"
            if " dev lo " in joined:
                return "lfib_pop"
            return "lfib_replace"
        if command_family == "iptables":
            if "SNAT" in " ".join(command_list):
                return "gateway_snat_rule"
            if "ESTABLISHED,RELATED" in " ".join(command_list):
                return "gateway_reverse_filter_rule"
            if "FORWARD" in " ".join(command_list):
                return "gateway_forward_filter_rule"
            return "iptables"
        if command_family == "sysctl":
            if "net.mpls.conf." in " ".join(command_list):
                return "mpls_input_sysctl"
            return "sysctl"
        if "normalize internal guardrail routes" in stage:
            return "guardrail_normalize"
        return command_family

    def _record_command_execution(
        self,
        *,
        command: Sequence[str],
        failure_stage: str,
        allow_failure: bool,
        result: subprocess.CompletedProcess[str],
        elapsed_seconds: float,
        observation: Optional[Dict[str, Any]] = None,
    ) -> None:
        active = self._active_command_observation
        if active is None:
            return
        observation = observation or {}
        node_id, effective_command = self._command_target_node_and_effective_command(command)
        command_family = str(
            observation.get("command_family")
            or self._infer_command_family(effective_command)
        )
        logical_category = str(
            observation.get("logical_category")
            or (
                "runtime_mutation"
                if command_family.startswith("docker_network_")
                or command_family in {"ip_link", "ip_addr"}
                else "runtime_command"
            )
        )
        logical_kind = str(
            observation.get("logical_kind")
            or self._infer_logical_kind(
                effective_command=effective_command,
                command_family=command_family,
                failure_stage=failure_stage,
            )
        )
        commands = active["commands"]
        commands.append(
            {
                "index": len(commands) + 1,
                "node_id": node_id,
                "command_family": command_family,
                "logical_category": logical_category,
                "logical_kind": logical_kind,
                "failure_stage": failure_stage,
                "allow_failure": allow_failure,
                "returncode": result.returncode,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "command": shlex.join(list(command)),
                "effective_command": shlex.join(effective_command),
                "shared_key": observation.get("shared_key"),
            }
        )

    def _docker_network_exists(self, docker_network_name: str) -> bool:
        result = subprocess.run(
            ["docker", "network", "inspect", docker_network_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _command_exists(self, command_name: str) -> bool:
        return shutil.which(command_name) is not None

    def _probe_command(
        self,
        command: List[str],
        *,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        effective_command = command
        if node_id is not None:
            effective_command = ["docker", "exec", node_id, *command]

        rendered = shlex.join(effective_command)
        try:
            result = subprocess.run(
                effective_command,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as error:
            return {
                "command": rendered,
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": str(error),
            }

        return {
            "command": rendered,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    def _probe_text(self, result: Dict[str, Any]) -> str:
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if stdout and stderr:
            return f"{stdout}\n{stderr}"
        return stdout or stderr

    def _probe_sysctl(self, key: str, *, node_id: Optional[str] = None) -> Dict[str, Any]:
        result = self._probe_command(["sysctl", key], node_id=node_id)
        text = self._probe_text(result)
        value: Optional[str] = None
        int_value: Optional[int] = None
        if result.get("ok") and "=" in text:
            value = text.split("=", 1)[1].strip()
            try:
                int_value = int(value)
            except (TypeError, ValueError):
                int_value = None
        return {
            "name": key,
            "available": bool(result.get("ok")),
            "value": value,
            "int_value": int_value,
            "command": result.get("command"),
            "returncode": result.get("returncode"),
            "error": None if result.get("ok") else text,
        }

    def _ensure_runtime_node_mpls_platform_labels(
        self,
        node_id: str,
        *,
        value: int = 1000,
    ) -> None:
        result = self._probe_sysctl("net.mpls.platform_labels", node_id=node_id)
        if (result.get("int_value") or 0) > 0:
            return
        self._docker_exec(
            node_id,
            ["sysctl", "-w", f"net.mpls.platform_labels={value}"],
            failure_stage=f"set net.mpls.platform_labels on {node_id}",
        )

    def _probe_host_module(self, module_name: str) -> Dict[str, Any]:
        loaded = False
        modules_path = Path("/proc/modules")
        if modules_path.exists():
            loaded = any(
                line.startswith(f"{module_name} ")
                for line in modules_path.read_text(encoding="utf-8").splitlines()
            )

        if not self._command_exists("modprobe"):
            return {
                "module": module_name,
                "loaded": loaded,
                "loadable": False,
                "dry_run": None,
                "error": "modprobe not found on host",
            }

        dry_run = self._probe_command(["modprobe", "-n", "-v", module_name])
        dry_run_text = self._probe_text(dry_run)
        return {
            "module": module_name,
            "loaded": loaded,
            "loadable": bool(dry_run.get("ok")),
            "dry_run": {
                "command": dry_run.get("command"),
                "returncode": dry_run.get("returncode"),
                "output": dry_run_text,
            },
            "error": None if dry_run.get("ok") else dry_run_text,
        }

    def _docker_container_inspect(self, node_id: str) -> Dict[str, Any]:
        if not self._command_exists("docker"):
            return {
                "exists": False,
                "running": False,
                "error": "docker not found on host",
                "host_config": None,
            }

        result = subprocess.run(
            ["docker", "inspect", node_id],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            text = "\n".join(
                value.strip()
                for value in (result.stdout, result.stderr)
                if value and value.strip() and value.strip() != "[]"
            )
            return {
                "exists": False,
                "running": False,
                "error": text or f"docker inspect failed for {node_id}",
                "host_config": None,
            }

        payload = json.loads(result.stdout)
        if not payload:
            return {
                "exists": False,
                "running": False,
                "error": f"docker inspect returned empty payload for {node_id}",
                "host_config": None,
            }

        entry = payload[0]
        host_config = entry.get("HostConfig") or {}
        state = entry.get("State") or {}
        return {
            "exists": True,
            "running": bool(state.get("Running", False)),
            "error": None,
            "host_config": {
                "privileged": bool(host_config.get("Privileged", False)),
                "cap_add": list(host_config.get("CapAdd") or []),
                "cap_drop": list(host_config.get("CapDrop") or []),
                "security_opt": list(host_config.get("SecurityOpt") or []),
                "sysctls": dict(host_config.get("Sysctls") or {}),
            },
        }

    def _default_mpls_probe_nodes(self) -> List[str]:
        return sorted(self._satellite_ids() | self._gateway_ids())

    def _probe_runtime_node_mpls(self, node_id: str) -> Dict[str, Any]:
        inspect = self._docker_container_inspect(node_id)
        report: Dict[str, Any] = {
            "node_id": node_id,
            "exists": inspect.get("exists", False),
            "running": inspect.get("running", False),
            "error": inspect.get("error"),
            "host_config": inspect.get("host_config"),
            "checks": {},
            "ready": False,
            "blocking_reasons": [],
        }
        if not inspect.get("exists", False):
            report["blocking_reasons"] = ["container_not_found"]
            return report
        if not inspect.get("running", False):
            report["blocking_reasons"] = ["container_not_running"]
            return report

        sysctl_probe = self._probe_sysctl("net.mpls.platform_labels", node_id=node_id)
        route_show = self._probe_command(["ip", "-f", "mpls", "route", "show"], node_id=node_id)
        proc_dir = self._probe_command(
            ["sh", "-lc", "test -d /proc/sys/net/mpls && echo present || echo absent"],
            node_id=node_id,
        )

        report["checks"] = {
            "sysctl_platform_labels": sysctl_probe,
            "ip_f_mpls_route_show": {
                "command": route_show.get("command"),
                "returncode": route_show.get("returncode"),
                "ok": route_show.get("ok"),
                "output": self._probe_text(route_show),
            },
            "proc_sys_net_mpls_dir": {
                "command": proc_dir.get("command"),
                "returncode": proc_dir.get("returncode"),
                "present": str(proc_dir.get("stdout") or "").strip() == "present",
                "error": None if proc_dir.get("ok") else self._probe_text(proc_dir),
            },
        }

        blocking_reasons: List[str] = []
        host_config = inspect.get("host_config") or {}
        cap_add = {str(value) for value in host_config.get("cap_add") or []}
        privileged = bool(host_config.get("privileged", False))
        if not privileged and "ALL" not in cap_add and "NET_ADMIN" not in cap_add:
            blocking_reasons.append("docker_capability_missing:need_NET_ADMIN_or_privileged")
        if not report["checks"]["proc_sys_net_mpls_dir"]["present"]:
            blocking_reasons.append("proc_sys_net_mpls_missing")
        if not sysctl_probe.get("available", False):
            blocking_reasons.append("sysctl_net.mpls.platform_labels_unavailable")
        elif sysctl_probe.get("int_value") is None:
            blocking_reasons.append("sysctl_net.mpls.platform_labels_non_numeric")
        elif int(sysctl_probe["int_value"]) <= 0:
            blocking_reasons.append("sysctl_net.mpls.platform_labels_zero")
        if not route_show.get("ok", False):
            blocking_reasons.append("ip_f_mpls_route_show_failed")

        report["blocking_reasons"] = blocking_reasons
        report["ready"] = not blocking_reasons
        return report

    def probe_mpls(self) -> Dict[str, Any]:
        host_sysctl = self._probe_sysctl("net.mpls.platform_labels")
        host_route_show = self._probe_command(["ip", "-f", "mpls", "route", "show"])
        host_route_help = self._probe_command(["ip", "route", "help"])
        host_route_help_text = self._probe_text(host_route_help).lower()

        docker_info: Dict[str, Any] = {
            "available": self._command_exists("docker"),
            "daemon_reachable": False,
            "security_options": [],
            "error": None,
        }
        if docker_info["available"]:
            docker_info_result = self._probe_command(
                ["docker", "info", "--format", "{{json .SecurityOptions}}"]
            )
            docker_info["daemon_reachable"] = bool(docker_info_result.get("ok"))
            if docker_info_result.get("ok"):
                try:
                    docker_info["security_options"] = json.loads(
                        docker_info_result.get("stdout") or "[]"
                    )
                except json.JSONDecodeError:
                    docker_info["security_options"] = []
            else:
                docker_info["error"] = self._probe_text(docker_info_result)
        else:
            docker_info["error"] = "docker not found on host"

        runtime_nodes = {
            node_id: self._probe_runtime_node_mpls(node_id)
            for node_id in self._default_mpls_probe_nodes()
        }
        running_node_reports = [
            report for report in runtime_nodes.values() if report.get("running", False)
        ]

        modules = {
            module_name: self._probe_host_module(module_name)
            for module_name in ("mpls_router", "mpls_iptunnel")
        }
        host_route_show_ok = bool(host_route_show.get("ok"))
        host_supports_encap_mpls = "encaptype := [ mpls" in host_route_help_text
        host_supports_mpls_family = "family := [ inet | inet6 | mpls" in host_route_help_text

        summary_reasons: List[str] = []
        if not all(module.get("loaded", False) for module in modules.values()):
            summary_reasons.append("host_mpls_modules_not_loaded")
        if not all(module.get("loadable", False) for module in modules.values()):
            summary_reasons.append("host_mpls_modules_not_loadable")
        if not host_sysctl.get("available", False):
            summary_reasons.append("host_net.mpls.platform_labels_unavailable")
        elif host_sysctl.get("int_value") is None:
            summary_reasons.append("host_net.mpls.platform_labels_non_numeric")
        elif int(host_sysctl["int_value"]) <= 0:
            summary_reasons.append("host_net.mpls.platform_labels_zero")
        if not host_route_show_ok:
            summary_reasons.append("host_ip_f_mpls_route_show_failed")
        if not host_supports_encap_mpls or not host_supports_mpls_family:
            summary_reasons.append("host_iproute2_mpls_support_incomplete")
        if not docker_info.get("daemon_reachable", False):
            summary_reasons.append("docker_daemon_unreachable")
        if running_node_reports and not all(report.get("ready", False) for report in running_node_reports):
            summary_reasons.append("runtime_nodes_not_mpls_ready")

        recommended_next_steps = [
            "Load host modules with modprobe mpls_router and modprobe mpls_iptunnel if they are not already loaded.",
            "Expose net.mpls.platform_labels on the host before testing label programming inside containers.",
            "Start the satellite example containers, then rerun --probe-mpls to verify container-visible sysctls and iproute2.",
            "Only move to MPLS-1 push/swap/pop tests after host and runtime node checks are both green.",
        ]
        if not summary_reasons:
            recommended_next_steps = [
                "MPLS-0 readiness is green on both the host and current satellite/gateway containers.",
                "Proceed to MPLS-1 with a minimal static label lab such as sat-a1 -> sat-b1 -> gw1.",
                "Keep link_state.json as the topology truth and add label programming separately from slot-runtime state.",
            ]

        report = {
            "schema_version": "satellite-mpls-probe-v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "topology_schema_version": self.topology.get("schema_version"),
            "requirements": {
                "host_kernel_modules": ["mpls_router", "mpls_iptunnel"],
                "host_sysctls": ["net.mpls.platform_labels"],
                "iproute2_features": ["ip -f mpls route", "ip route ... encap mpls"],
                "docker_runtime": [
                    "running container",
                    "NET_ADMIN or privileged container",
                    "visible /proc/sys/net/mpls inside container",
                ],
            },
            "host": {
                "kernel_release": self._probe_text(self._probe_command(["uname", "-r"])),
                "commands": {
                    "docker": self._command_exists("docker"),
                    "ip": self._command_exists("ip"),
                    "modprobe": self._command_exists("modprobe"),
                    "sysctl": self._command_exists("sysctl"),
                },
                "modules": modules,
                "sysctls": {
                    "net.mpls.platform_labels": host_sysctl,
                },
                "iproute2": {
                    "ip_f_mpls_route_show": {
                        "command": host_route_show.get("command"),
                        "returncode": host_route_show.get("returncode"),
                        "ok": host_route_show_ok,
                        "output": self._probe_text(host_route_show),
                    },
                    "supports_mpls_family": host_supports_mpls_family,
                    "supports_encap_mpls": host_supports_encap_mpls,
                    "route_help_command": host_route_help.get("command"),
                },
                "docker": docker_info,
            },
            "runtime_nodes": runtime_nodes,
            "assessment": {
                "running_node_checks_completed": bool(running_node_reports),
                "running_nodes_ready": all(
                    report.get("ready", False) for report in running_node_reports
                )
                if running_node_reports
                else None,
                "ready_for_mpls1": not summary_reasons,
                "blocking_reasons": summary_reasons,
                "recommended_next_steps": recommended_next_steps,
            },
        }

        if self.args.probe_report:
            write_json(self.args.probe_report, report)
        return report

    def print_mpls_probe(self, report: Dict[str, Any]) -> None:
        print("MPLS capability probe")
        host = report.get("host", {})
        modules = host.get("modules", {})
        for module_name in ("mpls_router", "mpls_iptunnel"):
            module = modules.get(module_name, {})
            print(
                "  "
                f"{module_name}: loaded={module.get('loaded')} "
                f"loadable={module.get('loadable')}"
            )

        host_sysctl = host.get("sysctls", {}).get("net.mpls.platform_labels", {})
        print(
            "  "
            "host sysctl net.mpls.platform_labels: "
            f"available={host_sysctl.get('available')} "
            f"value={host_sysctl.get('value')}"
        )

        iproute2 = host.get("iproute2", {})
        print(
            "  "
            "host iproute2: "
            f"mpls_family={iproute2.get('supports_mpls_family')} "
            f"encap_mpls={iproute2.get('supports_encap_mpls')} "
            f"route_show_ok={iproute2.get('ip_f_mpls_route_show', {}).get('ok')}"
        )

        docker_info = host.get("docker", {})
        print(
            "  "
            "docker: "
            f"available={docker_info.get('available')} "
            f"daemon_reachable={docker_info.get('daemon_reachable')}"
        )

        print("  runtime nodes:")
        for node_id, node_report in sorted(report.get("runtime_nodes", {}).items()):
            if not node_report.get("exists", False):
                status = "missing"
            elif not node_report.get("running", False):
                status = "stopped"
            elif node_report.get("ready", False):
                status = "ready"
            else:
                status = "blocked"
            blocking = ", ".join(node_report.get("blocking_reasons", [])) or "none"
            print(f"    {node_id}: {status} ({blocking})")

        assessment = report.get("assessment", {})
        print(
            "  "
            f"ready_for_mpls1={assessment.get('ready_for_mpls1')} "
            f"blocking_reasons={', '.join(assessment.get('blocking_reasons', [])) or 'none'}"
        )
        if self.args.probe_report:
            print(f"  probe_report: {self.args.probe_report}")

    def _docker_network_connected_nodes(self, docker_network_name: str) -> List[str]:
        if not self._docker_network_exists(docker_network_name):
            return []
        result = subprocess.run(
            ["docker", "network", "inspect", docker_network_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"inspect docker network failed: {docker_network_name}")
        payload = json.loads(result.stdout)
        containers = payload[0].get("Containers") or {}
        connected: List[str] = []
        for container in containers.values():
            name = container.get("Name")
            if name:
                connected.append(str(name))
        return sorted(set(connected))

    def _remove_docker_network_force(self, docker_network_name: str) -> None:
        if not self._docker_network_exists(docker_network_name):
            return
        for node_id in self._docker_network_connected_nodes(docker_network_name):
            self._run_command(
                ["docker", "network", "disconnect", "-f", docker_network_name, node_id],
                failure_stage=f"disconnect {node_id} from {docker_network_name}",
                allow_failure=True,
            )
        self._run_command(
            ["docker", "network", "rm", docker_network_name],
            failure_stage=f"remove docker network {docker_network_name}",
        )

    def _list_interfaces(self, node_id: str) -> set[str]:
        result = self._run_command(
            ["docker", "exec", node_id, "ip", "-o", "link", "show"],
            failure_stage=f"capture interfaces for {node_id}",
        )
        interface_names: set[str] = set()
        for line in result.stdout.splitlines():
            if ": " not in line:
                continue
            iface_with_peer = line.split(": ", 1)[1].split(":", 1)[0].strip()
            iface_name = iface_with_peer.split("@", 1)[0]
            if iface_name:
                interface_names.add(iface_name)
        return interface_names

    def _interface_exists(self, *, node_id: str, interface_name: str) -> bool:
        result = subprocess.run(
            ["docker", "exec", node_id, "ip", "link", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _delete_interface_if_present(self, *, node_id: str, interface_name: str) -> bool:
        if not self._interface_exists(node_id=node_id, interface_name=interface_name):
            return False
        self._run_command(
            ["docker", "exec", node_id, "ip", "link", "del", interface_name],
            failure_stage=f"delete stale interface {interface_name} on {node_id}",
        )
        if self._interface_exists(node_id=node_id, interface_name=interface_name):
            raise RuntimeError(
                f"stale interface cleanup failed: {interface_name} still exists on {node_id}"
            )
        return True

    def _connect_and_discover_new_interface(
        self,
        *,
        docker_network_name: str,
        node_id: str,
        requested_ip: Optional[str] = None,
    ) -> str:
        before = self._list_interfaces(node_id)
        connect_command = ["docker", "network", "connect"]
        if requested_ip is not None:
            connect_command.extend(["--ip", requested_ip])
        connect_command.extend([docker_network_name, node_id])
        self._run_command(
            connect_command,
            failure_stage=f"connect {node_id} to {docker_network_name}",
        )
        after = self._list_interfaces(node_id)
        new_ifaces = sorted(after - before)
        if len(new_ifaces) != 1:
            raise RuntimeError(
                f"discover new interface for {node_id}: expected exactly one new "
                f"interface, found {len(new_ifaces)} ({', '.join(new_ifaces) or 'none'})"
            )
        return new_ifaces[0]

    def _configure_runtime_interface(
        self,
        *,
        node_id: str,
        new_iface: str,
        target_iface_name: str,
        target_ip: str,
    ) -> None:
        self._run_command(
            ["docker", "exec", node_id, "ip", "link", "set", new_iface, "down"],
            failure_stage=f"set {new_iface} down on {node_id}",
        )
        self._run_command(
            [
                "docker",
                "exec",
                node_id,
                "ip",
                "link",
                "set",
                new_iface,
                "name",
                target_iface_name,
            ],
            failure_stage=f"rename {new_iface} to {target_iface_name} on {node_id}",
        )
        self._run_command(
            ["docker", "exec", node_id, "ip", "addr", "flush", "dev", target_iface_name],
            failure_stage=f"flush addresses on {target_iface_name} in {node_id}",
        )
        self._run_command(
            [
                "docker",
                "exec",
                node_id,
                "ip",
                "addr",
                "add",
                target_ip,
                "dev",
                target_iface_name,
            ],
            failure_stage=f"assign {target_ip} to {target_iface_name} in {node_id}",
        )
        self._run_command(
            ["docker", "exec", node_id, "ip", "link", "set", target_iface_name, "up"],
            failure_stage=f"bring {target_iface_name} up in {node_id}",
        )

    def _validate_interface_ip(
        self,
        *,
        node_id: str,
        interface_name: str,
        expected_ip: str,
    ) -> bool:
        result = subprocess.run(
            ["docker", "exec", node_id, "ip", "-o", "addr", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False
        return expected_ip in result.stdout

    def _ping(self, *, node_id: str, target_ip: str) -> None:
        self._run_command(
            ["docker", "exec", node_id, "ping", "-c", "3", target_ip],
            failure_stage=f"ping {target_ip} from {node_id}",
        )

    def _set_host_sysctl(self, *, key: str, value: int) -> None:
        self._run_command(
            ["sudo", "sysctl", "-w", f"{key}={value}"],
            failure_stage=f"set host sysctl {key}={value}",
        )

    def _prepare_bridge_netfilter_passthrough(self) -> None:
        print("Preparing host bridge netfilter passthrough for UT access runtime")
        for key in BRIDGE_NETFILTER_SYSCTLS:
            self._set_host_sysctl(key=key, value=0)

    def _require_bridge_netfilter_passthrough(self) -> None:
        blocking: List[str] = []
        for key in BRIDGE_NETFILTER_SYSCTLS:
            result = self._probe_sysctl(key)
            int_value = result.get("int_value")
            if int_value is None or int(int_value) != 0:
                blocking.append(f"{key}={result.get('value')}")
        if blocking:
            raise RuntimeError(
                "host bridge netfilter passthrough is not ready: "
                + ", ".join(blocking)
                + ". Rerun --init-slot-runtime or set the sysctls to 0."
            )

    def _slot_dynamic_candidates(self, slot: Dict[str, Any]) -> List[Dict[str, str]]:
        slot_type = str(slot.get("slot_type"))
        if slot_type in {"ut_access", "gw_access"}:
            dynamic_template = dict(slot.get("dynamic_endpoint_template", {}))
            return [
                {
                    "node_id": satellite_id,
                    "interface_name": str(dynamic_template.get("interface_name")),
                }
                for satellite_id in slot.get("candidate_satellite_ids", [])
            ]
        if slot_type == "cross_plane_isl":
            endpoint_a_template = dict(slot.get("endpoint_a_template", {}))
            endpoint_b_template = dict(slot.get("endpoint_b_template", {}))
            candidates: List[Dict[str, str]] = []
            candidates.extend(
                {
                    "node_id": satellite_id,
                    "interface_name": str(endpoint_a_template.get("interface_name")),
                }
                for satellite_id in slot.get("orbit_a_satellite_ids", [])
            )
            candidates.extend(
                {
                    "node_id": satellite_id,
                    "interface_name": str(endpoint_b_template.get("interface_name")),
                }
                for satellite_id in slot.get("orbit_b_satellite_ids", [])
            )
            return candidates
        return []

    def _cleanup_slot_runtime_artifacts(self, slot: Dict[str, Any]) -> None:
        docker_network_name = str(slot.get("docker_network_name"))
        self._remove_docker_network_force(docker_network_name)

        preconnected_endpoint = slot.get("preconnected_endpoint")
        if isinstance(preconnected_endpoint, dict):
            node_id = preconnected_endpoint.get("node_id")
            interface_name = preconnected_endpoint.get("interface_name")
            if node_id and interface_name:
                self._delete_interface_if_present(
                    node_id=str(node_id),
                    interface_name=str(interface_name),
                )

        for candidate in self._slot_dynamic_candidates(slot):
            self._delete_interface_if_present(
                node_id=str(candidate["node_id"]),
                interface_name=str(candidate["interface_name"]),
            )

    def _create_slot_network(self, slot: Dict[str, Any]) -> None:
        self._run_command(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                "--subnet",
                str(slot.get("subnet")),
                "--gateway",
                str(slot.get("bridge_gateway_ip")),
                str(slot.get("docker_network_name")),
            ],
            failure_stage=f"create slot network {slot.get('docker_network_name')}",
        )

    def _connect_and_configure_endpoint(
        self,
        *,
        docker_network_name: str,
        endpoint: Dict[str, Any],
    ) -> None:
        node_id = str(endpoint.get("node_id"))
        interface_name = str(endpoint.get("interface_name"))
        ip_address = str(endpoint.get("ip_address"))
        if self._interface_exists(node_id=node_id, interface_name=interface_name):
            raise RuntimeError(
                f"refusing to reuse uncertain interface {interface_name} on {node_id}"
            )
        requested_ip = ip_address.split("/", 1)[0]
        new_iface = self._connect_and_discover_new_interface(
            docker_network_name=docker_network_name,
            node_id=node_id,
            requested_ip=requested_ip,
        )
        if new_iface == interface_name:
            raise RuntimeError(
                f"pre-configure validation: unexpected interface-name collision "
                f"for target name {interface_name} on {node_id}"
            )
        self._configure_runtime_interface(
            node_id=node_id,
            new_iface=new_iface,
            target_iface_name=interface_name,
            target_ip=ip_address,
        )
        if not self._validate_interface_ip(
            node_id=node_id,
            interface_name=interface_name,
            expected_ip=ip_address,
        ):
            raise RuntimeError(
                f"interface validation failed for {interface_name} on {node_id}"
            )

    def _rollback_dynamic_endpoint(
        self,
        *,
        docker_network_name: str,
        endpoint: Dict[str, Any],
    ) -> None:
        node_id = str(endpoint.get("node_id"))
        interface_name = str(endpoint.get("interface_name"))
        self._run_command(
            ["docker", "network", "disconnect", "-f", docker_network_name, node_id],
            failure_stage=f"rollback disconnect {node_id} from {docker_network_name}",
            allow_failure=True,
        )
        self._delete_interface_if_present(
            node_id=node_id,
            interface_name=interface_name,
        )

    def _require_initialized_state(self) -> Dict[str, Any]:
        if not self.link_state.get("runtime_initialized", False):
            raise RuntimeError(
                "slot runtime is not initialized; run --init-slot-runtime first"
            )
        return self.link_state

    def _ensure_runtime_ready_for_scenario(self) -> Dict[str, Any]:
        if not self.link_state.get("runtime_initialized", False):
            return self.initialize_slot_runtime()
        self._prepare_bridge_netfilter_passthrough()
        return self.link_state

    def _scenario_definition(self, scenario_name: str) -> Dict[str, Any]:
        try:
            return SCENARIO_DEFINITIONS[scenario_name]
        except KeyError as exc:
            supported = ", ".join(sorted(SCENARIO_DEFINITIONS))
            raise ValueError(
                f"unsupported scenario: {scenario_name}. Supported scenarios: {supported}"
            ) from exc

    def _scenario_release_order(self, managed_slots: Dict[str, Any]) -> List[str]:
        priority = {"cross_plane_isl": 0, "gw_access": 1, "ut_access": 2}
        state_by_name = self._slot_state_by_name()
        return sorted(
            managed_slots,
            key=lambda slot_name: (
                priority.get(
                    str(state_by_name.get(slot_name, {}).get("slot_type")),
                    99,
                ),
                slot_name,
            ),
        )

    def _scenario_attach_order(self, managed_slots: Dict[str, Any]) -> List[str]:
        priority = {"attach_ut": 0, "attach_gw": 1, "connect_cross": 2}
        return sorted(
            [
                slot_name
                for slot_name, target in managed_slots.items()
                if target is not None
            ],
            key=lambda slot_name: (
                priority.get(str(managed_slots[slot_name].get("action")), 99),
                slot_name,
            ),
        )

    def _scenario_slot_matches(
        self,
        *,
        slot_entry: Dict[str, Any],
        target: Optional[Dict[str, Any]],
    ) -> bool:
        if target is None:
            return not slot_entry.get("occupied", False)
        return bool(slot_entry.get("occupied", False)) and slot_entry.get("binding") == target.get(
            "binding"
        )

    def _replace_slot_state_entry(
        self,
        *,
        state: Dict[str, Any],
        updated_entry: Dict[str, Any],
    ) -> Dict[str, Any]:
        new_slots = [
            updated_entry if entry.get("slot_name") == updated_entry.get("slot_name") else entry
            for entry in state.get("slots", [])
        ]
        new_state = dict(state)
        new_state["slots"] = new_slots
        return new_state

    def _validate_preconnected_slot_endpoint(self, slot_name: str) -> None:
        slot_inventory = self._slot_inventory_by_name().get(slot_name)
        if slot_inventory is None:
            raise ValueError(f"unknown slot inventory: {slot_name}")
        pre = slot_inventory.get("preconnected_endpoint")
        if not isinstance(pre, dict):
            return
        if not self._docker_network_exists(str(slot_inventory.get("docker_network_name"))):
            raise RuntimeError(
                f"slot bridge missing for {slot_name}; rerun --init-slot-runtime"
            )
        if not self._validate_interface_ip(
            node_id=str(pre.get("node_id")),
            interface_name=str(pre.get("interface_name")),
            expected_ip=str(pre.get("ip_address")),
        ):
            raise RuntimeError(
                f"preconnected endpoint validation failed for {slot_name}; rerun --init-slot-runtime"
            )

    def _select_owner_slot_for_attach(
        self,
        *,
        state: Dict[str, Any],
        owner_slot_type: str,
        owner_node_id: str,
        requested_slot_name: Optional[str],
    ) -> Dict[str, Any]:
        slot_states = [
            entry
            for entry in state.get("slots", [])
            if entry.get("slot_type") == owner_slot_type
            and entry.get("owner_node_id") == owner_node_id
        ]
        occupied_slots = [entry for entry in slot_states if entry.get("occupied", False)]
        if occupied_slots:
            raise RuntimeError(
                f"{owner_node_id} already occupies {occupied_slots[0].get('slot_name')}; release it first"
            )

        if requested_slot_name is not None:
            slot_state = self._require_slot_type(
                state=state,
                slot_name=requested_slot_name,
                expected_slot_type=owner_slot_type,
            )
            if slot_state.get("owner_node_id") != owner_node_id:
                raise RuntimeError(
                    f"{requested_slot_name} is not owned by {owner_node_id}"
                )
            if slot_state.get("occupied", False):
                raise RuntimeError(f"{requested_slot_name} is already occupied")
            return slot_state

        free_slots = [
            entry
            for entry in sorted(slot_states, key=lambda item: str(item.get("slot_name")))
            if not entry.get("occupied", False)
        ]
        if not free_slots:
            raise RuntimeError(f"no free slot for {owner_node_id}")
        return free_slots[0]

    def _select_owner_slot_for_release(
        self,
        *,
        state: Dict[str, Any],
        owner_slot_type: str,
        owner_node_id: str,
        requested_slot_name: Optional[str],
    ) -> Dict[str, Any]:
        slot_states = [
            entry
            for entry in state.get("slots", [])
            if entry.get("slot_type") == owner_slot_type
            and entry.get("owner_node_id") == owner_node_id
        ]

        if requested_slot_name is not None:
            slot_state = self._require_slot_type(
                state=state,
                slot_name=requested_slot_name,
                expected_slot_type=owner_slot_type,
            )
            if slot_state.get("owner_node_id") != owner_node_id:
                raise RuntimeError(
                    f"{requested_slot_name} is not owned by {owner_node_id}"
                )
            if not slot_state.get("occupied", False):
                raise RuntimeError(f"{requested_slot_name} is not occupied")
            return slot_state

        occupied_slots = [
            entry for entry in slot_states if entry.get("occupied", False)
        ]
        if not occupied_slots:
            raise RuntimeError(f"{owner_node_id} has no occupied slot")
        return occupied_slots[0]

    def _select_cross_plane_slot_for_connect(
        self,
        *,
        state: Dict[str, Any],
        requested_slot_name: Optional[str],
    ) -> Dict[str, Any]:
        if requested_slot_name is not None:
            slot_state = self._require_slot_type(
                state=state,
                slot_name=requested_slot_name,
                expected_slot_type="cross_plane_isl",
            )
            if slot_state.get("occupied", False):
                raise RuntimeError(f"{requested_slot_name} is already occupied")
            return slot_state

        free_slots = [
            entry
            for entry in sorted(
                [
                    slot
                    for slot in state.get("slots", [])
                    if slot.get("slot_type") == "cross_plane_isl"
                ],
                key=lambda item: str(item.get("slot_name")),
            )
            if not entry.get("occupied", False)
        ]
        if not free_slots:
            raise RuntimeError("no free cross-plane slot")
        return free_slots[0]

    def initialize_slot_runtime(self) -> Dict[str, Any]:
        print("Initializing slot runtime")
        self._prepare_bridge_netfilter_passthrough()
        slot_inventory = self._slot_inventory()

        for slot_group in (
            slot_inventory.get("ut_access", []),
            slot_inventory.get("gateway_access", []),
            slot_inventory.get("cross_plane_isl", []),
        ):
            for slot in slot_group:
                print(f"Rebuilding slot {slot.get('slot_name')}")
                self._cleanup_slot_runtime_artifacts(slot)
                self._create_slot_network(slot)
                preconnected_endpoint = slot.get("preconnected_endpoint")
                if isinstance(preconnected_endpoint, dict):
                    self._connect_and_configure_endpoint(
                        docker_network_name=str(slot.get("docker_network_name")),
                        endpoint=preconnected_endpoint,
                    )

        new_state = self._build_initial_link_state()
        self._save_link_state(new_state)
        return new_state

    def _parse_ut_attach_request(self, attach_request: str) -> Dict[str, str]:
        parts = attach_request.split(":", 1)
        if len(parts) != 2:
            raise ValueError("attach-ut expects <ut-id>:<satellite-id>")
        ut_id, satellite_id = parts
        if ut_id not in self._ut_ids():
            raise ValueError(f"unknown ut_id: {ut_id}")
        if satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {satellite_id}")
        return {"ut_id": ut_id, "satellite_id": satellite_id}

    def _parse_gw_attach_request(self, attach_request: str) -> Dict[str, str]:
        parts = attach_request.split(":", 1)
        if len(parts) != 2:
            raise ValueError("attach-gw expects <gateway-id>:<satellite-id>")
        gateway_id, satellite_id = parts
        if gateway_id not in self._gateway_ids():
            raise ValueError(f"unknown gateway_id: {gateway_id}")
        if satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {satellite_id}")
        return {"gateway_id": gateway_id, "satellite_id": satellite_id}

    def _parse_cross_request(self, request: str) -> Dict[str, str]:
        parts = request.split(":")
        if len(parts) != 4:
            raise ValueError(
                "connect-cross expects <sat-a>:<sat-b>:<dir-a>:<dir-b>"
            )
        first_satellite_id, second_satellite_id, first_direction, second_direction = parts
        if first_direction not in VALID_CROSS_DIRECTIONS:
            raise ValueError(f"invalid direction: {first_direction}")
        if second_direction not in VALID_CROSS_DIRECTIONS:
            raise ValueError(f"invalid direction: {second_direction}")
        if first_satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {first_satellite_id}")
        if second_satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {second_satellite_id}")

        first_orbit = self._satellite_orbit(first_satellite_id)
        second_orbit = self._satellite_orbit(second_satellite_id)
        if first_orbit == second_orbit:
            raise ValueError("cross-plane link requires satellites from different orbits")

        if first_orbit == "orbit-a":
            return {
                "satellite_a_id": first_satellite_id,
                "satellite_b_id": second_satellite_id,
                "satellite_a_direction": first_direction,
                "satellite_b_direction": second_direction,
            }
        return {
            "satellite_a_id": second_satellite_id,
            "satellite_b_id": first_satellite_id,
            "satellite_a_direction": second_direction,
            "satellite_b_direction": first_direction,
        }

    def attach_ut(
        self,
        attach_request: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        request = self._parse_ut_attach_request(attach_request)
        ut_id = request["ut_id"]
        satellite_id = request["satellite_id"]
        slot_state = self._select_owner_slot_for_attach(
            state=state,
            owner_slot_type="ut_access",
            owner_node_id=ut_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        self._validate_preconnected_slot_endpoint(slot_name)

        template = dict(slot_inventory.get("dynamic_endpoint_template", {}))
        dynamic_endpoint = {
            "node_id": satellite_id,
            "node_type": template.get("node_type"),
            "interface_name": template.get("interface_name"),
            "ip_address": template.get("ip_address"),
        }
        satellite_target_ip = str(template.get("ip_address")).split("/", 1)[0]
        try:
            self._connect_and_configure_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            self._ping(node_id=ut_id, target_ip=satellite_target_ip)
        except RuntimeError:
            self._rollback_dynamic_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            raise

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = [dynamic_endpoint]
        updated_entry["occupied"] = True
        updated_entry["binding"] = {
            "ut_id": ut_id,
            "satellite_id": satellite_id,
        }
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def detach_ut(
        self,
        ut_id: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        if ut_id not in self._ut_ids():
            raise ValueError(f"unknown ut_id: {ut_id}")
        slot_state = self._select_owner_slot_for_release(
            state=state,
            owner_slot_type="ut_access",
            owner_node_id=ut_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints", []))
        if len(dynamic_endpoints) != 1:
            raise RuntimeError(f"{slot_name} has unexpected dynamic endpoint count")
        dynamic_endpoint = dynamic_endpoints[0]

        self._run_command(
            [
                "docker",
                "network",
                "disconnect",
                "-f",
                str(slot_inventory.get("docker_network_name")),
                str(dynamic_endpoint.get("node_id")),
            ],
            failure_stage=f"disconnect {dynamic_endpoint.get('node_id')} from {slot_name}",
        )
        self._delete_interface_if_present(
            node_id=str(dynamic_endpoint.get("node_id")),
            interface_name=str(dynamic_endpoint.get("interface_name")),
        )

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = []
        updated_entry["occupied"] = False
        updated_entry["binding"] = None
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def attach_gateway(
        self,
        attach_request: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        request = self._parse_gw_attach_request(attach_request)
        gateway_id = request["gateway_id"]
        satellite_id = request["satellite_id"]
        slot_state = self._select_owner_slot_for_attach(
            state=state,
            owner_slot_type="gw_access",
            owner_node_id=gateway_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        self._validate_preconnected_slot_endpoint(slot_name)

        template = dict(slot_inventory.get("dynamic_endpoint_template", {}))
        dynamic_endpoint = {
            "node_id": satellite_id,
            "node_type": template.get("node_type"),
            "interface_name": template.get("interface_name"),
            "ip_address": template.get("ip_address"),
        }
        satellite_target_ip = str(template.get("ip_address")).split("/", 1)[0]
        try:
            self._connect_and_configure_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            self._ping(node_id=gateway_id, target_ip=satellite_target_ip)
        except RuntimeError:
            self._rollback_dynamic_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            raise

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = [dynamic_endpoint]
        updated_entry["occupied"] = True
        updated_entry["binding"] = {
            "gateway_id": gateway_id,
            "satellite_id": satellite_id,
        }
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def detach_gateway(
        self,
        gateway_id: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        if gateway_id not in self._gateway_ids():
            raise ValueError(f"unknown gateway_id: {gateway_id}")
        slot_state = self._select_owner_slot_for_release(
            state=state,
            owner_slot_type="gw_access",
            owner_node_id=gateway_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints", []))
        if len(dynamic_endpoints) != 1:
            raise RuntimeError(f"{slot_name} has unexpected dynamic endpoint count")
        dynamic_endpoint = dynamic_endpoints[0]

        self._run_command(
            [
                "docker",
                "network",
                "disconnect",
                "-f",
                str(slot_inventory.get("docker_network_name")),
                str(dynamic_endpoint.get("node_id")),
            ],
            failure_stage=f"disconnect {dynamic_endpoint.get('node_id')} from {slot_name}",
        )
        self._delete_interface_if_present(
            node_id=str(dynamic_endpoint.get("node_id")),
            interface_name=str(dynamic_endpoint.get("interface_name")),
        )

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = []
        updated_entry["occupied"] = False
        updated_entry["binding"] = None
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def connect_cross_plane(
        self,
        request_text: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        request = self._parse_cross_request(request_text)
        satellite_a_id = request["satellite_a_id"]
        satellite_b_id = request["satellite_b_id"]
        satellite_a_direction = request["satellite_a_direction"]
        satellite_b_direction = request["satellite_b_direction"]

        for entry in state.get("slots", []):
            if entry.get("slot_type") != "cross_plane_isl":
                continue
            if not entry.get("occupied", False):
                continue
            binding = entry.get("binding") or {}
            if satellite_a_id in {binding.get("satellite_a_id"), binding.get("satellite_b_id")}:
                raise RuntimeError(f"{satellite_a_id} already uses cross-plane slot {entry.get('slot_name')}")
            if satellite_b_id in {binding.get("satellite_a_id"), binding.get("satellite_b_id")}:
                raise RuntimeError(f"{satellite_b_id} already uses cross-plane slot {entry.get('slot_name')}")

        slot_state = self._select_cross_plane_slot_for_connect(
            state=state,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        docker_network_name = str(slot_inventory.get("docker_network_name"))
        if not self._docker_network_exists(docker_network_name):
            raise RuntimeError(
                f"slot bridge missing for {slot_name}; rerun --init-slot-runtime"
            )

        endpoint_a_template = dict(slot_inventory.get("endpoint_a_template", {}))
        endpoint_b_template = dict(slot_inventory.get("endpoint_b_template", {}))
        endpoint_a = {
            "node_id": satellite_a_id,
            "node_type": endpoint_a_template.get("node_type"),
            "interface_name": endpoint_a_template.get("interface_name"),
            "ip_address": endpoint_a_template.get("ip_address"),
            "logical_direction": satellite_a_direction,
        }
        endpoint_b = {
            "node_id": satellite_b_id,
            "node_type": endpoint_b_template.get("node_type"),
            "interface_name": endpoint_b_template.get("interface_name"),
            "ip_address": endpoint_b_template.get("ip_address"),
            "logical_direction": satellite_b_direction,
        }
        satellite_b_target_ip = str(endpoint_b_template.get("ip_address")).split("/", 1)[0]

        endpoint_a_connected = False
        endpoint_b_connected = False
        try:
            self._connect_and_configure_endpoint(
                docker_network_name=docker_network_name,
                endpoint=endpoint_a,
            )
            endpoint_a_connected = True
            self._connect_and_configure_endpoint(
                docker_network_name=docker_network_name,
                endpoint=endpoint_b,
            )
            endpoint_b_connected = True
            self._ping(node_id=satellite_a_id, target_ip=satellite_b_target_ip)
        except RuntimeError:
            if endpoint_b_connected:
                self._rollback_dynamic_endpoint(
                    docker_network_name=docker_network_name,
                    endpoint=endpoint_b,
                )
            if endpoint_a_connected:
                self._rollback_dynamic_endpoint(
                    docker_network_name=docker_network_name,
                    endpoint=endpoint_a,
                )
            raise

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = [endpoint_a, endpoint_b]
        updated_entry["occupied"] = True
        updated_entry["binding"] = {
            "satellite_a_id": satellite_a_id,
            "satellite_b_id": satellite_b_id,
            "satellite_a_direction": satellite_a_direction,
            "satellite_b_direction": satellite_b_direction,
        }
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def release_slot(self, slot_name: str) -> Dict[str, Any]:
        state = self._require_initialized_state()
        slot_states = self._slot_state_by_name(state)
        slot_inventory = self._slot_inventory_by_name()

        slot_state = slot_states.get(slot_name)
        if slot_state is None:
            raise ValueError(f"unknown slot name: {slot_name}")
        if slot_state.get("slot_type") == "fixed_isl":
            raise RuntimeError(f"{slot_name} is fixed_isl and cannot be released")
        if not slot_state.get("occupied", False):
            return slot_state

        slot_inventory_entry = slot_inventory.get(slot_name)
        if slot_inventory_entry is None:
            raise ValueError(f"missing slot inventory for {slot_name}")
        docker_network_name = str(slot_inventory_entry.get("docker_network_name"))
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints", []))
        errors: List[str] = []

        for endpoint in dynamic_endpoints:
            node_id = str(endpoint.get("node_id"))
            interface_name = str(endpoint.get("interface_name"))
            result = self._run_command(
                ["docker", "network", "disconnect", "-f", docker_network_name, node_id],
                failure_stage=f"disconnect {node_id} from {slot_name}",
                allow_failure=True,
            )
            if result.returncode != 0:
                errors.append(f"disconnect_failed:{node_id}")
                continue
            try:
                self._delete_interface_if_present(
                    node_id=node_id,
                    interface_name=interface_name,
                )
            except RuntimeError as error:
                errors.append(str(error))

        if errors:
            raise RuntimeError(
                f"slot release incomplete for {slot_name}; state not updated: {'; '.join(errors)}"
            )

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = []
        updated_entry["occupied"] = False
        updated_entry["binding"] = None
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def _resolve_static_mpls_profile(self, profile_id: str):
        try:
            return get_static_mpls_profile(profile_id)
        except ValueError as error:
            raise ValueError(str(error)) from error

    def _docker_exec(
        self,
        node_id: str,
        command: List[str],
        *,
        failure_stage: str,
        allow_failure: bool = False,
        observation: Optional[Dict[str, Any]] = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run_command(
            ["docker", "exec", node_id, *command],
            failure_stage=failure_stage,
            allow_failure=allow_failure,
            observation=observation,
        )

    def _spawn_docker_exec_capture(
        self,
        node_id: str,
        command: List[str],
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            ["docker", "exec", node_id, *command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def _print_ut_egress_spec_plan(
        self,
        *,
        action_name: str,
        specs: Sequence[UtEgressCommandSpec],
    ) -> None:
        category_counts = Counter(spec.category for spec in specs)
        node_counts = Counter(spec.node_id for spec in specs)
        logical_kind_counts = Counter(self._ut_egress_logical_kind(spec) for spec in specs)
        per_node_update_plan = self._ut_egress_per_node_update_plan(
            action_name=action_name,
            specs=specs,
        )
        batch_plan = list(per_node_update_plan["node_plans"])
        print(f"UT egress plan: {action_name}")
        print(f"  dry_run={self.args.dry_run}")
        print(f"  total_commands={len(specs)}")
        print(
            "  category_counts="
            + json.dumps(dict(sorted(category_counts.items())), sort_keys=True)
        )
        print(
            "  node_counts="
            + json.dumps(dict(sorted(node_counts.items())), sort_keys=True)
        )
        print(
            "  logical_kind_counts="
            + json.dumps(dict(sorted(logical_kind_counts.items())), sort_keys=True)
        )
        print(
            "  node_batch_counts="
            + json.dumps(
                {
                    entry["node_id"]: entry["command_count"]
                    for entry in batch_plan
                },
                sort_keys=True,
            )
        )
        print(
            "  execution_boundary="
            + json.dumps(per_node_update_plan["control_boundary"], sort_keys=True)
        )
        print(
            "  payload_header="
            + json.dumps(
                {
                    "schema_version": per_node_update_plan["schema_version"],
                    "protocol_version": per_node_update_plan["protocol_version"],
                    "plan_id": per_node_update_plan["plan_id"],
                    "scope": per_node_update_plan["scope"],
                    "generation": per_node_update_plan["generation"],
                    "sequence": per_node_update_plan["sequence"],
                    "phase": per_node_update_plan["phase"],
                    "scenario": per_node_update_plan["scenario"],
                    "created_at": per_node_update_plan["created_at"],
                    "replayable": per_node_update_plan["replayable"],
                    "idempotency_scope": per_node_update_plan["idempotency_scope"],
                    "transaction_boundary": per_node_update_plan["transaction_boundary"],
                    "distribution_source": per_node_update_plan["distribution_source"],
                    "expected_transport": per_node_update_plan["expected_transport"],
                },
                sort_keys=True,
            )
        )
        if not self.args.dry_run:
            return
        for batch_index, batch in enumerate(batch_plan, start=1):
            print(
                "  "
                f"node_plan[{batch_index:02d}] node={batch['node_id']} commands={batch['command_count']}"
            )
            print(f"    node_plan_id={batch['node_plan_id']}")
            print(f"    generation={batch['generation']}")
            print(f"    sequence={batch['sequence']}")
            print(f"    created_at={batch['created_at']}")
            print(
                "    categories="
                + json.dumps(batch["category_counts"], sort_keys=True)
            )
            print(
                "    logical_kinds="
                + json.dumps(batch["logical_kind_counts"], sort_keys=True)
            )
            print(
                "    target_node_type="
                + json.dumps(batch["target_node_type"], sort_keys=True)
            )
            print(
                "    distribution="
                + json.dumps(batch["distribution"], sort_keys=True)
            )
            print(
                "    expected_transport="
                + json.dumps(batch["expected_transport"], sort_keys=True)
            )
            print(f"    apply_mode={batch['apply_mode']}")
            print(f"    expected_ack_mode={batch['expected_ack_mode']}")
            print(
                "    replay_policy="
                + json.dumps(batch["replay_policy"], sort_keys=True)
            )
            print(
                "    transaction_semantics="
                + json.dumps(batch["transaction_semantics"], sort_keys=True)
            )
            print(
                "    node_metadata_summary="
                + json.dumps(
                    self._summarize_node_metadata(batch["node_metadata"]),
                    sort_keys=True,
                )
            )
            print(
                "    response_envelope_summary="
                + json.dumps(
                    self._summarize_response_envelope(batch["response_envelope"]),
                    sort_keys=True,
                )
            )
            print(
                "    replay_info="
                + json.dumps(
                    batch["response_envelope"]["replay_info"],
                    sort_keys=True,
                )
            )
            print(
                "    error_contract_summary="
                + json.dumps(
                    batch["response_envelope"]["error"],
                    sort_keys=True,
                )
            )
            print(
                "    validation_report_summary="
                + json.dumps(
                    batch["response_envelope"]["validation_report"],
                    sort_keys=True,
                )
            )
            for command_entry in batch["commands"]:
                print(
                    "    "
                    f"[{command_entry['index']:02d}] category={command_entry['category']} "
                    f"kind={command_entry['logical_kind']} stage={command_entry['stage']}"
                )
                print("      " + command_entry["command"])
                if command_entry["shared_key"]:
                    print(f"      shared_key={command_entry['shared_key']}")
                print(f"      idempotency_key={command_entry['idempotency_key']}")
                if command_entry["expected_state"]:
                    print(f"      expected_state={command_entry['expected_state']}")
        for index, spec in enumerate(specs, start=1):
            print(
                "  "
                f"[{index:02d}] node={spec.node_id} category={spec.category} "
                f"stage={spec.stage}"
            )
            print("    " + shlex.join(list(spec.command)))
            if spec.shared_key:
                print(f"    shared_key={spec.shared_key}")
            if spec.expected_state:
                print(f"    expected_state={spec.expected_state}")

    def _ut_egress_logical_kind(self, spec: UtEgressCommandSpec) -> str:
        stage = spec.stage.lower()
        rendered = " ".join(spec.command)
        if spec.category == "static_guardrail":
            if "source-policy rule" in stage:
                return "source_policy_rule"
            if "normalize internal guardrail routes" in stage:
                return "guardrail_normalize"
            return "guardrail_route"
        if spec.category == "dynamic_lfib":
            if spec.command[:2] == ("sysctl", "-w"):
                return "mpls_input"
            if "reverse lfib" in stage:
                return "reverse_lfib"
            if "forward lfib" in stage:
                return "forward_lfib"
            if "reverse pop" in stage:
                return "reverse_pop"
            if "forward pop" in stage:
                return "forward_pop"
            return "mpls_lfib"
        if spec.category == "dynamic_path":
            if "ut default egress route" in stage:
                return "ut_default_route"
            if "source-policy" in stage or "table" in rendered:
                return "policy_table_default_route"
            if "delivery route" in stage:
                return "ut_delivery_route"
            return "dynamic_route"
        if spec.category == "dynamic_gateway_nat":
            if "snat" in stage:
                return "gateway_snat"
            if "reverse ingress route" in stage:
                return "reverse_ingress_route"
            if "reverse filter rule" in stage:
                return "gateway_reverse_filter"
            if "forward filter rule" in stage:
                return "gateway_forward_filter"
            return "gateway_nat"
        if spec.category == "verify":
            if spec.command[:4] == ("ip", "-f", "mpls", "route"):
                return "lfib_show"
            if spec.command[:2] == ("ip", "route"):
                return "route_show"
            if spec.command[:2] == ("ip", "rule"):
                return "rule_show"
            if spec.command[:2] == ("sh", "-lc") and "iptables" in rendered:
                return "iptables_show"
            return "verify"
        if spec.category == "cleanup":
            if spec.command[:2] == ("sysctl", "-w"):
                return "mpls_input_disable"
            if spec.command[:4] == ("ip", "-f", "mpls", "route"):
                if "reverse" in stage:
                    return "reverse_lfib_cleanup"
                if "forward" in stage:
                    return "forward_lfib_cleanup"
                return "lfib_cleanup"
            if spec.command[:2] == ("ip", "route"):
                if "delivery route" in stage:
                    return "ut_delivery_route_cleanup"
                if "reverse ingress route" in stage:
                    return "reverse_ingress_route_cleanup"
                if "source-policy" in stage:
                    return "policy_table_default_route_cleanup"
                if "ut default egress route" in stage:
                    return "ut_default_route_cleanup"
                return "route_cleanup"
            if spec.command[:2] == ("sh", "-lc"):
                if "SNAT" in rendered:
                    return "gateway_snat_cleanup"
                if "ESTABLISHED,RELATED" in rendered:
                    return "gateway_reverse_filter_cleanup"
                if "FORWARD" in rendered:
                    return "gateway_forward_filter_cleanup"
            return "cleanup"
        return spec.category

    def _ut_egress_action_context(self, action_name: str) -> Dict[str, Any]:
        phase = action_name.split(":", 1)[0]
        return {
            "plan_id": action_name,
            "generation": 1,
            "sequence": 1,
            "phase": phase,
            "scenario": None,
            "scope": "ut_egress_runtime_update",
            "created_at": self._utc_timestamp(),
            "replayable": True,
            "idempotency_scope": "node_plan_command_set",
        }

    def _runtime_node_type(self, node_id: str) -> str:
        if node_id in self._satellite_ids():
            return "satellite"
        if node_id in self._gateway_ids():
            return "gateway"
        if node_id in self._ut_ids():
            return "user_terminal"
        return "runtime_node"

    def _slot_interface_role(self, *, node_type: str, slot_type: str) -> str:
        if slot_type == "ut_access":
            return "ut_facing" if node_type == "satellite" else "satellite_access"
        if slot_type == "gw_access":
            return "gateway_facing" if node_type == "satellite" else "satellite_facing"
        if slot_type == "cross_plane_isl":
            return "isl_cross_plane"
        if slot_type == "fixed_isl":
            return "isl_fixed"
        return "runtime_slot_endpoint"

    def _controller_visible_ut_attachment(self, ut_id: str) -> Dict[str, Any]:
        for slot in self.link_state.get("slots", []):
            if slot.get("slot_type") != "ut_access":
                continue
            if slot.get("owner_node_id") != ut_id:
                continue
            binding = slot.get("binding") or {}
            if slot.get("occupied") and binding.get("satellite_id") is not None:
                return {
                    "serving_satellite": str(binding.get("satellite_id")),
                    "attachment_state": "attached",
                    "slot_name": slot.get("slot_name"),
                }
        return {
            "serving_satellite": None,
            "attachment_state": "detached",
            "slot_name": None,
        }

    def _controller_visible_interface_roles(self, node_id: str) -> List[Dict[str, Any]]:
        node_type = self._runtime_node_type(node_id)
        roles: List[Dict[str, Any]] = []

        def add_role(role: Dict[str, Any]) -> None:
            roles.append(role)

        if node_type == "satellite":
            entry = self._satellite_topology_entry(node_id)
            bootstrap_endpoint = entry.get("bootstrap_network", {}).get("endpoint", {})
            if bootstrap_endpoint.get("interface_name"):
                add_role(
                    {
                        "interface_name": bootstrap_endpoint.get("interface_name"),
                        "role": "compile_time_bootstrap",
                        "source": "topology.bootstrap_network",
                    }
                )
            for link in self._fixed_links():
                endpoints = list(link.get("endpoints", []))
                for endpoint in endpoints:
                    if endpoint.get("node_id") != node_id:
                        continue
                    peer = next(
                        (
                            candidate
                            for candidate in endpoints
                            if candidate.get("node_id") != node_id
                        ),
                        None,
                    )
                    add_role(
                        {
                            "interface_name": endpoint.get("interface_name"),
                            "role": "isl_fixed",
                            "source": "topology.fixed_links",
                            "link_id": link.get("link_id"),
                            "logical_direction": endpoint.get("logical_direction"),
                            "peer_node_id": None if peer is None else peer.get("node_id"),
                            "peer_node_type": None if peer is None else peer.get("node_type"),
                        }
                    )
        elif node_type == "gateway":
            entry = self._gateway_topology_entry(node_id)
            bootstrap_endpoint = entry.get("bootstrap_network", {}).get("endpoint", {})
            if bootstrap_endpoint.get("interface_name"):
                add_role(
                    {
                        "interface_name": bootstrap_endpoint.get("interface_name"),
                        "role": "compile_time_bootstrap",
                        "source": "topology.bootstrap_network",
                    }
                )
            uplink = entry.get("internet_uplink", {})
            if uplink.get("network_name"):
                add_role(
                    {
                        "interface_name": uplink.get("network_name"),
                        "role": "mini_internet_egress",
                        "source": "topology.internet_uplink",
                        "ix_id": uplink.get("ix_id"),
                    }
                )
        elif node_type == "user_terminal":
            entry = self._ut_topology_entry(node_id)
            identity_interface = entry.get("identity_interface", {})
            if identity_interface.get("if_name"):
                add_role(
                    {
                        "interface_name": identity_interface.get("if_name"),
                        "role": "stable_identity",
                        "source": "topology.identity_interface",
                    }
                )
            bootstrap_endpoint = entry.get("bootstrap_network", {}).get("endpoint", {})
            if bootstrap_endpoint.get("interface_name"):
                add_role(
                    {
                        "interface_name": bootstrap_endpoint.get("interface_name"),
                        "role": "compile_time_bootstrap",
                        "source": "topology.bootstrap_network",
                    }
                )

        for slot in self.link_state.get("slots", []):
            endpoints = list(slot.get("preconnected_endpoints", [])) + list(
                slot.get("dynamic_endpoints", [])
            )
            for endpoint in endpoints:
                if endpoint.get("node_id") != node_id:
                    continue
                peer = next(
                    (
                        candidate
                        for candidate in endpoints
                        if candidate.get("node_id") != node_id
                    ),
                    None,
                )
                add_role(
                    {
                        "interface_name": endpoint.get("interface_name"),
                        "role": self._slot_interface_role(
                            node_type=node_type,
                            slot_type=str(slot.get("slot_type")),
                        ),
                        "source": (
                            "link_state.slot_binding"
                            if slot.get("occupied")
                            else "link_state.slot_inventory"
                        ),
                        "slot_name": slot.get("slot_name"),
                        "slot_type": slot.get("slot_type"),
                        "occupied": bool(slot.get("occupied")),
                        "logical_direction": endpoint.get("logical_direction"),
                        "peer_node_id": None if peer is None else peer.get("node_id"),
                        "peer_node_type": None if peer is None else peer.get("node_type"),
                    }
                )

        return sorted(
            roles,
            key=lambda entry: (
                str(entry.get("interface_name") or ""),
                str(entry.get("role") or ""),
                str(entry.get("slot_name") or ""),
                str(entry.get("source") or ""),
            ),
        )

    def _runtime_node_metadata(self, node_id: str) -> Dict[str, Any]:
        node_type = self._runtime_node_type(node_id)
        metadata: Dict[str, Any] = {
            "schema_version": RUNTIME_NODE_METADATA_SCHEMA_VERSION,
            "node_id": node_id,
            "node_type": node_type,
            "interface_roles": self._controller_visible_interface_roles(node_id),
            "local_agent_capabilities": {
                "current": {
                    "available": False,
                    "endpoint": None,
                },
                "future": {
                    "expected_endpoint": "node_local_forwarding_agent",
                    "accepted_transports": [
                        "in_emulation_control_plane_delivery",
                    ],
                    "ack_modes": [
                        "terminal_node_ack",
                    ],
                },
            },
            "control_plane_endpoint": {
                "current": None,
                "future": "node_local_forwarding_agent",
            },
            "metadata_ownership": {
                "controller_authoritative": [
                    "node_type",
                    "runtime_forwarding_role",
                    "interface_roles",
                    "forwarding_capabilities",
                    "control_plane_endpoint",
                ],
                "node_authoritative": [
                    "local_interface_health",
                    "neighbor_reachability",
                    "local_runtime_state",
                    "local_forwarding_state",
                    "local_agent_health",
                ],
            },
        }
        if node_type == "satellite":
            metadata.update(
                {
                    "runtime_forwarding_role": "satellite_mpls_forwarder",
                    "forwarding_capabilities": {
                        "supports_kernel_routes": True,
                        "supports_mpls": True,
                        "supports_lfib": True,
                        "supports_iptables": False,
                        "supports_cgnat": False,
                        "supports_bgp": False,
                    },
                    "supports_mpls": True,
                    "supports_runtime_attach": True,
                }
            )
            return metadata
        if node_type == "gateway":
            gateway_entry = self._gateway_topology_entry(node_id)
            uplink = gateway_entry.get("internet_uplink", {})
            metadata.update(
                {
                    "runtime_forwarding_role": "gateway_cgnat_egress",
                    "forwarding_capabilities": {
                        "supports_kernel_routes": True,
                        "supports_mpls": True,
                        "supports_lfib": True,
                        "supports_iptables": True,
                        "supports_cgnat": True,
                        "supports_bgp": True,
                    },
                    "supports_mpls": True,
                    "supports_runtime_attach": True,
                    "supports_cgnat": True,
                    "supports_egress": True,
                    "supports_bgp": True,
                    "gateway_region": None,
                    "gateway_cluster": uplink.get("network_name"),
                    "connected_ground_control": None,
                }
            )
            return metadata
        if node_type == "user_terminal":
            attachment = self._controller_visible_ut_attachment(node_id)
            metadata.update(
                {
                    "runtime_forwarding_role": "user_terminal_edge",
                    "forwarding_capabilities": {
                        "supports_kernel_routes": True,
                        "supports_mpls": False,
                        "supports_lfib": False,
                        "supports_iptables": False,
                        "supports_cgnat": False,
                        "supports_bgp": False,
                    },
                    "supports_mpls": False,
                    "supports_runtime_attach": True,
                    "serving_satellite": attachment["serving_satellite"],
                    "attachment_state": attachment["attachment_state"],
                    "attachment_slot_name": attachment["slot_name"],
                    "mobility_runtime_attach_capable": True,
                }
            )
            return metadata
        metadata.update(
            {
                "runtime_forwarding_role": "runtime_node",
                "forwarding_capabilities": {},
                "supports_mpls": False,
                "supports_runtime_attach": False,
            }
        )
        return metadata

    def _runtime_update_replay_policy(self, *, idempotency_scope: str) -> Dict[str, Any]:
        return {
            "replayable": True,
            "retry_unit": "entire_node_plan",
            "controller_retry_allowed": True,
            "replay_detection": "stateless_best_effort_only",
            "same_node_plan_id_behavior_current": (
                "protocol_reserved_no_persistent_replay_store"
            ),
            "same_node_plan_id_behavior_future": (
                "detect_replay_and_ack_already_applied_or_replay_conflict"
            ),
            "idempotency_scope": idempotency_scope,
            "idempotency_basis": [
                "command.idempotency_key",
                "command.replace_or_check_then_add_shape",
                "node_local_command_order",
            ],
        }

    def _runtime_update_transaction_semantics(self) -> Dict[str, Any]:
        return {
            "atomicity": "non_atomic_partial_apply_possible",
            "ordering": "serial_in_order",
            "rollback_on_failure": "none",
            "failure_boundary": "stop_on_first_non_allowed_failure",
            "replayable": True,
            "idempotent": "best_effort_command_level",
        }

    def _initial_runtime_agent_health(self) -> Dict[str, Any]:
        return {
            "status": "controller_transport_only",
            "healthy": None,
            "source": "controller_serial_docker_exec",
            "message": "local agent not implemented in current transport",
        }

    def _initial_runtime_ack(self, *, expected_ack_mode: str) -> Dict[str, Any]:
        return {
            "ack_status": "pending",
            "expected_ack_mode": expected_ack_mode,
            "supported_ack_statuses": self._runtime_supported_ack_statuses(),
        }

    def _initial_runtime_apply_result(self) -> Dict[str, Any]:
        return {
            "apply_status": "not_started",
            "supported_apply_statuses": self._runtime_supported_apply_statuses(),
            "applied_commands": [],
            "skipped_commands": [],
            "failed_command": None,
            "stdout_summary": None,
            "stderr_summary": None,
            "transport_returncode": None,
        }

    def _initial_runtime_error_report(self) -> Dict[str, Any]:
        return {
            "error_code": "none",
            "error_category": "none",
            "error_message": None,
            "error_details": None,
            "failed_command_id": None,
            "failed_command_family": None,
            "failed_logical_kind": None,
            "retryable": False,
            "fatal": False,
            "suggested_action": None,
            "supported_error_codes": self._runtime_supported_error_codes(),
            "supported_error_categories": self._runtime_supported_error_categories(),
        }

    def _initial_runtime_replay_info(
        self,
        *,
        replay_policy: Dict[str, Any],
        idempotency_scope: str,
    ) -> Dict[str, Any]:
        return {
            "replay_detected": False,
            "already_applied": False,
            "replay_policy": dict(replay_policy),
            "idempotency_scope": idempotency_scope,
        }

    def _initial_runtime_validation_report(self) -> Dict[str, Any]:
        return {
            "validation_status": "not_run",
            "supported_validation_statuses": self._runtime_supported_validation_statuses(),
            "validation_errors": [],
            "unsupported_commands": [],
            "unsupported_command_families": [],
            "unsupported_protocol_version": None,
            "malformed_payload": False,
            "missing_required_fields": [],
            "current_validation_mode": "protocol_reserved_no_agent_validation_engine",
            "future_validation_boundary": "node_local_agent_pre_apply_validation",
            "reserved_error_codes": [
                "validation_failed",
                "unsupported_command",
                "unsupported_protocol_version",
                "malformed_payload",
            ],
            "reserved_failure_paths": [
                "payload_shape_validation",
                "protocol_version_validation",
                "command_capability_validation",
                "node_local_precondition_validation",
            ],
        }

    def _initial_runtime_update_response_envelope(
        self,
        *,
        node_plan_id: str,
        plan_id: str,
        generation: int,
        sequence: int,
        expected_ack_mode: str,
        replay_policy: Dict[str, Any],
        idempotency_scope: str,
    ) -> Dict[str, Any]:
        return {
            "schema_version": RUNTIME_RESPONSE_ENVELOPE_SCHEMA_VERSION,
            "protocol_version": self._runtime_protocol_version(),
            "agent_schema_version": self._runtime_agent_schema_version(),
            "supported_protocol_versions": [
                self._runtime_protocol_version(),
            ],
            "node_plan_id": node_plan_id,
            "plan_id": plan_id,
            "generation": generation,
            "sequence": sequence,
            "execution_nonce": None,
            "agent_health": self._initial_runtime_agent_health(),
            "ack": self._initial_runtime_ack(expected_ack_mode=expected_ack_mode),
            "apply_result": self._initial_runtime_apply_result(),
            "error": self._initial_runtime_error_report(),
            "timestamps": {
                "execution_started_at": None,
                "execution_finished_at": None,
            },
            "replay_info": self._initial_runtime_replay_info(
                replay_policy=replay_policy,
                idempotency_scope=idempotency_scope,
            ),
            "validation_report": self._initial_runtime_validation_report(),
        }

    def _summarize_stream_output(self, text: str) -> Optional[Dict[str, Any]]:
        cleaned = text.strip()
        if not cleaned:
            return None
        lines = cleaned.splitlines()
        return {
            "line_count": len(lines),
            "head": lines[:3],
            "tail": lines[-3:],
        }

    def _summarize_node_metadata(self, node_metadata: Dict[str, Any]) -> Dict[str, Any]:
        interface_roles = list(node_metadata.get("interface_roles", []))
        return {
            "schema_version": node_metadata.get("schema_version"),
            "node_id": node_metadata.get("node_id"),
            "node_type": node_metadata.get("node_type"),
            "runtime_forwarding_role": node_metadata.get("runtime_forwarding_role"),
            "supports_mpls": node_metadata.get("supports_mpls"),
            "supports_runtime_attach": node_metadata.get("supports_runtime_attach"),
            "control_plane_endpoint": node_metadata.get("control_plane_endpoint"),
            "interface_role_count": len(interface_roles),
            "interface_role_names": [
                entry.get("role")
                for entry in interface_roles
                if entry.get("role") is not None
            ],
            "forwarding_capabilities": node_metadata.get("forwarding_capabilities"),
            "metadata_ownership": node_metadata.get("metadata_ownership"),
        }

    def _summarize_response_envelope(self, response_envelope: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": response_envelope.get("schema_version"),
            "protocol_version": response_envelope.get("protocol_version"),
            "agent_schema_version": response_envelope.get("agent_schema_version"),
            "supported_protocol_versions": response_envelope.get("supported_protocol_versions"),
            "node_plan_id": response_envelope.get("node_plan_id"),
            "plan_id": response_envelope.get("plan_id"),
            "generation": response_envelope.get("generation"),
            "sequence": response_envelope.get("sequence"),
            "execution_nonce": response_envelope.get("execution_nonce"),
            "ack": response_envelope.get("ack"),
            "apply_result": {
                "apply_status": response_envelope.get("apply_result", {}).get("apply_status"),
                "supported_apply_statuses": response_envelope.get("apply_result", {}).get(
                    "supported_apply_statuses"
                ),
            },
            "error": {
                "error_code": response_envelope.get("error", {}).get("error_code"),
                "error_category": response_envelope.get("error", {}).get("error_category"),
                "supported_error_codes": response_envelope.get("error", {}).get(
                    "supported_error_codes"
                ),
                "supported_error_categories": response_envelope.get("error", {}).get(
                    "supported_error_categories"
                ),
            },
            "replay_info": response_envelope.get("replay_info"),
            "validation_report": {
                "validation_status": response_envelope.get("validation_report", {}).get(
                    "validation_status"
                ),
                "supported_validation_statuses": response_envelope.get(
                    "validation_report",
                    {},
                ).get("supported_validation_statuses"),
                "reserved_error_codes": response_envelope.get("validation_report", {}).get(
                    "reserved_error_codes"
                ),
            },
            "agent_health": response_envelope.get("agent_health"),
        }

    def _ut_egress_per_node_update_plan(
        self,
        *,
        action_name: str,
        specs: Sequence[UtEgressCommandSpec],
    ) -> Dict[str, Any]:
        action_context = self._ut_egress_action_context(action_name)
        batches: Dict[str, Dict[str, Any]] = {}
        node_order: List[str] = []
        for index, spec in enumerate(specs, start=1):
            batch = batches.get(spec.node_id)
            if batch is None:
                batch = {
                    "node_id": spec.node_id,
                    "node_plan_id": f"{action_context['plan_id']}:{spec.node_id}",
                    "command_count": 0,
                    "category_counts": Counter(),
                    "logical_kind_counts": Counter(),
                    "commands": [],
                }
                batches[spec.node_id] = batch
                node_order.append(spec.node_id)
            logical_kind = self._ut_egress_logical_kind(spec)
            batch["command_count"] += 1
            batch["category_counts"][spec.category] += 1
            batch["logical_kind_counts"][logical_kind] += 1
            batch["commands"].append(
                {
                    "index": index,
                    "sequence": index,
                    "category": spec.category,
                    "command_family": self._infer_command_family(spec.command),
                    "logical_kind": logical_kind,
                    "stage": spec.stage,
                    "allow_failure": spec.allow_failure,
                    "argv": list(spec.command),
                    "command": shlex.join(list(spec.command)),
                    "shared_key": spec.shared_key,
                    "idempotency_key": (
                        spec.shared_key
                        or f"{action_context['plan_id']}:{spec.node_id}:{index}"
                    ),
                    "expected_state": spec.expected_state,
                }
            )
        node_plans: List[Dict[str, Any]] = []
        for node_sequence, node_id in enumerate(node_order, start=1):
            batch = batches[node_id]
            replay_policy = self._runtime_update_replay_policy(
                idempotency_scope=str(action_context["idempotency_scope"])
            )
            node_plans.append(
                {
                    "node_id": node_id,
                    "node_plan_id": batch["node_plan_id"],
                    "plan_id": action_context["plan_id"],
                    "generation": action_context["generation"],
                    "sequence": node_sequence,
                    "phase": action_context["phase"],
                    "scenario": action_context["scenario"],
                    "target_node": node_id,
                    "target_node_type": self._runtime_node_type(node_id),
                    "created_at": action_context["created_at"],
                    "command_count": batch["command_count"],
                    "apply_mode": "ordered_command_list",
                    "replay_policy": replay_policy,
                    "expected_ack_mode": "terminal_node_ack",
                    "transaction_semantics": self._runtime_update_transaction_semantics(),
                    "execution_boundary": {
                        "global_decision_maker": "controller",
                        "local_execution_target": "node_local_forwarding_agent",
                        "execution_location": "in_container",
                        "node_plan_visibility": "node_local_only",
                    },
                    "expected_transport": {
                        "current": "controller_serial_docker_exec",
                        "future": "in_emulation_control_plane_delivery",
                    },
                    "distribution": {
                        "source": {
                            "current": "controller_process_on_host",
                            "future_candidates": [
                                "ground_control_host",
                                "data_center",
                            ],
                        },
                        "target": {
                            "node_id": node_id,
                            "node_local_endpoint": "node_local_forwarding_agent",
                            "execution_location": "in_container",
                        },
                    },
                    "node_metadata": self._runtime_node_metadata(node_id),
                    "category_counts": dict(sorted(batch["category_counts"].items())),
                    "logical_kind_counts": dict(sorted(batch["logical_kind_counts"].items())),
                    "commands": batch["commands"],
                    "response_envelope": self._initial_runtime_update_response_envelope(
                        node_plan_id=str(batch["node_plan_id"]),
                        plan_id=str(action_context["plan_id"]),
                        generation=int(action_context["generation"]),
                        sequence=node_sequence,
                        expected_ack_mode="terminal_node_ack",
                        replay_policy=replay_policy,
                        idempotency_scope=str(action_context["idempotency_scope"]),
                    ),
                }
            )
        return {
            "schema_version": RUNTIME_PAYLOAD_SCHEMA_VERSION,
            "protocol_version": self._runtime_protocol_version(),
            "plan_id": action_context["plan_id"],
            "action": action_name,
            "scope": action_context["scope"],
            "generation": action_context["generation"],
            "sequence": action_context["sequence"],
            "phase": action_context["phase"],
            "scenario": action_context["scenario"],
            "created_at": action_context["created_at"],
            "replayable": action_context["replayable"],
            "idempotency_scope": action_context["idempotency_scope"],
            "control_boundary": {
                "global_decision_maker": "controller",
                "global_inputs": [
                    "topology_snapshot",
                    "attachment_state",
                    "path_computation",
                    "gateway_selection",
                    "forwarding_plan_generation",
                    "scenario_level_consistency_decision",
                ],
                "local_execution_target": "node_local_forwarding_agent",
                "current_execution_mode": "controller_serial_docker_exec",
                "current_parallelism": "none",
                "node_plan_visibility": "node_local_only",
            },
            "transaction_boundary": {
                "unit": "node_plan",
                "atomicity": "non_atomic_partial_apply_possible",
                "rollback_on_failure": "none",
                "replay_unit": "entire_node_plan",
                "current_failure_behavior": "stop_on_first_non_allowed_failure",
            },
            "distribution_source": {
                "current": "controller_process_on_host",
                "future_candidates": [
                    "ground_control_host",
                    "data_center",
                ],
            },
            "expected_transport": {
                "current": "controller_serial_docker_exec",
                "future": "in_emulation_control_plane_delivery",
            },
            "node_plans": node_plans,
        }

    def _ut_egress_batchable_phase(self, phase: str) -> bool:
        return phase in {
            "prepare-ut-egress-policy",
            "install-ut-egress-mpls",
            "cleanup-ut-egress-mpls",
        }

    def _strip_batch_marker_lines(self, text: str) -> str:
        if not text:
            return ""
        kept_lines = [
            line
            for line in text.splitlines()
            if not line.startswith("__SKYSEED_BATCH_")
        ]
        return "\n".join(kept_lines)

    def _build_node_batch_script(self, node_plan: Dict[str, Any]) -> str:
        lines = [
            "run_cmd() {",
            '  idx="$1"',
            '  allow_failure="$2"',
            "  shift 2",
            '  printf "__SKYSEED_BATCH_CMD_START__:%s\\n" "$idx"',
            '  "$@"',
            '  rc="$?"',
            '  printf "__SKYSEED_BATCH_CMD_EXIT__:%s:%s\\n" "$idx" "$rc"',
            '  if [ "$rc" -ne 0 ] && [ "$allow_failure" -ne 1 ]; then',
            '    printf "__SKYSEED_BATCH_FAIL__:%s:%s\\n" "$idx" "$rc"',
            '    exit "$rc"',
            "  fi",
            "}",
        ]
        for command in node_plan["commands"]:
            allow_failure = "1" if command["allow_failure"] else "0"
            lines.append(
                "run_cmd "
                f"{command['index']} "
                f"{allow_failure} "
                + shlex.join(list(command["argv"]))
            )
        lines.append('printf "__SKYSEED_BATCH_OK__\\n"')
        return "\n".join(lines)

    def _apply_node_batch_result_markers(
        self,
        *,
        node_plan: Dict[str, Any],
        stdout: str,
        stderr: str,
        cleaned_stdout: str,
        cleaned_stderr: str,
        returncode: int,
    ) -> None:
        command_by_index = {
            int(command["index"]): command
            for command in node_plan["commands"]
        }
        command_exit_codes: Dict[int, int] = {}
        failed_index: Optional[int] = None
        failed_returncode: Optional[int] = None
        marker_stream = "\n".join(value for value in (stdout, stderr) if value)
        for line in marker_stream.splitlines():
            if line.startswith("__SKYSEED_BATCH_CMD_START__:"):
                continue
            elif line.startswith("__SKYSEED_BATCH_CMD_EXIT__:"):
                _, index_text, rc_text = line.split(":", 2)
                index = int(index_text)
                rc = int(rc_text)
                command = command_by_index.get(index)
                if command is None:
                    continue
                command_exit_codes[index] = rc
                if rc != 0 and not bool(command.get("allow_failure")) and failed_index is None:
                    failed_index = index
                    failed_returncode = rc
            elif line.startswith("__SKYSEED_BATCH_FAIL__:") and failed_index is None:
                _, index_text, rc_text = line.split(":", 2)
                failed_index = int(index_text)
                failed_returncode = int(rc_text)

        applied_commands: List[Dict[str, Any]] = []
        skipped_commands: List[Dict[str, Any]] = []
        for command in node_plan["commands"]:
            index = int(command["index"])
            if index in command_exit_codes:
                exit_code = command_exit_codes[index]
                apply_outcome = "applied" if exit_code == 0 else "allowed_failure"
                applied_commands.append(
                    {
                        **command,
                        "returncode": exit_code,
                        "apply_outcome": apply_outcome,
                    }
                )
                continue
            skipped_commands.append(
                {
                    **command,
                    "skip_reason": (
                        "not_executed_after_batch_abort"
                        if failed_index is not None
                        else "not_executed"
                    ),
                }
            )

        response_state = node_plan["response_envelope"]
        ack_state = response_state["ack"]
        apply_state = response_state["apply_result"]
        error_state = response_state["error"]
        replay_state = response_state["replay_info"]
        timestamp_state = response_state["timestamps"]
        ack_state["ack_status"] = (
            "acknowledged_success" if returncode == 0 else "acknowledged_error"
        )
        apply_state["apply_status"] = (
            "applied"
            if returncode == 0 and failed_index is None
            else "partially_applied"
            if failed_index is not None
            else "transport_error"
        )
        apply_state["applied_commands"] = applied_commands
        apply_state["skipped_commands"] = skipped_commands
        apply_state["stdout_summary"] = self._summarize_stream_output(cleaned_stdout)
        apply_state["stderr_summary"] = self._summarize_stream_output(cleaned_stderr)
        apply_state["transport_returncode"] = returncode
        timestamp_state["execution_finished_at"] = self._utc_timestamp()
        replay_state["replay_detected"] = False
        replay_state["already_applied"] = False
        error_state["error_code"] = "none"
        error_state["error_category"] = "none"
        error_state["error_message"] = None
        error_state["error_details"] = None
        apply_state["failed_command"] = None
        error_state["failed_command_id"] = None
        error_state["failed_command_family"] = None
        error_state["failed_logical_kind"] = None
        error_state["retryable"] = False
        error_state["fatal"] = False
        error_state["suggested_action"] = None
        if failed_index is not None and failed_index in command_by_index:
            apply_state["failed_command"] = {
                **command_by_index[failed_index],
                "returncode": failed_returncode,
            }
            error_state["failed_command_id"] = command_by_index[failed_index].get(
                "idempotency_key"
            )
            error_state["failed_command_family"] = command_by_index[failed_index].get(
                "command_family"
            )
            error_state["failed_logical_kind"] = command_by_index[failed_index].get(
                "logical_kind"
            )
            error_state["error_code"] = "command_failed"
            error_state["error_category"] = "execution"
            error_state["error_message"] = command_by_index[failed_index]["stage"]
            error_state["error_details"] = {
                "code": "command_failed",
                "message": command_by_index[failed_index]["stage"],
                "node_id": node_plan["node_id"],
                "node_plan_id": node_plan["node_plan_id"],
                "failed_command_index": failed_index,
                "failed_command_stage": command_by_index[failed_index]["stage"],
                "returncode": failed_returncode,
            }
            error_state["retryable"] = True
            error_state["fatal"] = False
            error_state["suggested_action"] = "inspect failed command and retry node_plan"
        elif returncode != 0:
            error_state["error_code"] = "transport_error"
            error_state["error_category"] = "transport"
            error_state["error_message"] = (
                f"node batch exited with status {returncode}"
            )
            error_state["error_details"] = {
                "code": "transport_exit_nonzero",
                "message": f"node batch exited with status {returncode}",
                "node_id": node_plan["node_id"],
                "node_plan_id": node_plan["node_plan_id"],
                "failed_command_index": None,
                "failed_command_stage": None,
                "returncode": returncode,
            }
            error_state["retryable"] = True
            error_state["fatal"] = False
            error_state["suggested_action"] = "retry transport delivery for node_plan"

    def _run_node_plan_batch(
        self,
        *,
        action_name: str,
        node_plan: Dict[str, Any],
    ) -> None:
        script = self._build_node_batch_script(node_plan)
        command = ["docker", "exec", node_plan["node_id"], "sh", "-lc", script]
        print(
            "  docker exec "
            f"{node_plan['node_id']} sh -lc "
            f"'<node-batch plan_id={node_plan['node_plan_id']} "
            f"commands={node_plan['command_count']}>'"
        )
        node_plan["response_envelope"]["ack"]["ack_status"] = "awaiting_transport_completion"
        node_plan["response_envelope"]["apply_result"]["apply_status"] = "in_progress"
        node_plan["response_envelope"]["timestamps"]["execution_started_at"] = self._utc_timestamp()
        node_plan["response_envelope"]["execution_nonce"] = self._execution_nonce(
            str(node_plan["node_plan_id"])
        )
        started_at = time.monotonic()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed_seconds = time.monotonic() - started_at
        stdout = self._strip_batch_marker_lines(result.stdout)
        stderr = self._strip_batch_marker_lines(result.stderr)
        if stdout.strip():
            print(stdout.rstrip())
        if stderr.strip():
            print(stderr.rstrip())
        self._record_command_execution(
            command=command,
            failure_stage=f"execute node batch on {node_plan['node_id']} for {action_name}",
            allow_failure=all(
                bool(command_entry["allow_failure"])
                for command_entry in node_plan["commands"]
            ),
            result=result,
            elapsed_seconds=elapsed_seconds,
            observation={
                "logical_category": "batched_runtime_update",
                "logical_kind": "node_batch_exec",
                "command_family": "docker_exec_batch",
                "shared_key": node_plan["node_plan_id"],
            },
        )
        self._apply_node_batch_result_markers(
            node_plan=node_plan,
            stdout=result.stdout,
            stderr=result.stderr,
            cleaned_stdout=stdout,
            cleaned_stderr=stderr,
            returncode=result.returncode,
        )
        if result.returncode != 0:
            failed_command = node_plan["response_envelope"]["apply_result"]["failed_command"]
            if failed_command is not None:
                raise RuntimeError(
                    "node batch execution failed on "
                    f"{node_plan['node_id']}: "
                    f"{failed_command['stage']} "
                    f"(command_index={failed_command['index']}, "
                    f"returncode={failed_command['returncode']})"
                )
            raise RuntimeError(
                "node batch execution failed on "
                f"{node_plan['node_id']} for {action_name}: "
                f"exit status {result.returncode}"
            )

    def _execute_ut_egress_payload_serial_batches(
        self,
        *,
        payload: Dict[str, Any],
    ) -> Dict[str, float]:
        node_elapsed_seconds: Dict[str, float] = {}
        for node_plan in payload["node_plans"]:
            started_at = time.monotonic()
            self._run_node_plan_batch(
                action_name=str(payload["action"]),
                node_plan=node_plan,
            )
            node_elapsed_seconds[str(node_plan["node_id"])] = round(
                time.monotonic() - started_at,
                3,
            )
        return node_elapsed_seconds

    def _execute_ut_egress_specs(
        self,
        *,
        action_name: str,
        specs: Sequence[UtEgressCommandSpec],
    ) -> Dict[str, Any]:
        self._print_ut_egress_spec_plan(action_name=action_name, specs=specs)
        logical_kind_counts = Counter(self._ut_egress_logical_kind(spec) for spec in specs)
        per_node_update_plan = self._ut_egress_per_node_update_plan(
            action_name=action_name,
            specs=specs,
        )
        action_context = self._ut_egress_action_context(action_name)
        batch_plan = list(per_node_update_plan["node_plans"])
        if self.args.dry_run:
            return {
                "action": action_name,
                "plan_id": per_node_update_plan["plan_id"],
                "phase": per_node_update_plan["phase"],
                "total_commands": len(specs),
                "category_counts": dict(sorted(Counter(spec.category for spec in specs).items())),
                "node_counts": dict(sorted(Counter(spec.node_id for spec in specs).items())),
                "logical_kind_counts": dict(sorted(logical_kind_counts.items())),
                "node_batch_plan": batch_plan,
                "per_node_update_plan": per_node_update_plan,
                "dry_run": True,
                "elapsed_seconds": 0.0,
            }

        started_at = time.monotonic()
        category_elapsed: Dict[str, float] = {}
        self._begin_command_observation(action_name)
        try:
            if self._ut_egress_batchable_phase(action_context["phase"]):
                node_elapsed_seconds = self._execute_ut_egress_payload_serial_batches(
                    payload=per_node_update_plan,
                )
            else:
                node_elapsed_seconds = {}
                for spec in specs:
                    command_started_at = time.monotonic()
                    self._docker_exec(
                        spec.node_id,
                        list(spec.command),
                        failure_stage=spec.stage,
                        allow_failure=spec.allow_failure,
                        observation={
                            "logical_category": spec.category,
                            "logical_kind": self._ut_egress_logical_kind(spec),
                            "shared_key": spec.shared_key,
                        },
                    )
                    category_elapsed[spec.category] = category_elapsed.get(spec.category, 0.0) + (
                        time.monotonic() - command_started_at
                    )
        finally:
            command_observation = self._end_command_observation()
            self._print_command_observation_summary(
                command_observation,
                label="UT egress execution summary",
            )
        elapsed_seconds = time.monotonic() - started_at
        summary = {
            "action": action_name,
            "plan_id": per_node_update_plan["plan_id"],
            "phase": per_node_update_plan["phase"],
            "total_commands": len(specs),
            "category_counts": dict(sorted(Counter(spec.category for spec in specs).items())),
            "node_counts": dict(sorted(Counter(spec.node_id for spec in specs).items())),
            "logical_kind_counts": dict(sorted(logical_kind_counts.items())),
            "node_batch_plan": batch_plan,
            "per_node_update_plan": per_node_update_plan,
            "transport_command_count": (
                len(batch_plan)
                if self._ut_egress_batchable_phase(action_context["phase"])
                else len(specs)
            ),
            "transport_mode": (
                "controller_serial_node_batch"
                if self._ut_egress_batchable_phase(action_context["phase"])
                else "controller_serial_docker_exec"
            ),
            "node_elapsed_seconds": node_elapsed_seconds,
            "category_elapsed_seconds": {
                key: round(value, 3) for key, value in sorted(category_elapsed.items())
            },
            "elapsed_seconds": round(elapsed_seconds, 3),
            "dry_run": False,
            "command_observation": command_observation,
        }
        if self.args.timing:
            print(
                "UT egress timing: "
                + json.dumps(summary, sort_keys=True)
            )
        return summary

    def validate_static_mpls_lsp_path(self, profile_id: str) -> Dict[str, Any]:
        profile = self._resolve_static_mpls_profile(profile_id)
        issues = validate_static_mpls_path(self.link_state, profile)
        if issues:
            raise RuntimeError(
                "static MPLS path validation failed against current link_state: "
                + ", ".join(issues)
            )

        rendered_path = " -> ".join(
            f"{entry.role}:{entry.node_id}"
            for entry in profile.path_roles
        )
        print(f"Static MPLS path validation passed: {profile.profile_id}")
        print(f"  path: {rendered_path}")
        print(
            "  required_slots: "
            + ", ".join(requirement.slot_name for requirement in profile.required_slots)
        )
        return {
            "profile_id": profile.profile_id,
            "path": [entry.node_id for entry in profile.path_roles],
            "required_slots": [
                requirement.slot_name for requirement in profile.required_slots
            ],
        }

    def _require_static_mpls_ready(self, profile_id: str) -> None:
        profile = self._resolve_static_mpls_profile(profile_id)
        for node_id in static_mpls_ready_node_ids(profile):
            self._ensure_runtime_node_mpls_platform_labels(node_id)
            result = self._probe_sysctl("net.mpls.platform_labels", node_id=node_id)
            if (result.get("int_value") or 0) <= 0:
                raise RuntimeError(
                    f"{node_id} is not MPLS-ready: {self._probe_text(result)}"
                )

    def install_static_mpls_lsp(self, profile_id: str) -> Dict[str, Any]:
        profile = self._resolve_static_mpls_profile(profile_id)
        self.validate_static_mpls_lsp_path(profile.profile_id)
        self._require_static_mpls_ready(profile.profile_id)
        print(f"Installing static MPLS LSP: {profile.profile_id}")
        for node_id, command, stage in static_mpls_install_specs(profile):
            self._docker_exec(node_id, command, failure_stage=stage)
        return {
            "profile_id": profile.profile_id,
            "ping_target": profile.ping_target,
            "labels": {
                "forward_push": profile.forward_push_label,
                "forward_swap": profile.forward_swap_label,
                "reverse_push": profile.reverse_push_label,
                "reverse_swap": profile.reverse_swap_label,
            },
        }

    def cleanup_static_mpls_lsp(self, profile_id: str) -> Dict[str, Any]:
        profile = self._resolve_static_mpls_profile(profile_id)
        print(f"Cleaning up static MPLS LSP: {profile.profile_id}")
        for node_id, command, allow_failure, stage in static_mpls_cleanup_specs(profile):
            self._docker_exec(
                node_id,
                command,
                failure_stage=stage,
                allow_failure=allow_failure,
            )
        return {"profile_id": profile.profile_id}

    def verify_static_mpls_lsp(self, profile_id: str) -> Dict[str, Any]:
        profile = self._resolve_static_mpls_profile(profile_id)
        self.validate_static_mpls_lsp_path(profile.profile_id)
        print(f"Verifying static MPLS LSP: {profile.profile_id}")
        for node_id, command, stage in static_mpls_route_show_specs(profile):
            self._docker_exec(node_id, command, failure_stage=stage)

        push_capture = self._spawn_docker_exec_capture(
            profile.transit_node_id,
            static_mpls_push_capture_command(profile),
        )
        pop_capture = self._spawn_docker_exec_capture(
            profile.egress_node_id,
            static_mpls_pop_capture_command(profile),
        )
        time.sleep(0.5)

        self._docker_exec(
            profile.ingress_node_id,
            static_mpls_ping_command(profile),
            failure_stage=(
                f"ping {profile.ping_target} from {profile.ingress_node_id} "
                "over static MPLS LSP"
            ),
        )

        push_output, _ = push_capture.communicate(timeout=7)
        pop_output, _ = pop_capture.communicate(timeout=7)

        print("Push capture")
        print(push_output.rstrip())
        print("Pop capture")
        print(pop_output.rstrip())

        if f"label {profile.forward_push_label}" not in push_output:
            raise RuntimeError(
                f"push validation failed: expected label {profile.forward_push_label} "
                f"on {profile.transit_node_id}/{profile.transit_ingress_interface}"
            )
        if f"{profile.ping_source} > {profile.ping_target}: ICMP echo request" not in pop_output:
            raise RuntimeError(
                "pop validation failed: expected decapsulated ICMP echo request "
                f"on {profile.egress_node_id}"
            )

        transit_lfib = self._docker_exec(
            profile.transit_node_id,
            ["ip", "-f", "mpls", "route", "show"],
            failure_stage=f"re-read {profile.transit_node_id} LFIB",
        ).stdout
        if f"{profile.forward_push_label} as to {profile.forward_swap_label}" not in transit_lfib:
            raise RuntimeError(
                "swap validation failed: transit LFIB does not show "
                f"{profile.forward_push_label} -> {profile.forward_swap_label}"
            )

        print("Static MPLS verification summary")
        print(
            f"  push: label {profile.forward_push_label} observed on "
            f"{profile.transit_node_id}/{profile.transit_ingress_interface}"
        )
        print(
            f"  swap: {profile.transit_node_id} LFIB contains "
            f"{profile.forward_push_label} -> {profile.forward_swap_label}"
        )
        print(
            f"  pop: decapsulated ICMP observed on {profile.egress_node_id} "
            f"for {profile.ping_target}"
        )
        print(
            f"  round-trip ping: {profile.ping_source} -> {profile.ping_target} succeeded"
        )
        return {
            "profile_id": profile.profile_id,
            "push_label": profile.forward_push_label,
            "swap_label": profile.forward_swap_label,
            "ping_source": profile.ping_source,
            "ping_target": profile.ping_target,
        }

    def _resolve_ut_egress_plan(self, ut_id: str) -> Any:
        intent = get_ut_egress_intent(ut_id)
        plan, issues = compute_ut_egress_plan(self.topology, self.link_state, intent.ut_id)
        if plan is None:
            raise RuntimeError(
                "UT egress path validation failed against current link_state: "
                + ", ".join(issues)
            )
        return plan

    def _resolve_all_attached_ut_egress_plans(self) -> List[Any]:
        plans, issues = compute_all_attached_ut_egress_plans(
            self.topology,
            self.link_state,
        )
        if issues:
            raise RuntimeError(
                "all-attached UT egress path validation failed against current "
                "link_state: " + ", ".join(issues)
            )
        return list(plans)

    def _require_ut_egress_ready(self, plan: Any) -> None:
        self._require_bridge_netfilter_passthrough()
        for node_id in ut_egress_ready_node_ids(plan):
            self._ensure_runtime_node_mpls_platform_labels(node_id)
            result = self._probe_sysctl("net.mpls.platform_labels", node_id=node_id)
            if (result.get("int_value") or 0) <= 0:
                raise RuntimeError(
                    f"{node_id} is not MPLS-ready: {self._probe_text(result)}"
                )

    def prepare_ut_egress_policy(self, ut_id: str) -> Dict[str, Any]:
        self._require_initialized_state()
        prep_plan = ut_egress_policy_preparation_plan(self.topology, ut_id)
        specs = ut_egress_policy_prepare_specs(prep_plan)
        execution = self._execute_ut_egress_specs(
            action_name=f"prepare-ut-egress-policy:{ut_id}",
            specs=specs,
        )
        return {
            "ut_id": ut_id,
            "policy_table_id": prep_plan.policy_table_id,
            "guardrail_prefixes": list(prep_plan.guardrail_prefixes),
            "execution": execution,
        }

    def prepare_all_ut_egress_policies(self) -> Dict[str, Any]:
        self._require_initialized_state()
        prepared_ut_ids: List[str] = []
        summaries: List[Dict[str, Any]] = []
        for ut_id in supported_ut_egress_ut_ids():
            summary = self.prepare_ut_egress_policy(ut_id)
            prepared_ut_ids.append(ut_id)
            summaries.append(summary)
        return {"ut_ids": prepared_ut_ids, "summaries": summaries}

    def apply_scenario(self, scenario_name: str) -> Dict[str, Any]:
        self._begin_command_observation(f"apply-scenario:{scenario_name}")
        try:
            state = self._ensure_runtime_ready_for_scenario()
            definition = self._scenario_definition(scenario_name)
            managed_slots = dict(definition.get("managed_slots", {}))
            state_by_name = self._slot_state_by_name(state)

            for slot_name in self._scenario_release_order(managed_slots):
                slot_entry = state_by_name[slot_name]
                target = managed_slots.get(slot_name)
                if self._scenario_slot_matches(slot_entry=slot_entry, target=target):
                    continue
                if slot_entry.get("occupied", False):
                    self.release_slot(slot_name)
                    state = self.link_state
                    state_by_name = self._slot_state_by_name(state)

            for slot_name in self._scenario_attach_order(managed_slots):
                slot_entry = state_by_name[slot_name]
                target = managed_slots[slot_name]
                if self._scenario_slot_matches(slot_entry=slot_entry, target=target):
                    continue
                action = str(target.get("action"))
                request = str(target.get("request"))
                if action == "attach_ut":
                    self.attach_ut(request, requested_slot_name=slot_name)
                elif action == "attach_gw":
                    self.attach_gateway(request, requested_slot_name=slot_name)
                elif action == "connect_cross":
                    self.connect_cross_plane(request, requested_slot_name=slot_name)
                else:
                    raise RuntimeError(f"unsupported scenario action: {action}")
                state = self.link_state
                state_by_name = self._slot_state_by_name(state)

            result = self.verify_scenario(scenario_name)
        finally:
            command_observation = self._end_command_observation()
            self._print_command_observation_summary(
                command_observation,
                label="Apply scenario execution summary",
            )

        result["execution"] = command_observation
        return result

    def verify_scenario(self, scenario_name: str) -> Dict[str, Any]:
        state = self._require_initialized_state()
        definition = self._scenario_definition(scenario_name)
        managed_slots = dict(definition.get("managed_slots", {}))
        state_by_name = self._slot_state_by_name(state)
        mismatches: List[str] = []

        for slot_name, target in managed_slots.items():
            slot_entry = state_by_name[slot_name]
            if self._scenario_slot_matches(slot_entry=slot_entry, target=target):
                continue
            if target is None:
                mismatches.append(f"{slot_name}: expected released")
            else:
                mismatches.append(
                    f"{slot_name}: expected {json.dumps(target.get('binding', {}), sort_keys=True)} "
                    f"got occupied={slot_entry.get('occupied')} binding={json.dumps(slot_entry.get('binding'), sort_keys=True)}"
                )

        verify_ut_id = str(definition.get("verify_ut_id"))
        plan = self._resolve_ut_egress_plan(verify_ut_id)
        expected_serving_satellite_id = str(definition.get("expected_serving_satellite_id"))
        expected_selected_gateway_id = str(definition.get("expected_selected_gateway_id"))
        expected_satellite_path = list(definition.get("expected_satellite_path", []))

        if plan.serving_satellite_id != expected_serving_satellite_id:
            mismatches.append(
                f"{verify_ut_id}: expected serving satellite {expected_serving_satellite_id}, got {plan.serving_satellite_id}"
            )
        if plan.selected_gateway_id != expected_selected_gateway_id:
            mismatches.append(
                f"{verify_ut_id}: expected selected gateway {expected_selected_gateway_id}, got {plan.selected_gateway_id}"
            )
        if list(plan.satellite_path) != expected_satellite_path:
            mismatches.append(
                f"{verify_ut_id}: expected satellite path {expected_satellite_path}, got {list(plan.satellite_path)}"
            )

        if mismatches:
            raise RuntimeError(
                f"scenario verification failed for {scenario_name}: " + "; ".join(mismatches)
            )

        return {
            "scenario": scenario_name,
            "verify_ut_id": verify_ut_id,
            "serving_satellite_id": plan.serving_satellite_id,
            "selected_gateway_id": plan.selected_gateway_id,
            "satellite_path": list(plan.satellite_path),
        }

    def install_ut_egress_mpls(self, ut_id: str) -> Dict[str, Any]:
        plan = self._resolve_ut_egress_plan(ut_id)
        self._require_ut_egress_ready(plan)
        print(f"Installing UT egress MPLS: {plan.profile_id}")
        execution = self._execute_ut_egress_specs(
            action_name=f"install-ut-egress-mpls:{plan.profile_id}",
            specs=ut_egress_install_specs(plan),
        )
        return {
            "intent_id": plan.profile_id,
            "ut_id": plan.ut_id,
            "selected_gateway_id": plan.selected_gateway_id,
            "satellite_path": list(plan.satellite_path),
            "execution": execution,
        }

    def verify_ut_egress_mpls(
        self,
        ut_id: str,
        *,
        ping_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan = self._resolve_ut_egress_plan(ut_id)
        resolved_target = resolve_ut_egress_ping_target(plan, ping_target)
        print(f"Verifying UT egress MPLS: {plan.profile_id}")
        execution = self._execute_ut_egress_specs(
            action_name=f"verify-ut-egress-mpls:{plan.profile_id}",
            specs=ut_egress_route_show_specs(plan),
        )
        if self.args.dry_run:
            return {
                "intent_id": plan.profile_id,
                "ut_id": plan.ut_id,
                "selected_gateway_id": plan.selected_gateway_id,
                "satellite_path": list(plan.satellite_path),
                "ping_target": resolved_target,
                "execution": execution,
            }
        self._docker_exec(
            plan.ut_id,
            ut_access_probe_command(plan),
            failure_stage=f"probe access link from {plan.ut_id}",
        )
        self._docker_exec(
            plan.ut_id,
            ut_egress_ping_command(plan, resolved_target),
            failure_stage=f"ping {resolved_target} from {plan.ut_id}",
        )
        return {
            "intent_id": plan.profile_id,
            "ut_id": plan.ut_id,
            "selected_gateway_id": plan.selected_gateway_id,
            "satellite_path": list(plan.satellite_path),
            "ping_target": resolved_target,
            "execution": execution,
        }

    def cleanup_ut_egress_mpls(self, ut_id: str) -> Dict[str, Any]:
        plan = self._resolve_ut_egress_plan(ut_id)
        print(f"Cleaning up UT egress MPLS: {plan.profile_id}")
        execution = self._execute_ut_egress_specs(
            action_name=f"cleanup-ut-egress-mpls:{plan.profile_id}",
            specs=ut_egress_cleanup_specs(plan),
        )
        return {
            "intent_id": plan.profile_id,
            "ut_id": plan.ut_id,
            "execution": execution,
        }

    def install_all_attached_ut_egress_mpls(self) -> Dict[str, Any]:
        plans = self._resolve_all_attached_ut_egress_plans()
        installed_ut_ids: List[str] = []
        for plan in plans:
            self.install_ut_egress_mpls(plan.ut_id)
            installed_ut_ids.append(plan.ut_id)
        return {"ut_ids": installed_ut_ids}

    def verify_all_attached_ut_egress_mpls(
        self,
        *,
        ping_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        plans = self._resolve_all_attached_ut_egress_plans()
        verified_ut_ids: List[str] = []
        for plan in plans:
            self.verify_ut_egress_mpls(plan.ut_id, ping_target=ping_target)
            verified_ut_ids.append(plan.ut_id)
        return {"ut_ids": verified_ut_ids}

    def cleanup_all_attached_ut_egress_mpls(self) -> Dict[str, Any]:
        plans = self._resolve_all_attached_ut_egress_plans()
        cleaned_ut_ids: List[str] = []
        for plan in plans:
            self.cleanup_ut_egress_mpls(plan.ut_id)
            cleaned_ut_ids.append(plan.ut_id)
        return {"ut_ids": cleaned_ut_ids}

    def print_summary(self) -> None:
        slot_inventory = self._slot_inventory()
        print("Satellite slot-link prototype")
        print(f"  topology: {self.args.topology_config}")
        print(f"  link_state: {self.args.link_state}")
        print(f"  topology schema: {self.topology.get('schema_version', 'unknown')}")
        print(
            "  node counts: "
            f"satellites={len(self.topology.get('satellites', []))}, "
            f"gateways={len(self.topology.get('gateways', []))}, "
            f"user_terminals={len(self.topology.get('user_terminals', []))}"
        )
        print(
            "  slot inventory: "
            f"ut_access={len(slot_inventory.get('ut_access', []))}, "
            f"gateway_access={len(slot_inventory.get('gateway_access', []))}, "
            f"cross_plane_isl={len(slot_inventory.get('cross_plane_isl', []))}, "
            f"fixed_isl={len(self._fixed_links())}"
        )
        print(
            "  runtime initialized: "
            f"{self.link_state.get('runtime_initialized', False)}"
        )

    def print_link_state(self) -> None:
        state = self.link_state
        print("Link state")
        print(f"  schema: {state.get('schema_version', 'unknown')}")
        print(f"  runtime_initialized: {state.get('runtime_initialized', False)}")
        for entry in state.get("slots", []):
            print(
                "  "
                f"{entry.get('slot_name')} "
                f"type={entry.get('slot_type')} "
                f"occupied={entry.get('occupied')}"
            )
            print(
                "    "
                f"network={entry.get('docker_network_name')} "
                f"subnet={entry.get('subnet')}"
            )
            preconnected = entry.get("preconnected_endpoints", [])
            if preconnected:
                joined = ", ".join(
                    f"{endpoint.get('node_id')}:{endpoint.get('interface_name')}:{endpoint.get('ip_address')}"
                    for endpoint in preconnected
                )
                print(f"    preconnected={joined}")
            dynamic = entry.get("dynamic_endpoints", [])
            if dynamic:
                joined = ", ".join(
                    f"{endpoint.get('node_id')}:{endpoint.get('interface_name')}:{endpoint.get('ip_address')}"
                    + (
                        f":{endpoint.get('logical_direction')}"
                        if endpoint.get("logical_direction") is not None
                        else ""
                    )
                    for endpoint in dynamic
                )
                print(f"    dynamic={joined}")
            binding = entry.get("binding")
            if binding:
                print(f"    binding={json.dumps(binding, sort_keys=True)}")

    def run(self) -> int:
        self.print_summary()
        try:
            if self.args.init_slot_runtime:
                state = self.initialize_slot_runtime()
                print(f"slot runtime initialized: {len(state.get('slots', []))} state entries")
                return 0
            if self.args.show_link_state:
                self.print_link_state()
                return 0
            if self.args.probe_mpls:
                report = self.probe_mpls()
                self.print_mpls_probe(report)
                return 0
            if self.args.install_static_mpls_lsp:
                result = self.install_static_mpls_lsp(self.args.install_static_mpls_lsp)
                print(f"static MPLS LSP installed: {result.get('profile_id')}")
                return 0
            if self.args.verify_static_mpls_lsp:
                result = self.verify_static_mpls_lsp(self.args.verify_static_mpls_lsp)
                print(f"static MPLS LSP verified: {result.get('profile_id')}")
                return 0
            if self.args.cleanup_static_mpls_lsp:
                result = self.cleanup_static_mpls_lsp(self.args.cleanup_static_mpls_lsp)
                print(f"static MPLS LSP cleaned up: {result.get('profile_id')}")
                return 0
            if self.args.prepare_ut_egress_policy:
                result = self.prepare_ut_egress_policy(self.args.prepare_ut_egress_policy)
                print(f"UT egress policy prepared: {result.get('ut_id')}")
                return 0
            if self.args.prepare_all_ut_egress_policies:
                result = self.prepare_all_ut_egress_policies()
                print(
                    "UT egress policies prepared for supported UTs: "
                    + ", ".join(result.get("ut_ids", []))
                )
                return 0
            if self.args.install_ut_egress_mpls:
                result = self.install_ut_egress_mpls(self.args.install_ut_egress_mpls)
                print(f"UT egress MPLS installed: {result.get('intent_id')}")
                return 0
            if self.args.verify_ut_egress_mpls:
                result = self.verify_ut_egress_mpls(
                    self.args.verify_ut_egress_mpls,
                    ping_target=self.args.ping_target,
                )
                print(f"UT egress MPLS verified: {result.get('intent_id')}")
                return 0
            if self.args.cleanup_ut_egress_mpls:
                result = self.cleanup_ut_egress_mpls(self.args.cleanup_ut_egress_mpls)
                print(f"UT egress MPLS cleaned up: {result.get('intent_id')}")
                return 0
            if self.args.install_all_attached_ut_egress_mpls:
                result = self.install_all_attached_ut_egress_mpls()
                print(
                    "UT egress MPLS installed for attached supported UTs: "
                    + ", ".join(result.get("ut_ids", []))
                )
                return 0
            if self.args.verify_all_attached_ut_egress_mpls:
                result = self.verify_all_attached_ut_egress_mpls(
                    ping_target=self.args.ping_target,
                )
                print(
                    "UT egress MPLS verified for attached supported UTs: "
                    + ", ".join(result.get("ut_ids", []))
                )
                return 0
            if self.args.cleanup_all_attached_ut_egress_mpls:
                result = self.cleanup_all_attached_ut_egress_mpls()
                print(
                    "UT egress MPLS cleaned up for attached supported UTs: "
                    + ", ".join(result.get("ut_ids", []))
                )
                return 0
            if self.args.apply_scenario:
                result = self.apply_scenario(self.args.apply_scenario)
                print(f"scenario applied: {result.get('scenario')}")
                return 0
            if self.args.verify_scenario:
                result = self.verify_scenario(self.args.verify_scenario)
                print(f"scenario verified: {result.get('scenario')}")
                return 0
            if self.args.attach_ut:
                result = self.attach_ut(
                    self.args.attach_ut,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"ut attached via {result.get('slot_name')}")
                return 0
            if self.args.detach_ut:
                result = self.detach_ut(
                    self.args.detach_ut,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"ut slot released: {result.get('slot_name')}")
                return 0
            if self.args.attach_gw:
                result = self.attach_gateway(
                    self.args.attach_gw,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"gateway attached via {result.get('slot_name')}")
                return 0
            if self.args.detach_gw:
                result = self.detach_gateway(
                    self.args.detach_gw,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"gateway slot released: {result.get('slot_name')}")
                return 0
            if self.args.connect_cross:
                result = self.connect_cross_plane(
                    self.args.connect_cross,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"cross-plane slot occupied: {result.get('slot_name')}")
                return 0
            if self.args.release_slot:
                result = self.release_slot(self.args.release_slot)
                print(f"slot released: {result.get('slot_name')}")
                return 0
        except (ValueError, RuntimeError) as error:
            print(f"Controller action failed: {error}")
            return 1

        print("No action requested.")
        return 0


def main() -> int:
    args = parse_args()
    controller = SatelliteController(args)
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())
