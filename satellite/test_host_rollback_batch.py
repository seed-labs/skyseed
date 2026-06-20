#!/usr/bin/env python3
# encoding: utf-8

import unittest
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contracts import NETWORK_MUTATION_PLAN_SCHEMA_VERSION, NetworkMutationPlan
from network_runtime import (
    InProcessHostRuntimeManager,
    _inverse_operation,
    _state_entry_from_operation,
)


def _operation(
    *,
    slot_name: str,
    source_binding: dict[str, Any],
    target_binding: dict[str, Any],
    source_parameters: dict[str, Any],
    target_parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "operation": "rebind_slot_attachment",
        "execution_domain": "host_runtime",
        "slot_name": slot_name,
        "slot_type": "ut_access",
        "source_occupied": bool(source_binding),
        "target_occupied": bool(target_binding),
        "source_binding": dict(source_binding),
        "target_binding": dict(target_binding),
        "source_endpoints": [],
        "target_endpoints": [],
        "source_runtime_parameters": dict(source_parameters),
        "target_runtime_parameters": dict(target_parameters),
    }


def _plan(mutation_id: str, operation: dict[str, Any]) -> NetworkMutationPlan:
    return NetworkMutationPlan(
        schema_version=NETWORK_MUTATION_PLAN_SCHEMA_VERSION,
        mutation_id=mutation_id,
        source_snapshot={},
        target_scope={"state_model": "delta_mutation"},
        execution_boundary={"consumer": "host_network_runtime_manager"},
        operations=[dict(operation)],
        validation_expectations={},
    )


class _FakeHostInstaller:
    installer_name = "fake_host_installer"
    installer_kind = "test_double"
    installer_version = "0"
    execution_transport = "fake"

    def __init__(self, state: dict[str, dict[str, Any]]) -> None:
        self.state = {slot_name: dict(slot_state) for slot_name, slot_state in state.items()}
        self.state_refresh_count = 0
        self.applied_operations: list[dict[str, Any]] = []

    def controller(self, **_: Any) -> object:
        return object()

    def state_by_name(self, *, link_state_path: Path) -> dict[str, dict[str, Any]]:
        self.state_refresh_count += 1
        return {slot_name: dict(slot_state) for slot_name, slot_state in self.state.items()}

    def apply_operation(
        self,
        *,
        controller: object,
        link_state_path: Path,
        operation: dict[str, Any],
        state_by_name: dict[str, dict[str, Any]] | None = None,
        persist_link_state: bool = True,
    ) -> dict[str, Any]:
        slot_name = str(operation["slot_name"])
        self.applied_operations.append(dict(operation))
        updated = _state_entry_from_operation(operation, source=False)
        self.state[slot_name] = updated
        if state_by_name is not None:
            state_by_name[slot_name] = dict(updated)
        return {
            "slot_name": slot_name,
            "slot_type": operation["slot_type"],
            "operation": operation["operation"],
            "delta_category": "changed",
            "change_class": "endpoint_rebind",
            "reused_slot_runtime": True,
            "resulting_state": {
                "occupied": updated["occupied"],
                "binding": dict(updated["binding"]),
                "runtime_parameters": dict(updated["runtime_parameters"]),
            },
            "wall_seconds": 0.001,
            "qdisc_apply_wall_seconds": 0.0,
            "metadata_update_wall_seconds": 0.0,
            "controller_stdout": "",
        }

    def public_runtime_summary(self) -> dict[str, Any]:
        return {"fake_installer": True, "state_refresh_count": self.state_refresh_count}


class HostRollbackBatchTests(unittest.TestCase):
    def test_rollback_many_compacts_final_teardown_by_slot(self) -> None:
        op1 = _operation(
            slot_name="ut101-access-slot0",
            source_binding={},
            target_binding={"ut_id": "ut101", "satellite_id": "stl-950001"},
            source_parameters={},
            target_parameters={"delay_ms": 1.0},
        )
        op2 = _operation(
            slot_name="ut101-access-slot0",
            source_binding={"ut_id": "ut101", "satellite_id": "stl-950001"},
            target_binding={"ut_id": "ut101", "satellite_id": "stl-950002"},
            source_parameters={"delay_ms": 1.0},
            target_parameters={"delay_ms": 2.0},
        )
        fake_installer = _FakeHostInstaller(
            {
                "ut101-access-slot0": _state_entry_from_operation(
                    op2,
                    source=False,
                )
            }
        )
        manager = InProcessHostRuntimeManager(installer=fake_installer)
        progress: list[dict[str, Any]] = []

        with TemporaryDirectory() as tmpdir:
            link_state_path = Path(tmpdir) / "link_state.json"
            link_state_path.write_text('{"runtime_initialized": true}\n', encoding="utf-8")
            result = manager.rollback_many(
                topology_path=Path("topology.json"),
                link_state_path=link_state_path,
                rollback_requests=[
                    {"host_plan": _plan("mut-1", op1), "applied_operations": [op1]},
                    {"host_plan": _plan("mut-2", op2), "applied_operations": [op2]},
                ],
                progress_callback=progress.append,
            )

        self.assertTrue(result["outcome"]["rollback_completed"])
        self.assertTrue(result["rollback_verification"]["all_valid"])
        self.assertEqual(len(result["host_rollbacks"]), 1)
        self.assertEqual(fake_installer.state_refresh_count, 2)
        self.assertEqual(progress[-1]["completed_host_rollbacks"], 2)
        self.assertEqual(progress[-1]["total_host_rollbacks"], 2)
        self.assertEqual(progress[-1]["compacted_host_rollback_records"], 1)
        self.assertEqual(result["outcome"]["requested_operation_count"], 2)
        self.assertEqual(result["outcome"]["operation_count"], 1)
        self.assertEqual(
            result["outcome"]["cleanup_metrics"]["duplicate_cleanup_skipped_count"],
            1,
        )
        self.assertEqual(
            [operation["target_binding"] for operation in fake_installer.applied_operations],
            [_inverse_operation(op1)["target_binding"]],
        )
        self.assertEqual(
            fake_installer.state["ut101-access-slot0"],
            _state_entry_from_operation(op1, source=True),
        )

    def test_rollback_many_skips_current_state_noop(self) -> None:
        op1 = _operation(
            slot_name="ut101-access-slot0",
            source_binding={},
            target_binding={"ut_id": "ut101", "satellite_id": "stl-950001"},
            source_parameters={},
            target_parameters={"delay_ms": 1.0},
        )
        fake_installer = _FakeHostInstaller(
            {
                "ut101-access-slot0": _state_entry_from_operation(
                    op1,
                    source=True,
                )
            }
        )
        manager = InProcessHostRuntimeManager(installer=fake_installer)

        with TemporaryDirectory() as tmpdir:
            link_state_path = Path(tmpdir) / "link_state.json"
            link_state_path.write_text('{"runtime_initialized": true}\n', encoding="utf-8")
            result = manager.rollback_many(
                topology_path=Path("topology.json"),
                link_state_path=link_state_path,
                rollback_requests=[
                    {"host_plan": _plan("mut-1", op1), "applied_operations": [op1]},
                ],
            )

        self.assertTrue(result["outcome"]["rollback_completed"])
        self.assertEqual(result["outcome"]["operation_count"], 0)
        self.assertEqual(result["outcome"]["cleanup_metrics"]["skipped_noop_count"], 1)
        self.assertEqual(fake_installer.applied_operations, [])


if __name__ == "__main__":
    unittest.main()
