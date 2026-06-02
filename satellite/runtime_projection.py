#!/usr/bin/env python3
# encoding: utf-8

"""Dry-run runtime projection boundary for Iteration 2."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from contracts import (
    RUNTIME_ACTION_MODEL_VERSION,
    RuntimeUpdatePlan,
    _collect_forbidden_key_paths,
    validate_runtime_update_plan,
)


RUNTIME_PROJECTION_SCHEMA_VERSION = "satellite-runtime-projection-v1alpha1"
RUNTIME_INSTALLER_PREVIEW_FORBIDDEN_FIELDS = (
    "argv",
    "command_family",
    "shell_command",
    "docker_exec_command",
    "docker_network_operation",
    "iptables_command",
    "ip_route_command",
    "sysctl_command",
)
RUNTIME_INSTALLER_PREVIEW_TYPES = (
    "service_binding_preview",
    "traffic_classification_preview",
    "forwarding_entry_preview",
    "node_capability_preview",
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ProjectionValidationResult:
    validation_scope: str
    projector_name: str
    source_plan_id: str
    projection_id: Optional[str]
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_scope": self.validation_scope,
            "projector_name": self.projector_name,
            "source_plan_id": self.source_plan_id,
            "projection_id": self.projection_id,
            "status": "valid" if self.is_valid else "issues_detected",
            "issues": list(self.issues),
            "issue_count": len(self.issues),
        }


@dataclass(frozen=True)
class DryRunInstallerPlan:
    schema_version: str
    projection_id: str
    source_plan: Dict[str, Any]
    projector: Dict[str, Any]
    apply_model: Dict[str, Any]
    node_installer_subplans: Dict[str, Dict[str, Any]]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != RUNTIME_PROJECTION_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.projection_id:
            issues.append("projection_id_missing")
        if not self.source_plan.get("plan_id"):
            issues.append("source_plan_missing_plan_id")
        if not self.projector.get("projector_name"):
            issues.append("projector_name_missing")
        if self.apply_model.get("projection_model") != "installer_intent_preview":
            issues.append(
                "apply_model_unexpected_projection_model:"
                f"{self.apply_model.get('projection_model')}"
            )
        if self.apply_model.get("dry_run_mode") is not True:
            issues.append("apply_model_not_dry_run")
        for node_id, subplan in sorted(self.node_installer_subplans.items()):
            if subplan.get("target_node_id") != node_id:
                issues.append(f"installer_subplan_target_mismatch:{node_id}")
            instructions = list(subplan.get("installer_intents", []))
            if len(instructions) == 0:
                issues.append(f"installer_subplan_without_intents:{node_id}")
            for index, instruction in enumerate(instructions, start=1):
                if instruction.get("target_node_id") != node_id:
                    issues.append(f"instruction_target_mismatch:{node_id}:{index}")
                issues.extend(
                    f"{node_id}:{index}:{issue}"
                    for issue in validate_installer_preview_instruction(instruction)
                )
        return issues

    def validation_result(self) -> ProjectionValidationResult:
        return ProjectionValidationResult(
            validation_scope="projection_output",
            projector_name=str(self.projector.get("projector_name")),
            source_plan_id=str(self.source_plan.get("plan_id")),
            projection_id=self.projection_id,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


def validate_installer_preview_instruction(instruction: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    instruction_id = str(instruction.get("instruction_id") or "")
    instruction_type = str(instruction.get("instruction_type") or "")
    if not instruction_id:
        issues.append("instruction_id_missing")
    if instruction_type not in RUNTIME_INSTALLER_PREVIEW_TYPES:
        issues.append(
            f"instruction_type_unsupported:{instruction_id or '<missing>'}:{instruction_type}"
        )
    if not instruction.get("source_action_id"):
        issues.append(f"instruction_source_action_missing:{instruction_id or '<missing>'}")
    if not instruction.get("target_node_id"):
        issues.append(f"instruction_target_node_missing:{instruction_id or '<missing>'}")
    if not isinstance(instruction.get("intent"), dict) or len(instruction.get("intent", {})) == 0:
        issues.append(f"instruction_intent_missing_or_invalid:{instruction_id or '<missing>'}")
    preview = instruction.get("execution_preview")
    if not isinstance(preview, dict):
        issues.append(f"instruction_execution_preview_missing:{instruction_id or '<missing>'}")
    else:
        if preview.get("mode") != "dry_run":
            issues.append(f"instruction_execution_preview_not_dry_run:{instruction_id}")
        if not preview.get("candidate_backends"):
            issues.append(f"instruction_candidate_backends_missing:{instruction_id}")
    forbidden_paths = _collect_forbidden_key_paths(
        instruction,
        forbidden_keys=RUNTIME_INSTALLER_PREVIEW_FORBIDDEN_FIELDS,
        path=f"installer_preview_instruction[{instruction_id or '<missing>'}]",
    )
    issues.extend(
        f"installer_preview_contains_forbidden_field:{path}"
        for path in forbidden_paths
    )
    return issues


class RuntimeProjectorError(RuntimeError):
    """Base error for runtime projection failures."""


class RuntimeProjectionInputValidationError(RuntimeProjectorError):
    """Raised when a RuntimeUpdatePlan does not satisfy projector requirements."""

    def __init__(self, result: ProjectionValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "runtime projection input validation failed")


class RuntimeProjectionOutputValidationError(RuntimeProjectorError):
    """Raised when a generated dry-run projection is invalid."""

    def __init__(self, result: ProjectionValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "runtime projection output validation failed")


class RuntimeProjector(ABC):
    """Replaceable projection boundary from runtime plan to installer preview."""

    projector_name: str
    projector_kind: str
    projector_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "projector_name": self.projector_name,
            "projector_kind": self.projector_kind,
            "projector_version": self.projector_version,
        }

    def validate_input(self, runtime_update_plan: RuntimeUpdatePlan) -> ProjectionValidationResult:
        issues = list(validate_runtime_update_plan(runtime_update_plan).issues)
        if runtime_update_plan.apply_model.get("action_model") != RUNTIME_ACTION_MODEL_VERSION:
            issues.append(
                "runtime_projection_input_unexpected_action_model:"
                f"{runtime_update_plan.apply_model.get('action_model')}"
            )
        if len(runtime_update_plan.node_subplans) == 0:
            issues.append("runtime_projection_input_missing_node_subplans")
        return ProjectionValidationResult(
            validation_scope="projection_input",
            projector_name=self.projector_name,
            source_plan_id=runtime_update_plan.plan_id,
            projection_id=None,
            issues=tuple(issues),
        )

    def validate_output(
        self,
        runtime_update_plan: RuntimeUpdatePlan,
        installer_plan: DryRunInstallerPlan,
    ) -> ProjectionValidationResult:
        issues = list(installer_plan.validate())
        if installer_plan.source_plan.get("plan_id") != runtime_update_plan.plan_id:
            issues.append("runtime_projection_output_source_plan_mismatch")
        plan_actions_by_node = {
            node_id: {
                str(action.get("action_id"))
                for action in subplan.get("actions", [])
            }
            for node_id, subplan in runtime_update_plan.node_subplans.items()
        }
        for node_id, subplan in sorted(installer_plan.node_installer_subplans.items()):
            if node_id not in runtime_update_plan.node_subplans:
                issues.append(f"runtime_projection_output_unknown_target_node:{node_id}")
            for index, instruction in enumerate(subplan.get("installer_intents", []), start=1):
                source_action_id = str(instruction.get("source_action_id"))
                if source_action_id not in plan_actions_by_node.get(node_id, set()):
                    issues.append(
                        f"runtime_projection_output_unknown_source_action:{node_id}:{index}:{source_action_id}"
                    )
        return ProjectionValidationResult(
            validation_scope="projection_output",
            projector_name=self.projector_name,
            source_plan_id=runtime_update_plan.plan_id,
            projection_id=installer_plan.projection_id,
            issues=tuple(issues),
        )

    def project(self, runtime_update_plan: RuntimeUpdatePlan) -> DryRunInstallerPlan:
        input_result = self.validate_input(runtime_update_plan)
        if not input_result.is_valid:
            raise RuntimeProjectionInputValidationError(input_result)
        installer_plan = self.build_installer_plan(runtime_update_plan)
        output_result = self.validate_output(runtime_update_plan, installer_plan)
        if not output_result.is_valid:
            raise RuntimeProjectionOutputValidationError(output_result)
        return installer_plan

    @abstractmethod
    def build_installer_plan(self, runtime_update_plan: RuntimeUpdatePlan) -> DryRunInstallerPlan:
        """Project one RuntimeUpdatePlan into a dry-run installer preview."""


class GenericDryRunProjector(RuntimeProjector):
    """Generic dry-run installer-intent projector for typed planner actions."""

    projector_name = "generic_dry_run_projector"
    projector_kind = "installer_preview"
    projector_version = "0.1.0"

    def _instruction_preview(
        self,
        *,
        backend_family: str,
        realization_scope: str,
        candidate_backends: List[str],
        summary: str,
    ) -> Dict[str, Any]:
        return {
            "mode": "dry_run",
            "backend_family": backend_family,
            "realization_scope": realization_scope,
            "candidate_backends": list(candidate_backends),
            "summary": summary,
        }

    def _instruction_from_action(
        self,
        *,
        node_id: str,
        action: Dict[str, Any],
    ) -> Dict[str, Any]:
        action_id = str(action.get("action_id"))
        action_type = str(action.get("action_type"))
        intent = dict(action.get("intent", {}))
        instruction_type = "node_capability_preview"
        installer_intent: Dict[str, Any]
        execution_preview: Dict[str, Any]

        if action_type == "service_binding":
            instruction_type = "service_binding_preview"
            installer_intent = {
                "binding_kind": intent.get("binding_kind"),
                "subject": dict(intent.get("subject", {})),
                "target": dict(intent.get("target", {})),
                "binding_scope": intent.get("binding_scope"),
            }
            execution_preview = self._instruction_preview(
                backend_family="generic_service_binding",
                realization_scope="node_local",
                candidate_backends=[
                    "container_local_installer",
                    "host_daemon_adapter",
                    "research_runtime_backend",
                ],
                summary=(
                    f"Dry-run bind {installer_intent.get('binding_kind')} on {node_id}"
                ),
            )
        elif action_type == "traffic_classification":
            instruction_type = "traffic_classification_preview"
            installer_intent = {
                "classification_scope": intent.get("classification_scope"),
                "match": dict(intent.get("match", {})),
                "outcome": dict(intent.get("outcome", {})),
            }
            execution_preview = self._instruction_preview(
                backend_family="generic_traffic_classification",
                realization_scope="node_local_ingress",
                candidate_backends=[
                    "container_local_installer",
                    "research_runtime_backend",
                ],
                summary=f"Dry-run classify ingress traffic on {node_id}",
            )
        elif action_type == "forwarding_entry":
            instruction_type = "forwarding_entry_preview"
            installer_intent = {
                "forwarding_plane": intent.get("forwarding_plane"),
                "match": dict(intent.get("match", {})),
                "outcome": dict(intent.get("outcome", {})),
            }
            execution_preview = self._instruction_preview(
                backend_family="generic_forwarding_state",
                realization_scope="node_local_forwarding",
                candidate_backends=[
                    "container_local_installer",
                    "optimized_runtime_manager",
                ],
                summary=(
                    "Dry-run realize forwarding state "
                    f"for {installer_intent.get('forwarding_plane')} on {node_id}"
                ),
            )
        else:
            installer_intent = {
                "capability": intent.get("capability"),
                "operation": intent.get("operation"),
                "capability_scope": dict(intent.get("capability_scope", {})),
            }
            execution_preview = self._instruction_preview(
                backend_family="generic_node_capability",
                realization_scope="node_local",
                candidate_backends=[
                    "container_local_installer",
                    "research_runtime_backend",
                ],
                summary=f"Dry-run ensure node capability on {node_id}",
            )

        return {
            "instruction_id": f"installer-preview:{action_id}",
            "instruction_type": instruction_type,
            "source_action_id": action_id,
            "source_action_type": action_type,
            "target_node_id": node_id,
            "intent": installer_intent,
            "execution_preview": execution_preview,
            "validation": dict(action.get("validation", {})),
            "metadata": {
                "projector_family": self.projector_name,
                "source_action_metadata": dict(action.get("metadata", {})),
            },
        }

    def build_installer_plan(self, runtime_update_plan: RuntimeUpdatePlan) -> DryRunInstallerPlan:
        node_installer_subplans: Dict[str, Dict[str, Any]] = {}
        for node_id, subplan in sorted(runtime_update_plan.node_subplans.items()):
            installer_intents = [
                self._instruction_from_action(node_id=node_id, action=action)
                for action in subplan.get("actions", [])
            ]
            node_installer_subplans[node_id] = {
                "target_node_id": node_id,
                "target_node_type": subplan.get("target_node_type"),
                "projection_mode": "dry_run_installer_intents",
                "installer_intents": installer_intents,
                "execution_preview": {
                    "instruction_count": len(installer_intents),
                    "ordered_instruction_ids": [
                        str(instruction.get("instruction_id"))
                        for instruction in installer_intents
                    ],
                    "requires_runtime_side_effects": False,
                },
            }
        projection_seed = {
            "source_plan_id": runtime_update_plan.plan_id,
            "projector": self.metadata(),
            "node_ids": sorted(node_installer_subplans),
            "instruction_ids_by_node": {
                node_id: subplan["execution_preview"]["ordered_instruction_ids"]
                for node_id, subplan in sorted(node_installer_subplans.items())
            },
        }
        projection_id = f"rtproj-{_fingerprint(projection_seed)}"
        return DryRunInstallerPlan(
            schema_version=RUNTIME_PROJECTION_SCHEMA_VERSION,
            projection_id=projection_id,
            source_plan={
                "plan_id": runtime_update_plan.plan_id,
                "snapshot_id": runtime_update_plan.target_snapshot.get("snapshot_id"),
                "slice_id": runtime_update_plan.target_snapshot.get("slice_id"),
                "replacement_semantics": dict(runtime_update_plan.replacement_semantics),
                "validation_expectations": dict(
                    runtime_update_plan.validation_expectations
                ),
            },
            projector={
                **self.metadata(),
                "dry_run_mode": True,
                "backend_policy_visibility": "none",
            },
            apply_model={
                "projection_model": "installer_intent_preview",
                "action_model": runtime_update_plan.apply_model.get("action_model"),
                "dry_run_mode": True,
                "execution_scope": "node_local_projection",
            },
            node_installer_subplans=node_installer_subplans,
            validation_expectations={
                "projection_input_validation": "required_before_projection",
                "projection_output_validation": "required_before_publish",
                "runtime_execution_validation": "not_applicable_in_dry_run",
            },
        )
