#!/usr/bin/env python3
# encoding: utf-8

"""Minimal real NetworkMutationPlan execution for Iteration 2 experiments."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import time
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
    inverse_name = str(operation.get("operation"))
    if inverse_name == "activate_slot_attachment":
        inverse_name = "deactivate_slot_attachment"
    elif inverse_name == "deactivate_slot_attachment":
        inverse_name = "activate_slot_attachment"
    return {
        **dict(operation),
        "operation": inverse_name,
        "source_binding": dict(operation.get("target_binding", {}) or {}),
        "target_binding": dict(operation.get("source_binding", {}) or {}),
        "source_occupied": bool(operation.get("target_occupied", False)),
        "target_occupied": bool(operation.get("source_occupied", False)),
    }


def _state_entry_from_operation(operation: Dict[str, Any], *, source: bool) -> Dict[str, Any]:
    return {
        "slot_name": str(operation.get("slot_name")),
        "slot_type": str(operation.get("slot_type")),
        "occupied": bool(operation.get("source_occupied" if source else "target_occupied", False)),
        "binding": dict(
            operation.get("source_binding" if source else "target_binding", {}) or {}
        ),
        "endpoints": [],
    }


def _summarize_operation_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    operation_counts: Dict[str, int] = {}
    per_operation_wall_seconds: Dict[str, float] = {}
    total_wall_seconds = 0.0
    for result in results:
        operation_name = str(result.get("operation"))
        wall_seconds = float(result.get("wall_seconds", 0.0))
        operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
        per_operation_wall_seconds[operation_name] = (
            per_operation_wall_seconds.get(operation_name, 0.0) + wall_seconds
        )
        total_wall_seconds += wall_seconds
    return {
        "operation_count": len(results),
        "operation_counts": operation_counts,
        "per_operation_wall_seconds": {
            name: round(seconds, 3)
            for name, seconds in sorted(per_operation_wall_seconds.items())
        },
        "total_wall_seconds": round(total_wall_seconds, 3),
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
        state_by_name: Dict[str, Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        slot_name = str(operation.get("slot_name"))
        slot_type = str(operation.get("slot_type"))
        operation_name = str(operation.get("operation"))
        source_binding = dict(operation.get("source_binding", {}) or {})
        target_binding = dict(operation.get("target_binding", {}) or {})
        captured_stdout = io.StringIO()
        started_at = time.perf_counter()

        with contextlib.redirect_stdout(captured_stdout):
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
            elif slot_type == "cross_plane_isl":
                if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                    controller.release_slot(slot_name)
                if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                    controller.connect_cross_plane(
                        (
                            f"{target_binding['satellite_a_id']}:"
                            f"{target_binding['satellite_b_id']}:"
                            f"{target_binding['satellite_a_direction']}:"
                            f"{target_binding['satellite_b_direction']}"
                        ),
                        requested_slot_name=slot_name,
                    )
            else:
                raise NetworkMutationExecutionError(
                    f"unsupported real network mutation slot_type: {slot_type}"
                )

        if state_by_name is not None:
            state_by_name[slot_name] = _state_entry_from_operation(operation, source=False)
        return {
            "slot_name": slot_name,
            "slot_type": slot_type,
            "operation": operation_name,
            "resulting_state": _slot_summary(_state_entry_from_operation(operation, source=False)),
            "wall_seconds": round(time.perf_counter() - started_at, 3),
            "controller_stdout": captured_stdout.getvalue().strip(),
        }

    def _apply_operations(
        self,
        *,
        controller: SatelliteController,
        link_state_path: Path,
        operations: Sequence[Dict[str, Any]],
        working_state: Dict[str, Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        apply_results: List[Dict[str, Any]] = []
        applied_operations: List[Dict[str, Any]] = []
        for operation in operations:
            apply_results.append(
                self._apply_operation(
                    controller=controller,
                    operation=operation,
                    state_by_name=working_state,
                )
            )
            applied_operations.append(dict(operation))
        apply_state = self._state_by_name(link_state_path=link_state_path)
        apply_verification = self._verify_plan_state(
            state_by_name=apply_state,
            operations=operations,
            source=False,
        )
        if not apply_verification["all_valid"]:
            raise NetworkMutationExecutionError(
                "host-side slot state does not match the NetworkMutationPlan target state"
            )
        return apply_results, applied_operations, apply_verification

    def _bootstrap_runtime_if_needed(
        self,
        *,
        controller: SatelliteController,
        link_state_path: Path,
        failure_reason: str,
    ) -> Dict[str, Any] | None:
        bootstrap_markers = (
            "rerun --init-slot-runtime",
            "slot bridge missing",
            "slot runtime is not initialized",
            "preconnected endpoint validation failed",
        )
        if not any(marker in failure_reason for marker in bootstrap_markers):
            return None
        initialized_state = controller.initialize_slot_runtime()
        refreshed_state = self._state_by_name(link_state_path=link_state_path)
        return {
            "trigger": failure_reason,
            "action": "initialize_slot_runtime",
            "slot_count": len(initialized_state.get("slots", [])),
            "post_bootstrap_runtime_initialized": bool(
                _load_json(link_state_path).get("runtime_initialized", False)
            ),
            "post_bootstrap_slot_count": len(refreshed_state),
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
        working_state = {
            slot_name: dict(slot_state)
            for slot_name, slot_state in preflight_state.items()
        }
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
        bootstrap_summary: Dict[str, Any] | None = None

        try:
            apply_results, applied_operations, apply_verification = self._apply_operations(
                controller=controller,
                link_state_path=link_state_path,
                operations=network_mutation_plan.operations,
                working_state=working_state,
            )
        except Exception as exc:  # noqa: BLE001
            failure_reason = str(exc)
            if not applied_operations:
                bootstrap_summary = self._bootstrap_runtime_if_needed(
                    controller=controller,
                    link_state_path=link_state_path,
                    failure_reason=failure_reason,
                )
                if bootstrap_summary is not None:
                    failure_reason = None
                    apply_results, applied_operations, apply_verification = self._apply_operations(
                        controller=controller,
                        link_state_path=link_state_path,
                        operations=network_mutation_plan.operations,
                        working_state=working_state,
                    )
                else:
                    apply_state = self._state_by_name(link_state_path=link_state_path)
                    apply_verification = self._verify_plan_state(
                        state_by_name=apply_state,
                        operations=network_mutation_plan.operations,
                        source=False,
                    )
            else:
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
                "bootstrap_model": (
                    "auto_init_slot_runtime_once_if_runtime_artifacts_missing"
                ),
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
                "operation_timing_summary": _summarize_operation_results(apply_results),
                "bootstrap_summary": bootstrap_summary,
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
        rollback_state = self._state_by_name(link_state_path=link_state_path)
        try:
            for operation in reversed(list(applied_operations)):
                rollback_results.append(
                    self._apply_operation(
                        controller=rollback_controller,
                        operation=_inverse_operation(operation),
                        state_by_name=rollback_state,
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
                "operation_timing_summary": _summarize_operation_results(rollback_results),
            },
        )
