import tempfile
import unittest
from pathlib import Path
import sys

SATELLITE_DIR = Path(__file__).resolve().parent
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))
APPS_DIR = SATELLITE_DIR / "apps"
if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from apps.validate_bpf_mpls_offline import _zone_anchor_semantic_validation
from apps.validate_bpf_mpls_offline import _build_path_cardinality_audit
from linux_mpls_runtime import (
    DirectGatewayExperimentContext,
    MplsPathSegment,
    _bpf_mpls_context_fec_ids,
)


def _segment(source: str, destination: str, label: int) -> MplsPathSegment:
    return MplsPathSegment(
        link_id=f"{source}-{destination}",
        source_node_id=source,
        source_node_type="satellite" if source.startswith("sat") else "gateway",
        source_interface=f"{source}-if",
        source_ip="10.0.0.1",
        destination_node_id=destination,
        destination_node_type="satellite" if destination.startswith("sat") else "gateway",
        destination_interface=f"{destination}-if",
        destination_ip="10.0.0.2",
        label=label,
    )


def _context(
    *,
    flow_id: str,
    ut_id: str,
    gateway_class_id: str = "zone-a:default-gateway:gw-a",
    selected_gateway_id: str = "gw-a",
    anchor: str = "sat-anchor-a",
) -> DirectGatewayExperimentContext:
    forward = (_segment(anchor, "sat-gw-a", 100), _segment("sat-gw-a", selected_gateway_id, 101))
    reverse = (_segment(selected_gateway_id, "sat-gw-a", 200), _segment("sat-gw-a", anchor, 201))
    return DirectGatewayExperimentContext(
        flow_id=flow_id,
        ut_id=ut_id,
        zone_id="zone-a",
        serving_satellite_id=anchor,
        selected_gateway_id=selected_gateway_id,
        selected_gateway_satellite_id="sat-gw-a",
        zone_return_prefixes=("10.97.0.0/24",),
        ping_target_ip="10.154.0.129",
        target_host_node_id="as154-host",
        policy_table_id=100,
        policy_rule_priority=1000,
        satellite_path=(anchor, "sat-gw-a"),
        forward_labels=(100, 101),
        reverse_labels=(200, 201),
        ut_access_interface=f"{ut_id}-access",
        ut_access_ip=f"10.97.0.{ut_id[-1]}",
        ut_access_peer_ip=f"10.97.0.{int(ut_id[-1]) + 10}",
        gateway_access_interface="gw-access",
        gateway_access_ip="10.91.0.2",
        gateway_access_peer_ip="10.91.0.1",
        gateway_nat_interface="gw-nat",
        gateway_nat_source_ip="10.242.1.10",
        forward_segments=forward,
        reverse_segments=reverse,
        validation_targets=({"node_id": "as154-host", "ip_address": "10.154.0.129"},),
        gateway_class_id=gateway_class_id,
        traffic_scope=(
            "special_prefix_override"
            if "override" in gateway_class_id
            else "ordinary_internet_default"
        ),
    )


def _flow_context(flow_id: str, ut_id: str, anchor: str = "sat-anchor-a") -> dict:
    return {
        "flow_id": flow_id,
        "ut_id": ut_id,
        "zone_id": "zone-a",
        "zone_anchor_satellite_id": anchor,
        "serving_satellite_id": anchor,
        "selected_gateway_id": "gw-a",
        "selected_gateway_satellite_id": "sat-gw-a",
        "gateway_class_id": "zone-a:default-gateway:gw-a",
        "satellite_path": [anchor, "sat-gw-a"],
        "forward_link_ids": ["sat-anchor-a-sat-gw-a", "sat-gw-a-gw-a"],
        "reverse_link_ids": ["gw-a-sat-gw-a", "sat-gw-a-sat-anchor-a"],
        "forward_labels": [100, 101],
        "reverse_labels": [200, 201],
        "zone_return_prefixes": ["10.97.0.0/24"],
        "return_path_intent": {
            "zone_serving_satellite_id": anchor,
            "shared_backbone_return_target": {
                "target_key": "gw-a:zone-a:sat-anchor-a",
                "zone_serving_satellite_id": anchor,
            },
        },
    }


