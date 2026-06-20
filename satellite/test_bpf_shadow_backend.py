#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from satellite.bpf_backend import (
    BACKEND_MODE,
    compile_bpf_shadow_backend,
    emit_shadow_artifacts_for_run_dir,
    parameter_lookup_decision,
)


def forward_fec(fec_id: str, gateway: str, satellite: str) -> dict:
    return {
        "fec_id": fec_id,
        "group_kind": "g_forward_fec",
        "selected_gateway_id": gateway,
        "selected_gateway_satellite_id": satellite,
        "serving_satellite_id": satellite,
        "zone_id": "zone-north",
        "forward_link_ids": [f"{gateway}-gateway-slot0"],
        "forward_labels": [11792],
        "satellite_path": [satellite],
    }


def return_fec(fec_id: str, gateway: str, satellite: str) -> dict:
    return {
        "fec_id": fec_id,
        "group_kind": "gz_return_fec",
        "selected_gateway_id": gateway,
        "selected_gateway_satellite_id": satellite,
        "zone_serving_satellite_id": satellite,
        "zone_id": "zone-north",
        "reverse_link_ids": [f"{gateway}-gateway-slot0"],
        "reverse_labels": [200000],
        "satellite_path": [satellite],
    }


def parameter_record(
    epoch_id: str,
    activation: float,
    link_id: str,
    *,
    loss_percent: float,
    occupied: bool = True,
) -> dict:
    return {
        "epoch_id": epoch_id,
        "epoch_type": "parameter_epoch",
        "activation_group_id": f"activation-{activation}",
        "activation_relative_time_seconds": activation,
        "activation_time_utc": f"2026-06-11T00:00:{int(activation):02d}Z",
        "host_mutation_execution": {
            "apply_results": [
                {
                    "operation": "update_slot_parameters",
                    "slot_name": link_id,
                    "slot_type": "ut_access",
                    "resulting_state": {
                        "occupied": occupied,
                        "binding": {"ut_id": "ut101", "satellite_id": "stl-1"},
                        "runtime_parameters": {
                            "bandwidth_mbps": 100.0,
                            "delay_ms": 10.0,
                            "loss_percent": loss_percent,
                            "routing_cost": 10,
                            "slot_type": "ut_access",
                        },
                    },
                }
            ]
        },
    }


