#!/usr/bin/env python3
# encoding: utf-8

"""Dry-run distribution/store boundary for Iteration 2 runtime realization."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from skyseed_v1.runtime.envelope import (
    AGENT_ENVELOPE_SCHEMA_VERSION,
    AgentInstallerEnvelopeBatch,
)
from skyseed_v1.core.ir.contracts import _collect_forbidden_key_paths


DISTRIBUTION_STORE_SCHEMA_VERSION = "satellite-runtime-distribution-store-v1alpha1"
DISTRIBUTION_STORE_FORBIDDEN_FIELDS = (
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
    "watch_url",
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DistributionValidationResult:
    validation_scope: str
    distributor_name: str
    source_envelope_batch_id: str
    distribution_id: Optional[str]
    issues: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_scope": self.validation_scope,
            "distributor_name": self.distributor_name,
            "source_envelope_batch_id": self.source_envelope_batch_id,
            "distribution_id": self.distribution_id,
            "status": "valid" if self.is_valid else "issues_detected",
            "issues": list(self.issues),
            "issue_count": len(self.issues),
        }


@dataclass(frozen=True)
class DistributedEnvelopeBatch:
    schema_version: str
    distribution_id: str
    source_envelope_batch: Dict[str, Any]
    distributor: Dict[str, Any]
    store_contract: Dict[str, Any]
    node_delivery_views: Dict[str, Dict[str, Any]]
    validation_expectations: Dict[str, Any]

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.schema_version != DISTRIBUTION_STORE_SCHEMA_VERSION:
            issues.append(f"unexpected_schema_version:{self.schema_version}")
        if not self.distribution_id:
            issues.append("distribution_id_missing")
        if not self.source_envelope_batch.get("envelope_batch_id"):
            issues.append("source_envelope_batch_missing_id")
        if self.source_envelope_batch.get("schema_version") != AGENT_ENVELOPE_SCHEMA_VERSION:
            issues.append(
                "source_envelope_batch_unexpected_schema_version:"
                f"{self.source_envelope_batch.get('schema_version')}"
            )
        if not self.distributor.get("distributor_name"):
            issues.append("distributor_name_missing")
        if self.store_contract.get("transport_binding") != "transport_agnostic":
            issues.append(
                "store_contract_unexpected_transport_binding:"
                f"{self.store_contract.get('transport_binding')}"
            )
        if self.store_contract.get("dry_run_mode") is not True:
            issues.append("store_contract_not_dry_run")

        for node_id, delivery_view in sorted(self.node_delivery_views.items()):
            if delivery_view.get("target_node_id") != node_id:
                issues.append(f"delivery_view_target_mismatch:{node_id}")
            if delivery_view.get("delivery_scope") != "node_local_envelope":
                issues.append(
                    "delivery_view_unexpected_scope:"
                    f"{node_id}:{delivery_view.get('delivery_scope')}"
                )
            envelope = delivery_view.get("node_envelope")
            if not isinstance(envelope, dict):
                issues.append(f"delivery_view_missing_node_envelope:{node_id}")
                continue
            if envelope.get("target_node_id") != node_id:
                issues.append(f"delivery_view_envelope_target_mismatch:{node_id}")
            if not envelope.get("envelope_id"):
                issues.append(f"delivery_view_envelope_missing_id:{node_id}")
            availability = delivery_view.get("availability", {})
            if availability.get("dry_run_mode") is not True:
                issues.append(f"delivery_view_not_dry_run:{node_id}")
        forbidden_paths = _collect_forbidden_key_paths(
            asdict(self),
            forbidden_keys=DISTRIBUTION_STORE_FORBIDDEN_FIELDS,
            path="distributed_envelope_batch",
        )
        issues.extend(
            f"distributed_envelope_batch_contains_forbidden_field:{path}"
            for path in forbidden_paths
        )
        return issues

    def validation_result(self) -> DistributionValidationResult:
        return DistributionValidationResult(
            validation_scope="distribution_output",
            distributor_name=str(self.distributor.get("distributor_name")),
            source_envelope_batch_id=str(
                self.source_envelope_batch.get("envelope_batch_id")
            ),
            distribution_id=self.distribution_id,
            issues=tuple(self.validate()),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["validation"] = self.validation_result().to_dict()
        return data


class DistributionError(RuntimeError):
    """Base error for distribution/store failures."""


class DistributionInputValidationError(DistributionError):
    """Raised when an envelope batch does not satisfy distributor requirements."""

    def __init__(self, result: DistributionValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "distribution input validation failed")


class DistributionOutputValidationError(DistributionError):
    """Raised when a generated distribution batch is invalid."""

    def __init__(self, result: DistributionValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "distribution output validation failed")


class EnvelopeDistributor(ABC):
    """Replaceable distribution/store boundary for node-local agent envelopes."""

    distributor_name: str
    distributor_kind: str
    distributor_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "distributor_name": self.distributor_name,
            "distributor_kind": self.distributor_kind,
            "distributor_version": self.distributor_version,
        }

    def validate_input(
        self,
        envelope_batch: AgentInstallerEnvelopeBatch,
    ) -> DistributionValidationResult:
        issues = list(envelope_batch.validate())
        if len(envelope_batch.node_envelopes) == 0:
            issues.append("distribution_input_missing_node_envelopes")
        return DistributionValidationResult(
            validation_scope="distribution_input",
            distributor_name=self.distributor_name,
            source_envelope_batch_id=envelope_batch.envelope_batch_id,
            distribution_id=None,
            issues=tuple(issues),
        )

    def validate_output(
        self,
        envelope_batch: AgentInstallerEnvelopeBatch,
        distributed_batch: DistributedEnvelopeBatch,
    ) -> DistributionValidationResult:
        issues = list(distributed_batch.validate())
        if (
            distributed_batch.source_envelope_batch.get("envelope_batch_id")
            != envelope_batch.envelope_batch_id
        ):
            issues.append("distribution_output_source_envelope_batch_mismatch")
        for node_id, delivery_view in sorted(distributed_batch.node_delivery_views.items()):
            if node_id not in envelope_batch.node_envelopes:
                issues.append(f"distribution_output_unknown_target_node:{node_id}")
                continue
            source_envelope_id = str(envelope_batch.node_envelopes[node_id].get("envelope_id"))
            published_envelope_id = str(
                delivery_view.get("node_envelope", {}).get("envelope_id")
            )
            if source_envelope_id != published_envelope_id:
                issues.append(
                    "distribution_output_envelope_id_mismatch:"
                    f"{node_id}:{source_envelope_id}:{published_envelope_id}"
                )
        return DistributionValidationResult(
            validation_scope="distribution_output",
            distributor_name=self.distributor_name,
            source_envelope_batch_id=envelope_batch.envelope_batch_id,
            distribution_id=distributed_batch.distribution_id,
            issues=tuple(issues),
        )

    def publish(
        self,
        envelope_batch: AgentInstallerEnvelopeBatch,
    ) -> DistributedEnvelopeBatch:
        input_result = self.validate_input(envelope_batch)
        if not input_result.is_valid:
            raise DistributionInputValidationError(input_result)
        distributed_batch = self.build_distribution_batch(envelope_batch)
        output_result = self.validate_output(envelope_batch, distributed_batch)
        if not output_result.is_valid:
            raise DistributionOutputValidationError(output_result)
        return distributed_batch

    @abstractmethod
    def build_distribution_batch(
        self,
        envelope_batch: AgentInstallerEnvelopeBatch,
    ) -> DistributedEnvelopeBatch:
        """Publish one dry-run envelope batch into a transport-agnostic store view."""


class GenericDryRunEnvelopeDistributor(EnvelopeDistributor):
    """Generic dry-run envelope distributor that stays transport and store agnostic."""

    distributor_name = "generic_dry_run_envelope_distributor"
    distributor_kind = "distribution_store_boundary"
    distributor_version = "0.1.0"

    def build_distribution_batch(
        self,
        envelope_batch: AgentInstallerEnvelopeBatch,
    ) -> DistributedEnvelopeBatch:
        node_delivery_views = {}
        for node_id, envelope in sorted(envelope_batch.node_envelopes.items()):
            node_delivery_views[node_id] = {
                "target_node_id": node_id,
                "target_node_type": envelope.get("target_node_type"),
                "delivery_scope": "node_local_envelope",
                "delivery_mode": "push_or_pull_defined_outside_distribution",
                "envelope_ref": {
                    "envelope_id": envelope.get("envelope_id"),
                    "envelope_kind": envelope.get("envelope_kind"),
                },
                "availability": {
                    "published": True,
                    "dry_run_mode": True,
                    "retrieval_modes": [
                        "controller_push_candidate",
                        "node_pull_candidate",
                        "snapshot_store_candidate",
                    ],
                },
                "node_envelope": dict(envelope),
            }
        distribution_seed = {
            "source_envelope_batch_id": envelope_batch.envelope_batch_id,
            "distributor": self.metadata(),
            "node_ids": sorted(node_delivery_views),
            "envelope_ids": {
                node_id: view["envelope_ref"]["envelope_id"]
                for node_id, view in sorted(node_delivery_views.items())
            },
        }
        distribution_id = f"dist-{_fingerprint(distribution_seed)}"
        return DistributedEnvelopeBatch(
            schema_version=DISTRIBUTION_STORE_SCHEMA_VERSION,
            distribution_id=distribution_id,
            source_envelope_batch={
                "envelope_batch_id": envelope_batch.envelope_batch_id,
                "schema_version": envelope_batch.schema_version,
                "projection_id": envelope_batch.source_projection.get("projection_id"),
                "plan_id": envelope_batch.source_projection.get("plan_id"),
                "snapshot_id": envelope_batch.source_projection.get("snapshot_id"),
                "slice_id": envelope_batch.source_projection.get("slice_id"),
            },
            distributor={
                **self.metadata(),
                "delivery_policy_visibility": "none",
                "dry_run_mode": True,
            },
            store_contract={
                "transport_binding": "transport_agnostic",
                "store_binding": "store_agnostic",
                "distribution_model": "publish_and_expose_node_local_views",
                "dry_run_mode": True,
            },
            node_delivery_views=node_delivery_views,
            validation_expectations={
                "distribution_input_validation": "required_before_publish",
                "distribution_output_validation": "required_before_agent_fetch",
                "delivery_transport_validation": "not_applicable_in_dry_run",
            },
        )