def _plan(slice_id: str, flow_contexts: list[dict]) -> dict:
    actions = [
        {
            "action_type": "forwarding_entry",
            "intent": {
                "match": {"flow_id": context["flow_id"], "node_role": "serving_satellite"},
                "outcome": {"local_role": "transit_toward_gateway", "flow_context": context},
            },
        }
        for context in flow_contexts
    ]
    return {
        "schema_version": "runtime-update-plan-v1",
        "plan_id": f"plan-{slice_id}",
        "target_snapshot": {"slice_id": slice_id},
        "planner": {},
        "apply_model": {},
        "node_subplans": {"sat-anchor-a": {"actions": actions}},
        "replacement_semantics": {},
        "validation_expectations": {},
    }


def _snapshot(slice_id: str, anchor: str = "sat-anchor-a") -> dict:
    return {
        "schema_version": "topology-snapshot-v1",
        "snapshot_id": f"snapshot-{slice_id}",
        "time_slice": {"slice_id": slice_id, "relative_time_seconds": 0},
        "provenance": {},
        "nodes": {},
        "active_links": [],
        "zones": {
            "zone-a": {
                "zone_id": "zone-a",
                "service_role": "required_service",
                "active_service": True,
                "required_for_runtime": True,
                "active_serving_satellite_id": anchor,
                "candidate_serving_satellite_ids": [anchor, "sat-backup"],
                "resolution_state": "resolved",
                "zone_anchor_resolved": True,
                "zone_service_resolved": True,
                "ut_ids": ["ut1", "ut2"],
                "anchor_selection": {
                    "selected_satellite_id": anchor,
                    "backup_candidate_satellite_ids": ["sat-backup"],
                    "change_reason": "best_zone_anchor_score",
                },
            }
        },
        "ut_zone_membership": {"ut1": "zone-a", "ut2": "zone-a"},
        "planner_hints": {},
    }


