#!/usr/bin/env python3
# encoding: utf-8

import unittest
from unittest.mock import patch

from contracts import RUNTIME_UPDATE_PLAN_SCHEMA_VERSION, TOPOLOGY_SNAPSHOT_SCHEMA_VERSION
from linux_mpls_runtime import DirectGatewayExperimentContext, MplsPathSegment
from runtime_queue_coverage_audit import (
    audit_bpf_generation_slot_coverage,
    audit_bpf_route_identity_coverage,
    audit_runtime_queue_coverage,
    bpf_route_identity_contexts_from_normal_runtime_inputs,
    planned_bpf_route_identity_evidence_from_context_records,
)


def _segment(index: int) -> MplsPathSegment:
    return MplsPathSegment(
        link_id=f"link-{index}",
        source_node_id=f"stl-{index}",
        source_node_type="satellite",
        source_interface=f"if{index}a",
        source_ip=f"10.0.{index}.1",
        destination_node_id="gw-a",
        destination_node_type="gateway",
        destination_interface=f"ga{index}",
        destination_ip=f"10.0.{index}.2",
        label=1000 + index,
    )


def _context(
    *,
    flow_id: str,
    ut_id: str,
    ut_access_ip: str,
    gateway_id: str = "gw-a",
    gateway_access_interface: str = "ga0",
) -> DirectGatewayExperimentContext:
    segment = _segment(int(ut_access_ip.rsplit(".", 1)[-1]))
    return DirectGatewayExperimentContext(
        flow_id=flow_id,
        ut_id=ut_id,
        zone_id="zone-a",
        serving_satellite_id="stl-a",
        selected_gateway_id=gateway_id,
        selected_gateway_satellite_id="stl-a",
        zone_return_prefixes=(f"{ut_access_ip}/32",),
        ping_target_ip="192.0.2.10",
        target_host_node_id="target",
        policy_table_id=101,
        policy_rule_priority=101,
        satellite_path=("stl-a",),
        forward_labels=(100,),
        reverse_labels=(200,),
        ut_access_interface="ua0",
        ut_access_ip=ut_access_ip,
        ut_access_peer_ip="10.90.0.1",
        gateway_access_interface=gateway_access_interface,
        gateway_access_ip="10.91.0.2",
        gateway_access_peer_ip="10.91.0.1",
        gateway_nat_interface="gnat0",
        gateway_nat_source_ip="198.51.100.2",
        forward_segments=(segment,),
        reverse_segments=(segment,),
        validation_targets=(),
    )


def _record(segment_key: str, context: DirectGatewayExperimentContext):
    return {
        "segment_epoch_key": segment_key,
        "slice_id": segment_key.split(":", 1)[-1],
        "epoch_id": f"epoch-{segment_key}",
        "context": context,
    }


def _normal_inputs_with_two_runtime_entries():
    snapshot = {
        "schema_version": TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": "snapshot-a",
        "time_slice": {"slice_id": "slice-a", "state_model": "full_state"},
        "provenance": {"source": "unit-test"},
        "nodes": {},
        "active_links": [],
        "zones": {},
        "ut_zone_membership": {},
    }
    runtime_update_plan = {
        "schema_version": RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
        "plan_id": "plan-a",
        "target_snapshot": {"snapshot_id": "snapshot-a"},
        "planner": {"planner_name": "unit-test"},
        "apply_model": {
            "state_model": "full_state_intent",
            "action_model": "satellite-runtime-update-action-v1alpha1",
        },
        "node_subplans": {},
        "replacement_semantics": {},
        "validation_expectations": {},
    }
    return {
        "topology_path": "/tmp/topology.json",
        "initial_segment": {
            "segment_id": "initial",
            "current_execution_queue": [
                {
                    "classification": "runtime_epoch",
                    "segment_epoch_key": "initial:slice-a",
                    "slice_id": "slice-a",
                    "epoch_id": "epoch-a",
                    "runtime_apply_allowed": True,
                },
                {
                    "classification": "runtime_epoch",
                    "segment_epoch_key": "initial:slice-b",
                    "slice_id": "slice-b",
                    "epoch_id": "epoch-b",
                    "runtime_apply_allowed": True,
                },
            ],
        },
        "context_by_slice_by_segment": {
            "initial": {
                "slice-a": {
                    "snapshot": snapshot,
                    "runtime_update_plan": runtime_update_plan,
                },
                "slice-b": {
                    "snapshot": snapshot,
                    "runtime_update_plan": runtime_update_plan,
                },
            }
        },
    }


