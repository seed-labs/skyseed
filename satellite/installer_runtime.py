#!/usr/bin/env python3
# encoding: utf-8

"""Dry-run installer boundary for Iteration 2 runtime realization."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from contracts import _collect_forbidden_key_paths


INSTALLER_EXECUTION_SCHEMA_VERSION = "satellite-runtime-installer-execution-v1alpha1"
INSTALLER_EXECUTION_FORBIDDEN_FIELDS = (
    "argv",
    "command_family",
    "shell_command",
    "docker_exec_command",
    "docker_network_operation",
    "iptables_command",
    "ip_route_command",
    "sysctl_command",
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class InstallerValidationResult:
    validation_scope: str
    installer_name: str
    source_envelope_id: str
    execution_id: Optional[str]
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_scope": self.validation_scope,
            "installer_name": self.installer_name,
            "source_envelope_id": self.source_envelope_id,
            "execution_id": self.execution_id,
            "status": "valid" if self.is_valid else "issues_detected",
            "issues": list(self.issues),
            "issue_count": len(self.issues),
        }


@dataclass(frozen=True)
class DryRunInstallerExecution:
    schema_version: str
    execution_id: str
    source_envelope: Dict[str, Any]
    installer: Dict[str, Any]
    target_node: Dict[str, Any]
    execution_summary: Dict[str, Any]
    step_results: List[Dict[str, Any]]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != INSTALLER_EXECUTION_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.execution_id:
            issues.append("execution_id_missing")
        if not self.source_envelope.get("envelope_id"):
            issues.append("source_envelope_missing_id")
        if not self.installer.get("installer_name"):
            issues.append("installer_name_missing")
        if self.execution_summary.get("dry_run_mode") is not True:
            issues.append("execution_summary_not_dry_run")
        if self.execution_summary.get("step_count") != len(self.step_results):
            issues.append("execution_summary_step_count_mismatch")
        if len(self.step_results) == 0:
            issues.append("execution_without_step_results")
        for index, step_result in enumerate(self.step_results, start=1):
            if step_result.get("target_node_id") != self.target_node.get("node_id"):
                issues.append(f"step_result_target_mismatch:{index}")
            if not step_result.get("instruction_id"):
                issues.append(f"step_result_missing_instruction_id:{index}")
            if step_result.get("realization_mode") != "dry_run_preview":
                issues.append(f"step_result_not_dry_run_preview:{index}")
        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=INSTALLER_EXECUTION_FORBIDDEN_FIELDS,
            path="dry_run_installer_execution",
        )
        issues.extend(
            f"dry_run_installer_execution_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )
        return issues

    def validation_result(self) -> InstallerValidationResult:
        return InstallerValidationResult(
            validation_scope="installer_output",
            installer_name=str(self.installer.get("installer_name")),
            source_envelope_id=str(self.source_envelope.get("envelope_id")),
            execution_id=self.execution_id,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


class InstallerError(RuntimeError):
    """Base error for installer failures."""


class InstallerInputValidationError(InstallerError):
    """Raised when a node envelope does not satisfy installer requirements."""

    def __init__(self, result: InstallerValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "installer input validation failed")


class InstallerOutputValidationError(InstallerError):
    """Raised when a generated installer execution report is invalid."""

    def __init__(self, result: InstallerValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "installer output validation failed")


class NodeLocalInstaller(ABC):
    """Replaceable boundary for node-local installer execution."""

    installer_name: str
    installer_kind: str
    installer_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "installer_name": self.installer_name,
            "installer_kind": self.installer_kind,
            "installer_version": self.installer_version,
        }

    def validate_input(self, node_envelope: Dict[str, Any]) -> InstallerValidationResult:
        issues: List[str] = []
        if node_envelope.get("envelope_kind") != "node_local_installer_preview":
            issues.append(
                "installer_input_unexpected_envelope_kind:"
                f"{node_envelope.get('envelope_kind')}"
            )
        if not node_envelope.get("target_node_id"):
            issues.append("installer_input_missing_target_node_id")
        if not node_envelope.get("envelope_id"):
            issues.append("installer_input_missing_envelope_id")
        installer_contract = node_envelope.get("installer_contract", {})
        if installer_contract.get("projection_model") != "installer_intent_preview":
            issues.append(
                "installer_input_unexpected_projection_model:"
                f"{installer_contract.get('projection_model')}"
            )
        installer_intents = list(node_envelope.get("installer_intents", []))
        if len(installer_intents) == 0:
            issues.append("installer_input_missing_installer_intents")
        return InstallerValidationResult(
            validation_scope="installer_input",
            installer_name=self.installer_name,
            source_envelope_id=str(node_envelope.get("envelope_id")),
            execution_id=None,
            issues=tuple(issues),
        )

    def validate_output(
        self,
        node_envelope: Dict[str, Any],
        execution: DryRunInstallerExecution,
    ) -> InstallerValidationResult:
        issues = list(execution.validate())
        if execution.source_envelope.get("envelope_id") != node_envelope.get("envelope_id"):
            issues.append("installer_output_source_envelope_mismatch")
        if execution.target_node.get("node_id") != node_envelope.get("target_node_id"):
            issues.append("installer_output_target_node_mismatch")
        source_instruction_ids = {
            str(intent.get("instruction_id"))
            for intent in node_envelope.get("installer_intents", [])
        }
        for index, step_result in enumerate(execution.step_results, start=1):
            instruction_id = str(step_result.get("instruction_id"))
            if instruction_id not in source_instruction_ids:
                issues.append(
                    f"installer_output_unknown_instruction:{index}:{instruction_id}"
                )
        return InstallerValidationResult(
            validation_scope="installer_output",
            installer_name=self.installer_name,
            source_envelope_id=str(node_envelope.get("envelope_id")),
            execution_id=execution.execution_id,
            issues=tuple(issues),
        )

    def apply(self, node_envelope: Dict[str, Any]) -> DryRunInstallerExecution:
        input_result = self.validate_input(node_envelope)
        if not input_result.is_valid:
            raise InstallerInputValidationError(input_result)
        execution = self.build_execution(node_envelope)
        output_result = self.validate_output(node_envelope, execution)
        if not output_result.is_valid:
            raise InstallerOutputValidationError(output_result)
        return execution

    @abstractmethod
    def build_execution(self, node_envelope: Dict[str, Any]) -> DryRunInstallerExecution:
        """Build one dry-run installer execution from a node-local envelope."""


def _linux_forwarding_operation_preview(
    *,
    instruction: Dict[str, Any],
    target_node_id: str,
) -> List[Dict[str, Any]]:
    instruction_type = str(instruction.get("instruction_type"))
    intent = dict(instruction.get("intent", {}))
    operations: List[Dict[str, Any]] = []

    if instruction_type == "traffic_classification_preview":
        outcome = dict(intent.get("outcome", {}))
        operations.append(
            {
                "operation_family": "linux_forwarding_dry_run",
                "operation_kind": "routing_policy_classification_candidate",
                "target_node_id": target_node_id,
                "candidate_state": {
                    "classification_scope": intent.get("classification_scope"),
                    "traffic_role": outcome.get("traffic_role"),
                    "zone_id": outcome.get("zone_id"),
                    "serving_satellite_id": outcome.get("serving_satellite_id"),
                },
                "preview_reason": "Map generic traffic classification intent into Linux policy-routing style state",
            }
        )
        return operations

    if instruction_type == "service_binding_preview":
        binding_kind = str(intent.get("binding_kind"))
        target = dict(intent.get("target", {}))
        subject = dict(intent.get("subject", {}))
        if binding_kind == "gateway_egress":
            operations.append(
                {
                    "operation_family": "linux_forwarding_dry_run",
                    "operation_kind": "default_gateway_selection_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "gateway_id": subject.get("entity_id"),
                        "attached_satellite_id": target.get("entity_id"),
                        "binding_scope": intent.get("binding_scope"),
                    },
                    "preview_reason": "Represent gateway egress binding as Linux egress-role state",
                }
            )
        elif binding_kind == "gateway_reachability_anchor":
            operations.append(
                {
                    "operation_family": "linux_forwarding_dry_run",
                    "operation_kind": "gateway_reachability_route_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "satellite_id": subject.get("entity_id"),
                        "gateway_id": target.get("entity_id"),
                        "gateway_role": target.get("role"),
                    },
                    "preview_reason": "Represent gateway reachability anchor as Linux route-oriented state",
                }
            )
        elif binding_kind == "zone_service":
            operations.append(
                {
                    "operation_family": "linux_forwarding_dry_run",
                    "operation_kind": "local_zone_delivery_role_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "zone_id": subject.get("entity_id"),
                        "serving_satellite_id": target.get("entity_id"),
                        "delivery_role": target.get("role"),
                    },
                    "preview_reason": "Represent zone service binding as Linux local-delivery role state",
                }
            )
        else:
            operations.append(
                {
                    "operation_family": "linux_forwarding_dry_run",
                    "operation_kind": "generic_service_binding_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "binding_kind": binding_kind,
                        "subject_entity_id": subject.get("entity_id"),
                        "target_entity_id": target.get("entity_id"),
                    },
                    "preview_reason": "Preserve generic service binding when no narrower Linux projection rule exists",
                }
            )
        return operations

    if instruction_type == "forwarding_entry_preview":
        operations.append(
            {
                "operation_family": "linux_forwarding_dry_run",
                "operation_kind": "forwarding_table_entry_candidate",
                "target_node_id": target_node_id,
                "candidate_state": {
                    "forwarding_plane": intent.get("forwarding_plane"),
                    "match": dict(intent.get("match", {})),
                    "outcome": dict(intent.get("outcome", {})),
                },
                "preview_reason": "Represent generic forwarding entry as Linux forwarding-table state",
            }
        )
        return operations

    operations.append(
        {
            "operation_family": "linux_forwarding_dry_run",
            "operation_kind": "node_capability_candidate",
            "target_node_id": target_node_id,
            "candidate_state": {
                "capability": intent.get("capability"),
                "operation": intent.get("operation"),
                "capability_scope": dict(intent.get("capability_scope", {})),
            },
            "preview_reason": "Represent generic node capability as Linux runtime readiness state",
        }
    )
    return operations


def _linux_mpls_operation_preview(
    *,
    instruction: Dict[str, Any],
    target_node_id: str,
) -> List[Dict[str, Any]]:
    instruction_type = str(instruction.get("instruction_type"))
    intent = dict(instruction.get("intent", {}))
    operations: List[Dict[str, Any]] = []

    if instruction_type == "traffic_classification_preview":
        outcome = dict(intent.get("outcome", {}))
        operations.append(
            {
                "operation_family": "linux_mpls_dry_run",
                "operation_kind": "policy_routing_classification_candidate",
                "target_node_id": target_node_id,
                "candidate_state": {
                    "classification_scope": intent.get("classification_scope"),
                    "traffic_role": outcome.get("traffic_role"),
                    "serving_satellite_id": outcome.get("serving_satellite_id"),
                    "selected_gateway_id": outcome.get("selected_gateway_id"),
                },
                "preview_reason": (
                    "Translate generic traffic classification into Linux policy-routing style"
                    " ingress state"
                ),
            }
        )
        return operations

    if instruction_type == "service_binding_preview":
        binding_kind = str(intent.get("binding_kind"))
        subject = dict(intent.get("subject", {}))
        target = dict(intent.get("target", {}))
        if binding_kind == "zone_service":
            operations.append(
                {
                    "operation_family": "linux_mpls_dry_run",
                    "operation_kind": "serving_satellite_role_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "zone_id": subject.get("entity_id"),
                        "serving_satellite_id": target.get("entity_id"),
                        "serving_role": target.get("role"),
                    },
                    "preview_reason": (
                        "Keep serving-satellite ownership separate from packet-forwarding state"
                    ),
                }
            )
        elif binding_kind == "gateway_egress":
            operations.append(
                {
                    "operation_family": "linux_mpls_dry_run",
                    "operation_kind": "gateway_attachment_role_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "gateway_id": subject.get("entity_id"),
                        "gateway_satellite_id": target.get("entity_id"),
                        "gateway_role": target.get("role"),
                    },
                    "preview_reason": (
                        "Represent gateway attachment as node-local realization context"
                    ),
                }
            )
        else:
            operations.append(
                {
                    "operation_family": "linux_mpls_dry_run",
                    "operation_kind": "generic_service_binding_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        "binding_kind": binding_kind,
                        "subject": subject,
                        "target": target,
                    },
                    "preview_reason": "Preserve generic service binding when no MPLS-specific rule is needed",
                }
            )
        return operations

    if instruction_type == "forwarding_entry_preview":
        outcome = dict(intent.get("outcome", {}))
        local_role = str(outcome.get("local_role"))
        if local_role == "ut_ingress":
            gateway_selection = dict(outcome.get("gateway_selection", {}))
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
                operations.append(
                    {
                        "operation_family": "linux_mpls_dry_run",
                        "operation_kind": "default_gateway_class_candidate",
                        "target_node_id": target_node_id,
                        "candidate_state": {
                            "flow_id": gateway_binding.get("flow_id"),
                            "gateway_class_id": gateway_binding.get("gateway_class_id"),
                            "selected_gateway_id": gateway_binding.get("selected_gateway_id"),
                            "serving_satellite_id": gateway_binding.get(
                                "serving_satellite_id"
                            ),
                            "ordinary_traffic_behavior": gateway_binding.get(
                                "ordinary_traffic_behavior"
                            ),
                            "traffic_scope": gateway_binding.get("traffic_scope"),
                        },
                        "preview_reason": (
                            "Model UT traffic as gateway-class selection without exposing end-to-end path state"
                        ),
                    }
                )
                operations.append(
                    {
                        "operation_family": "linux_mpls_dry_run",
                        "operation_kind": "gateway_class_ingress_label_candidate",
                        "target_node_id": target_node_id,
                        "candidate_state": {
                            "flow_id": gateway_binding.get("flow_id"),
                            "gateway_class_id": gateway_binding.get("gateway_class_id"),
                            "selected_gateway_id": gateway_binding.get("selected_gateway_id"),
                            "serving_satellite_id": gateway_binding.get(
                                "serving_satellite_id"
                            ),
                            "ingress_transport": ingress_transport,
                            "special_prefix_overrides": list(
                                gateway_selection.get("special_prefix_overrides", [])
                            ),
                        },
                        "preview_reason": (
                            "Model UT handoff as serving-satellite-provided ingress labels plus prefix-level gateway-class overrides"
                        ),
                    }
                )
            return operations

        flow_context = dict(outcome.get("flow_context", {}))
        base_context = {
            "flow_id": intent.get("match", {}).get("flow_id"),
            "local_role": local_role,
            "serving_satellite_id": flow_context.get("serving_satellite_id"),
            "selected_gateway_id": flow_context.get("selected_gateway_id"),
            "selected_gateway_satellite_id": flow_context.get(
                "selected_gateway_satellite_id"
            ),
            "forward_labels": list(flow_context.get("forward_labels", [])),
            "reverse_labels": list(flow_context.get("reverse_labels", [])),
            "policy_table_id": flow_context.get("policy_table_id"),
            "policy_rule_priority": flow_context.get("policy_rule_priority"),
        }
        if local_role in {"transit_toward_gateway", "direct_gateway_egress"}:
            operations.append(
                {
                    "operation_family": "linux_mpls_dry_run",
                    "operation_kind": "satellite_lfib_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        **base_context,
                        "satellite_path": list(flow_context.get("satellite_path", [])),
                        "forward_link_ids": list(flow_context.get("forward_link_ids", [])),
                    },
                    "preview_reason": (
                        "Model satellite forwarding as LFIB-style label switching state"
                    ),
                }
            )
        elif local_role == "gateway_nat_and_return_ingress":
            operations.extend(
                [
                    {
                        "operation_family": "linux_mpls_dry_run",
                        "operation_kind": "gateway_return_ingress_candidate",
                        "target_node_id": target_node_id,
                        "candidate_state": {
                            **base_context,
                            "gateway_access_link_id": flow_context.get(
                                "gateway_access_link_id"
                            ),
                        },
                        "preview_reason": (
                            "Model gateway return traffic handling as reverse-path ingress state"
                        ),
                    },
                    {
                        "operation_family": "linux_mpls_dry_run",
                        "operation_kind": "gateway_internet_egress_candidate",
                        "target_node_id": target_node_id,
                        "candidate_state": {
                            **base_context,
                            "ping_target_ip": flow_context.get("ping_target_ip"),
                            "target_host_node_id": flow_context.get(
                                "target_host_node_id"
                            ),
                        },
                        "preview_reason": (
                            "Model gateway internet breakout without committing to a concrete NAT implementation"
                        ),
                    },
                ]
            )
        else:
            operations.append(
                {
                    "operation_family": "linux_mpls_dry_run",
                    "operation_kind": "generic_forwarding_entry_candidate",
                    "target_node_id": target_node_id,
                    "candidate_state": {
                        **base_context,
                        "forwarding_plane": intent.get("forwarding_plane"),
                    },
                    "preview_reason": "Preserve forwarding state when no narrower MPLS rule is available",
                }
            )
        return operations

    operations.append(
        {
            "operation_family": "linux_mpls_dry_run",
            "operation_kind": "generic_node_capability_candidate",
            "target_node_id": target_node_id,
            "candidate_state": {
                "capability": intent.get("capability"),
                "operation": intent.get("operation"),
                "capability_scope": dict(intent.get("capability_scope", {})),
            },
            "preview_reason": "Represent generic node capability as Linux/MPLS readiness state",
        }
    )
    return operations


class GenericDryRunInstaller(NodeLocalInstaller):
    """Generic dry-run installer that previews node-local realization steps."""

    installer_name = "generic_dry_run_installer"
    installer_kind = "node_local_installer"
    installer_version = "0.1.0"

    def build_execution(self, node_envelope: Dict[str, Any]) -> DryRunInstallerExecution:
        target_node_id = str(node_envelope.get("target_node_id"))
        step_results: List[Dict[str, Any]] = []
        for sequence, instruction in enumerate(node_envelope.get("installer_intents", []), start=1):
            execution_preview = dict(instruction.get("execution_preview", {}))
            step_results.append(
                {
                    "sequence": sequence,
                    "instruction_id": instruction.get("instruction_id"),
                    "instruction_type": instruction.get("instruction_type"),
                    "target_node_id": target_node_id,
                    "source_action_id": instruction.get("source_action_id"),
                    "realization_mode": "dry_run_preview",
                    "backend_family": execution_preview.get("backend_family"),
                    "candidate_backends": list(
                        execution_preview.get("candidate_backends", [])
                    ),
                    "realization_scope": execution_preview.get("realization_scope"),
                    "summary": execution_preview.get("summary"),
                    "observed_effect": "no_runtime_mutation_performed",
                }
            )
        execution_seed = {
            "source_envelope_id": node_envelope.get("envelope_id"),
            "installer": self.metadata(),
            "instruction_ids": [
                step_result["instruction_id"] for step_result in step_results
            ],
        }
        execution_id = f"instexec-{_fingerprint(execution_seed)}"
        return DryRunInstallerExecution(
            schema_version=INSTALLER_EXECUTION_SCHEMA_VERSION,
            execution_id=execution_id,
            source_envelope={
                "envelope_id": node_envelope.get("envelope_id"),
                "distribution_mode": node_envelope.get("delivery_contract", {}).get(
                    "distribution_model"
                ),
            },
            installer={
                **self.metadata(),
                "dry_run_mode": True,
                "planner_policy_visibility": "none",
            },
            target_node={
                "node_id": target_node_id,
                "node_type": node_envelope.get("target_node_type"),
            },
            execution_summary={
                "status": "dry_run_preview_complete",
                "dry_run_mode": True,
                "step_count": len(step_results),
                "requires_runtime_side_effects": False,
            },
            step_results=step_results,
            validation_expectations={
                "pre_apply_validation": "required",
                "post_apply_validation": "preview_only",
                "real_linux_mutation": "not_applicable_in_dry_run",
            },
        )


class LinuxForwardingDryRunInstaller(NodeLocalInstaller):
    """Backend-specific dry-run installer that previews Linux forwarding realization."""

    installer_name = "linux_forwarding_dry_run_installer"
    installer_kind = "backend_specific_node_local_installer"
    installer_version = "0.1.0"

    def validate_input(self, node_envelope: Dict[str, Any]) -> InstallerValidationResult:
        result = super().validate_input(node_envelope)
        issues = list(result.issues)
        for index, instruction in enumerate(node_envelope.get("installer_intents", []), start=1):
            preview = dict(instruction.get("execution_preview", {}))
            if preview.get("mode") != "dry_run":
                issues.append(f"linux_forwarding_input_not_dry_run:{index}")
        return InstallerValidationResult(
            validation_scope=result.validation_scope,
            installer_name=result.installer_name,
            source_envelope_id=result.source_envelope_id,
            execution_id=result.execution_id,
            issues=tuple(issues),
        )

    def build_execution(self, node_envelope: Dict[str, Any]) -> DryRunInstallerExecution:
        target_node_id = str(node_envelope.get("target_node_id"))
        step_results: List[Dict[str, Any]] = []
        for sequence, instruction in enumerate(node_envelope.get("installer_intents", []), start=1):
            operation_preview = _linux_forwarding_operation_preview(
                instruction=instruction,
                target_node_id=target_node_id,
            )
            step_results.append(
                {
                    "sequence": sequence,
                    "instruction_id": instruction.get("instruction_id"),
                    "instruction_type": instruction.get("instruction_type"),
                    "target_node_id": target_node_id,
                    "source_action_id": instruction.get("source_action_id"),
                    "realization_mode": "dry_run_preview",
                    "realization_target": "linux_forwarding_dry_run",
                    "backend_adapter": "linux_forwarding_backend_adapter",
                    "operation_preview": operation_preview,
                    "summary": (
                        "Preview Linux forwarding-state realization for "
                        f"{instruction.get('instruction_id')}"
                    ),
                    "observed_effect": "no_runtime_mutation_performed",
                }
            )
        execution_seed = {
            "source_envelope_id": node_envelope.get("envelope_id"),
            "installer": self.metadata(),
            "instruction_ids": [
                step_result["instruction_id"] for step_result in step_results
            ],
        }
        execution_id = f"instexec-{_fingerprint(execution_seed)}"
        return DryRunInstallerExecution(
            schema_version=INSTALLER_EXECUTION_SCHEMA_VERSION,
            execution_id=execution_id,
            source_envelope={
                "envelope_id": node_envelope.get("envelope_id"),
                "distribution_mode": node_envelope.get("delivery_contract", {}).get(
                    "distribution_model"
                ),
            },
            installer={
                **self.metadata(),
                "dry_run_mode": True,
                "planner_policy_visibility": "none",
                "backend_target": "linux_forwarding_dry_run",
            },
            target_node={
                "node_id": target_node_id,
                "node_type": node_envelope.get("target_node_type"),
            },
            execution_summary={
                "status": "dry_run_preview_complete",
                "dry_run_mode": True,
                "step_count": len(step_results),
                "requires_runtime_side_effects": False,
                "backend_specific_preview": True,
                "backend_target": "linux_forwarding_dry_run",
            },
            step_results=step_results,
            validation_expectations={
                "pre_apply_validation": "required",
                "post_apply_validation": "preview_only",
                "real_linux_mutation": "not_applicable_in_dry_run",
                "backend_adapter_validation": "required_before_real_execution",
            },
        )


class LinuxMplsDryRunInstaller(NodeLocalInstaller):
    """Backend-specific dry-run installer that previews Linux/MPLS realization."""

    installer_name = "linux_mpls_dry_run_installer"
    installer_kind = "backend_specific_node_local_installer"
    installer_version = "0.1.0"

    def validate_input(self, node_envelope: Dict[str, Any]) -> InstallerValidationResult:
        result = super().validate_input(node_envelope)
        issues = list(result.issues)
        for index, instruction in enumerate(node_envelope.get("installer_intents", []), start=1):
            preview = dict(instruction.get("execution_preview", {}))
            if preview.get("mode") != "dry_run":
                issues.append(f"linux_mpls_input_not_dry_run:{index}")
        return InstallerValidationResult(
            validation_scope=result.validation_scope,
            installer_name=result.installer_name,
            source_envelope_id=result.source_envelope_id,
            execution_id=result.execution_id,
            issues=tuple(issues),
        )

    def build_execution(self, node_envelope: Dict[str, Any]) -> DryRunInstallerExecution:
        target_node_id = str(node_envelope.get("target_node_id"))
        step_results: List[Dict[str, Any]] = []
        for sequence, instruction in enumerate(node_envelope.get("installer_intents", []), start=1):
            operation_preview = _linux_mpls_operation_preview(
                instruction=instruction,
                target_node_id=target_node_id,
            )
            step_results.append(
                {
                    "sequence": sequence,
                    "instruction_id": instruction.get("instruction_id"),
                    "instruction_type": instruction.get("instruction_type"),
                    "target_node_id": target_node_id,
                    "source_action_id": instruction.get("source_action_id"),
                    "realization_mode": "dry_run_preview",
                    "realization_target": "linux_mpls_dry_run",
                    "backend_adapter": "linux_mpls_backend_adapter",
                    "operation_preview": operation_preview,
                    "summary": (
                        "Preview Linux/MPLS realization for "
                        f"{instruction.get('instruction_id')}"
                    ),
                    "observed_effect": "no_runtime_mutation_performed",
                }
            )
        execution_seed = {
            "source_envelope_id": node_envelope.get("envelope_id"),
            "installer": self.metadata(),
            "instruction_ids": [
                step_result["instruction_id"] for step_result in step_results
            ],
        }
        execution_id = f"instexec-{_fingerprint(execution_seed)}"
        return DryRunInstallerExecution(
            schema_version=INSTALLER_EXECUTION_SCHEMA_VERSION,
            execution_id=execution_id,
            source_envelope={
                "envelope_id": node_envelope.get("envelope_id"),
                "distribution_mode": node_envelope.get("delivery_contract", {}).get(
                    "distribution_model"
                ),
            },
            installer={
                **self.metadata(),
                "dry_run_mode": True,
                "planner_policy_visibility": "none",
                "backend_target": "linux_mpls_dry_run",
            },
            target_node={
                "node_id": target_node_id,
                "node_type": node_envelope.get("target_node_type"),
            },
            execution_summary={
                "status": "dry_run_preview_complete",
                "dry_run_mode": True,
                "step_count": len(step_results),
                "requires_runtime_side_effects": False,
                "backend_specific_preview": True,
                "backend_target": "linux_mpls_dry_run",
            },
            step_results=step_results,
            validation_expectations={
                "pre_apply_validation": "required",
                "post_apply_validation": "preview_only",
                "real_linux_mutation": "not_applicable_in_dry_run",
                "backend_adapter_validation": "required_before_real_execution",
            },
        )