class ZoneAnchorSemanticTests(unittest.TestCase):
    def test_fec_ids_are_shared_by_zone_anchor_not_ut_or_flow(self) -> None:
        first = _context(flow_id="flow-ut1", ut_id="ut1")
        second = _context(flow_id="flow-ut2", ut_id="ut2")
        self.assertEqual(_bpf_mpls_context_fec_ids(first, lambda value: 42), _bpf_mpls_context_fec_ids(second, lambda value: 42))

    def test_default_and_override_classes_are_distinct_without_ut_identity(self) -> None:
        default = _context(flow_id="flow-ut1", ut_id="ut1")
        override = _context(
            flow_id="flow-ut1:override",
            ut_id="ut1",
            gateway_class_id="zone-a:override:gw-b",
            selected_gateway_id="gw-b",
        )
        self.assertNotEqual(
            _bpf_mpls_context_fec_ids(default, lambda value: hash(value) & 0xFFFFFFFF),
            _bpf_mpls_context_fec_ids(override, lambda value: hash(value) & 0xFFFFFFFF),
        )

    def test_semantic_validation_passes_shared_anchor_and_handover_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner_preview = {
                "snapshots": [
                    {"slice_id": "slice-0001", "runtime_update_plan": _plan("slice-0001", [_flow_context("flow-ut1", "ut1"), _flow_context("flow-ut2", "ut2")])},
                    {"slice_id": "slice-0002", "runtime_update_plan": _plan("slice-0002", [_flow_context("flow-ut1", "ut1", anchor="sat-anchor-b"), _flow_context("flow-ut2", "ut2", anchor="sat-anchor-b")])},
                ]
            }
            snapshot_preview = {
                "snapshots": [
                    _snapshot("slice-0001", anchor="sat-anchor-a"),
                    _snapshot("slice-0002", anchor="sat-anchor-b"),
                ]
            }
            report = _zone_anchor_semantic_validation(
                run_dir=Path(tmp),
                planner_preview=planner_preview,
                snapshot_preview=snapshot_preview,
                selected_runtime_slice_ids=["slice-0001", "slice-0002"],
            )
        self.assertTrue(report["passed"], report["violation_table"])
        self.assertEqual(report["summary"]["multi_active_zone_count"], 0)
        self.assertEqual(report["summary"]["zero_active_required_zone_count"], 0)
        self.assertEqual(report["summary"]["per_ut_serving_divergence_count"], 0)

    def test_semantic_validation_blocks_multi_active_anchor(self) -> None:
        snapshot = _snapshot("slice-0001")
        snapshot["zones"]["zone-a"]["active_anchor_candidates"] = [
            "sat-anchor-a",
            "sat-anchor-b",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = _zone_anchor_semantic_validation(
                run_dir=Path(tmp),
                planner_preview={
                    "snapshots": [
                        {"slice_id": "slice-0001", "runtime_update_plan": _plan("slice-0001", [_flow_context("flow-ut1", "ut1")])}
                    ]
                },
                snapshot_preview={"snapshots": [snapshot]},
                selected_runtime_slice_ids=["slice-0001"],
            )
        self.assertFalse(report["passed"])
        self.assertEqual(report["summary"]["multi_active_zone_count"], 1)

    def test_path_cardinality_audit_explains_direct_and_core_paths(self) -> None:
        direct = _context(flow_id="flow-ut1", ut_id="ut1")
        multi = _context(flow_id="flow-ut2", ut_id="ut2")
        multi = multi.__class__(
            **{
                **multi.__dict__,
                "satellite_path": ("sat-anchor-a", "sat-mid-a", "sat-gw-a"),
                "forward_segments": (
                    _segment("sat-anchor-a", "sat-mid-a", 100),
                    _segment("sat-mid-a", "sat-gw-a", 101),
                    _segment("sat-gw-a", "gw-a", 102),
                ),
                "reverse_segments": (
                    _segment("gw-a", "sat-gw-a", 200),
                    _segment("sat-gw-a", "sat-mid-a", 201),
                    _segment("sat-mid-a", "sat-anchor-a", 202),
                ),
            }
        )
        semantic = {
            "passed": True,
            "summary": {
                "ut_driven_zone_definition_count": 0,
                "per_ut_serving_divergence_count": 0,
            },
            "zone_anchor_ledger": [
                {
                    "slice_id": "slice-0001",
                    "zone_id": "zone-a",
                    "required_for_runtime": True,
                    "zone_anchor_resolved": True,
                    "active_anchor_count": 1,
                    "zone_anchor_satellite_id": "sat-anchor-a",
                    "zone_service_domain_source": "coverage_aware_test_domain",
                    "anchor_selection": {"change_reason": "best_zone_anchor_score"},
                }
            ],
            "gz_fec_ledger": [
                {
                    "slice_id": "slice-0001",
                    "fec_kind": "g_forward_fec",
                    "fec_id": "fec-g",
                    "zone_id": "zone-a",
                    "gateway_class_id": "zone-a:default-gateway:gw-a",
                    "zone_anchor_satellite_id": "sat-anchor-a",
                },
                {
                    "slice_id": "slice-0001",
                    "fec_kind": "gz_return_fec",
                    "fec_id": "fec-z",
                    "zone_id": "zone-a",
                    "gateway_class_id": "zone-a:default-gateway:gw-a",
                    "zone_anchor_satellite_id": "sat-anchor-a",
                },
            ],
        }
        audit = _build_path_cardinality_audit(
            run_dir=Path("/tmp/path-cardinality-test"),
            selected_runtime_slice_ids=["slice-0001"],
            semantic_validation=semantic,
            compiled_slices=[
                {
                    "slice_id": "slice-0001",
                    "generation": 0,
                    "compiled_mpls_map_counts": {
                        "ingress_classifier_map": 2,
                        "core_lfib_map": 4,
                        "egress_handoff_map": 2,
                    },
                    "map_conflict_summary": {
                        "safe_duplicate_count": 0,
                        "unsafe_conflict_count": 0,
                    },
                }
            ],
            label_allocation_ledger=[
                {
                    "slice_id": "slice-0001",
                    "map_type": "core_lfib_map",
                    "generation": 0,
                    "direction": "g_to_z",
                    "logical_ingress": "sat-mid-a:if0",
                    "incoming_label": 100,
                    "out_logical_interface": "sat-mid-a:if1",
                    "next_label": 101,
                    "handoff_action": "swap",
                }
            ],
            per_slice_contexts={"slice-0001": [direct, multi]},
        )
        self.assertTrue(audit["aggregate"]["meaningfulness_gate_passed"])
        self.assertEqual(audit["aggregate"]["skipped_eligible_core_scope_count"], 0)
        self.assertGreater(audit["aggregate"]["multi_hop_satellite_core_path_count"], 0)


if __name__ == "__main__":
    unittest.main()