def _runtime_update_plan_with_generation(
    *,
    logical_generation_id: int,
    physical_generation_id: int,
    slot_width: int = 32,
    horizon_sequence_index: int = 0,
    horizon_id: str = "initial",
):
    buffer_index = horizon_sequence_index % 3
    return {
        "schema_version": RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
        "plan_id": f"plan-{logical_generation_id}",
        "target_snapshot": {
            "snapshot_id": f"snapshot-{logical_generation_id}",
            "bpf_logical_generation_id": logical_generation_id,
            "bpf_physical_generation_id": physical_generation_id,
            "bpf_generation_slot_width": slot_width,
            "bpf_generation_horizon_sequence_index": horizon_sequence_index,
            "bpf_generation_buffer_index": buffer_index,
            "bpf_generation_horizon_id": horizon_id,
        },
        "planner": {
            "planner_name": "unit-test",
            "bpf_generation_slot_allocator": {
                "schema_version": "skyseed-bpf-generation-slot-allocation-v1",
                "buffer_model": "previous_current_next_triple_buffer",
                "horizon_id": horizon_id,
                "horizon_sequence_index": horizon_sequence_index,
                "buffer_index": buffer_index,
                "logical_generation_id": logical_generation_id,
                "physical_generation_id": physical_generation_id,
                "generation_slot_width": slot_width,
            },
        },
        "apply_model": {
            "state_model": "full_state_intent",
            "action_model": "satellite-runtime-update-action-v1alpha1",
        },
        "node_subplans": {},
        "replacement_semantics": {},
        "validation_expectations": {},
    }


def _normal_inputs_with_generation_contexts(*, second_context: bool = True):
    payload = _normal_inputs_with_two_runtime_entries()
    payload["context_by_slice_by_segment"]["initial"]["slice-a"][
        "runtime_update_plan"
    ] = _runtime_update_plan_with_generation(
        logical_generation_id=0,
        physical_generation_id=0,
    )
    if second_context:
        payload["context_by_slice_by_segment"]["initial"]["slice-b"][
            "runtime_update_plan"
        ] = _runtime_update_plan_with_generation(
            logical_generation_id=1,
            physical_generation_id=1,
        )
    else:
        payload["context_by_slice_by_segment"]["initial"].pop("slice-b", None)
    return payload


