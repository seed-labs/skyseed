#!/usr/bin/env python3
# encoding: utf-8

"""Minimal real Linux/MPLS runtime realization for the Iteration 2 experiment."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from contracts import RuntimeUpdatePlan, TopologySnapshot
from distribution_store import DistributedEnvelopeBatch


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _topology_entry_by_id(
    entries: Sequence[Dict[str, Any]],
    key: str,
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        entry_id = str(entry.get(key))
        result[entry_id] = dict(entry)
    return result


def _strip_prefix(ip_address: str) -> str:
    return str(ip_address).split("/", 1)[0]


def _active_link_by_id(
    topology_snapshot: TopologySnapshot,
    link_id: str,
) -> Dict[str, Any]:
    return next(
        link
        for link in topology_snapshot.active_links
        if str(link.get("link_id")) == link_id
    )


def _endpoint_for_node(link: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    return next(
        dict(endpoint)
        for endpoint in link.get("endpoints", [])
        if str(endpoint.get("node_id")) == node_id
    )


def _flow_contexts_from_plan(
    runtime_update_plan: RuntimeUpdatePlan,
    *,
    target_ut_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    selected_ut_ids = (
        set(str(ut_id) for ut_id in target_ut_ids)
        if target_ut_ids is not None
        else None
    )
    flow_contexts: Dict[str, Dict[str, Any]] = {}
    for subplan in runtime_update_plan.node_subplans.values():
        for action in subplan.get("actions", []):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            match = dict(action.get("intent", {}).get("match", {}))
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            local_role = str(outcome.get("local_role", match.get("node_role", "")))
            if local_role == "ut_ingress":
                gateway_selection = dict(outcome.get("gateway_selection", {}))
                ut_id = str(gateway_selection.get("ut_id", ""))
                if selected_ut_ids is not None and ut_id not in selected_ut_ids:
                    continue
                gateway_bindings = [
                    dict(binding)
                    for binding in gateway_selection.get("gateway_class_bindings", [])
                ]
                if not gateway_bindings:
                    gateway_bindings = [
                        dict(gateway_selection.get("gateway_class_binding", {}))
                    ]
                for gateway_binding in gateway_bindings:
                    ingress_transport = dict(gateway_binding.get("ingress_transport", {}))
                    flow_id = str(
                        gateway_binding.get(
                            "flow_id",
                            gateway_selection.get("flow_id", match.get("flow_id", ut_id)),
                        )
                    )
                    if not flow_id:
                        continue
                    flow_contexts.setdefault(flow_id, {}).update(
                        {
                            "flow_id": flow_id,
                            "ut_id": ut_id,
                            "zone_id": gateway_selection.get("zone_id"),
                            "serving_satellite_id": gateway_binding.get(
                                "serving_satellite_id"
                            ),
                            "selected_gateway_id": gateway_binding.get(
                                "selected_gateway_id"
                            ),
                            "ut_ingress_label": ingress_transport.get("ingress_label"),
                        }
                    )
                continue

            flow_context = dict(outcome.get("flow_context", {}))
            if not flow_context:
                continue
            ut_id = str(flow_context.get("ut_id", ""))
            if selected_ut_ids is not None and ut_id not in selected_ut_ids:
                continue
            flow_id = str(flow_context.get("flow_id", match.get("flow_id", ut_id)))
            if not flow_id:
                continue
            flow_contexts.setdefault(flow_id, {}).update(flow_context)
    if not flow_contexts:
        raise RuntimeError("unable to locate experiment flow context in runtime update plan")
    return [flow_contexts[flow_id] for flow_id in sorted(flow_contexts)]


def _realization_support_summary(
    runtime_update_plan: RuntimeUpdatePlan,
    *,
    target_ut_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    flow_contexts = _flow_contexts_from_plan(
        runtime_update_plan,
        target_ut_ids=target_ut_ids,
    )
    blockers: List[str] = []
    direct_flow_ids: List[str] = []
    multi_hop_flow_ids: List[str] = []
    flow_ids_by_node: Dict[str, set[str]] = {}
    for node_id, subplan in runtime_update_plan.node_subplans.items():
        for action in subplan.get("actions", []):
            flow_context = dict(action.get("intent", {}).get("outcome", {}).get("flow_context", {}))
            flow_id = str(flow_context.get("flow_id", ""))
            if flow_id:
                flow_ids_by_node.setdefault(str(node_id), set()).add(flow_id)
    for flow_context in flow_contexts:
        flow_id = str(flow_context.get("flow_id"))
        satellite_path = tuple(str(node_id) for node_id in flow_context.get("satellite_path", []))
        forward_labels = tuple(int(label) for label in flow_context.get("forward_labels", []))
        reverse_labels = tuple(int(label) for label in flow_context.get("reverse_labels", []))
        if len(satellite_path) <= 1:
            direct_flow_ids.append(flow_id)
        else:
            multi_hop_flow_ids.append(flow_id)
        if not satellite_path:
            blockers.append(f"missing_satellite_path:{flow_id}")
        if len(forward_labels) == 0 or len(reverse_labels) == 0:
            blockers.append(f"missing_mpls_labels:{flow_id}")
        if len(forward_labels) != len(reverse_labels):
            blockers.append(f"mismatched_forward_reverse_label_counts:{flow_id}")
        expected_segment_count = len(satellite_path)
        if len(forward_labels) != expected_segment_count:
            blockers.append(
                f"unexpected_forward_label_count:{flow_id}:{len(forward_labels)}:{expected_segment_count}"
            )
        for satellite_node_id in satellite_path:
            if flow_id not in flow_ids_by_node.get(str(satellite_node_id), set()):
                blockers.append(
                    f"missing_satellite_subplan_for_flow:{flow_id}:{satellite_node_id}"
                )
    return {
        "supported": len(blockers) == 0,
        "total_flow_count": len(flow_contexts),
        "direct_flow_ids": direct_flow_ids,
        "multi_hop_flow_ids": multi_hop_flow_ids,
        "blockers": blockers,
    }


@dataclass(frozen=True)
class MplsPathSegment:
    link_id: str
    source_node_id: str
    source_node_type: str
    source_interface: str
    source_ip: str
    destination_node_id: str
    destination_node_type: str
    destination_interface: str
    destination_ip: str
    label: int


@dataclass(frozen=True)
class DirectGatewayExperimentContext:
    flow_id: str
    ut_id: str
    serving_satellite_id: str
    selected_gateway_id: str
    selected_gateway_satellite_id: str
    ping_target_ip: str
    target_host_node_id: str
    policy_table_id: int
    policy_rule_priority: int
    satellite_path: Tuple[str, ...]
    forward_labels: Tuple[int, ...]
    reverse_labels: Tuple[int, ...]
    ut_access_interface: str
    ut_access_ip: str
    ut_access_peer_ip: str
    gateway_access_interface: str
    gateway_access_ip: str
    gateway_access_peer_ip: str
    gateway_nat_interface: str
    gateway_nat_source_ip: str
    forward_segments: Tuple[MplsPathSegment, ...]
    reverse_segments: Tuple[MplsPathSegment, ...]
    validation_targets: Tuple[Dict[str, str], ...]

    @property
    def return_target_prefix(self) -> str:
        return f"{self.ut_access_ip}/32"

    @property
    def forward_label(self) -> int:
        return int(self.forward_labels[0])

    @property
    def reverse_label(self) -> int:
        return int(self.reverse_labels[0])

    @property
    def is_direct_gateway_path(self) -> bool:
        return len(self.satellite_path) <= 1

    @property
    def nodes_requiring_platform_labels(self) -> Tuple[str, ...]:
        nodes = {
            self.serving_satellite_id,
            self.selected_gateway_id,
            *self.satellite_path,
        }
        return tuple(sorted(node_id for node_id in nodes if node_id))

    @property
    def mpls_input_interfaces(self) -> Tuple[Tuple[str, str], ...]:
        receiving_interfaces = {
            (segment.destination_node_id, segment.destination_interface)
            for segment in (*self.forward_segments, *self.reverse_segments)
            if segment.destination_node_type in {"satellite", "gateway"}
        }
        return tuple(sorted(receiving_interfaces))


@dataclass(frozen=True)
class RuntimeCommandSpec:
    node_id: str
    phase: str
    description: str
    command: Tuple[str, ...]
    allow_failure: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "description": self.description,
            "command": list(self.command),
            "allow_failure": self.allow_failure,
        }


@dataclass(frozen=True)
class RuntimeCommandResult:
    node_id: str
    phase: str
    description: str
    command: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    allow_failure: bool

    @property
    def success(self) -> bool:
        return self.returncode == 0 or self.allow_failure

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "description": self.description,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "allow_failure": self.allow_failure,
            "success": self.success,
        }


@dataclass(frozen=True)
class NodeLocalBatchSpec:
    node_id: str
    phase: str
    command_specs: Tuple[RuntimeCommandSpec, ...]

    @property
    def command_count(self) -> int:
        return len(self.command_specs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "command_count": self.command_count,
            "descriptions": [spec.description for spec in self.command_specs],
            "commands": [list(spec.command) for spec in self.command_specs],
            "allow_failures": [spec.allow_failure for spec in self.command_specs],
        }


@dataclass(frozen=True)
class NodeLocalBatchResult:
    node_id: str
    phase: str
    command_count: int
    descriptions: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    wall_seconds: float

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "command_count": self.command_count,
            "descriptions": list(self.descriptions),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "success": self.success,
            "node_local_batch": True,
            "wall_seconds": self.wall_seconds,
        }


@dataclass(frozen=True)
class RuntimeExecutionResult:
    execution_id: str
    backend: Dict[str, Any]
    target_slice: Dict[str, Any]
    host_runtime_boundary: Dict[str, Any]
    preflight: Dict[str, Any]
    apply_results: List[Dict[str, Any]]
    verify_results: List[Dict[str, Any]]
    ping_result: Dict[str, Any]
    rollback_results: List[Dict[str, Any]]
    rollback_verification: Dict[str, Any]
    timing: Dict[str, Any]
    outcome: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HostRuntimeMutationBlocked(RuntimeError):
    """Raised when real execution would require host-side slot mutation."""


class RuntimeCommandFailure(RuntimeError):
    """Raised when one real runtime command fails."""

    def __init__(self, result: RuntimeCommandResult):
        self.result = result
        super().__init__(
            f"{result.node_id}:{result.phase}:{result.description} failed with {result.returncode}"
        )


class NodeLocalBatchFailure(RuntimeError):
    """Raised when one node-local batch execution fails."""

    def __init__(self, result: NodeLocalBatchResult):
        self.result = result
        super().__init__(f"{result.node_id}:{result.phase} batch failed with {result.returncode}")


@dataclass(frozen=True)
class ActiveRuntimeSession:
    session_id: str
    backend: Dict[str, Any]
    target_slice: Dict[str, Any]
    host_runtime_boundary: Dict[str, Any]
    preflight: Dict[str, Any]
    apply_results: List[Dict[str, Any]]
    verify_results: List[Dict[str, Any]]
    ping_results: List[Dict[str, Any]]
    timing: Dict[str, Any]
    contexts: Tuple[DirectGatewayExperimentContext, ...]
    context_preflights: Tuple[Dict[str, Any], ...]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": dict(self.backend),
            "target_slice": dict(self.target_slice),
            "host_runtime_boundary": dict(self.host_runtime_boundary),
            "preflight": dict(self.preflight),
            "apply_results": list(self.apply_results),
            "verify_results": list(self.verify_results),
            "ping_results": list(self.ping_results),
            "timing": dict(self.timing),
        }


def _run_docker_command(
    *,
    node_id: str,
    command: Sequence[str],
    phase: str,
    description: str,
    allow_failure: bool = False,
) -> RuntimeCommandResult:
    completed = subprocess.run(
        ["docker", "exec", node_id, *command],
        capture_output=True,
        text=True,
        check=False,
    )
    result = RuntimeCommandResult(
        node_id=node_id,
        phase=phase,
        description=description,
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        allow_failure=allow_failure,
    )
    if completed.returncode != 0 and not allow_failure:
        raise RuntimeCommandFailure(result)
    return result


def _batch_shell_line(spec: RuntimeCommandSpec) -> str:
    command_line = shlex.join(spec.command)
    if spec.allow_failure:
        return f"{command_line} || true"
    return command_line


def _run_node_local_batch(batch: NodeLocalBatchSpec) -> NodeLocalBatchResult:
    started_at = time.perf_counter()
    script_lines = ["set -eu"]
    for spec in batch.command_specs:
        script_lines.append(f"printf '%s\\n' {shlex.quote(f'[{spec.phase}] {spec.description}')}")
        script_lines.append(_batch_shell_line(spec))
    completed = subprocess.run(
        ["docker", "exec", "-i", batch.node_id, "sh", "-s"],
        input="\n".join(script_lines) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    result = NodeLocalBatchResult(
        node_id=batch.node_id,
        phase=batch.phase,
        command_count=batch.command_count,
        descriptions=tuple(spec.description for spec in batch.command_specs),
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        wall_seconds=time.perf_counter() - started_at,
    )
    if completed.returncode != 0:
        raise NodeLocalBatchFailure(result)
    return result


def _group_node_local_batches(
    specs: Sequence[RuntimeCommandSpec],
) -> List[NodeLocalBatchSpec]:
    grouped: Dict[Tuple[str, str], List[RuntimeCommandSpec]] = {}
    for spec in specs:
        grouped.setdefault((spec.node_id, spec.phase), []).append(spec)
    return [
        NodeLocalBatchSpec(
            node_id=node_id,
            phase=phase,
            command_specs=tuple(grouped_specs),
        )
        for (node_id, phase), grouped_specs in grouped.items()
    ]


def _execute_node_local_batches(
    batches: Sequence[NodeLocalBatchSpec],
    *,
    max_workers: int,
) -> List[NodeLocalBatchResult]:
    if not batches:
        return []
    bounded_workers = max(1, min(int(max_workers), len(batches)))
    results: List[Tuple[int, NodeLocalBatchResult]] = []
    with ThreadPoolExecutor(max_workers=bounded_workers) as executor:
        future_by_index = {
            executor.submit(_run_node_local_batch, batch): (index, batch)
            for index, batch in enumerate(batches)
        }
        for future in as_completed(future_by_index):
            index, _batch = future_by_index[future]
            results.append((index, future.result()))
    return [result for _index, result in sorted(results, key=lambda item: item[0])]


def _batch_timing_summary(
    batch_results: Sequence[NodeLocalBatchResult],
) -> Dict[str, Any]:
    per_node_seconds: Dict[str, float] = {}
    per_phase_seconds: Dict[str, float] = {}
    for batch in batch_results:
        per_node_seconds[batch.node_id] = per_node_seconds.get(batch.node_id, 0.0) + float(
            batch.wall_seconds
        )
        per_phase_seconds[batch.phase] = per_phase_seconds.get(batch.phase, 0.0) + float(
            batch.wall_seconds
        )
    return {
        "batch_count": len(batch_results),
        "total_wall_seconds": sum(float(batch.wall_seconds) for batch in batch_results),
        "per_node_wall_seconds": {
            node_id: round(seconds, 6)
            for node_id, seconds in sorted(per_node_seconds.items())
        },
        "per_phase_wall_seconds": {
            phase: round(seconds, 6)
            for phase, seconds in sorted(per_phase_seconds.items())
        },
    }


def _context_path_summary(context: DirectGatewayExperimentContext) -> Dict[str, Any]:
    target_ids = [str(target.get("node_id")) for target in context.validation_targets]
    target_ips = [str(target.get("ip_address")) for target in context.validation_targets]
    return {
        "flow_id": context.flow_id,
        "ut_id": context.ut_id,
        "serving_satellite_id": context.serving_satellite_id,
        "selected_gateway_id": context.selected_gateway_id,
        "selected_gateway_satellite_id": context.selected_gateway_satellite_id,
        "satellite_path": list(context.satellite_path),
        "path_hop_count": len(context.satellite_path),
        "path_string": " -> ".join(
            [
                context.ut_id,
                *context.satellite_path,
                context.selected_gateway_id,
            ]
        ),
        "target_host_node_ids": target_ids,
        "target_host_ips": target_ips,
        "forward_labels": list(context.forward_labels),
        "reverse_labels": list(context.reverse_labels),
        "is_direct_gateway_path": context.is_direct_gateway_path,
    }


def _context_primary_target_matches(
    context: DirectGatewayExperimentContext,
    target_selector: str,
) -> bool:
    return (
        str(context.target_host_node_id) == target_selector
        or str(context.ping_target_ip) == target_selector
    )


def _context_validation_target_matches(
    context: DirectGatewayExperimentContext,
    target_selector: str,
) -> bool:
    return any(
        str(target.get("node_id")) == target_selector
        or str(target.get("ip_address")) == target_selector
        for target in context.validation_targets
    )


def _context_observation_target(
    context: DirectGatewayExperimentContext,
    target_selector: str,
) -> Dict[str, Any]:
    for target in context.validation_targets:
        if (
            str(target.get("node_id")) == target_selector
            or str(target.get("ip_address")) == target_selector
        ):
            return dict(target)
    return {
        "node_id": context.target_host_node_id,
        "ip_address": context.ping_target_ip,
    }


def _ping_validation_targets(
    context: DirectGatewayExperimentContext,
    *,
    phase: str,
    description: str,
) -> Tuple[List[Dict[str, Any]], float]:
    validation_targets = list(context.validation_targets) or [
        {
            "node_id": context.target_host_node_id,
            "ip_address": context.ping_target_ip,
        }
    ]
    ping_results: List[Dict[str, Any]] = []
    started_at = time.perf_counter()
    for validation_target in validation_targets:
        ping = _run_docker_command(
            node_id=context.ut_id,
            command=(
                "ping",
                "-c",
                "1",
                "-W",
                "2",
                str(validation_target["ip_address"]),
            ),
            phase=phase,
            description=description,
            allow_failure=True,
        )
        ping_results.append(
            {
                "flow_id": context.flow_id,
                "ut_id": context.ut_id,
                "traffic_scope": (
                    "default_gateway_class"
                    if str(validation_target["node_id"]) == str(context.target_host_node_id)
                    else "special_prefix_override"
                ),
                "ping_target_ip": str(validation_target["ip_address"]),
                "target_host_node_id": str(validation_target["node_id"]),
                "selected_gateway_id": context.selected_gateway_id,
                "serving_satellite_id": context.serving_satellite_id,
                "satellite_path": list(context.satellite_path),
                "path_hop_count": len(context.satellite_path),
                "path_string": " -> ".join(
                    [
                        context.ut_id,
                        *context.satellite_path,
                        context.selected_gateway_id,
                        str(validation_target["node_id"]),
                    ]
                ),
                "success": ping.returncode == 0,
                "stdout": ping.stdout,
                "stderr": ping.stderr,
                "returncode": ping.returncode,
            }
        )
    return ping_results, time.perf_counter() - started_at


def _rule_probe_command(
    *,
    source_ip: str,
    policy_table_id: int,
) -> Tuple[str, ...]:
    return (
        "sh",
        "-lc",
        (
            "ip rule show | grep -Fq "
            f"'from {source_ip} lookup {policy_table_id}'"
        ),
    )


def _iptables_probe_command(
    *,
    table_name: Optional[str],
    rule_args: Sequence[str],
) -> Tuple[str, ...]:
    table_prefix = f"-t {table_name} " if table_name is not None else ""
    joined_args = " ".join(rule_args)
    return ("sh", "-lc", f"iptables {table_prefix}-C {joined_args}")


def _sysctl_read_command(interface_name: str) -> Tuple[str, ...]:
    return ("sysctl", "-n", f"net.mpls.conf.{interface_name}.input")


def _platform_labels_read_command() -> Tuple[str, ...]:
    return ("sysctl", "-n", "net.mpls.platform_labels")


def _interface_packet_counter_command(interface_name: str) -> Tuple[str, ...]:
    return (
        "sh",
        "-lc",
        (
            f"printf '%s %s' "
            f"\"$(cat /sys/class/net/{interface_name}/statistics/rx_packets)\" "
            f"\"$(cat /sys/class/net/{interface_name}/statistics/tx_packets)\""
        ),
    )


def _as_int(value: str, *, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _default_preflight_state(context: DirectGatewayExperimentContext) -> Dict[str, Any]:
    return {
        "ut_default_route_present": False,
        "ut_default_route_stdout": "",
        "sat_source_rule_present": False,
        "sat_policy_default_present": False,
        "sat_policy_default_stdout": "",
        "sat_delivery_route_present": False,
        "sat_delivery_route_stdout": "",
        "sat_reverse_pop_present": False,
        "sat_reverse_pop_stdout": "",
        "gw_forward_pop_present": False,
        "gw_forward_pop_stdout": "",
        "gw_reverse_route_present": False,
        "gw_reverse_route_stdout": "",
        "gw_forward_rule_present": False,
        "gw_reverse_rule_present": False,
        "gw_snat_rule_present": False,
        "node_platform_labels": {
            node_id: "0"
            for node_id in context.nodes_requiring_platform_labels
        },
        "mpls_input_state": {
            f"{node_id}:{interface_name}": "0"
            for node_id, interface_name in context.mpls_input_interfaces
        },
        "forward_lfib_state": {},
        "reverse_lfib_state": {},
    }


def _route_show_command(*parts: str) -> Tuple[str, ...]:
    return ("ip", "route", "show", *parts)


def _mpls_route_show_command(label: int) -> Tuple[str, ...]:
    return ("sh", "-lc", f"ip -f mpls route show {label}")


def _capture_preflight_state(context: DirectGatewayExperimentContext) -> Dict[str, Any]:
    ut_default_route = _run_docker_command(
        node_id=context.ut_id,
        command=_route_show_command("default"),
        phase="preflight",
        description="capture UT default route",
        allow_failure=True,
    )
    sat_source_rule = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_rule_probe_command(
            source_ip=context.ut_access_ip,
            policy_table_id=context.policy_table_id,
        ),
        phase="preflight",
        description="probe serving-satellite source rule",
        allow_failure=True,
    )
    sat_policy_default = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_route_show_command("table", str(context.policy_table_id), "default"),
        phase="preflight",
        description="capture serving-satellite policy-table default",
        allow_failure=True,
    )
    sat_delivery_route = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_route_show_command(context.return_target_prefix),
        phase="preflight",
        description="capture serving-satellite UT delivery route",
        allow_failure=True,
    )
    sat_reverse_pop = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_mpls_route_show_command(context.reverse_labels[-1]),
        phase="preflight",
        description="capture serving-satellite reverse pop label",
        allow_failure=True,
    )
    gw_forward_pop = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_mpls_route_show_command(context.forward_labels[-1]),
        phase="preflight",
        description="capture gateway forward pop label",
        allow_failure=True,
    )
    gw_reverse_route = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_route_show_command(context.return_target_prefix),
        phase="preflight",
        description="capture gateway reverse ingress route",
        allow_failure=True,
    )
    node_platform_labels: Dict[str, str] = {}
    for node_id in context.nodes_requiring_platform_labels:
        result = _run_docker_command(
            node_id=node_id,
            command=_platform_labels_read_command(),
            phase="preflight",
            description=f"read {node_id} MPLS platform labels",
        )
        node_platform_labels[node_id] = result.stdout
    mpls_input_state: Dict[str, str] = {}
    for node_id, interface_name in context.mpls_input_interfaces:
        result = _run_docker_command(
            node_id=node_id,
            command=_sysctl_read_command(interface_name),
            phase="preflight",
            description=f"read MPLS input state on {node_id}:{interface_name}",
        )
        mpls_input_state[f"{node_id}:{interface_name}"] = result.stdout
    forward_lfib_state: Dict[str, str] = {}
    for index, segment in enumerate(context.forward_segments[1:], start=1):
        result = _run_docker_command(
            node_id=segment.source_node_id,
            command=_mpls_route_show_command(context.forward_labels[index - 1]),
            phase="preflight",
            description=(
                f"capture forward LFIB on {segment.source_node_id} "
                f"for label {context.forward_labels[index - 1]}"
            ),
            allow_failure=True,
        )
        forward_lfib_state[f"{segment.source_node_id}:{context.forward_labels[index - 1]}"] = (
            result.stdout
        )
    reverse_lfib_state: Dict[str, str] = {}
    for index, segment in enumerate(context.reverse_segments[1:], start=1):
        result = _run_docker_command(
            node_id=segment.source_node_id,
            command=_mpls_route_show_command(context.reverse_labels[index - 1]),
            phase="preflight",
            description=(
                f"capture reverse LFIB on {segment.source_node_id} "
                f"for label {context.reverse_labels[index - 1]}"
            ),
            allow_failure=True,
        )
        reverse_lfib_state[f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"] = (
            result.stdout
        )
    forward_rule_args = (
        "FORWARD",
        "-i",
        context.gateway_access_interface,
        "-o",
        context.gateway_nat_interface,
        "-s",
        f"{context.ut_access_ip}/32",
        "-j",
        "ACCEPT",
    )
    reverse_rule_args = (
        "FORWARD",
        "-i",
        context.gateway_nat_interface,
        "-o",
        context.gateway_access_interface,
        "-d",
        f"{context.ut_access_ip}/32",
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )
    snat_rule_args = (
        "POSTROUTING",
        "-s",
        f"{context.ut_access_ip}/32",
        "-o",
        context.gateway_nat_interface,
        "-j",
        "SNAT",
        "--to-source",
        context.gateway_nat_source_ip,
    )
    gw_forward_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name=None, rule_args=forward_rule_args),
        phase="preflight",
        description="probe gateway forward filter rule",
        allow_failure=True,
    )
    gw_reverse_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name=None, rule_args=reverse_rule_args),
        phase="preflight",
        description="probe gateway reverse filter rule",
        allow_failure=True,
    )
    gw_snat_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name="nat", rule_args=snat_rule_args),
        phase="preflight",
        description="probe gateway SNAT rule",
        allow_failure=True,
    )
    return {
        "ut_default_route_present": bool(ut_default_route.stdout),
        "ut_default_route_stdout": ut_default_route.stdout,
        "sat_source_rule_present": sat_source_rule.returncode == 0,
        "sat_policy_default_present": bool(sat_policy_default.stdout),
        "sat_policy_default_stdout": sat_policy_default.stdout,
        "sat_delivery_route_present": bool(sat_delivery_route.stdout),
        "sat_delivery_route_stdout": sat_delivery_route.stdout,
        "sat_reverse_pop_present": bool(sat_reverse_pop.stdout),
        "sat_reverse_pop_stdout": sat_reverse_pop.stdout,
        "gw_forward_pop_present": bool(gw_forward_pop.stdout),
        "gw_forward_pop_stdout": gw_forward_pop.stdout,
        "gw_reverse_route_present": bool(gw_reverse_route.stdout),
        "gw_reverse_route_stdout": gw_reverse_route.stdout,
        "gw_forward_rule_present": gw_forward_rule.returncode == 0,
        "gw_reverse_rule_present": gw_reverse_rule.returncode == 0,
        "gw_snat_rule_present": gw_snat_rule.returncode == 0,
        "node_platform_labels": node_platform_labels,
        "mpls_input_state": mpls_input_state,
        "forward_lfib_state": forward_lfib_state,
        "reverse_lfib_state": reverse_lfib_state,
    }


def _validate_preflight_state(
    context: DirectGatewayExperimentContext,
    preflight: Dict[str, Any],
) -> None:
    conflicts: List[str] = []
    if preflight["ut_default_route_present"] and (
        context.ut_access_peer_ip not in preflight["ut_default_route_stdout"]
        or context.ut_access_interface not in preflight["ut_default_route_stdout"]
    ):
        conflicts.append("ut_default_route_conflict")
    if preflight["sat_policy_default_present"] and (
        str(context.forward_labels[0]) not in preflight["sat_policy_default_stdout"]
        or context.forward_segments[0].source_interface not in preflight["sat_policy_default_stdout"]
    ):
        conflicts.append("serving_satellite_policy_default_conflict")
    if preflight["sat_delivery_route_present"] and (
        context.ut_access_ip not in preflight["sat_delivery_route_stdout"]
        or context.ut_access_interface not in preflight["sat_delivery_route_stdout"]
    ):
        conflicts.append("serving_satellite_delivery_route_conflict")
    if preflight["gw_reverse_route_present"] and (
        str(context.reverse_labels[0]) not in preflight["gw_reverse_route_stdout"]
        or context.gateway_access_interface not in preflight["gw_reverse_route_stdout"]
    ):
        conflicts.append("gateway_reverse_route_conflict")
    for index, segment in enumerate(context.forward_segments[1:], start=1):
        state_key = f"{segment.source_node_id}:{context.forward_labels[index - 1]}"
        output = str(preflight.get("forward_lfib_state", {}).get(state_key, ""))
        if output and (
            str(context.forward_labels[index]) not in output
            or segment.source_interface not in output
        ):
            conflicts.append(f"forward_lfib_conflict:{state_key}")
    for index, segment in enumerate(context.reverse_segments[1:], start=1):
        state_key = f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"
        output = str(preflight.get("reverse_lfib_state", {}).get(state_key, ""))
        if output and (
            str(context.reverse_labels[index]) not in output
            or segment.source_interface not in output
        ):
            conflicts.append(f"reverse_lfib_conflict:{state_key}")
    if conflicts:
        raise RuntimeError(
            "real runtime preflight found conflicting existing state: "
            + ", ".join(conflicts)
        )


def _realization_contexts(
    *,
    topology_path: Path,
    topology_snapshot: TopologySnapshot,
    runtime_update_plan: RuntimeUpdatePlan,
    target_ut_ids: Optional[Sequence[str]] = None,
) -> List[DirectGatewayExperimentContext]:
    topology = _load_json(topology_path)
    gateways = _topology_entry_by_id(topology.get("gateways", []), "gateway_id")
    contexts: List[DirectGatewayExperimentContext] = []
    for flow_context in _flow_contexts_from_plan(
        runtime_update_plan,
        target_ut_ids=target_ut_ids,
    ):
        satellite_path = [str(node_id) for node_id in flow_context.get("satellite_path", [])]
        forward_link_ids = [str(link_id) for link_id in flow_context.get("forward_link_ids", [])]
        reverse_link_ids = [str(link_id) for link_id in flow_context.get("reverse_link_ids", [])]
        forward_labels = tuple(int(label) for label in flow_context.get("forward_labels", []))
        reverse_labels = tuple(int(label) for label in flow_context.get("reverse_labels", []))
        serving_satellite_id = str(flow_context["serving_satellite_id"])
        selected_gateway_id = str(flow_context["selected_gateway_id"])
        selected_gateway_satellite_id = str(flow_context["selected_gateway_satellite_id"])
        if not satellite_path or satellite_path[0] != serving_satellite_id:
            raise RuntimeError(f"invalid satellite path for {flow_context.get('flow_id')}")
        if satellite_path[-1] != selected_gateway_satellite_id:
            raise RuntimeError(
                "selected gateway satellite does not match the final satellite path node"
            )
        forward_nodes = [*satellite_path, selected_gateway_id]
        reverse_nodes = [selected_gateway_id, *reversed(satellite_path)]
        if len(forward_link_ids) != len(forward_nodes) - 1:
            raise RuntimeError(f"unexpected forward link count for {flow_context.get('flow_id')}")
        if len(reverse_link_ids) != len(reverse_nodes) - 1:
            raise RuntimeError(f"unexpected reverse link count for {flow_context.get('flow_id')}")
        if len(forward_labels) != len(forward_link_ids):
            raise RuntimeError(f"unexpected forward label count for {flow_context.get('flow_id')}")
        if len(reverse_labels) != len(reverse_link_ids):
            raise RuntimeError(f"unexpected reverse label count for {flow_context.get('flow_id')}")

        ut_link = _active_link_by_id(topology_snapshot, str(flow_context["ut_access_link_id"]))
        ut_endpoint = _endpoint_for_node(ut_link, str(flow_context["ut_id"]))
        sat_ut_endpoint = _endpoint_for_node(ut_link, serving_satellite_id)
        forward_segments: List[MplsPathSegment] = []
        for index, link_id in enumerate(forward_link_ids):
            link = _active_link_by_id(topology_snapshot, link_id)
            source_node_id = forward_nodes[index]
            destination_node_id = forward_nodes[index + 1]
            source_endpoint = _endpoint_for_node(link, source_node_id)
            destination_endpoint = _endpoint_for_node(link, destination_node_id)
            forward_segments.append(
                MplsPathSegment(
                    link_id=link_id,
                    source_node_id=source_node_id,
                    source_node_type=str(source_endpoint.get("node_type")),
                    source_interface=str(source_endpoint["interface_name"]),
                    source_ip=_strip_prefix(str(source_endpoint["ip_address"])),
                    destination_node_id=destination_node_id,
                    destination_node_type=str(destination_endpoint.get("node_type")),
                    destination_interface=str(destination_endpoint["interface_name"]),
                    destination_ip=_strip_prefix(str(destination_endpoint["ip_address"])),
                    label=forward_labels[index],
                )
            )
        reverse_segments: List[MplsPathSegment] = []
        for index, link_id in enumerate(reverse_link_ids):
            link = _active_link_by_id(topology_snapshot, link_id)
            source_node_id = reverse_nodes[index]
            destination_node_id = reverse_nodes[index + 1]
            source_endpoint = _endpoint_for_node(link, source_node_id)
            destination_endpoint = _endpoint_for_node(link, destination_node_id)
            reverse_segments.append(
                MplsPathSegment(
                    link_id=link_id,
                    source_node_id=source_node_id,
                    source_node_type=str(source_endpoint.get("node_type")),
                    source_interface=str(source_endpoint["interface_name"]),
                    source_ip=_strip_prefix(str(source_endpoint["ip_address"])),
                    destination_node_id=destination_node_id,
                    destination_node_type=str(destination_endpoint.get("node_type")),
                    destination_interface=str(destination_endpoint["interface_name"]),
                    destination_ip=_strip_prefix(str(destination_endpoint["ip_address"])),
                    label=reverse_labels[index],
                )
            )
        gateway_entry = gateways[selected_gateway_id]
        bootstrap_endpoint = dict(
            gateway_entry.get("bootstrap_network", {}).get("endpoint", {})
        )
        contexts.append(
            DirectGatewayExperimentContext(
                flow_id=str(flow_context["flow_id"]),
                ut_id=str(flow_context["ut_id"]),
                serving_satellite_id=serving_satellite_id,
                selected_gateway_id=selected_gateway_id,
                selected_gateway_satellite_id=selected_gateway_satellite_id,
                ping_target_ip=str(flow_context["ping_target_ip"]),
                target_host_node_id=str(flow_context["target_host_node_id"]),
                policy_table_id=int(flow_context["policy_table_id"]),
                policy_rule_priority=int(flow_context["policy_rule_priority"]),
                satellite_path=tuple(satellite_path),
                forward_labels=forward_labels,
                reverse_labels=reverse_labels,
                ut_access_interface=str(ut_endpoint["interface_name"]),
                ut_access_ip=_strip_prefix(str(ut_endpoint["ip_address"])),
                ut_access_peer_ip=_strip_prefix(str(sat_ut_endpoint["ip_address"])),
                gateway_access_interface=str(forward_segments[-1].destination_interface),
                gateway_access_ip=str(forward_segments[-1].destination_ip),
                gateway_access_peer_ip=str(reverse_segments[0].destination_ip),
                gateway_nat_interface=str(
                    gateway_entry.get("internet_uplink", {}).get("network_name")
                ),
                gateway_nat_source_ip=_strip_prefix(str(bootstrap_endpoint.get("ip_address"))),
                forward_segments=tuple(forward_segments),
                reverse_segments=tuple(reverse_segments),
                validation_targets=tuple(
                    {
                        "node_id": str(target.get("node_id")),
                        "ip_address": str(target.get("ip_address")),
                    }
                    for target in flow_context.get("validation_targets", [])
                    if target.get("node_id") and target.get("ip_address")
                ),
            )
        )
    return contexts


class MinimalHostRuntimeManager:
    """Separate host/runtime boundary kept only as a transitional guardrail."""

    manager_name = "minimal_host_runtime_manager"

    def validate_real_execution(self, network_mutation_plan: NetworkMutationPlan) -> Dict[str, Any]:
        if len(network_mutation_plan.operations) != 0:
            raise HostRuntimeMutationBlocked(
                "real non-legacy runtime execution currently requires zero host-side "
                "NetworkMutationPlan operations"
            )
        return {
            "manager_name": self.manager_name,
            "execution_mode": "no_host_mutation_required",
            "mutation_id": network_mutation_plan.mutation_id,
            "operation_count": len(network_mutation_plan.operations),
            "future_runtime_target": "host_runtime_manager_daemon",
            "concurrency_model": "serial_bounded",
        }


class MinimalRealLinuxMplsInstaller:
    """Real-but-minimal Linux/MPLS installer using a transitional direct-container shim."""

    backend_name = "minimal_real_linux_mpls_installer"
    backend_kind = "nonlegacy_backend_specific_runtime_target"
    backend_version = "0.1.0"

    def _observation_endpoints(
        self,
        context: DirectGatewayExperimentContext,
    ) -> List[Tuple[str, str, str]]:
        endpoints: List[Tuple[str, str, str]] = [
            (context.ut_id, context.ut_access_interface, "ut_access"),
            (
                context.serving_satellite_id,
                context.forward_segments[0].destination_interface,
                "serving_satellite_ingress",
            ),
        ]
        for index, segment in enumerate(context.forward_segments, start=1):
            endpoints.append(
                (
                    segment.source_node_id,
                    segment.source_interface,
                    f"forward_tx_hop_{index}",
                )
            )
            endpoints.append(
                (
                    segment.destination_node_id,
                    segment.destination_interface,
                    f"forward_rx_hop_{index}",
                )
            )
        endpoints.append(
            (
                context.selected_gateway_id,
                context.gateway_nat_interface,
                "gateway_nat_egress",
            )
        )
        deduped: List[Tuple[str, str, str]] = []
        seen: set[Tuple[str, str, str]] = set()
        for endpoint in endpoints:
            if endpoint in seen:
                continue
            seen.add(endpoint)
            deduped.append(endpoint)
        return deduped

    def _capture_interface_counters(
        self,
        endpoints: Sequence[Tuple[str, str, str]],
    ) -> Dict[str, Dict[str, Any]]:
        counters: Dict[str, Dict[str, Any]] = {}
        for node_id, interface_name, endpoint_role in endpoints:
            result = _run_docker_command(
                node_id=node_id,
                command=_interface_packet_counter_command(interface_name),
                phase="observe",
                description=f"read packet counters on {node_id}:{interface_name}",
            )
            rx_packets, tx_packets = (result.stdout.split() + ["0", "0"])[:2]
            counters[f"{node_id}:{interface_name}"] = {
                "node_id": node_id,
                "interface_name": interface_name,
                "endpoint_role": endpoint_role,
                "rx_packets": _as_int(rx_packets),
                "tx_packets": _as_int(tx_packets),
            }
        return counters

    def observe_packet_path(
        self,
        session: ActiveRuntimeSession,
        *,
        observe_selectors: Sequence[str],
    ) -> Dict[str, Any]:
        normalized_selectors = [str(selector).strip() for selector in observe_selectors if str(selector).strip()]
        if not normalized_selectors:
            return {
                "enabled": False,
                "observations": [],
            }
        observations: List[Dict[str, Any]] = []
        for selector in normalized_selectors:
            if ":" not in selector:
                raise RuntimeError(
                    f"invalid observe selector {selector!r}; expected ut_id:target_host_node_id_or_ip"
                )
            ut_id, target_selector = selector.split(":", 1)
            ut_contexts = [
                context
                for context in session.contexts
                if context.ut_id == ut_id
            ]
            matching_context = next(
                (
                    context
                    for context in ut_contexts
                    if _context_primary_target_matches(context, target_selector)
                ),
                None,
            )
            if matching_context is None:
                matching_context = next(
                    (
                        context
                        for context in ut_contexts
                        if _context_validation_target_matches(context, target_selector)
                    ),
                    None,
                )
            if matching_context is None:
                observations.append(
                    {
                        "selector": selector,
                        "matched": False,
                        "reason": "no_matching_context_in_active_slice",
                    }
                )
                continue
            target = _context_observation_target(matching_context, target_selector)
            endpoints = self._observation_endpoints(matching_context)
            before_counters = self._capture_interface_counters(endpoints)
            ping_results, wall_seconds = _ping_validation_targets(
                matching_context,
                phase="observe",
                description="observe packet path with targeted ping",
            )
            after_counters = self._capture_interface_counters(endpoints)
            counter_deltas: List[Dict[str, Any]] = []
            for endpoint_key, before in before_counters.items():
                after = after_counters[endpoint_key]
                delta = {
                    "node_id": before["node_id"],
                    "interface_name": before["interface_name"],
                    "endpoint_role": before["endpoint_role"],
                    "rx_packets_before": before["rx_packets"],
                    "tx_packets_before": before["tx_packets"],
                    "rx_packets_after": after["rx_packets"],
                    "tx_packets_after": after["tx_packets"],
                    "rx_packets_delta": after["rx_packets"] - before["rx_packets"],
                    "tx_packets_delta": after["tx_packets"] - before["tx_packets"],
                }
                counter_deltas.append(delta)
            observations.append(
                {
                    "selector": selector,
                    "matched": True,
                    "flow_id": matching_context.flow_id,
                    "path_string": " -> ".join(
                        [
                            matching_context.ut_id,
                            *matching_context.satellite_path,
                            matching_context.selected_gateway_id,
                            str(target.get("node_id")),
                        ]
                    ),
                    "target_host_node_id": str(target.get("node_id")),
                    "target_ip_address": str(target.get("ip_address")),
                    "selected_gateway_id": matching_context.selected_gateway_id,
                    "serving_satellite_id": matching_context.serving_satellite_id,
                    "satellite_path": list(matching_context.satellite_path),
                    "path_hop_count": len(matching_context.satellite_path),
                    "ping_wall_seconds": wall_seconds,
                    "ping_results": [
                        result
                        for result in ping_results
                        if str(result.get("target_host_node_id")) == str(target.get("node_id"))
                    ],
                    "interface_counter_deltas": counter_deltas,
                }
            )
        return {
            "enabled": True,
            "observations": observations,
        }

    def assess_realization(
        self,
        *,
        runtime_update_plan: RuntimeUpdatePlan,
        target_ut_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        support = _realization_support_summary(
            runtime_update_plan,
            target_ut_ids=target_ut_ids,
        )
        return {
            "backend_name": self.backend_name,
            "supported": support["supported"],
            "total_flow_count": support["total_flow_count"],
            "direct_flow_count": len(support["direct_flow_ids"]),
            "multi_hop_flow_count": len(support["multi_hop_flow_ids"]),
            "direct_flow_ids": list(support["direct_flow_ids"]),
            "multi_hop_flow_ids": list(support["multi_hop_flow_ids"]),
            "blockers": list(support["blockers"]),
        }

    def _apply_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        required_platform_labels = max(
            1000,
            max(context.forward_labels) + 1,
            max(context.reverse_labels) + 1,
        )
        for node_id in context.nodes_requiring_platform_labels:
            if _as_int(preflight["node_platform_labels"].get(node_id, "0")) < required_platform_labels:
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="apply",
                        description=f"set {node_id} MPLS platform labels",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.platform_labels={required_platform_labels}",
                        ),
                    )
                )
        if not preflight["sat_source_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite source rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "ip rule show | grep -Fq "
                            f"'from {context.ut_access_ip} lookup {context.policy_table_id}' "
                            "|| ip rule add "
                            f"pref {context.policy_rule_priority} "
                            f"from {context.ut_access_ip}/32 "
                            f"table {context.policy_table_id}"
                        ),
                    ),
                )
            )
        for node_id, interface_name in context.mpls_input_interfaces:
            state_key = f"{node_id}:{interface_name}"
            if str(preflight["mpls_input_state"].get(state_key, "0")).strip() != "1":
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="apply",
                        description=f"enable MPLS input on {node_id}:{interface_name}",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.conf.{interface_name}.input=1",
                        ),
                    )
                )
        first_forward_segment = context.forward_segments[0]
        specs.extend(
            [
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite policy-table default route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        "default",
                        "encap",
                        "mpls",
                        str(context.forward_labels[0]),
                        "via",
                        "inet",
                        first_forward_segment.destination_ip,
                        "dev",
                        first_forward_segment.source_interface,
                        "table",
                        str(context.policy_table_id),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite UT delivery route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        context.return_target_prefix,
                        "via",
                        context.ut_access_ip,
                        "dev",
                        context.ut_access_interface,
                    ),
                ),
            ]
        )
        for index, segment in enumerate(context.forward_segments[1:], start=1):
            specs.append(
                RuntimeCommandSpec(
                    node_id=segment.source_node_id,
                    phase="apply",
                    description=(
                        f"install forward LFIB on {segment.source_node_id} "
                        f"for label {context.forward_labels[index - 1]}"
                    ),
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "replace",
                        str(context.forward_labels[index - 1]),
                        "as",
                        str(context.forward_labels[index]),
                        "via",
                        "inet",
                        segment.destination_ip,
                        "dev",
                        segment.source_interface,
                    ),
                )
            )
        specs.extend(
            [
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway forward pop entry",
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "replace",
                        str(context.forward_labels[-1]),
                        "dev",
                        "lo",
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway reverse ingress route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        context.return_target_prefix,
                        "encap",
                        "mpls",
                        str(context.reverse_labels[0]),
                        "via",
                        "inet",
                        context.reverse_segments[0].destination_ip,
                        "dev",
                        context.reverse_segments[0].source_interface,
                    ),
                ),
            ]
        )
        for index, segment in enumerate(context.reverse_segments[1:], start=1):
            specs.append(
                RuntimeCommandSpec(
                    node_id=segment.source_node_id,
                    phase="apply",
                    description=(
                        f"install reverse LFIB on {segment.source_node_id} "
                        f"for label {context.reverse_labels[index - 1]}"
                    ),
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "replace",
                        str(context.reverse_labels[index - 1]),
                        "as",
                        str(context.reverse_labels[index]),
                        "via",
                        "inet",
                        segment.destination_ip,
                        "dev",
                        segment.source_interface,
                    ),
                )
            )
        specs.extend(
            [
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite reverse pop entry",
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "replace",
                        str(context.reverse_labels[-1]),
                        "dev",
                        "lo",
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway forward filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "iptables -C FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT 2>/dev/null || "
                            "iptables -A FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT"
                        ),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway reverse filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "iptables -C FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT "
                            "2>/dev/null || "
                            "iptables -A FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                        ),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway SNAT rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "iptables -t nat -C POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip} "
                            "2>/dev/null || "
                            "iptables -t nat -A POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip}"
                        ),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.ut_id,
                    phase="apply",
                    description="install UT default route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        "default",
                        "via",
                        context.ut_access_peer_ip,
                        "dev",
                        context.ut_access_interface,
                        "src",
                        context.ut_access_ip,
                    ),
                ),
            ]
        )
        return specs

    def _verify_specs(self, context: DirectGatewayExperimentContext) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = [
            RuntimeCommandSpec(
                node_id=context.ut_id,
                phase="verify",
                description="show UT default route",
                command=_route_show_command("default"),
            ),
            RuntimeCommandSpec(
                node_id=context.serving_satellite_id,
                phase="verify",
                description="show serving-satellite source-policy rules",
                command=("ip", "rule", "show"),
            ),
            RuntimeCommandSpec(
                node_id=context.serving_satellite_id,
                phase="verify",
                description="show serving-satellite policy table",
                command=_route_show_command("table", str(context.policy_table_id)),
            ),
            RuntimeCommandSpec(
                node_id=context.serving_satellite_id,
                phase="verify",
                description="show serving-satellite reverse pop LFIB",
                command=("ip", "-f", "mpls", "route", "show"),
            ),
            RuntimeCommandSpec(
                node_id=context.selected_gateway_id,
                phase="verify",
                description="show gateway LFIB",
                command=("ip", "-f", "mpls", "route", "show"),
            ),
            RuntimeCommandSpec(
                node_id=context.selected_gateway_id,
                phase="verify",
                description="show gateway NAT table",
                command=("sh", "-lc", "iptables -t nat -S"),
            ),
        ]
        for node_id in dict.fromkeys(
            list(context.satellite_path) + [context.selected_gateway_id]
        ):
            specs.append(
                RuntimeCommandSpec(
                    node_id=node_id,
                    phase="verify",
                    description=f"show MPLS LFIB on {node_id}",
                    command=("ip", "-f", "mpls", "route", "show"),
                )
            )
        return specs

    def _rollback_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        required_platform_labels = max(
            1000,
            max(context.forward_labels) + 1,
            max(context.reverse_labels) + 1,
        )
        if not preflight["ut_default_route_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.ut_id,
                    phase="rollback",
                    description="delete UT default route",
                    command=("ip", "route", "del", "default"),
                    allow_failure=True,
                )
            )
        if not preflight["sat_policy_default_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite policy-table default route",
                    command=(
                        "ip",
                        "route",
                        "del",
                        "default",
                        "table",
                        str(context.policy_table_id),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["gw_snat_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway SNAT rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "iptables -t nat -D POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip}"
                        ),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["gw_forward_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway forward filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "iptables -D FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT"
                        ),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["gw_reverse_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway reverse filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "iptables -D FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                        ),
                    ),
                    allow_failure=True,
                )
            )
        for index, segment in enumerate(context.forward_segments[1:], start=1):
            state_key = f"{segment.source_node_id}:{context.forward_labels[index - 1]}"
            if not str(preflight.get("forward_lfib_state", {}).get(state_key, "")).strip():
                specs.append(
                    RuntimeCommandSpec(
                        node_id=segment.source_node_id,
                        phase="rollback",
                        description=(
                            f"delete forward LFIB on {segment.source_node_id} "
                            f"for label {context.forward_labels[index - 1]}"
                        ),
                        command=(
                            "ip",
                            "-f",
                            "mpls",
                            "route",
                            "del",
                            str(context.forward_labels[index - 1]),
                        ),
                        allow_failure=True,
                    )
                )
        if not preflight["gw_forward_pop_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway forward pop entry",
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "del",
                        str(context.forward_labels[-1]),
                    ),
                    allow_failure=True,
                )
            )
        for index, segment in enumerate(context.reverse_segments[1:], start=1):
            state_key = f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"
            if not str(preflight.get("reverse_lfib_state", {}).get(state_key, "")).strip():
                specs.append(
                    RuntimeCommandSpec(
                        node_id=segment.source_node_id,
                        phase="rollback",
                        description=(
                            f"delete reverse LFIB on {segment.source_node_id} "
                            f"for label {context.reverse_labels[index - 1]}"
                        ),
                        command=(
                            "ip",
                            "-f",
                            "mpls",
                            "route",
                            "del",
                            str(context.reverse_labels[index - 1]),
                        ),
                        allow_failure=True,
                    )
                )
        if not preflight["gw_reverse_route_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway reverse ingress route",
                    command=("ip", "route", "del", context.return_target_prefix),
                    allow_failure=True,
                )
            )
        if not preflight["sat_reverse_pop_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite reverse pop entry",
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "del",
                        str(context.reverse_labels[-1]),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["sat_delivery_route_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite UT delivery route",
                    command=("ip", "route", "del", context.return_target_prefix),
                    allow_failure=True,
                )
            )
        if not preflight["sat_source_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite source rule",
                    command=(
                        "ip",
                        "rule",
                        "del",
                        "pref",
                        str(context.policy_rule_priority),
                        "from",
                        f"{context.ut_access_ip}/32",
                        "table",
                        str(context.policy_table_id),
                    ),
                    allow_failure=True,
                )
            )
        for node_id, interface_name in sorted(context.mpls_input_interfaces):
            state_key = f"{node_id}:{interface_name}"
            previous_value = str(preflight["mpls_input_state"].get(state_key, "0")).strip() or "0"
            if previous_value != "1":
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="rollback",
                        description=f"restore MPLS input state on {node_id}:{interface_name}",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.conf.{interface_name}.input={previous_value}",
                        ),
                        allow_failure=True,
                    )
                )
        for node_id in sorted(context.nodes_requiring_platform_labels):
            previous_value = str(preflight["node_platform_labels"].get(node_id, "0")).strip() or "0"
            if _as_int(previous_value) < required_platform_labels:
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="rollback",
                        description=f"restore {node_id} MPLS platform labels",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.platform_labels={previous_value}",
                        ),
                        allow_failure=True,
                    )
                )
        return specs

    def _rollback_verification(
        self,
        context: DirectGatewayExperimentContext,
    ) -> Dict[str, Any]:
        ut_default = _run_docker_command(
            node_id=context.ut_id,
            command=_route_show_command("default"),
            phase="rollback_verify",
            description="verify UT default route cleanup",
            allow_failure=True,
        )
        sat_policy_default = _run_docker_command(
            node_id=context.serving_satellite_id,
            command=_route_show_command("table", str(context.policy_table_id), "default"),
            phase="rollback_verify",
            description="verify serving-satellite policy default cleanup",
            allow_failure=True,
        )
        gw_forward_pop = _run_docker_command(
            node_id=context.selected_gateway_id,
            command=_mpls_route_show_command(context.forward_labels[-1]),
            phase="rollback_verify",
            description="verify gateway forward pop cleanup",
            allow_failure=True,
        )
        transit_forward_state: Dict[str, bool] = {}
        for index, segment in enumerate(context.forward_segments[1:], start=1):
            result = _run_docker_command(
                node_id=segment.source_node_id,
                command=_mpls_route_show_command(context.forward_labels[index - 1]),
                phase="rollback_verify",
                description=(
                    f"verify forward LFIB cleanup on {segment.source_node_id} "
                    f"for label {context.forward_labels[index - 1]}"
                ),
                allow_failure=True,
            )
            transit_forward_state[f"{segment.source_node_id}:{context.forward_labels[index - 1]}"] = bool(
                result.stdout
            )
        transit_reverse_state: Dict[str, bool] = {}
        for index, segment in enumerate(context.reverse_segments[1:], start=1):
            result = _run_docker_command(
                node_id=segment.source_node_id,
                command=_mpls_route_show_command(context.reverse_labels[index - 1]),
                phase="rollback_verify",
                description=(
                    f"verify reverse LFIB cleanup on {segment.source_node_id} "
                    f"for label {context.reverse_labels[index - 1]}"
                ),
                allow_failure=True,
            )
            transit_reverse_state[f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"] = bool(
                result.stdout
            )
        return {
            "ut_default_route_present": bool(ut_default.stdout),
            "sat_policy_default_present": bool(sat_policy_default.stdout),
            "gw_forward_pop_present": bool(gw_forward_pop.stdout),
            "transit_forward_entries_present": transit_forward_state,
            "transit_reverse_entries_present": transit_reverse_state,
            "cleanup_success": not bool(ut_default.stdout)
            and not bool(sat_policy_default.stdout)
            and not bool(gw_forward_pop.stdout)
            and not any(transit_forward_state.values())
            and not any(transit_reverse_state.values()),
        }

    def apply_and_validate(
        self,
        *,
        topology_path: Path,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
        distribution_batch: DistributedEnvelopeBatch,
        require_preview_validation: bool = True,
        target_ut_ids: Optional[Sequence[str]] = None,
        host_runtime_summary: Optional[Dict[str, Any]] = None,
        max_node_workers: int = 2,
        runtime_mode: str = "correctness",
    ) -> ActiveRuntimeSession:
        session_started_at = time.perf_counter()
        preview_targets = sorted(distribution_batch.node_delivery_views)
        realization_support = self.assess_realization(
            runtime_update_plan=runtime_update_plan,
            target_ut_ids=target_ut_ids,
        )
        if realization_support["supported"] is not True:
            raise RuntimeError(
                "minimal real runtime path is not available for this slice: "
                + ", ".join(realization_support["blockers"])
            )
        contexts = tuple(
            _realization_contexts(
                topology_path=topology_path,
                topology_snapshot=topology_snapshot,
                runtime_update_plan=runtime_update_plan,
                target_ut_ids=target_ut_ids,
            )
        )
        if require_preview_validation:
            required_node_ids = {
                node_id
                for context in contexts
                for node_id in (
                    context.ut_id,
                    context.selected_gateway_id,
                    *context.satellite_path,
                )
            }
            for node_id in sorted(required_node_ids):
                if node_id not in preview_targets:
                    raise RuntimeError(
                        f"preview distribution is missing required target node {node_id}"
                    )

        apply_results: List[Dict[str, Any]] = []
        verify_results: List[Dict[str, Any]] = []
        ping_results: List[Dict[str, Any]] = []
        preflight_results: List[Dict[str, Any]] = []
        context_preflights: List[Dict[str, Any]] = []
        apply_batch_plan: List[Dict[str, Any]] = []
        verify_batch_plan: List[Dict[str, Any]] = []
        apply_batch_results: List[NodeLocalBatchResult] = []
        verify_batch_results: List[NodeLocalBatchResult] = []
        failure_reason: Optional[str] = None
        phase_timing: Dict[str, float] = {
            "preflight_wall_seconds": 0.0,
            "apply_wall_seconds": 0.0,
            "verify_wall_seconds": 0.0,
            "ping_wall_seconds": 0.0,
        }
        try:
            preflight_started_at = time.perf_counter()
            for context in contexts:
                if runtime_mode == "fast-demo":
                    preflight = _default_preflight_state(context)
                else:
                    preflight = _capture_preflight_state(context)
                    _validate_preflight_state(context, preflight)
                preflight_results.append(
                    {
                        "ut_id": context.ut_id,
                        "runtime_mode": runtime_mode,
                        **preflight,
                    }
                )
                context_preflights.append(preflight)
            phase_timing["preflight_wall_seconds"] = time.perf_counter() - preflight_started_at

            apply_specs = [
                spec
                for context, preflight in zip(contexts, context_preflights)
                for spec in self._apply_specs(context, preflight)
            ]
            apply_batches = _group_node_local_batches(apply_specs)
            apply_batch_plan = [batch.to_dict() for batch in apply_batches]
            apply_started_at = time.perf_counter()
            apply_batch_results = _execute_node_local_batches(
                apply_batches,
                max_workers=max_node_workers,
            )
            phase_timing["apply_wall_seconds"] = time.perf_counter() - apply_started_at
            apply_results = [batch.to_dict() for batch in apply_batch_results]

            if runtime_mode == "fast-demo":
                verify_specs = []
                verify_batches = []
                verify_batch_plan = []
                verify_batch_results = []
                verify_results = []
                phase_timing["verify_wall_seconds"] = 0.0
            else:
                verify_specs = [
                    spec
                    for context in contexts
                    for spec in self._verify_specs(context)
                ]
                verify_batches = _group_node_local_batches(verify_specs)
                verify_batch_plan = [batch.to_dict() for batch in verify_batches]
                verify_started_at = time.perf_counter()
                verify_batch_results = _execute_node_local_batches(
                    verify_batches,
                    max_workers=max_node_workers,
                )
                phase_timing["verify_wall_seconds"] = time.perf_counter() - verify_started_at
                verify_results = [batch.to_dict() for batch in verify_batch_results]

            for context in contexts:
                context_ping_results, ping_seconds = _ping_validation_targets(
                    context,
                    phase="verify",
                    description="ping Mini Internet host from UT",
                )
                phase_timing["ping_wall_seconds"] += ping_seconds
                ping_results.extend(context_ping_results)
                if any(result["success"] is not True for result in context_ping_results):
                    failure_reason = f"ut_ping_failed_after_real_apply:{context.ut_id}"
                    break
        except (RuntimeCommandFailure, NodeLocalBatchFailure) as exc:
            failure_reason = str(exc)

        if failure_reason is not None:
            cleanup_session = ActiveRuntimeSession(
                session_id="failed-session-cleanup",
                backend={},
                target_slice={
                    "slice_id": topology_snapshot.time_slice.get("slice_id"),
                    "snapshot_id": topology_snapshot.snapshot_id,
                },
                host_runtime_boundary={},
                preflight={
                    "contexts": preflight_results,
                    "preview_targets": preview_targets,
                    "realization_support": realization_support,
                    "node_local_batch_plan": {
                        "apply": apply_batch_plan,
                        "verify": verify_batch_plan,
                        "rollback": [],
                    },
                },
                apply_results=apply_results,
                verify_results=verify_results,
                ping_results=ping_results,
                timing={
                    **phase_timing,
                    "total_wall_seconds": time.perf_counter() - session_started_at,
                    "apply_batch_timing": _batch_timing_summary(apply_batch_results),
                    "verify_batch_timing": _batch_timing_summary(verify_batch_results),
                    "flow_path_observations": [
                        _context_path_summary(context) for context in contexts
                    ],
                },
                contexts=contexts,
                context_preflights=tuple(context_preflights),
            )
            rollback_report = self.rollback_session(
                cleanup_session,
                max_node_workers=max_node_workers,
            )
            raise RuntimeError(
                f"{failure_reason}; cleanup_success={rollback_report['rollback_verification']['cleanup_success']}"
            )

        execution_seed = {
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
            "contexts": [asdict(context) for context in contexts],
            "backend": self.backend_name,
            "mode": "apply_and_hold",
        }
        session_id = f"rtsess-{_fingerprint(execution_seed)}"
        return ActiveRuntimeSession(
            session_id=session_id,
            backend={
                "backend_name": self.backend_name,
                "backend_kind": self.backend_kind,
                "backend_version": self.backend_version,
                "execution_mode": "real_apply_verify_hold",
                "runtime_boundary": "node_local_linux_mpls_only",
                "execution_transport": "transitional_node_local_batch_via_docker_exec",
                "future_runtime_target": "node_local_agent_or_daemon",
                "concurrency_model": "per_node_serial_bounded_parallelism",
                "max_node_workers": max(1, int(max_node_workers)),
                "node_local_execution_split": {
                    "external_orchestrator_role": "publish_per_node_ordered_batches",
                    "node_local_role": "execute_ordered_batch_within_node_namespace",
                },
            },
            target_slice={
                "slice_id": topology_snapshot.time_slice.get("slice_id"),
                "snapshot_id": topology_snapshot.snapshot_id,
                "contains_multi_hop": any(
                    not context.is_direct_gateway_path for context in contexts
                ),
                "executed_ut_ids": [context.ut_id for context in contexts],
                "executed_flow_ids": [context.flow_id for context in contexts],
            },
            host_runtime_boundary=(
                dict(host_runtime_summary)
                if host_runtime_summary is not None
                else {
                    "execution_mode": "node_local_only",
                    "operation_count": 0,
                }
            ),
            preflight={
                "contexts": preflight_results,
                "preview_targets": preview_targets,
                "realization_support": realization_support,
                "node_local_batch_plan": {
                    "apply": apply_batch_plan,
                    "verify": verify_batch_plan,
                    "rollback": [],
                },
                "runtime_mode": runtime_mode,
            },
            apply_results=apply_results,
            verify_results=verify_results,
            ping_results=ping_results,
            timing={
                **phase_timing,
                "total_wall_seconds": time.perf_counter() - session_started_at,
                "apply_batch_timing": _batch_timing_summary(apply_batch_results),
                "verify_batch_timing": _batch_timing_summary(verify_batch_results),
                "flow_path_observations": [
                    _context_path_summary(context) for context in contexts
                ],
                "runtime_mode": runtime_mode,
            },
            contexts=contexts,
            context_preflights=tuple(context_preflights),
        )

    def probe_connectivity(
        self,
        session: ActiveRuntimeSession,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        flow_results: List[Dict[str, Any]] = []
        for context in session.contexts:
            context_ping_results, _ping_seconds = _ping_validation_targets(
                context,
                phase="observe",
                description="probe Mini Internet host from UT before transition",
            )
            flow_results.extend(context_ping_results)
        return {
            "slice_id": session.target_slice.get("slice_id"),
            "wall_seconds": time.perf_counter() - started_at,
            "flow_results": flow_results,
            "ping_success": bool(flow_results)
            and all(result["success"] for result in flow_results),
        }

    def rollback_session(
        self,
        session: ActiveRuntimeSession,
        *,
        max_node_workers: int = 2,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        rollback_specs = [
            spec
            for context, preflight in zip(
                reversed(session.contexts),
                reversed(session.context_preflights),
            )
            for spec in self._rollback_specs(context, preflight)
        ]
        rollback_batches = _group_node_local_batches(rollback_specs)
        rollback_batch_plan = [batch.to_dict() for batch in rollback_batches]
        rollback_batch_results = _execute_node_local_batches(
            rollback_batches,
            max_workers=max_node_workers,
        )
        rollback_results = [batch.to_dict() for batch in rollback_batch_results]
        rollback_verifications = [
            {
                "ut_id": context.ut_id,
                **self._rollback_verification(context),
            }
            for context in reversed(session.contexts)
        ]
        cleanup_success = all(
            bool(verification.get("cleanup_success"))
            for verification in rollback_verifications
        )
        return {
            "rollback_results": rollback_results,
            "rollback_verification": {
                "contexts": rollback_verifications,
                "cleanup_success": cleanup_success,
            },
            "timing": {
                "rollback_wall_seconds": time.perf_counter() - started_at,
                "rollback_batch_timing": _batch_timing_summary(rollback_batch_results),
            },
            "node_local_batch_plan": rollback_batch_plan,
        }

    def execute(
        self,
        *,
        topology_path: Path,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
        distribution_batch: DistributedEnvelopeBatch,
        require_preview_validation: bool = True,
        target_ut_ids: Optional[Sequence[str]] = None,
        host_runtime_summary: Optional[Dict[str, Any]] = None,
        max_node_workers: int = 2,
        runtime_mode: str = "correctness",
    ) -> RuntimeExecutionResult:
        session = self.apply_and_validate(
            topology_path=topology_path,
            topology_snapshot=topology_snapshot,
            runtime_update_plan=runtime_update_plan,
            distribution_batch=distribution_batch,
            require_preview_validation=require_preview_validation,
            target_ut_ids=target_ut_ids,
            host_runtime_summary=host_runtime_summary,
            max_node_workers=max_node_workers,
            runtime_mode=runtime_mode,
        )
        rollback_report = self.rollback_session(
            session,
            max_node_workers=max_node_workers,
        )
        ping_results = list(session.ping_results)
        primary_ping_result = ping_results[0] if ping_results else {
            "ut_id": None,
            "ping_target_ip": None,
            "target_host_node_id": None,
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }
        return RuntimeExecutionResult(
            execution_id=session.session_id,
            backend={**session.backend, "execution_mode": "real_apply_verify_rollback"},
            target_slice=dict(session.target_slice),
            host_runtime_boundary=dict(session.host_runtime_boundary),
            preflight={
                **dict(session.preflight),
                "node_local_batch_plan": {
                    **dict(session.preflight.get("node_local_batch_plan", {})),
                    "rollback": list(rollback_report["node_local_batch_plan"]),
                },
            },
            apply_results=list(session.apply_results),
            verify_results=list(session.verify_results),
            ping_result=primary_ping_result,
            rollback_results=list(rollback_report["rollback_results"]),
            rollback_verification=dict(rollback_report["rollback_verification"]),
            timing={
                **dict(session.timing),
                **dict(rollback_report["timing"]),
                "total_wall_seconds": (
                    float(session.timing.get("total_wall_seconds", 0.0))
                    + float(rollback_report["timing"].get("rollback_wall_seconds", 0.0))
                ),
            },
            outcome={
                "real_apply_success": True,
                "ping_success": bool(ping_results)
                and all(result["success"] for result in ping_results),
                "rollback_completed": bool(
                    rollback_report["rollback_verification"]["cleanup_success"]
                ),
                "failure_reason": None,
                "flow_results": ping_results,
                "batched_node_execution": True,
            },
        )
