#!/usr/bin/env python3
# encoding: utf-8

"""Dry-run agent/installer envelope boundary for Iteration 2."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from contracts import _collect_forbidden_key_paths
from runtime_projection import (
    RUNTIME_PROJECTION_SCHEMA_VERSION,
    DryRunInstallerPlan,
    validate_installer_preview_instruction,
)


AGENT_ENVELOPE_SCHEMA_VERSION = "satellite-runtime-agent-envelope-v1alpha1"
AGENT_ENVELOPE_FORBIDDEN_FIELDS = (
    "argv",
    "command_family",
    "shell_command",
    "docker_exec_command",
    "docker_network_operation",
    "iptables_command",
    "ip_route_command",
    "sysctl_command",
    "etcd_endpoint",
    "grpc_endpoint",
    "http_endpoint",
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EnvelopeValidationResult:
    validation_scope: str
    envelope_builder_name: str
    source_projection_id: str
    envelope_batch_id: Optional[str]
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_scope": self.validation_scope,
            "envelope_builder_name": self.envelope_builder_name,
            "source_projection_id": self.source_projection_id,
            "envelope_batch_id": self.envelope_batch_id,
            "status": "valid" if self.is_valid else "issues_detected",
            "issues": list(self.issues),
            "issue_count": len(self.issues),
        }


@dataclass(frozen=True)
class AgentInstallerEnvelopeBatch:
    schema_version: str
    envelope_batch_id: str
    source_projection: Dict[str, Any]
    envelope_builder: Dict[str, Any]
    delivery_model: Dict[str, Any]
    node_envelopes: Dict[str, Dict[str, Any]]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != AGENT_ENVELOPE_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.envelope_batch_id:
            issues.append("envelope_batch_id_missing")
        if not self.source_projection.get("projection_id"):
            issues.append("source_projection_missing_projection_id")
        if self.source_projection.get("schema_version") != RUNTIME_PROJECTION_SCHEMA_VERSION:
            issues.append(
                "source_projection_unexpected_schema_version:"
                f"{self.source_projection.get('schema_version')}"
            )
        if not self.envelope_builder.get("envelope_builder_name"):
            issues.append("envelope_builder_name_missing")
        if self.delivery_model.get("transport_binding") != "transport_agnostic":
            issues.append(
                "delivery_model_unexpected_transport_binding:"
                f"{self.delivery_model.get('transport_binding')}"
            )
        if self.delivery_model.get("dry_run_mode") is not True:
            issues.append("delivery_model_not_dry_run")
        for node_id, envelope in sorted(self.node_envelopes.items()):
            if envelope.get("target_node_id") != node_id:
                issues.append(f"node_envelope_target_mismatch:{node_id}")
            if envelope.get("envelope_kind") != "node_local_installer_preview":
                issues.append(
                    "node_envelope_unexpected_kind:"
                    f"{node_id}:{envelope.get('envelope_kind')}"
                )
            agent_contract = envelope.get("agent_contract", {})
            if agent_contract.get("planner_policy_visibility") != "none":
                issues.append(f"node_envelope_unexpected_policy_visibility:{node_id}")
            installer_contract = envelope.get("installer_contract", {})
            if installer_contract.get("projection_model") != "installer_intent_preview":
                issues.append(
                    "node_envelope_unexpected_projection_model:"
                    f"{node_id}:{installer_contract.get('projection_model')}"
                )
            installer_intents = list(envelope.get("installer_intents", []))
            if len(installer_intents) == 0:
                issues.append(f"node_envelope_without_installer_intents:{node_id}")
            for index, instruction in enumerate(installer_intents, start=1):
                if instruction.get("target_node_id") != node_id:
                    issues.append(f"node_envelope_instruction_target_mismatch:{node_id}:{index}")
                issues.extend(
                    f"{node_id}:{index}:{issue}"
                    for issue in validate_installer_preview_instruction(instruction)
                )
        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=AGENT_ENVELOPE_FORBIDDEN_FIELDS,
            path="agent_installer_envelope_batch",
        )
        issues.extend(
            f"agent_installer_envelope_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )
        return issues

    def validation_result(self) -> EnvelopeValidationResult:
        return EnvelopeValidationResult(
            validation_scope="envelope_output",
            envelope_builder_name=str(
                self.envelope_builder.get("envelope_builder_name")
            ),
            source_projection_id=str(self.source_projection.get("projection_id")),
            envelope_batch_id=self.envelope_batch_id,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


class AgentEnvelopeError(RuntimeError):
    """Base error for agent-envelope failures."""


class AgentEnvelopeInputValidationError(AgentEnvelopeError):
    """Raised when a projection does not satisfy envelope-builder requirements."""

    def __init__(self, result: EnvelopeValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "agent envelope input validation failed")


class AgentEnvelopeOutputValidationError(AgentEnvelopeError):
    """Raised when a generated agent-envelope batch is invalid."""

    def __init__(self, result: EnvelopeValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "agent envelope output validation failed")


class AgentEnvelopeBuilder(ABC):
    """Replaceable boundary from installer preview to node-local agent envelopes."""

    envelope_builder_name: str
    envelope_builder_kind: str
    envelope_builder_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "envelope_builder_name": self.envelope_builder_name,
            "envelope_builder_kind": self.envelope_builder_kind,
            "envelope_builder_version": self.envelope_builder_version,
        }

    def validate_input(
        self,
        installer_plan: DryRunInstallerPlan,
    ) -> EnvelopeValidationResult:
        issues = list(installer_plan.validate())
        if installer_plan.apply_model.get("projection_model") != "installer_intent_preview":
            issues.append(
                "agent_envelope_input_unexpected_projection_model:"
                f"{installer_plan.apply_model.get('projection_model')}"
            )
        if installer_plan.apply_model.get("dry_run_mode") is not True:
            issues.append("agent_envelope_input_not_dry_run")
        if len(installer_plan.node_installer_subplans) == 0:
            issues.append("agent_envelope_input_missing_node_installer_subplans")
        return EnvelopeValidationResult(
            validation_scope="envelope_input",
            envelope_builder_name=self.envelope_builder_name,
            source_projection_id=installer_plan.projection_id,
            envelope_batch_id=None,
            issues=tuple(issues),
        )

    def validate_output(
        self,
        installer_plan: DryRunInstallerPlan,
        envelope_batch: AgentInstallerEnvelopeBatch,
    ) -> EnvelopeValidationResult:
        issues = list(envelope_batch.validate())
        if envelope_batch.source_projection.get("projection_id") != installer_plan.projection_id:
            issues.append("agent_envelope_output_source_projection_mismatch")
        plan_instructions_by_node = {
            node_id: {
                str(instruction.get("instruction_id"))
                for instruction in subplan.get("installer_intents", [])
            }
            for node_id, subplan in installer_plan.node_installer_subplans.items()
        }
        for node_id, envelope in sorted(envelope_batch.node_envelopes.items()):
            if node_id not in installer_plan.node_installer_subplans:
                issues.append(f"agent_envelope_output_unknown_target_node:{node_id}")
            for index, instruction in enumerate(envelope.get("installer_intents", []), start=1):
                instruction_id = str(instruction.get("instruction_id"))
                if instruction_id not in plan_instructions_by_node.get(node_id, set()):
                    issues.append(
                        "agent_envelope_output_unknown_instruction:"
                        f"{node_id}:{index}:{instruction_id}"
                    )
        return EnvelopeValidationResult(
            validation_scope="envelope_output",
            envelope_builder_name=self.envelope_builder_name,
            source_projection_id=installer_plan.projection_id,
            envelope_batch_id=envelope_batch.envelope_batch_id,
            issues=tuple(issues),
        )

    def build(self, installer_plan: DryRunInstallerPlan) -> AgentInstallerEnvelopeBatch:
        input_result = self.validate_input(installer_plan)
        if not input_result.is_valid:
            raise AgentEnvelopeInputValidationError(input_result)
        envelope_batch = self.build_envelope_batch(installer_plan)
        output_result = self.validate_output(installer_plan, envelope_batch)
        if not output_result.is_valid:
            raise AgentEnvelopeOutputValidationError(output_result)
        return envelope_batch

    @abstractmethod
    def build_envelope_batch(
        self,
        installer_plan: DryRunInstallerPlan,
    ) -> AgentInstallerEnvelopeBatch:
        """Build a dry-run node-local agent-envelope batch from one projection."""


class GenericDryRunAgentEnvelopeBuilder(AgentEnvelopeBuilder):
    """Generic dry-run builder for node-local agent and installer envelopes."""

    envelope_builder_name = "generic_dry_run_agent_envelope_builder"
    envelope_builder_kind = "node_local_agent_preview"
    envelope_builder_version = "0.1.0"

    def _node_envelope(
        self,
        *,
        node_id: str,
        installer_subplan: Dict[str, Any],
        projection_id: str,
        replacement_semantics: Dict[str, Any],
    ) -> Dict[str, Any]:
        installer_intents = [
            dict(instruction)
            for instruction in installer_subplan.get("installer_intents", [])
        ]
        ordered_instruction_ids = [
            str(instruction.get("instruction_id"))
            for instruction in installer_intents
        ]
        return {
            "envelope_id": f"agent-envelope:{projection_id}:{node_id}",
            "envelope_kind": "node_local_installer_preview",
            "target_node_id": node_id,
            "target_node_type": installer_subplan.get("target_node_type"),
            "delivery_contract": {
                "transport_binding": "transport_agnostic",
                "distribution_model": "push_or_pull_defined_outside_envelope",
                "replace_semantics": "replace_or_confirm",
                "dry_run_mode": True,
            },
            "agent_contract": {
                "applicability_scope": "node_local_only",
                "execution_model": "validate_then_preview_install",
                "planner_policy_visibility": "none",
                "reporting_contract": "local_status_preview",
            },
            "installer_contract": {
                "projection_model": "installer_intent_preview",
                "instruction_ordering": "preserve_projection_order",
                "instruction_count": len(installer_intents),
                "ordered_instruction_ids": ordered_instruction_ids,
                "requires_runtime_side_effects": False,
            },
            "installer_intents": installer_intents,
            "execution_preview": {
                "preview_mode": "no_real_mutation",
                "installer_step_count": len(installer_intents),
                "expected_report": "dry_run_local_status",
            },
            "rollback_semantics": {
                "rollback_on_failure": replacement_semantics.get("rollback_on_failure"),
                "replayable": replacement_semantics.get("replayable"),
                "idempotency_scope": replacement_semantics.get("idempotency_scope"),
            },
            "validation_expectations": {
                "pre_apply_validation": "required",
                "post_apply_validation": "preview_only",
                "local_observed_state_reporting": "required",
            },
        }

    def build_envelope_batch(
        self,
        installer_plan: DryRunInstallerPlan,
    ) -> AgentInstallerEnvelopeBatch:
        source_replacement = dict(
            installer_plan.source_plan.get("replacement_semantics", {})
        )
        node_envelopes = {
            node_id: self._node_envelope(
                node_id=node_id,
                installer_subplan=subplan,
                projection_id=installer_plan.projection_id,
                replacement_semantics=source_replacement,
            )
            for node_id, subplan in sorted(installer_plan.node_installer_subplans.items())
        }
        envelope_seed = {
            "source_projection_id": installer_plan.projection_id,
            "envelope_builder": self.metadata(),
            "node_ids": sorted(node_envelopes),
            "envelope_ids": {
                node_id: envelope.get("envelope_id")
                for node_id, envelope in sorted(node_envelopes.items())
            },
        }
        envelope_batch_id = f"agtenv-{_fingerprint(envelope_seed)}"
        return AgentInstallerEnvelopeBatch(
            schema_version=AGENT_ENVELOPE_SCHEMA_VERSION,
            envelope_batch_id=envelope_batch_id,
            source_projection={
                "projection_id": installer_plan.projection_id,
                "schema_version": installer_plan.schema_version,
                "plan_id": installer_plan.source_plan.get("plan_id"),
                "snapshot_id": installer_plan.source_plan.get("snapshot_id"),
                "slice_id": installer_plan.source_plan.get("slice_id"),
            },
            envelope_builder={
                **self.metadata(),
                "agent_role": "node_local_runtime_agent",
                "installer_role": "node_local_installer",
                "dry_run_mode": True,
            },
            delivery_model={
                "transport_binding": "transport_agnostic",
                "distribution_model": "store_or_push_defined_outside_builder",
                "delivery_scope": "node_local_envelopes",
                "dry_run_mode": True,
            },
            node_envelopes=node_envelopes,
            validation_expectations={
                "projection_validation": "required_before_envelope_build",
                "agent_envelope_validation": "required_before_delivery",
                "node_local_status_reporting": "preview_only",
            },
        )