class RuntimeQueueCoverageAuditTests(unittest.TestCase):
    def test_planned_producer_scope_covers_runtime_queue_consumers(self) -> None:
        records = (
            _record(
                "initial:starlink-shell-slice-0001",
                _context(flow_id="flow-a", ut_id="ut-a", ut_access_ip="10.90.0.2"),
            ),
            _record(
                "initial:starlink-shell-slice-0004",
                _context(flow_id="flow-b", ut_id="ut-b", ut_access_ip="10.90.0.3"),
            ),
        )

        report = audit_bpf_route_identity_coverage(
            consumer_context_records=records,
            producer_context_records=records,
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["missing_identity_count"], 0)
        self.assertEqual(report["summary"]["consumer_gateway_local_return_route_count"], 2)

    def test_actual_evidence_required_fails_when_prepare_artifact_lacks_evidence(self) -> None:
        records = (
            _record(
                "initial:starlink-shell-slice-0001",
                _context(flow_id="flow-a", ut_id="ut-a", ut_access_ip="10.90.0.2"),
            ),
        )

        report = audit_bpf_route_identity_coverage(
            consumer_context_records=records,
            actual_evidence_required=True,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["blockers"][0]["label"],
            "PREPARED_ROUTE_IDENTITY_EVIDENCE_MISSING",
        )

    def test_actual_evidence_must_cover_future_first_consumer_identity(self) -> None:
        first = _record(
            "initial:starlink-shell-slice-0001",
            _context(flow_id="flow-a", ut_id="ut-a", ut_access_ip="10.90.0.2"),
        )
        later = _record(
            "initial:starlink-shell-slice-0004",
            _context(flow_id="flow-b", ut_id="ut-b", ut_access_ip="10.90.0.3"),
        )
        first_only_evidence = planned_bpf_route_identity_evidence_from_context_records(
            (first,)
        )

        report = audit_bpf_route_identity_coverage(
            consumer_context_records=(first, later),
            prepared_route_identity_evidence=first_only_evidence,
            actual_evidence_required=True,
        )

        self.assertFalse(report["ok"])
        missing_kinds = {
            blocker["identity_kind"]
            for blocker in report["blockers"]
            if blocker["label"] == "ROUTE_IDENTITY_PRODUCER_MISSING"
        }
        self.assertEqual(
            missing_kinds,
            {"ut_default_route", "gw_bpf_local_return_route"},
        )

    def test_runtime_queue_context_builder_reads_topology_once_for_queue(self) -> None:
        context = _context(
            flow_id="flow-a",
            ut_id="ut-a",
            ut_access_ip="10.90.0.2",
        )

        with patch(
            "runtime_queue_coverage_audit._read_json",
            return_value={"gateways": []},
        ) as read_json, patch(
            "runtime_queue_coverage_audit._realization_contexts_from_topology",
            return_value=[context],
        ) as realize:
            records = bpf_route_identity_contexts_from_normal_runtime_inputs(
                _normal_inputs_with_two_runtime_entries(),
            )

        self.assertEqual(read_json.call_count, 1)
        self.assertEqual(realize.call_count, 2)
        self.assertEqual(len(records), 2)

    def test_bpf_generation_slot_coverage_detects_wrong_physical_slot(self) -> None:
        payload = _normal_inputs_with_generation_contexts()
        payload["context_by_slice_by_segment"]["initial"]["slice-b"][
            "runtime_update_plan"
        ] = _runtime_update_plan_with_generation(
            logical_generation_id=1,
            physical_generation_id=99,
        )

        report = audit_bpf_generation_slot_coverage(payload)

        self.assertFalse(report["ok"])
        labels = {blocker["label"] for blocker in report["blockers"]}
        self.assertIn("BPF_GENERATION_SLOT_ASSIGNMENT_MISMATCH", labels)

    def test_combined_runtime_queue_coverage_catches_missing_context(self) -> None:
        context = _context(
            flow_id="flow-a",
            ut_id="ut-a",
            ut_access_ip="10.90.0.2",
        )

        with patch(
            "runtime_queue_coverage_audit._read_json",
            return_value={"gateways": []},
        ), patch(
            "runtime_queue_coverage_audit._realization_contexts_from_topology",
            return_value=[context],
        ):
            report = audit_runtime_queue_coverage(
                _normal_inputs_with_generation_contexts(second_context=False),
            )

        self.assertFalse(report["ok"])
        labels = {blocker["label"] for blocker in report["blockers"]}
        self.assertIn("RUNTIME_QUEUE_CONTEXT_MISSING", labels)

    def test_combined_runtime_queue_coverage_catches_short_horizon_scope(self) -> None:
        context = _context(
            flow_id="flow-a",
            ut_id="ut-a",
            ut_access_ip="10.90.0.2",
        )

        with patch(
            "runtime_queue_coverage_audit._read_json",
            return_value={"gateways": []},
        ), patch(
            "runtime_queue_coverage_audit._realization_contexts_from_topology",
            return_value=[context],
        ):
            report = audit_runtime_queue_coverage(
                _normal_inputs_with_generation_contexts(),
                expected_horizon_count=3,
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["horizon_scope"]["actual_horizon_count"], 1)
        labels = {blocker["label"] for blocker in report["blockers"]}
        self.assertIn("PREPARED_HORIZON_SCOPE_COUNT_MISMATCH", labels)

    def test_combined_runtime_queue_coverage_passes_current_contract(self) -> None:
        context = _context(
            flow_id="flow-a",
            ut_id="ut-a",
            ut_access_ip="10.90.0.2",
        )

        with patch(
            "runtime_queue_coverage_audit._read_json",
            return_value={"gateways": []},
        ), patch(
            "runtime_queue_coverage_audit._realization_contexts_from_topology",
            return_value=[context],
        ):
            report = audit_runtime_queue_coverage(
                _normal_inputs_with_generation_contexts(),
                expected_horizon_count=1,
            )

        self.assertTrue(report["ok"])
        self.assertTrue(report["horizon_scope"]["ok"])
        self.assertTrue(report["queue_integrity"]["ok"])
        self.assertTrue(report["bpf_generation_slot_coverage"]["ok"])
        self.assertTrue(report["route_identity_coverage"]["ok"])


if __name__ == "__main__":
    unittest.main()
