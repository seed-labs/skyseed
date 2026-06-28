#!/usr/bin/env python3
# encoding: utf-8

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Sequence

SATELLITE_DIR = Path(__file__).resolve().parent
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))

from contracts import TOPOLOGY_SNAPSHOT_SCHEMA_VERSION, TopologySnapshot
from experiment_topology import ZoneAnchorQualityGenerator
from starlink_shell import PropagatedOrbitSlice
from temporal_planning import build_temporal_plan
from topology_pipeline import OrbitTopologySlice, VisibilitySlice, ZoneGenerationInput


class EchoRuntimeParameterModel:
    model_name = "test_echo_runtime_parameter_model"

    def derive_parameters(
        self,
        *,
        snapshot: TopologySnapshot,
        link: Dict[str, Any],
    ) -> Dict[str, Any]:
        return dict(link.get("runtime_parameters", {}))


def _orbit_slice(index: int) -> PropagatedOrbitSlice:
    return PropagatedOrbitSlice(
        slice_id=f"slice-{index:04d}",
        relative_time_seconds=float((index - 1) * 10),
        timestamp_utc=f"2026-06-21T00:00:{(index - 1) * 10:02d}Z",
        satellite_states={},
    )


def _base_snapshot(
    index: int,
    *,
    delay_ms: float = 10.0,
    loss_percent: float = 0.0,
    bandwidth_mbps: float = 1000.0,
) -> TopologySnapshot:
    runtime_parameters = {
        "delay_ms": delay_ms,
        "loss_percent": loss_percent,
        "bandwidth_mbps": bandwidth_mbps,
        "routing_cost": int(round(delay_ms * 100)),
    }
    return TopologySnapshot(
        schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=f"topsnap-{index:04d}",
        time_slice={
            "slice_id": f"slice-{index:04d}",
            "relative_time_seconds": float((index - 1) * 10),
            "absolute_time_utc": f"2026-06-21T00:00:{(index - 1) * 10:02d}Z",
            "state_model": "full_state",
        },
        provenance={"source": "test_sampling_update_optimization"},
        nodes={
            "ut1": {
                "node_id": "ut1",
                "node_type": "user_terminal",
                "attachment": {
                    "state": "attached",
                    "slot_name": "ut-link",
                    "active_serving_satellite_id": "sat-a",
                },
            },
            "gw1": {"node_id": "gw1", "node_type": "gateway"},
            "sat-a": {"node_id": "sat-a", "node_type": "satellite"},
            "sat-b": {"node_id": "sat-b", "node_type": "satellite"},
            "sat-g": {"node_id": "sat-g", "node_type": "satellite"},
        },
        active_links=[
            {
                "link_id": "ut-link",
                "slot_type": "ut_access",
                "binding": {"ut_id": "ut1", "satellite_id": "sat-a"},
                "endpoints": [
                    {"node_id": "ut1", "node_type": "user_terminal", "interface_name": "ut0"},
                    {"node_id": "sat-a", "node_type": "satellite", "interface_name": "sa-ut0"},
                ],
                "runtime_parameters": runtime_parameters,
            },
            {
                "link_id": "path-a",
                "slot_type": "same_plane_isl",
                "binding": {"satellite_pair_id": "sat-a:sat-g"},
                "endpoints": [
                    {"node_id": "sat-a", "node_type": "satellite", "interface_name": "sa-a"},
                    {"node_id": "sat-g", "node_type": "satellite", "interface_name": "sg-a"},
                ],
                "runtime_parameters": runtime_parameters,
            },
            {
                "link_id": "path-b",
                "slot_type": "same_plane_isl",
                "binding": {"satellite_pair_id": "sat-a:sat-b"},
                "endpoints": [
                    {"node_id": "sat-a", "node_type": "satellite", "interface_name": "sa-b"},
                    {"node_id": "sat-b", "node_type": "satellite", "interface_name": "sb-b"},
                ],
                "runtime_parameters": runtime_parameters,
            },
            {
                "link_id": "gw-link",
                "slot_type": "gw_access",
                "binding": {"gateway_id": "gw1", "satellite_id": "sat-g"},
                "endpoints": [
                    {"node_id": "gw1", "node_type": "gateway", "interface_name": "gw0"},
                    {"node_id": "sat-g", "node_type": "satellite", "interface_name": "sg-gw0"},
                ],
                "runtime_parameters": runtime_parameters,
            },
        ],
        zones={
            "zone-a": {
                "zone_id": "zone-a",
                "ut_ids": ["ut1"],
                "active_serving_satellite_id": "sat-a",
                "return_prefixes": ["10.10.0.0/24"],
                "resolution_state": "resolved",
            }
        },
        ut_zone_membership={"ut1": "zone-a"},
        planner_hints={"gateway_ids": ["gw1"], "satellite_ids": ["sat-a", "sat-b", "sat-g"]},
    )


