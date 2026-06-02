#!/usr/bin/env python3
# encoding: utf-8

"""Dry-run node-local agent boundary for Iteration 2 runtime realization."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from contracts import _collect_forbidden_key_paths
from distribution_store import DistributedEnvelopeBatch
from installer_runtime import (
    DryRunInstallerExecution,
    NodeLocalInstaller,
)


NODE_LOCAL_AGENT_REPORT_SCHEMA_VERSION = "satellite-runtime-agent-report-v1alpha1"
NODE_LOCAL_AGENT_REPORT_FORBIDDEN_FIELDS = (
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
class AgentValidationResult:
    validation_scope: str
    agent_name: str
    target_node_id: str
    execution_report_id: Optional[str]
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_scope": self.validation_scope,
            "agent_name": self.agent_name,
            "target_node_id": self.target_node_id,
            "execution_report_id": self.execution_report_id,
            "status": "valid" if self.is_valid else "issues_detected",
            "issues": list(self.issues),
            "issue_count": len(self.issues),
        }


@dataclass(frozen=True)
class NodeLocalAgentExecutionReport:
    schema_version: str
    execution_report_id: str
    source_distribution: Dict[str, Any]
    source_envelope: Dict[str, Any]
    agent: Dict[str, Any]
    installer: Dict[str, Any]
    target_node: Dict[str, Any]
    execution_summary: Dict[str, Any]
    installer_execution: Dict[str, Any]
    observed_state: Dict[str, Any]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != NODE_LOCAL_AGENT_REPORT_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.execution_report_id:
            issues.append("execution_report_id_missing")
        if not self.source_distribution.get("distribution_id"):
            issues.append("source_distribution_missing_id")
        if not self.source_envelope.get("envelope_id"):
            issues.append("source_envelope_missing_id")
        if not self.agent.get("agent_name"):
            issues.append("agent_name_missing")
        if not self.installer.get("installer_name"):
            issues.append("installer_name_missing")
        if self.execution_summary.get("dry_run_mode") is not True:
            issues.append("execution_summary_not_dry_run")
        if self.execution_summary.get("status") != "dry_run_execution_complete":
            issues.append(
                "execution_summary_unexpected_status:"
                f"{self.execution_summary.get('status')}"
            )
        if self.observed_state.get("runtime_mutation_performed") is not False:
            issues.append("observed_state_unexpected_runtime_mutation_flag")
        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=NODE_LOCAL_AGENT_REPORT_FORBIDDEN_FIELDS,
            path="node_local_agent_execution_report",
        )
        issues.extend(
            f"node_local_agent_execution_report_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )
        return issues

    def validation_result(self) -> AgentValidationResult:
        return AgentValidationResult(
            validation_scope="agent_output",
            agent_name=str(self.agent.get("agent_name")),
            target_node_id=str(self.target_node.get("node_id")),
            execution_report_id=self.execution_report_id,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


class NodeLocalAgentError(RuntimeError):
    """Base error for node-local agent failures."""


class NodeLocalAgentInputValidationError(NodeLocalAgentError):
    """Raised when a node delivery view does not satisfy agent requirements."""

    def __init__(self, result: AgentValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "node-local agent input validation failed")


class NodeLocalAgentOutputValidationError(NodeLocalAgentError):
    """Raised when a generated node-local execution report is invalid."""

    def __init__(self, result: AgentValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "node-local agent output validation failed")


class NodeLocalAgent(ABC):
    """Replaceable node-local agent boundary."""

    agent_name: str
    agent_kind: str
    agent_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "agent_name": self.agent_name,
            "agent_kind": self.agent_kind,
            "agent_version": self.agent_version,
        }

    def validate_input(
        self,
        distribution_batch: DistributedEnvelopeBatch,
        node_delivery_view: Dict[str, Any],
        target_node_id: str,
    ) -> AgentValidationResult:
        issues = list(distribution_batch.validate())
        if node_delivery_view.get("target_node_id") != target_node_id:
            issues.append(
                "agent_input_target_node_mismatch:"
                f"{target_node_id}:{node_delivery_view.get('target_node_id')}"
            )
        envelope = node_delivery_view.get("node_envelope", {})
        if envelope.get("target_node_id") != target_node_id:
            issues.append(
                "agent_input_envelope_target_node_mismatch:"
                f"{target_node_id}:{envelope.get('target_node_id')}"
            )
        agent_contract = envelope.get("agent_contract", {})
        if agent_contract.get("applicability_scope") != "node_local_only":
            issues.append(
                "agent_input_unexpected_applicability_scope:"
                f"{target_node_id}:{agent_contract.get('applicability_scope')}"
            )
        if agent_contract.get("planner_policy_visibility") != "none":
            issues.append(f"agent_input_unexpected_policy_visibility:{target_node_id}")
        return AgentValidationResult(
            validation_scope="agent_input",
            agent_name=self.agent_name,
            target_node_id=target_node_id,
            execution_report_id=None,
            issues=tuple(issues),
        )

    def validate_output(
        self,
        distribution_batch: DistributedEnvelopeBatch,
        node_delivery_view: Dict[str, Any],
        execution_report: NodeLocalAgentExecutionReport,
        target_node_id: str,
    ) -> AgentValidationResult:
        issues = list(execution_report.validate())
        if (
            execution_report.source_distribution.get("distribution_id")
            != distribution_batch.distribution_id
        ):
            issues.append("agent_output_source_distribution_mismatch")
        if execution_report.target_node.get("node_id") != target_node_id:
            issues.append("agent_output_target_node_mismatch")
        envelope_id = str(node_delivery_view.get("node_envelope", {}).get("envelope_id"))
        if execution_report.source_envelope.get("envelope_id") != envelope_id:
            issues.append("agent_output_source_envelope_mismatch")
        return AgentValidationResult(
            validation_scope="agent_output",
            agent_name=self.agent_name,
            target_node_id=target_node_id,
            execution_report_id=execution_report.execution_report_id,
            issues=tuple(issues),
        )

    def execute(
        self,
        *,
        distribution_batch: DistributedEnvelopeBatch,
        target_node_id: str,
        installer: NodeLocalInstaller,
    ) -> NodeLocalAgentExecutionReport:
        node_delivery_view = dict(distribution_batch.node_delivery_views[target_node_id])
        input_result = self.validate_input(
            distribution_batch,
            node_delivery_view,
            target_node_id,
        )
        if not input_result.is_valid:
            raise NodeLocalAgentInputValidationError(input_result)
        execution_report = self.build_execution_report(
            distribution_batch=distribution_batch,
            node_delivery_view=node_delivery_view,
            target_node_id=target_node_id,
            installer=installer,
        )
        output_result = self.validate_output(
            distribution_batch,
            node_delivery_view,
            execution_report,
            target_node_id,
        )
        if not output_result.is_valid:
            raise NodeLocalAgentOutputValidationError(output_result)
        return execution_report

    @abstractmethod
    def build_execution_report(
        self,
        *,
        distribution_batch: DistributedEnvelopeBatch,
        node_delivery_view: Dict[str, Any],
        target_node_id: str,
        installer: NodeLocalInstaller,
    ) -> NodeLocalAgentExecutionReport:
        """Execute one dry-run node-local agent pass for a single node."""


class GenericDryRunNodeLocalAgent(NodeLocalAgent):
    """Generic dry-run node-local agent for one node-local delivery view."""

    agent_name = "generic_dry_run_node_local_agent"
    agent_kind = "node_local_runtime_agent"
    agent_version = "0.1.0"

    def build_execution_report(
        self,
        *,
        distribution_batch: DistributedEnvelopeBatch,
        node_delivery_view: Dict[str, Any],
        target_node_id: str,
        installer: NodeLocalInstaller,
    ) -> NodeLocalAgentExecutionReport:
        node_envelope = dict(node_delivery_view.get("node_envelope", {}))
        installer_execution: DryRunInstallerExecution = installer.apply(node_envelope)
        report_seed = {
            "distribution_id": distribution_batch.distribution_id,
            "target_node_id": target_node_id,
            "agent": self.metadata(),
            "installer_execution_id": installer_execution.execution_id,
        }
        execution_report_id = f"agentexec-{_fingerprint(report_seed)}"
        return NodeLocalAgentExecutionReport(
            schema_version=NODE_LOCAL_AGENT_REPORT_SCHEMA_VERSION,
            execution_report_id=execution_report_id,
            source_distribution={
                "distribution_id": distribution_batch.distribution_id,
                "source_envelope_batch_id": distribution_batch.source_envelope_batch.get(
                    "envelope_batch_id"
                ),
                "delivery_mode": node_delivery_view.get("delivery_mode"),
            },
            source_envelope={
                "envelope_id": node_envelope.get("envelope_id"),
                "envelope_kind": node_envelope.get("envelope_kind"),
            },
            agent={
                **self.metadata(),
                "dry_run_mode": True,
                "topology_policy_visibility": "none",
            },
            installer=installer.metadata(),
            target_node={
                "node_id": target_node_id,
                "node_type": node_envelope.get("target_node_type"),
            },
            execution_summary={
                "status": "dry_run_execution_complete",
                "dry_run_mode": True,
                "applicability_scope": "node_local_only",
                "installer_execution_id": installer_execution.execution_id,
            },
            installer_execution=installer_execution.to_dict(),
            observed_state={
                "runtime_mutation_performed": False,
                "observed_effect": "installer_preview_only",
                "local_status_report": "dry_run_local_status",
            },
            validation_expectations={
                "input_validation": "required_before_installer_call",
                "installer_validation": "required_during_local_execution",
                "post_execution_reporting": "required",
            },
        )
