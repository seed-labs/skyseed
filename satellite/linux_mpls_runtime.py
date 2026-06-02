#!/usr/bin/env python3
# encoding: utf-8

"""Minimal real Linux/MPLS runtime realization for the Iteration 2 experiment."""

from __future__ import annotations

import hashlib
import json
import subprocess
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


@dataclass(frozen=True)
class DirectGatewayExperimentContext:
    ut_id: str
    serving_satellite_id: str
    selected_gateway_id: str
    selected_gateway_satellite_id: str
    ping_target_ip: str
    target_host_node_id: str
    policy_table_id: int
    policy_rule_priority: int
    forward_label: int
    reverse_label: int
    ut_access_interface: str
    ut_access_ip: str
    ut_access_peer_ip: str
    gateway_access_interface: str
    gateway_access_ip: str
    gateway_access_peer_ip: str
    gateway_nat_interface: str
    gateway_nat_source_ip: str
    validation_targets: Tuple[Dict[str, str], ...]

    @property
    def return_target_prefix(self) -> str:
        return f"{self.ut_access_ip}/32"


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


def _as_int(value: str, *, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


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
        command=_mpls_route_show_command(context.reverse_label),
        phase="preflight",
        description="capture serving-satellite reverse pop label",
        allow_failure=True,
    )
    sat_mpls_input = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_sysctl_read_command(context.gateway_access_interface),
        phase="preflight",
        description="read serving-satellite MPLS input state",
    )
    sat_platform_labels = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_platform_labels_read_command(),
        phase="preflight",
        description="read serving-satellite MPLS platform labels",
    )
    gw_forward_pop = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_mpls_route_show_command(context.forward_label),
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
    gw_mpls_input = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_sysctl_read_command(context.gateway_access_interface),
        phase="preflight",
        description="read gateway MPLS input state",
    )
    gw_platform_labels = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_platform_labels_read_command(),
        phase="preflight",
        description="read gateway MPLS platform labels",
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
        "sat_mpls_input_value": sat_mpls_input.stdout,
        "sat_platform_labels_value": sat_platform_labels.stdout,
        "gw_forward_pop_present": bool(gw_forward_pop.stdout),
        "gw_forward_pop_stdout": gw_forward_pop.stdout,
        "gw_reverse_route_present": bool(gw_reverse_route.stdout),
        "gw_reverse_route_stdout": gw_reverse_route.stdout,
        "gw_mpls_input_value": gw_mpls_input.stdout,
        "gw_platform_labels_value": gw_platform_labels.stdout,
        "gw_forward_rule_present": gw_forward_rule.returncode == 0,
        "gw_reverse_rule_present": gw_reverse_rule.returncode == 0,
        "gw_snat_rule_present": gw_snat_rule.returncode == 0,
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
        str(context.forward_label) not in preflight["sat_policy_default_stdout"]
        or context.gateway_access_interface not in preflight["sat_policy_default_stdout"]
    ):
        conflicts.append("serving_satellite_policy_default_conflict")
    if preflight["sat_delivery_route_present"] and (
        context.ut_access_ip not in preflight["sat_delivery_route_stdout"]
        or context.ut_access_interface not in preflight["sat_delivery_route_stdout"]
    ):
        conflicts.append("serving_satellite_delivery_route_conflict")
    if preflight["gw_reverse_route_present"] and (
        str(context.reverse_label) not in preflight["gw_reverse_route_stdout"]
        or context.gateway_access_interface not in preflight["gw_reverse_route_stdout"]
    ):
        conflicts.append("gateway_reverse_route_conflict")
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
        satellite_path = list(flow_context.get("satellite_path", []))
        if len(satellite_path) != 1:
            raise RuntimeError(
                "minimal real runtime path currently supports only direct-gateway slices"
            )
        serving_satellite_id = str(flow_context["serving_satellite_id"])
        selected_gateway_id = str(flow_context["selected_gateway_id"])
        selected_gateway_satellite_id = str(flow_context["selected_gateway_satellite_id"])
        if serving_satellite_id != selected_gateway_satellite_id:
            raise RuntimeError("selected slice is not a direct-gateway experiment path")

        ut_link = _active_link_by_id(topology_snapshot, str(flow_context["ut_access_link_id"]))
        gw_link = _active_link_by_id(
            topology_snapshot,
            str(flow_context["gateway_access_link_id"]),
        )
        ut_endpoint = _endpoint_for_node(ut_link, str(flow_context["ut_id"]))
        sat_ut_endpoint = _endpoint_for_node(ut_link, serving_satellite_id)
        gateway_endpoint = _endpoint_for_node(gw_link, selected_gateway_id)
        sat_gw_endpoint = _endpoint_for_node(gw_link, serving_satellite_id)
        gateway_entry = gateways[selected_gateway_id]
        bootstrap_endpoint = dict(
            gateway_entry.get("bootstrap_network", {}).get("endpoint", {})
        )
        contexts.append(
            DirectGatewayExperimentContext(
                ut_id=str(flow_context["ut_id"]),
                serving_satellite_id=serving_satellite_id,
                selected_gateway_id=selected_gateway_id,
                selected_gateway_satellite_id=selected_gateway_satellite_id,
                ping_target_ip=str(flow_context["ping_target_ip"]),
                target_host_node_id=str(flow_context["target_host_node_id"]),
                policy_table_id=int(flow_context["policy_table_id"]),
                policy_rule_priority=int(flow_context["policy_rule_priority"]),
                forward_label=int(list(flow_context.get("forward_labels", []))[0]),
                reverse_label=int(list(flow_context.get("reverse_labels", []))[0]),
                ut_access_interface=str(ut_endpoint["interface_name"]),
                ut_access_ip=_strip_prefix(str(ut_endpoint["ip_address"])),
                ut_access_peer_ip=_strip_prefix(str(sat_ut_endpoint["ip_address"])),
                gateway_access_interface=str(gateway_endpoint["interface_name"]),
                gateway_access_ip=_strip_prefix(str(gateway_endpoint["ip_address"])),
                gateway_access_peer_ip=_strip_prefix(str(sat_gw_endpoint["ip_address"])),
                gateway_nat_interface=str(
                    gateway_entry.get("internet_uplink", {}).get("network_name")
                ),
                gateway_nat_source_ip=_strip_prefix(str(bootstrap_endpoint.get("ip_address"))),
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

    def _apply_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        required_platform_labels = max(
            1000,
            context.forward_label + 1,
            context.reverse_label + 1,
        )
        if _as_int(preflight["sat_platform_labels_value"]) < required_platform_labels:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="set serving-satellite MPLS platform labels",
                    command=(
                        "sysctl",
                        "-w",
                        f"net.mpls.platform_labels={required_platform_labels}",
                    ),
                )
            )
        if _as_int(preflight["gw_platform_labels_value"]) < required_platform_labels:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="set gateway MPLS platform labels",
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
                        "ip",
                        "rule",
                        "add",
                        "pref",
                        str(context.policy_rule_priority),
                        "from",
                        f"{context.ut_access_ip}/32",
                        "table",
                        str(context.policy_table_id),
                    ),
                )
            )
        if preflight["sat_mpls_input_value"] != "1":
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="enable serving-satellite MPLS input",
                    command=(
                        "sysctl",
                        "-w",
                        f"net.mpls.conf.{context.gateway_access_interface}.input=1",
                    ),
                )
            )
        if preflight["gw_mpls_input_value"] != "1":
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="enable gateway MPLS input",
                    command=(
                        "sysctl",
                        "-w",
                        f"net.mpls.conf.{context.gateway_access_interface}.input=1",
                    ),
                )
            )
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
                        str(context.forward_label),
                        "via",
                        "inet",
                        context.gateway_access_ip,
                        "dev",
                        context.gateway_access_interface,
                        "table",
                        str(context.policy_table_id),
                    ),
                ),
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
                        str(context.reverse_label),
                        "dev",
                        "lo",
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
                        str(context.forward_label),
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
                        str(context.reverse_label),
                        "via",
                        "inet",
                        context.gateway_access_peer_ip,
                        "dev",
                        context.gateway_access_interface,
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
        return [
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

    def _rollback_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        required_platform_labels = max(
            1000,
            context.forward_label + 1,
            context.reverse_label + 1,
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
                        str(context.forward_label),
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
                        str(context.reverse_label),
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
        if preflight["gw_mpls_input_value"] != "1":
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="restore gateway MPLS input state",
                    command=(
                        "sysctl",
                        "-w",
                        f"net.mpls.conf.{context.gateway_access_interface}.input="
                        f"{preflight['gw_mpls_input_value'] or '0'}",
                    ),
                    allow_failure=True,
                )
            )
        if preflight["sat_mpls_input_value"] != "1":
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="restore serving-satellite MPLS input state",
                    command=(
                        "sysctl",
                        "-w",
                        f"net.mpls.conf.{context.gateway_access_interface}.input="
                        f"{preflight['sat_mpls_input_value'] or '0'}",
                    ),
                    allow_failure=True,
                )
            )
        if _as_int(preflight["gw_platform_labels_value"]) < required_platform_labels:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="restore gateway MPLS platform labels",
                    command=(
                        "sysctl",
                        "-w",
                        "net.mpls.platform_labels="
                        f"{preflight['gw_platform_labels_value'] or '0'}",
                    ),
                    allow_failure=True,
                )
            )
        if _as_int(preflight["sat_platform_labels_value"]) < required_platform_labels:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="restore serving-satellite MPLS platform labels",
                    command=(
                        "sysctl",
                        "-w",
                        "net.mpls.platform_labels="
                        f"{preflight['sat_platform_labels_value'] or '0'}",
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
            command=_mpls_route_show_command(context.forward_label),
            phase="rollback_verify",
            description="verify gateway forward pop cleanup",
            allow_failure=True,
        )
        return {
            "ut_default_route_present": bool(ut_default.stdout),
            "sat_policy_default_present": bool(sat_policy_default.stdout),
            "gw_forward_pop_present": bool(gw_forward_pop.stdout),
            "cleanup_success": not bool(ut_default.stdout)
            and not bool(sat_policy_default.stdout)
            and not bool(gw_forward_pop.stdout),
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
    ) -> RuntimeExecutionResult:
        preview_targets = sorted(distribution_batch.node_delivery_views)
        contexts = _realization_contexts(
            topology_path=topology_path,
            topology_snapshot=topology_snapshot,
            runtime_update_plan=runtime_update_plan,
            target_ut_ids=target_ut_ids,
        )
        if require_preview_validation:
            required_node_ids = {
                node_id
                for context in contexts
                for node_id in (
                    context.ut_id,
                    context.serving_satellite_id,
                    context.selected_gateway_id,
                )
            }
            for node_id in sorted(required_node_ids):
                if node_id not in preview_targets:
                    raise RuntimeError(
                        f"preview distribution is missing required target node {node_id}"
                    )
        apply_results: List[Dict[str, Any]] = []
        verify_results: List[Dict[str, Any]] = []
        rollback_results: List[Dict[str, Any]] = []
        ping_results: List[Dict[str, Any]] = []
        preflight_results: List[Dict[str, Any]] = []
        rollback_verifications: List[Dict[str, Any]] = []
        failure_reason: Optional[str] = None
        rollback_completed = False
        applied_contexts: List[Tuple[DirectGatewayExperimentContext, Dict[str, Any]]] = []
        try:
            for context in contexts:
                preflight = _capture_preflight_state(context)
                _validate_preflight_state(context, preflight)
                preflight_results.append(
                    {
                        "ut_id": context.ut_id,
                        **preflight,
                    }
                )
                apply_specs = self._apply_specs(context, preflight)
                verify_specs = self._verify_specs(context)
                for spec in apply_specs:
                    apply_results.append(
                        {
                            "ut_id": context.ut_id,
                            **_run_docker_command(
                                node_id=spec.node_id,
                                command=spec.command,
                                phase=spec.phase,
                                description=spec.description,
                                allow_failure=spec.allow_failure,
                            ).to_dict(),
                        }
                    )
                for spec in verify_specs:
                    verify_results.append(
                        {
                            "ut_id": context.ut_id,
                            **_run_docker_command(
                                node_id=spec.node_id,
                                command=spec.command,
                                phase=spec.phase,
                                description=spec.description,
                                allow_failure=spec.allow_failure,
                            ).to_dict(),
                        }
                    )
                validation_targets = list(context.validation_targets) or [
                    {
                        "node_id": context.target_host_node_id,
                        "ip_address": context.ping_target_ip,
                    }
                ]
                context_ping_results: List[Dict[str, Any]] = []
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
                        phase="verify",
                        description="ping Mini Internet host from UT",
                        allow_failure=True,
                    )
                    ping_result = {
                        "ut_id": context.ut_id,
                        "ping_target_ip": str(validation_target["ip_address"]),
                        "target_host_node_id": str(validation_target["node_id"]),
                        "success": ping.returncode == 0,
                        "stdout": ping.stdout,
                        "stderr": ping.stderr,
                        "returncode": ping.returncode,
                    }
                    ping_results.append(ping_result)
                    context_ping_results.append(ping_result)
                applied_contexts.append((context, preflight))
                if any(result["success"] is not True for result in context_ping_results):
                    failure_reason = f"ut_ping_failed_after_real_apply:{context.ut_id}"
                    break
        except RuntimeCommandFailure as exc:
            failure_reason = str(exc)
        finally:
            for context, preflight in reversed(applied_contexts):
                rollback_specs = self._rollback_specs(context, preflight)
                for spec in rollback_specs:
                    rollback_results.append(
                        {
                            "ut_id": context.ut_id,
                            **_run_docker_command(
                                node_id=spec.node_id,
                                command=spec.command,
                                phase=spec.phase,
                                description=spec.description,
                                allow_failure=True,
                            ).to_dict(),
                        }
                    )
                rollback_verifications.append(
                    {
                        "ut_id": context.ut_id,
                        **self._rollback_verification(context),
                    }
                )
            rollback_completed = all(
                bool(verification.get("cleanup_success"))
                for verification in rollback_verifications
            )

        execution_seed = {
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
            "contexts": [asdict(context) for context in contexts],
            "backend": self.backend_name,
        }
        execution_id = f"realexec-{_fingerprint(execution_seed)}"
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
            execution_id=execution_id,
            backend={
                "backend_name": self.backend_name,
                "backend_kind": self.backend_kind,
                "backend_version": self.backend_version,
                "execution_mode": "real_apply_verify_rollback",
                "runtime_boundary": "node_local_linux_mpls_only",
                "execution_transport": "transitional_direct_docker_exec",
                "future_runtime_target": "node_local_agent_or_daemon",
                "concurrency_model": "serial_bounded",
            },
            target_slice={
                "slice_id": topology_snapshot.time_slice.get("slice_id"),
                "snapshot_id": topology_snapshot.snapshot_id,
                "direct_gateway_only": True,
                "executed_ut_ids": [context.ut_id for context in contexts],
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
            },
            apply_results=apply_results,
            verify_results=verify_results,
            ping_result=primary_ping_result,
            rollback_results=rollback_results,
            rollback_verification={
                "contexts": rollback_verifications,
                "cleanup_success": rollback_completed,
            },
            outcome={
                "real_apply_success": failure_reason is None and bool(applied_contexts),
                "ping_success": bool(ping_results)
                and all(result["success"] for result in ping_results),
                "rollback_completed": rollback_completed,
                "failure_reason": failure_reason,
                "flow_results": ping_results,
            },
        )
