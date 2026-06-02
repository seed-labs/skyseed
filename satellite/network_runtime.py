#!/usr/bin/env python3
# encoding: utf-8

"""Minimal real NetworkMutationPlan execution for Iteration 2 experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from contracts import NetworkMutationPlan
from controller import SatelliteController
from network_mutation import dynamic_slot_state_from_link_state


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _slot_summary(slot_state: Dict[str, Any] | None) -> Dict[str, Any]:
    return {
        "occupied": bool((slot_state or {}).get("occupied", False)),
        "binding": dict((slot_state or {}).get("binding") or {}),
    }


def _expected_summary(operation: Dict[str, Any], *, source: bool) -> Dict[str, Any]:
    return {
        "occupied": bool(operation.get("source_occupied" if source else "target_occupied", False)),
        "binding": dict(operation.get("source_binding" if source else "target_binding", {}) or {}),
    }


def _inverse_operation(operation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **dict(operation),
        "source_binding": dict(operation.get("target_binding", {}) or {}),
        "target_binding": dict(operation.get("source_binding", {}) or {}),
        "source_occupied": bool(operation.get("target_occupied", False)),
        "target_occupied": bool(operation.get("source_occupied", False)),
    }


@dataclass(frozen=True)
class NetworkMutationApplyResult:
    execution_id: str
    boundary: Dict[str, Any]
    target_scope: Dict[str, Any]
    preflight: Dict[str, Any]
    apply_results: List[Dict[str, Any]]
    apply_verification: Dict[str, Any]
    outcome: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkMutationRollbackResult:
    execution_id: str
    boundary: Dict[str, Any]
    rollback_results: List[Dict[str, Any]]
    rollback_verification: Dict[str, Any]
    outcome: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NetworkMutationExecutionError(RuntimeError):
    """Raised when a real network-mutation transition fails validation or apply."""


class MinimalRealNetworkMutationExecutor:
    """Apply NetworkMutationPlan deltas through a transitional host-runtime boundary."""

    executor_name = "minimal_real_network_mutation_executor"
    executor_kind = "host_side_slot_link_runtime_boundary"
    executor_version = "0.1.0"

    def _controller(self, *, topology_path: Path, link_state_path: Path) -> SatelliteController:
        args = argparse.Namespace(
            topology_config=str(topology_path),
            link_state=str(link_state_path),
            fast_runtime_apply=True,
        )
        return SatelliteController(args)

    def _state_by_name(self, *, link_state_path: Path) -> Dict[str, Dict[str, Any]]:
        return dynamic_slot_state_from_link_state(_load_json(link_state_path))

    def _verify_plan_state(
        self,
        *,
        state_by_name: Dict[str, Dict[str, Any]],
        operations: Sequence[Dict[str, Any]],
        source: bool,
    ) -> Dict[str, Any]:
        mismatches: List[Dict[str, Any]] = []
        for operation in operations:
            slot_name = str(operation.get("slot_name"))
            actual = _slot_summary(state_by_name.get(slot_name))
            expected = _expected_summary(operation, source=source)
            if actual != expected:
                mismatches.append(
                    {
                        "slot_name": slot_name,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        return {
            "checked_slot_count": len(operations),
            "mismatches": mismatches,
            "all_valid": not mismatches,
        }

    def _apply_operation(
        self,
        *,
        controller: SatelliteController,
        operation: Dict[str, Any],
    ) -> Dict[str, Any]:
        slot_name = str(operation.get("slot_name"))
        slot_type = str(operation.get("slot_type"))
        operation_name = str(operation.get("operation"))
        source_binding = dict(operation.get("source_binding", {}) or {})
        target_binding = dict(operation.get("target_binding", {}) or {})

        if slot_type == "ut_access":
            ut_id = str((target_binding or source_binding).get("ut_id"))
            if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                controller.detach_ut(ut_id, requested_slot_name=slot_name)
            if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                controller.attach_ut(
                    f"{ut_id}:{target_binding['satellite_id']}",
                    requested_slot_name=slot_name,
                )
        elif slot_type == "gw_access":
            gateway_id = str((target_binding or source_binding).get("gateway_id"))
            if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                controller.detach_gateway(gateway_id, requested_slot_name=slot_name)
            if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                controller.attach_gateway(
                    f"{gateway_id}:{target_binding['satellite_id']}",
                    requested_slot_name=slot_name,
                )
        else:
            raise NetworkMutationExecutionError(
                f"unsupported real network mutation slot_type: {slot_type}"
            )

        resulting_state = self._state_by_name(link_state_path=Path(str(controller.args.link_state)))
        return {
            "slot_name": slot_name,
            "slot_type": slot_type,
            "operation": operation_name,
            "resulting_state": _slot_summary(resulting_state.get(slot_name)),
        }

    def apply(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
    ) -> NetworkMutationApplyResult:
        controller = self._controller(topology_path=topology_path, link_state_path=link_state_path)
        preflight_state = self._state_by_name(link_state_path=link_state_path)
        preflight_verification = self._verify_plan_state(
            state_by_name=preflight_state,
            operations=network_mutation_plan.operations,
            source=True,
        )
        if not preflight_verification["all_valid"]:
            raise NetworkMutationExecutionError(
                "host-side slot state does not match the NetworkMutationPlan source state"
            )

        apply_results: List[Dict[str, Any]] = []
        applied_operations: List[Dict[str, Any]] = []
        failure_reason: str | None = None

        try:
            for operation in network_mutation_plan.operations:
                apply_results.append(
                    self._apply_operation(controller=controller, operation=operation)
                )
                applied_operations.append(dict(operation))
            apply_state = self._state_by_name(link_state_path=link_state_path)
            apply_verification = self._verify_plan_state(
                state_by_name=apply_state,
                operations=network_mutation_plan.operations,
                source=False,
            )
            if not apply_verification["all_valid"]:
                raise NetworkMutationExecutionError(
                    "host-side slot state does not match the NetworkMutationPlan target state"
                )
        except Exception as exc:  # noqa: BLE001
            failure_reason = str(exc)
            apply_state = self._state_by_name(link_state_path=link_state_path)
            apply_verification = self._verify_plan_state(
                state_by_name=apply_state,
                operations=network_mutation_plan.operations,
                source=False,
            )

        execution_seed = {
            "mutation_id": network_mutation_plan.mutation_id,
            "operation_count": len(network_mutation_plan.operations),
            "executor_name": self.executor_name,
        }
        execution_id = f"hostmut-{_fingerprint(execution_seed)}"
        return NetworkMutationApplyResult(
            execution_id=execution_id,
            boundary={
                "executor_name": self.executor_name,
                "executor_kind": self.executor_kind,
                "executor_version": self.executor_version,
                "real_execution": True,
                "consumer": "host_network_runtime_manager",
                "execution_transport": "transitional_controller_public_boundary",
                "future_runtime_target": "host_runtime_manager_daemon",
                "concurrency_model": "serial_bounded",
            },
            target_scope=dict(network_mutation_plan.target_scope),
            preflight=preflight_verification,
            apply_results=apply_results,
            apply_verification=apply_verification,
            outcome={
                "apply_success": failure_reason is None
                and bool(apply_verification.get("all_valid")),
                "failure_reason": failure_reason,
                "operation_count": len(network_mutation_plan.operations),
                "applied_operations": applied_operations,
            },
        )

    def rollback(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
        applied_operations: Sequence[Dict[str, Any]],
    ) -> NetworkMutationRollbackResult:
        rollback_results: List[Dict[str, Any]] = []
        rollback_failure_reason: str | None = None
        rollback_controller = self._controller(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        try:
            for operation in reversed(list(applied_operations)):
                rollback_results.append(
                    self._apply_operation(
                        controller=rollback_controller,
                        operation=_inverse_operation(operation),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            rollback_failure_reason = str(exc)
        rollback_state = self._state_by_name(link_state_path=link_state_path)
        rollback_verification = self._verify_plan_state(
            state_by_name=rollback_state,
            operations=network_mutation_plan.operations,
            source=True,
        )
        if rollback_failure_reason is not None:
            rollback_verification["rollback_failure_reason"] = rollback_failure_reason

        execution_seed = {
            "mutation_id": network_mutation_plan.mutation_id,
            "operation_count": len(applied_operations),
            "executor_name": self.executor_name,
            "phase": "rollback",
        }
        execution_id = f"hostmut-{_fingerprint(execution_seed)}"
        return NetworkMutationRollbackResult(
            execution_id=execution_id,
            boundary={
                "executor_name": self.executor_name,
                "executor_kind": self.executor_kind,
                "executor_version": self.executor_version,
                "real_execution": True,
                "consumer": "host_network_runtime_manager",
                "execution_transport": "transitional_controller_public_boundary",
                "future_runtime_target": "host_runtime_manager_daemon",
                "concurrency_model": "serial_bounded",
            },
            rollback_results=rollback_results,
            rollback_verification=rollback_verification,
            outcome={
                "rollback_completed": bool(rollback_verification.get("all_valid")),
                "failure_reason": rollback_failure_reason,
                "operation_count": len(applied_operations),
            },
        )
