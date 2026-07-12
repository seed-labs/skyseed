from __future__ import annotations

import unittest
from types import SimpleNamespace

from skyseed_v1.runtime.experiment import (
    _bpf_attach_reuse_epoch_summary,
    _bpf_hotpath_reuse_audit,
    _operational_clean_node_local_execution_summary,
)


class RuntimeHotpathEvidenceTests(unittest.TestCase):
    def test_no_prepare_reuse_proof_survives_session_compaction(self) -> None:
        readiness = {
            "ready": True,
            "ok": True,
            "fallback_to_linux": False,
            "attached_interfaces": ["netns:gw1:if0", "netns:sat1:if1"],
            "prepare_steps": {
                "real_backend_prepare": {
                    "prepare_mode": "existing_readiness_reused_no_prepare",
                    "attach_lifecycle_mode": "existing_readiness_reused",
                    "evidence_source": "proved_existing_bpf_owned_readiness",
                    "attached_interfaces": ["netns:gw1:if0", "netns:sat1:if1"],
                    "attach_reuse": {
                        "reuse_eligible": True,
                        "attach_strategy": "existing_readiness_reused_no_prepare",
                        "reused_attach_count": 2,
                        "delta_attach_count": 0,
                        "full_attach_count": 0,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": True,
                    },
                    "timing_breakdown": {
                        "bpf_map_update_count": 1,
                        "active_generation_update_count": 1,
                    },
                }
            },
        }
        session = SimpleNamespace(
            session_id="session-a",
            backend={
                "forwarding_backend": "bpf_owned",
                "bpf_owned_readiness": readiness,
            },
            target_slice={
                "slice_id": "slice-2",
                "active_flow_count": 2,
                "reapplied_flow_count": 0,
                "held_flow_count": 2,
                "incremental_cleanup_flow_count": 0,
                "executed_flow_ids": ["flow-a", "flow-b"],
                "reapplied_flow_ids": [],
                "held_flow_ids": ["flow-a", "flow-b"],
                "incremental_cleanup_flow_ids": [],
            },
            preflight={"backend_apply_contract": {"backend_mode": "bpf_owned"}},
            timing={"bpf_attach_seconds": 0.0},
            apply_results=[],
            verify_results=[],
            ping_results=[],
        )

        compact = _operational_clean_node_local_execution_summary(session)
        reuse = _bpf_attach_reuse_epoch_summary(compact)
        audit = _bpf_hotpath_reuse_audit(
            [
                {
                    "sequence_index": 1,
                    "epoch_type": "runtime_epoch",
                    "bpf_attach_reuse": reuse,
                    "timing": {"bpf_attach_seconds": 0.0},
                },
                {
                    "sequence_index": 2,
                    "epoch_type": "runtime_epoch",
                    "original_epoch_type": "parameter_epoch",
                    "escalated_to_runtime_epoch": True,
                    "node_local_reapplied_flow_count": 0,
                    "bpf_attach_reuse": reuse,
                    "timing": {"bpf_attach_seconds": 0.0},
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertTrue(reuse["available"])
        self.assertEqual(
            reuse["attach_strategy"], "existing_readiness_reused_no_prepare"
        )
        self.assertEqual(reuse["full_attach_count"], 0)
        self.assertTrue(reuse["steady_epoch_map_only"])
        self.assertEqual(reuse["bpf_map_update_count"], 1)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["steady_map_only_epoch_count"], 1)


if __name__ == "__main__":
    unittest.main()