class BpfShadowBackendTests(unittest.TestCase):
    def test_compiles_backend_summary_and_audit(self) -> None:
        artifacts = self._artifacts()
        report = compile_bpf_shadow_backend(artifacts)
        summary = report["compact_summary"]

        self.assertEqual(summary["backend_mode"], BACKEND_MODE)
        self.assertEqual(summary["runtime_generation_count"], 2)
        self.assertEqual(summary["gz_fec_entry_count"], 5)
        self.assertEqual(summary["parameter_generation_count"], 3)
        self.assertEqual(summary["parameter_link_count"], 2)
        self.assertFalse(summary["fixed_10s_assumed"])
        self.assertEqual(summary["qdisc_timeline_backend_status"], "blocked_or_trace_only")
        self.assertTrue(summary["canonical_runtime_unchanged"])
        self.assertTrue(report["backend_audit_report"]["ok"])

        leakage = report["backend_audit_report"]["checks"][
            "linux_mpls_label_leakage_recorded_not_used_as_intent"
        ]["leakage"]
        self.assertTrue(leakage["found"])
        self.assertIn("forward_labels", leakage["ignored_by_compiler"])
        self.assertIn("reverse_labels", leakage["ignored_by_compiler"])

    def test_parameter_fail_closed_and_drop_actions(self) -> None:
        report = compile_bpf_shadow_backend(self._artifacts())
        parameter_map = {
            "bpf_maps": report["bpf_maps"]["parameter"],
            "self_checks": report["backend_audit_report"]["checks"][
                "parameter_fail_closed"
            ]["checks"],
        }

        self.assertEqual(
            parameter_lookup_decision(parameter_map, 0, "__missing__")["reason"],
            "missing_parameter",
        )
        self.assertEqual(
            parameter_lookup_decision(parameter_map, 99, "ut101-access-slot0")["reason"],
            "missing_parameter_generation",
        )
        self.assertEqual(
            parameter_lookup_decision(parameter_map, 1, "ut101-access-slot0")["reason"],
            "loss_drop",
        )
        self.assertEqual(
            parameter_lookup_decision(parameter_map, 2, "ut102-access-slot0")["reason"],
            "inactive_parameter",
        )

    def test_emits_run_dir_shadow_summary_and_consistency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skyseed-bpf-shadow-test-") as tmp:
            run_dir = Path(tmp)
            artifacts = self._artifacts()
            self._write_run_dir_artifacts(run_dir, artifacts)

            emission = emit_shadow_artifacts_for_run_dir(run_dir)

            self.assertTrue(emission["ok"])
            self.assertTrue(Path(emission["summary_path"]).exists())
            self.assertTrue(Path(emission["consistency_path"]).exists())
            summary = json.loads(Path(emission["summary_path"]).read_text())
            consistency = json.loads(Path(emission["consistency_path"]).read_text())
            self.assertEqual(
                summary["compact_summary"]["backend_mode"],
                "experimental_bpf_shadow",
            )
            self.assertTrue(consistency["ok"])
            self.assertTrue(
                consistency["checks"]["canonical_runtime_unchanged"]["ok"]
            )

    def test_no_inactive_fec_tombstone_is_recorded_as_unobserved_rule(self) -> None:
        artifacts = self._artifacts()
        artifacts["gz_update_diff_summary"]["diffs"][1]["removed_fec_groups"] = []

        report = compile_bpf_shadow_backend(artifacts)
        runtime_fail_closed = report["backend_audit_report"]["checks"][
            "runtime_fail_closed"
        ]

        self.assertTrue(runtime_fail_closed["ok"])
        inactive_check = runtime_fail_closed["checks"]["inactive_fec_fail_closed"]
        self.assertTrue(inactive_check["ok"])
        self.assertIn("No inactive G/Z FEC tombstones", inactive_check["note"])

    @staticmethod
    def _artifacts() -> dict:
        return {
            "source_root": "/tmp/fixture",
            "gz_fec_plan_path": "/tmp/fixture/gz_fec_plan.json",
            "gz_update_diff_summary_path": "/tmp/fixture/gz_update_diff_summary.json",
            "temporal_plan_path": "/tmp/fixture/temporal_plan.json",
            "parameter_epoch_summary_path": "/tmp/fixture/parameter_epoch_summary.json",
            "epoch_execution_details_path": "/tmp/fixture/epoch_execution_details.jsonl",
            "gz_fec_plan": {
                "model": "gz_fec_plan_sequence_v1",
                "plans": [
                    {
                        "plan_id": "rtplan-gen0",
                        "target_snapshot": {"slice_id": "slice-0"},
                        "forward_fecs": [forward_fec("g-old", "gw1", "stl-1")],
                        "return_fecs": [return_fec("gz-stable", "gw1", "stl-1")],
                    },
                    {
                        "plan_id": "rtplan-gen1",
                        "target_snapshot": {"slice_id": "slice-1"},
                        "forward_fecs": [forward_fec("g-new", "gw2", "stl-2")],
                        "return_fecs": [return_fec("gz-stable", "gw1", "stl-1")],
                    },
                ],
            },
            "gz_update_diff_summary": {
                "model": "gz_update_diff_summary_v1",
                "diffs": [
                    {"removed_fec_groups": []},
                    {
                        "removed_fec_groups": ["g-old"],
                        "estimated_lfib_route_operation_items": 3,
                    },
                ],
            },
            "temporal_plan": {
                "runtime_epochs": [
                    {"slice_id": "slice-0", "epoch_id": "runtimeepoch-0"},
                    {"slice_id": "slice-1", "epoch_id": "runtimeepoch-1"},
                ],
                "parameter_epochs": [
                    {"epoch_id": "parameterepoch-0", "slice_id": "slice-0"},
                    {"epoch_id": "parameterepoch-1", "slice_id": "slice-1"},
                    {"epoch_id": "parameterepoch-2", "slice_id": "slice-2"},
                ],
            },
            "parameter_epoch_summary": [],
            "epoch_execution_details": [
                parameter_record(
                    "parameterepoch-0",
                    3.0,
                    "ut101-access-slot0",
                    loss_percent=0.0,
                ),
                parameter_record(
                    "parameterepoch-1",
                    8.5,
                    "ut101-access-slot0",
                    loss_percent=100.0,
                ),
                parameter_record(
                    "parameterepoch-2",
                    13.0,
                    "ut102-access-slot0",
                    loss_percent=0.0,
                    occupied=False,
                ),
            ],
        }

    @staticmethod
    def _write_run_dir_artifacts(run_dir: Path, artifacts: dict) -> None:
        for key, filename in [
            ("gz_fec_plan", "gz_fec_plan.json"),
            ("gz_update_diff_summary", "gz_update_diff_summary.json"),
            ("temporal_plan", "temporal_plan.json"),
            ("parameter_epoch_summary", "parameter_epoch_summary.json"),
        ]:
            (run_dir / filename).write_text(
                json.dumps(artifacts[key], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        with (run_dir / "epoch_execution_details.jsonl").open(
            "w",
            encoding="utf-8",
        ) as handle:
            for record in artifacts["epoch_execution_details"]:
                handle.write(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