def _plan(
    slice_id: str,
    *,
    forward_link_ids: Sequence[str] = ("path-a", "gw-link"),
    reverse_link_ids: Sequence[str] | None = None,
    forward_labels: Sequence[int] = (100, 101),
    reverse_labels: Sequence[int] = (200, 201),
    selected_gateway_id: str = "gw1",
    serving_satellite_id: str = "sat-a",
) -> Dict[str, Any]:
    reverse = list(reverse_link_ids) if reverse_link_ids is not None else list(reversed(forward_link_ids))
    flow_context = {
        "flow_id": "flow:ut1:default",
        "ut_id": "ut1",
        "zone_id": "zone-a",
        "gateway_class_id": f"zone-a:default-gateway:{selected_gateway_id}",
        "serving_satellite_id": serving_satellite_id,
        "selected_gateway_id": selected_gateway_id,
        "selected_gateway_satellite_id": "sat-g",
        "satellite_path": [serving_satellite_id, "sat-g"] if list(forward_link_ids)[0] == "path-a" else [serving_satellite_id, "sat-b", "sat-g"],
        "forward_link_ids": list(forward_link_ids),
        "reverse_link_ids": reverse,
        "forward_labels": list(forward_labels),
        "reverse_labels": list(reverse_labels),
        "ut_access_link_id": "ut-link",
        "gateway_access_link_id": "gw-link",
        "zone_return_prefixes": ["10.10.0.0/24"],
        "return_path_intent": {
            "shared_backbone_return_target": {
                "target_key": f"{selected_gateway_id}:zone-a:{serving_satellite_id}",
                "zone_serving_satellite_id": serving_satellite_id,
            },
            "candidate_shared_zone_return_route": {
                "route_prefixes": ["10.10.0.0/24"],
            },
        },
    }
    return {
        "plan_id": f"plan-{slice_id}",
        "target_snapshot": {"slice_id": slice_id, "snapshot_id": f"topsnap-{slice_id}"},
        "node_subplans": {
            "sat-a": {
                "actions": [
                    {
                        "action_type": "forwarding_entry",
                        "intent": {
                            "match": {"flow_id": flow_context["flow_id"]},
                            "outcome": {"flow_context": flow_context},
                        },
                    }
                ]
            }
        },
    }


