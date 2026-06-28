#!/usr/bin/env python3
# encoding: utf-8

"""Compile and attempt a bounded real runtime for the Starlink shell path."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from agent_envelope import GenericDryRunAgentEnvelopeBuilder
from contracts import (
    TopologySnapshot,
    validate_network_mutation_plan,
    validate_runtime_update_plan,
    validate_topology_snapshot,
)
from distribution_store import GenericDryRunEnvelopeDistributor
from network_mutation import (
    build_network_mutation_plan,
    dynamic_slot_state_from_link_state,
    dynamic_slot_state_from_snapshot,
    load_link_state,
    summarize_network_mutation_plan,
)
from planner import UtInternetExperimentPlanner
from runtime_projection import GenericDryRunProjector
from linux_mpls_runtime import MinimalRealLinuxMplsInstaller
from iteration2_satellite_experiment import (
    _flow_path_summaries,
    _gateway_choice_summary,
    _gateway_nat_semantics,
    _run_continuous_real_execution,
    _targets_reuse_seed_inventory,
    _ut_gateway_behavior,
)


DEFAULT_CONFIG_DIR = SATELLITE_DIR / "configs" / "starlink_shell_runtime_attempt"
DEFAULT_OUTPUT_DIR = SATELLITE_DIR / "output" / "starlink_shell_runtime_attempt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attempt a bounded continuous-real Starlink shell runtime."
    )
    parser.add_argument(
        "--orbit-input-mode",
        choices=("planned-shell", "real-catalog"),
        default="planned-shell",
        help="Orbit input mode forwarded to the Starlink preview builder.",
    )
    parser.add_argument(
        "--planned-shell-id",
        default="S5",
        help="Planned shell preset when --orbit-input-mode planned-shell is used.",
    )
    parser.add_argument(
        "--orbit-data",
        default=None,
        help="Local orbit catalog file when --orbit-input-mode real-catalog is used.",
    )
    parser.add_argument(
        "--max-satellites",
        type=int,
        default=24,
        help="Requested Starlink shell size for the bounded runtime attempt.",
    )
    parser.add_argument(
        "--max-uts",
        type=int,
        default=2,
        help="Selected UT count forwarded to the preview builder.",
    )
    parser.add_argument(
        "--max-gateways",
        type=int,
        default=3,
        help="Selected gateway count forwarded to the preview builder.",
    )
    parser.add_argument(
        "--ut-ids",
        default="ut3,ut4",
        help="Comma-separated UT ids for the bounded runtime attempt.",
    )
    parser.add_argument(
        "--gateway-ids",
        default="gw5,gw6,gw7",
        help="Comma-separated gateway ids for the bounded runtime attempt.",
    )
    parser.add_argument(
        "--slice-count",
        type=int,
        default=3,
        help="Slice count for the runtime attempt.",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=120.0,
        help="Slice step for the runtime attempt.",
    )
    parser.add_argument(
        "--loop-count",
        type=int,
        default=1,
        help="Continuous-real loop count.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=("correctness", "fast-demo"),
        default="fast-demo",
        help="Runtime mode forwarded to the real executor.",
    )
    parser.add_argument(
        "--max-node-workers",
        type=int,
        default=2,
        help="Bounded per-node worker count.",
    )
    parser.add_argument(
        "--max-host-workers",
        type=int,
        default=1,
        help="Bounded host mutation worker count.",
    )
    parser.add_argument(
        "--ground-placement",
        choices=("catalog", "shell-aware"),
        default="shell-aware",
        help="Ground placement forwarded to the preview builder.",
    )
    parser.add_argument(
        "--observe-flow",
        action="append",
        default=[],
        help="Optional packet observation selector ut_id:target_host_node_id or ut_id:target_ip.",
    )
    parser.add_argument(
        "--stream-progress",
        action="store_true",
        help="Forward per-transition progress markers from the real executor.",
    )
    parser.add_argument(
        "--config-dir",
        default=str(DEFAULT_CONFIG_DIR),
        help="Config directory for generated Starlink topology artifacts.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="SEED compiler output directory for the Starlink runtime attempt.",
    )
    parser.add_argument(
        "--mini-internet-node-prefix",
        default="starlinkrt-",
        help="Container/node-name prefix for compiled Mini Internet routers and hosts.",
    )
    parser.add_argument(
        "--mini-internet-ix-offset",
        type=int,
        default=100,
        help="IX id offset for the compiled Mini Internet route-server/container namespace.",
    )
    parser.add_argument(
        "--docker-dummy-network-pool",
        default="10.224.0.0/12",
        help="SeedEmu self-managed Docker dummy network pool for the runtime attempt.",
    )
    parser.add_argument(
        "--docker-dummy-network-mask",
        type=int,
        default=24,
        help="SeedEmu self-managed Docker dummy network mask for the runtime attempt.",
    )
    parser.add_argument(
        "--enable-seed-internet-map",
        action="store_true",
        help="Keep the SeedEmu internet-map exposed for the compiled runtime attempt.",
    )
    parser.add_argument(
        "--keep-environment",
        action="store_true",
        help="Leave the docker-compose environment running after the attempt.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render the runtime attempt summary as text or JSON.",
    )
    return parser.parse_args()


def _run_command(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=True,
    )


def _build_preview(command_args: argparse.Namespace) -> Dict[str, Any]:
    command = [
        sys.executable,
        str((SATELLITE_DIR / "apps" / "build_starlink_shell_preview.py").resolve()),
        "--orbit-input-mode",
        str(command_args.orbit_input_mode),
        "--max-satellites",
        str(command_args.max_satellites),
        "--max-uts",
        str(command_args.max_uts),
        "--max-gateways",
        str(command_args.max_gateways),
        "--ut-ids",
        str(command_args.ut_ids),
        "--gateway-ids",
        str(command_args.gateway_ids),
        "--slice-count",
        str(command_args.slice_count),
        "--step-seconds",
        str(command_args.step_seconds),
        "--ground-placement",
        str(command_args.ground_placement),
        "--config-dir",
        str(Path(command_args.config_dir).resolve()),
        "--output-dir",
        str(Path(command_args.output_dir).resolve()),
        "--mini-internet-node-prefix",
        str(command_args.mini_internet_node_prefix),
        "--mini-internet-ix-offset",
        str(command_args.mini_internet_ix_offset),
        "--docker-dummy-network-pool",
        str(command_args.docker_dummy_network_pool),
        "--docker-dummy-network-mask",
        str(command_args.docker_dummy_network_mask),
        "--compile-seed-output",
        "--format",
        "json",
    ]
    if not bool(command_args.enable_seed_internet_map):
        command.append("--disable-seed-internet-map")
    if str(command_args.orbit_input_mode) == "planned-shell":
        command.extend(["--planned-shell-id", str(command_args.planned_shell_id)])
    if command_args.orbit_data:
        command.extend(["--orbit-data", str(command_args.orbit_data)])
    result = _run_command(command, cwd=SATELLITE_DIR.parent)
    return json.loads(result.stdout)


def _compose_project_name(output_dir: Path) -> str:
    env_path = output_dir / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "COMPOSE_PROJECT_NAME":
                project_name = value.strip().strip("\"'")
                if project_name:
                    return project_name
    return output_dir.name


def _compose_down(output_dir: Path) -> subprocess.CompletedProcess[str] | None:
    compose_file = output_dir / "docker-compose.yml"
    if not compose_file.exists():
        return None
    project_name = _compose_project_name(output_dir)
    return subprocess.run(
        [
            "docker-compose",
            "-f",
            str(compose_file),
            "-p",
            project_name,
            "down",
            "--remove-orphans",
            "-v",
        ],
        cwd=str(output_dir.parent),
        text=True,
        capture_output=True,
        check=False,
    )


def _compose_up(output_dir: Path) -> subprocess.CompletedProcess[str]:
    return _run_command(["docker-compose", "up", "-d", "--build"], cwd=output_dir)


def _classify_compose_failure(stdout: str, stderr: str) -> Dict[str, Any]:
    combined = f"{stdout}\n{stderr}"
    if "Pool overlaps with other one on this address space" in combined:
        return {
            "classified_blocker": "docker_network_overlap_with_existing_environment",
        }
    if "Cannot create container for service rs_ix_ix" in combined and 'container name "/ix' in combined:
        return {
            "classified_blocker": "docker_route_server_container_name_conflict",
        }
    if "NO_PUBKEY" in combined and "apt-get update" in combined:
        service_match = re.search(r"Service '([^']+)' failed to build", combined)
        return {
            "classified_blocker": "docker_image_build_apt_signing_failure",
            "failed_service": None if service_match is None else service_match.group(1),
        }
    return {
        "classified_blocker": "docker_compose_up_failed",
    }


def _snapshot_from_dict(payload: Dict[str, Any]) -> TopologySnapshot:
    return TopologySnapshot(
        schema_version=str(payload["schema_version"]),
        snapshot_id=str(payload["snapshot_id"]),
        time_slice=dict(payload["time_slice"]),
        provenance=dict(payload["provenance"]),
        nodes={str(key): dict(value) for key, value in dict(payload["nodes"]).items()},
        active_links=[dict(entry) for entry in list(payload["active_links"])],
        zones={str(key): dict(value) for key, value in dict(payload["zones"]).items()},
        ut_zone_membership={
            str(key): str(value)
            for key, value in dict(payload["ut_zone_membership"]).items()
        },
        planner_hints=dict(payload.get("planner_hints", {})),
    )


def _load_snapshots(snapshot_preview_json: Path) -> List[TopologySnapshot]:
    payload = json.loads(snapshot_preview_json.read_text(encoding="utf-8"))
    return [
        _snapshot_from_dict(snapshot)
        for snapshot in list(payload.get("snapshots", []))
    ]


def _build_slice_contexts(
    *,
    topology_path: Path,
    link_state_path: Path,
    snapshots: Sequence[TopologySnapshot],
) -> List[Dict[str, Any]]:
    planner = UtInternetExperimentPlanner()
    projector = GenericDryRunProjector()
    envelope_builder = GenericDryRunAgentEnvelopeBuilder()
    distributor = GenericDryRunEnvelopeDistributor()
    real_installer = MinimalRealLinuxMplsInstaller()

    baseline_slot_state = dynamic_slot_state_from_link_state(load_link_state(link_state_path))
    current_slot_state = dict(baseline_slot_state)
    current_state_id = f"link_state:{link_state_path.name}"
    contexts: List[Dict[str, Any]] = []

    for snapshot in snapshots:
        runtime_update_plan = planner.plan(snapshot)
        installer_plan = projector.project(runtime_update_plan)
        envelope_batch = envelope_builder.build(installer_plan)
        distribution_batch = distributor.publish(envelope_batch)
        network_mutation_plan = build_network_mutation_plan(
            source_state=current_slot_state,
            source_state_id=current_state_id,
            target_snapshot=snapshot,
            transition_name=f"{current_state_id}_to_{snapshot.time_slice.get('slice_id')}",
        )
        baseline_network_mutation_plan = build_network_mutation_plan(
            source_state=baseline_slot_state,
            source_state_id=f"link_state:{link_state_path.name}",
            target_snapshot=snapshot,
            transition_name=f"baseline_to_{snapshot.time_slice.get('slice_id')}",
        )
        topology_validation = validate_topology_snapshot(snapshot)
        runtime_update_validation = validate_runtime_update_plan(runtime_update_plan)
        mutation_validation = validate_network_mutation_plan(network_mutation_plan)
        planner_flow_validation = planner.validate_flow(snapshot, runtime_update_plan)
        ut_gateway_behavior = _ut_gateway_behavior(runtime_update_plan)
        gateway_choice_summary = _gateway_choice_summary(ut_gateway_behavior)
        flow_path_summaries = _flow_path_summaries(runtime_update_plan)
        realization_support = real_installer.assess_realization(
            runtime_update_plan=runtime_update_plan,
        )
        slice_report = {
            "time_slice_id": snapshot.time_slice.get("slice_id"),
            "snapshot_id": snapshot.snapshot_id,
            "gateway_choice_summary": gateway_choice_summary,
            "flow_path_summaries": flow_path_summaries,
            "multi_hop_status": {
                "multi_hop_flow_count": realization_support["multi_hop_flow_count"],
                "direct_flow_count": realization_support["direct_flow_count"],
                "multi_hop_flow_ids": list(realization_support["multi_hop_flow_ids"]),
                "present": realization_support["multi_hop_flow_count"] > 0,
            },
            "gateway_nat_semantics": _gateway_nat_semantics(snapshot),
            "real_execution_support": realization_support,
            "network_mutation_summary": summarize_network_mutation_plan(network_mutation_plan),
            "validation": {
                "topology_snapshot": topology_validation.to_dict(),
                "runtime_update_plan": runtime_update_validation.to_dict(),
                "network_mutation_plan": mutation_validation.to_dict(),
                "planner_flow": planner_flow_validation.to_dict(),
                "targets_reuse_seed_inventory": _targets_reuse_seed_inventory(snapshot),
                "all_valid": (
                    topology_validation.is_valid
                    and runtime_update_validation.is_valid
                    and mutation_validation.is_valid
                    and planner_flow_validation.is_valid
                ),
            },
        }
        contexts.append(
            {
                "snapshot": snapshot,
                "runtime_update_plan": runtime_update_plan,
                "distribution_batch": distribution_batch,
                "network_mutation_plan": network_mutation_plan,
                "baseline_network_mutation_plan": baseline_network_mutation_plan,
                "slice_report": slice_report,
            }
        )
        current_slot_state = dynamic_slot_state_from_snapshot(snapshot)
        current_state_id = snapshot.snapshot_id
    return contexts


def main() -> int:
    args = parse_args()
    config_dir = Path(args.config_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    preview_summary = _build_preview(args)
    topology_artifacts = dict(preview_summary.get("topology_artifacts", {}))
    topology_path = Path(topology_artifacts["topology_json"]).resolve()
    link_state_path = Path(topology_artifacts["link_state_json"]).resolve()
    snapshot_preview_json = Path(topology_artifacts["snapshot_preview_json"]).resolve()
    snapshots = _load_snapshots(snapshot_preview_json)
    slice_contexts = _build_slice_contexts(
        topology_path=topology_path,
        link_state_path=link_state_path,
        snapshots=snapshots,
    )

    preview_gate = {
        "all_preview_valid": all(
            context["slice_report"]["validation"]["all_valid"] is True
            for context in slice_contexts
        ),
        "all_real_supported": all(
            context["slice_report"]["real_execution_support"]["supported"] is True
            for context in slice_contexts
        ),
        "unsupported_slices": [
            {
                "slice_id": context["snapshot"].time_slice.get("slice_id"),
                "blockers": list(context["slice_report"]["real_execution_support"]["blockers"]),
            }
            for context in slice_contexts
            if context["slice_report"]["real_execution_support"]["supported"] is not True
        ],
    }

    compose_down_before = _compose_down(output_dir)
    compose_up = None
    runtime_report = None
    compose_down_after = None
    compose_error: Dict[str, Any] | None = None
    try:
        if preview_gate["all_preview_valid"] and preview_gate["all_real_supported"]:
            try:
                compose_up = _compose_up(output_dir)
                runtime_report = _run_continuous_real_execution(
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    slice_contexts=slice_contexts,
                    loop_count=int(args.loop_count),
                    pacing_interval_seconds=0.0,
                    max_node_workers=int(args.max_node_workers),
                    max_host_workers=int(args.max_host_workers),
                    runtime_mode=str(args.runtime_mode),
                    observe_selectors=list(args.observe_flow),
                    stream_progress=bool(args.stream_progress),
                )
            except subprocess.CalledProcessError as exc:
                classified = _classify_compose_failure(exc.stdout or "", exc.stderr or "")
                compose_error = {
                    "stage": "docker_compose_up",
                    "returncode": exc.returncode,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    **classified,
                }
    finally:
        if not bool(args.keep_environment):
            compose_down_after = _compose_down(output_dir)

    summary = {
        "preview_summary": preview_summary,
        "preview_gate": preview_gate,
        "runtime_attempt": runtime_report,
        "runtime_blocker": compose_error,
        "docker_compose": {
            "output_dir": str(output_dir),
            "compose_down_before": (
                {
                    "command": list(compose_down_before.args),
                    "returncode": compose_down_before.returncode,
                    "stdout": compose_down_before.stdout,
                    "stderr": compose_down_before.stderr,
                }
                if compose_down_before is not None
                else None
            ),
            "compose_up": (
                {
                    "stdout": compose_up.stdout,
                    "stderr": compose_up.stderr,
                }
                if compose_up is not None
                else None
            ),
            "compose_down_after": (
                {
                    "command": list(compose_down_after.args),
                    "returncode": compose_down_after.returncode,
                    "stdout": compose_down_after.stdout,
                    "stderr": compose_down_after.stderr,
                }
                if compose_down_after is not None
                else None
            ),
        },
        "config_dir": str(config_dir),
    }

    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print("Starlink shell runtime attempt complete.")
    print(
        "Preview gate: "
        f"preview_valid={preview_gate['all_preview_valid']} "
        f"real_supported={preview_gate['all_real_supported']}"
    )
    if runtime_report is not None:
        print(
            "Runtime attempt: "
            f"real_apply_success={runtime_report['outcome']['real_apply_success']} "
            f"ping_success={runtime_report['outcome']['ping_success']} "
            f"rollback_completed={runtime_report['outcome']['rollback_completed']}"
        )
    else:
        print("Runtime attempt: skipped")
    print(f"Topology inventory: {topology_path}")
    print(f"Link state: {link_state_path}")
    print(f"Snapshot preview: {snapshot_preview_json}")
    print(f"Output dir: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