def _build_plan(
    snapshots: Sequence[TopologySnapshot],
    plans: Sequence[Dict[str, Any]],
    *,
    delay_threshold_ms: float = 1.0,
    loss_threshold_pct: float = 1.0,
    bandwidth_threshold_pct: float = 10.0,
    routing_cost_threshold: int = 1000,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        link_state_path = Path(temp_dir) / "link_state.json"
        link_state_path.write_text(json.dumps({"slots": []}), encoding="utf-8")
        temporal_plan = build_temporal_plan(
            topology_inventory={"satellites": [], "slot_inventory": {}},
            link_state_path=link_state_path,
            propagated_slices=[_orbit_slice(index) for index in range(1, len(snapshots) + 1)],
            snapshots=snapshots,
            runtime_update_plans=plans,
            parameter_max_interval=None,
            delay_threshold_ms=delay_threshold_ms,
            loss_threshold_pct=loss_threshold_pct,
            bandwidth_threshold_pct=bandwidth_threshold_pct,
            routing_cost_threshold=routing_cost_threshold,
            observation_interval_seconds=10.0,
            event_evaluation_interval_seconds=10.0,
            runtime_parameter_model=EchoRuntimeParameterModel(),
        )
        return temporal_plan.to_dict()


class SamplingUpdateOptimizationTests(unittest.TestCase):
    def test_runtime_epoch_ignores_label_only_churn(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        plans = [
            _plan("slice-0001", forward_labels=(100, 101), reverse_labels=(200, 201)),
            _plan("slice-0002", forward_labels=(300, 301), reverse_labels=(400, 401)),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 1)
        self.assertEqual(temporal_plan["no_op_sample_count"], 1)
        counters = temporal_plan["preview_update_counters"]
        self.assertEqual(counters["runtime_epoch_count"], 1)
        self.assertEqual(counters["gz_fec_path_change_count"], 0)

    def test_runtime_epoch_emits_for_semantic_path_change(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        plans = [
            _plan("slice-0001"),
            _plan(
                "slice-0002",
                forward_link_ids=("path-b", "gw-link"),
                reverse_link_ids=("gw-link", "path-b"),
            ),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 2)
        reasons = set(temporal_plan["runtime_epochs"][1]["trigger_reasons"])
        self.assertIn("gz_fec_path_change", reasons)
        self.assertIn("g_to_z_path_change", reasons)
        counters = temporal_plan["preview_update_counters"]
        self.assertEqual(counters["gz_fec_path_change_count"], 1)
        self.assertEqual(counters["bpf_generation_count"], 2)

    def test_parameter_epoch_requires_threshold_crossing(self) -> None:
        snapshots = [
            _base_snapshot(1, delay_ms=10.0),
            _base_snapshot(2, delay_ms=10.4),
            _base_snapshot(3, delay_ms=12.0),
        ]
        plans = [_plan("slice-0001"), _plan("slice-0002"), _plan("slice-0003")]

        temporal_plan = _build_plan(snapshots, plans, delay_threshold_ms=1.0)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 1)
        self.assertEqual(len(temporal_plan["parameter_epochs"]), 1)
        parameter_epoch = temporal_plan["parameter_epochs"][0]
        self.assertEqual(parameter_epoch["slice_id"], "slice-0003")
        self.assertIn("delay_threshold_exceeded", parameter_epoch["trigger_reasons"])
        counters = temporal_plan["preview_update_counters"]
        self.assertGreater(counters["qdisc_parameter_threshold_crossing_count"], 0)
        self.assertEqual(counters["expected_qdisc_command_count"], len(parameter_epoch["changed_link_ids"]))

    def test_sample_tick_with_no_semantic_change_produces_no_runtime_transaction(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2), _base_snapshot(3)]
        plans = [_plan("slice-0001"), _plan("slice-0002"), _plan("slice-0003")]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 1)
        self.assertEqual(temporal_plan["preview_update_counters"]["runtime_transaction_count"], 1)
        self.assertEqual(temporal_plan["no_op_sample_count"], 2)

    def test_metadata_only_change_is_ignored_and_counted(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        plans = [
            _plan("slice-0001", forward_labels=(100, 101), reverse_labels=(200, 201)),
            _plan("slice-0002", forward_labels=(300, 301), reverse_labels=(400, 401)),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 1)
        counters = temporal_plan["preview_update_counters"]
        self.assertEqual(counters["ignored_metadata_only_change_count"], 1)
        self.assertEqual(counters["runtime_transaction_count"], counters["runtime_epoch_count"])

    def test_routing_cost_only_drift_is_ignored(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        drifted = deepcopy(snapshots[1])
        for link in drifted.active_links:
            link["runtime_parameters"]["routing_cost"] += 10
        snapshots[1] = drifted
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(snapshots, plans, routing_cost_threshold=1000)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 1)
        self.assertEqual(len(temporal_plan["parameter_epochs"]), 0)
        self.assertEqual(
            temporal_plan["preview_update_counters"]["ignored_cost_only_change_count"],
            4,
        )

    def test_below_threshold_parameter_drift_is_ignored(self) -> None:
        snapshots = [_base_snapshot(1, delay_ms=10.0), _base_snapshot(2, delay_ms=10.4)]
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(snapshots, plans, delay_threshold_ms=1.0)

        self.assertEqual(len(temporal_plan["parameter_epochs"]), 0)
        self.assertEqual(temporal_plan["no_op_sample_count"], 1)

    def test_coverage_invalidation_forces_runtime_transaction(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        invalidated = deepcopy(snapshots[1])
        invalidated.zones["zone-a"]["active_serving_satellite_id"] = None
        invalidated.zones["zone-a"]["resolution_state"] = "unresolved_no_zone_visible_satellite"
        snapshots[1] = invalidated
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 2)
        self.assertIn("coverage_invalidation", temporal_plan["runtime_epochs"][1]["trigger_reasons"])
        self.assertEqual(
            temporal_plan["preview_update_counters"]["coverage_invalidation_forced_change_count"],
            1,
        )

    def test_coverage_invalidation_refines_event_time_between_samples(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        invalidated = deepcopy(snapshots[1])
        invalidated.zones["zone-a"]["active_serving_satellite_id"] = None
        invalidated.zones["zone-a"]["resolution_state"] = "unresolved_no_zone_visible_satellite"
        snapshots[1] = invalidated
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(snapshots, plans)

        runtime_epoch = temporal_plan["runtime_epochs"][1]
        self.assertEqual(runtime_epoch["refinement_status"], "refined")
        self.assertEqual(runtime_epoch["refinement_method"], "bounded_local_bisection")
        self.assertEqual(runtime_epoch["refinement_reason"], "coverage_invalidation")
        self.assertEqual(runtime_epoch["event_time_accuracy"], "refined_bounded_interval_estimate")
        self.assertEqual(runtime_epoch["sample_aligned_relative_time_seconds"], 10.0)
        self.assertEqual(runtime_epoch["candidate_interval_start_relative_time_seconds"], 0.0)
        self.assertEqual(runtime_epoch["candidate_interval_end_relative_time_seconds"], 10.0)
        self.assertGreater(runtime_epoch["refinement_iteration_count"], 0)
        self.assertLessEqual(
            runtime_epoch["refinement_search_upper_relative_time_seconds"]
            - runtime_epoch["refinement_search_lower_relative_time_seconds"],
            runtime_epoch["refinement_precision_seconds"],
        )
        self.assertGreater(runtime_epoch["activation_relative_time_seconds"], 0.0)
        self.assertLess(runtime_epoch["activation_relative_time_seconds"], 10.0)
        self.assertEqual(
            temporal_plan["preview_update_counters"]["refined_event_count"],
            1,
        )

    def test_ut_handover_refines_event_time_between_samples(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        plans = [
            _plan("slice-0001", serving_satellite_id="sat-a"),
            _plan("slice-0002", serving_satellite_id="sat-b"),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        runtime_epoch = temporal_plan["runtime_epochs"][1]
        self.assertIn("ut_serving_satellite_change", runtime_epoch["trigger_reasons"])
        self.assertEqual(runtime_epoch["refinement_status"], "refined")
        self.assertEqual(runtime_epoch["refinement_method"], "bounded_local_bisection")
        self.assertEqual(runtime_epoch["refinement_reason"], "ut_serving_satellite_change")
        self.assertEqual(runtime_epoch["activation_relative_time_seconds"], 5.0)
        runtime_events = [
            event
            for event in temporal_plan["event_queue"]
            if event["event_type"] == "runtime_epoch"
        ]
        self.assertEqual(runtime_events[1]["activation_relative_time_seconds"], 5.0)
        self.assertEqual(runtime_events[1]["detected_relative_time_seconds"], 10.0)

    def test_active_inactive_link_transition_forces_runtime_transaction(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        removed_link = deepcopy(snapshots[1])
        snapshots[1] = replace(
            removed_link,
            active_links=[
                link for link in removed_link.active_links if link["link_id"] != "path-b"
            ],
        )
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 2)
        self.assertIn("structural_link_change", temporal_plan["runtime_epochs"][1]["trigger_reasons"])
        self.assertEqual(
            temporal_plan["preview_update_counters"]["structural_link_active_inactive_count"],
            1,
        )
        runtime_epoch = temporal_plan["runtime_epochs"][1]
        self.assertEqual(runtime_epoch["refinement_status"], "refined")
        self.assertEqual(runtime_epoch["refinement_reason"], "structural_link_change")
        self.assertEqual(runtime_epoch["refinement_method"], "bounded_local_bisection")

    def test_gateway_selection_change_refines_event_time_between_samples(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        plans = [
            _plan("slice-0001", selected_gateway_id="gw1"),
            _plan("slice-0002", selected_gateway_id="gw2"),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        runtime_epoch = temporal_plan["runtime_epochs"][1]
        self.assertIn("gw_selection_change", runtime_epoch["trigger_reasons"])
        self.assertEqual(runtime_epoch["refinement_status"], "refined")
        self.assertEqual(runtime_epoch["refinement_reason"], "gw_selection_change")
        self.assertEqual(runtime_epoch["activation_relative_time_seconds"], 5.0)

    def test_loss_and_bandwidth_threshold_crossings_produce_parameter_batches(self) -> None:
        snapshots = [
            _base_snapshot(1, loss_percent=0.0, bandwidth_mbps=1000.0),
            _base_snapshot(2, loss_percent=2.0, bandwidth_mbps=850.0),
        ]
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(
            snapshots,
            plans,
            loss_threshold_pct=1.0,
            bandwidth_threshold_pct=10.0,
        )

        self.assertEqual(len(temporal_plan["parameter_epochs"]), 1)
        counters = temporal_plan["preview_update_counters"]
        self.assertEqual(counters["parameter_update_batch_count"], 1)
        self.assertGreater(counters["loss_drop_threshold_crossing_count"], 0)
        self.assertGreater(counters["rate_bandwidth_threshold_crossing_count"], 0)
        parameter_epoch = temporal_plan["parameter_epochs"][0]
        self.assertEqual(parameter_epoch["refinement_status"], "unsupported")
        self.assertEqual(
            parameter_epoch["refinement_reason"],
            "parameter_threshold_batch_discrete_update",
        )
        self.assertEqual(parameter_epoch["event_time_accuracy"], "sample_aligned")

    def test_runtime_transaction_coalesces_multiple_changes_at_same_event_time(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        changed_zone = deepcopy(snapshots[1])
        changed_zone.zones["zone-a"]["active_serving_satellite_id"] = "sat-b"
        snapshots[1] = changed_zone
        plans = [
            _plan("slice-0001"),
            _plan(
                "slice-0002",
                forward_link_ids=("path-b", "gw-link"),
                reverse_link_ids=("gw-link", "path-b"),
            ),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        self.assertEqual(len(temporal_plan["runtime_epochs"]), 2)
        second_epoch = temporal_plan["runtime_epochs"][1]
        self.assertIn("zone_active_serving_satellite_change", second_epoch["trigger_reasons"])
        self.assertIn("g_to_z_path_change", second_epoch["trigger_reasons"])
        self.assertEqual(temporal_plan["preview_update_counters"]["coalesced_event_count"], 1)
        self.assertEqual(second_epoch["refinement_status"], "refined")
        self.assertEqual(
            second_epoch["refinement_reason"],
            "zone_active_serving_satellite_change",
        )

    def test_bpf_generation_event_time_follows_refined_runtime_transaction(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        changed_zone = deepcopy(snapshots[1])
        changed_zone.zones["zone-a"]["active_serving_satellite_id"] = "sat-b"
        snapshots[1] = changed_zone
        plans = [
            _plan("slice-0001"),
            _plan(
                "slice-0002",
                forward_link_ids=("path-b", "gw-link"),
                reverse_link_ids=("gw-link", "path-b"),
            ),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        second_epoch = temporal_plan["runtime_epochs"][1]
        self.assertIn("bpf_generation_content_change", second_epoch["trigger_reasons"])
        self.assertEqual(temporal_plan["preview_update_counters"]["bpf_generation_count"], 2)
        self.assertEqual(second_epoch["activation_relative_time_seconds"], 5.0)
        self.assertEqual(second_epoch["refinement_method"], "bounded_local_bisection")
        runtime_events = [
            event
            for event in temporal_plan["event_queue"]
            if event["event_type"] == "runtime_epoch"
        ]
        self.assertEqual(
            runtime_events[1]["activation_relative_time_seconds"],
            second_epoch["activation_relative_time_seconds"],
        )
        self.assertEqual(
            runtime_events[1]["metadata"]["event_time_accuracy"],
            "refined_bounded_interval_estimate",
        )

    def test_unsupported_runtime_event_falls_back_to_sample_boundary(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2)]
        plans = [
            _plan("slice-0001"),
            _plan(
                "slice-0002",
                forward_link_ids=("path-b", "gw-link"),
                reverse_link_ids=("gw-link", "path-b"),
            ),
        ]

        temporal_plan = _build_plan(snapshots, plans)

        second_epoch = temporal_plan["runtime_epochs"][1]
        self.assertEqual(second_epoch["activation_relative_time_seconds"], 10.0)
        self.assertEqual(second_epoch["refinement_status"], "unsupported")
        self.assertTrue(
            second_epoch["refinement_reason"].startswith("unsupported_runtime_reason:")
        )
        self.assertEqual(temporal_plan["preview_update_counters"]["refined_event_count"], 0)
        self.assertGreaterEqual(
            temporal_plan["preview_update_counters"]["refinement_unsupported_event_count"],
            1,
        )

    def test_event_timeline_preview_counters_are_populated_and_consistent(self) -> None:
        snapshots = [_base_snapshot(1), _base_snapshot(2, delay_ms=12.0)]
        plans = [_plan("slice-0001"), _plan("slice-0002")]

        temporal_plan = _build_plan(snapshots, plans, delay_threshold_ms=1.0)

        counters = temporal_plan["preview_update_counters"]
        self.assertEqual(counters["physical_sample_count"], 2)
        self.assertEqual(counters["runtime_transaction_count"], counters["runtime_epoch_count"])
        self.assertEqual(counters["parameter_update_batch_count"], counters["parameter_epoch_count"])
        self.assertEqual(
            counters["sample_aligned_event_count"],
            counters["runtime_transaction_count"] + counters["parameter_update_batch_count"],
        )
        self.assertIn("refinement_supported_event_count", counters)
        self.assertIn("refinement_failed_event_count", counters)
        self.assertIn("refinement_precision_seconds", counters)
        self.assertIn("refinement_reason_breakdown", counters)
        self.assertGreater(counters["link_span_count"], 0)
        self.assertGreater(counters["attachment_span_count"], 0)
        self.assertGreater(counters["fec_path_span_count"], 0)
        projection = temporal_plan["event_time_summary"]["event_timeline_projection"]
        self.assertEqual(
            projection["runtime_transaction_count"],
            counters["runtime_transaction_count"],
        )

    def test_min_dwell_keeps_current_zone_anchor_until_dwell_expires(self) -> None:
        generator = ZoneAnchorQualityGenerator(
            hysteresis_score_delta=0.0,
            coverage_loss_margin_deg=0.0,
            gateway_hop_penalty=0.0,
            min_anchor_dwell_seconds=60.0,
        )

        def orbit_slice(relative_time: float, *, sat_a_direct: bool) -> OrbitTopologySlice:
            sat_a = {"node_id": "sat-a", "node_type": "satellite"}
            sat_b = {"node_id": "sat-b", "node_type": "satellite"}
            if sat_a_direct:
                sat_a["position_state"] = {"x_km": 7000.0, "y_km": 0.0, "z_km": 0.0}
                sat_b["position_state"] = {"x_km": 6971.0, "y_km": 1000.0, "z_km": 0.0}
            else:
                sat_a["position_state"] = {"x_km": 6971.0, "y_km": 1000.0, "z_km": 0.0}
                sat_b["position_state"] = {"x_km": 7000.0, "y_km": 0.0, "z_km": 0.0}
            return OrbitTopologySlice(
                time_slice={
                    "slice_id": f"slice-{int(relative_time):04d}",
                    "relative_time_seconds": relative_time,
                },
                nodes={"sat-a": sat_a, "sat-b": sat_b},
                candidate_links=[],
                zone_generation_input=ZoneGenerationInput(
                    membership_mode="test",
                    zones={
                        "zone-a": {
                            "zone_id": "zone-a",
                            "ut_ids": [],
                            "candidate_serving_satellite_ids": ["sat-a", "sat-b"],
                            "min_elevation_deg": 0.0,
                            "coverage_margin_deg": 0.0,
                            "position_state": {"x_km": 6371.0, "y_km": 0.0, "z_km": 0.0},
                        }
                    },
                    ut_zone_membership={},
                ),
            )

        first = generator.generate(
            orbit_slice(0.0, sat_a_direct=True),
            VisibilitySlice(active_links=[], visibility_model="test"),
        )
        held = generator.generate(
            orbit_slice(10.0, sat_a_direct=False),
            VisibilitySlice(active_links=[], visibility_model="test"),
        )
        released = generator.generate(
            orbit_slice(70.0, sat_a_direct=False),
            VisibilitySlice(active_links=[], visibility_model="test"),
        )

        self.assertEqual(first.zones["zone-a"]["active_serving_satellite_id"], "sat-a")
        self.assertEqual(held.zones["zone-a"]["active_serving_satellite_id"], "sat-a")
        self.assertEqual(
            held.zones["zone-a"]["anchor_selection"]["change_reason"],
            "min_dwell_keep_current_anchor",
        )
        self.assertEqual(released.zones["zone-a"]["active_serving_satellite_id"], "sat-b")


if __name__ == "__main__":
    unittest.main()
