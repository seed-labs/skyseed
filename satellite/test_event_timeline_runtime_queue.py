#!/usr/bin/env python3
# encoding: utf-8

import sys
import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import patch

SATELLITE_DIR = Path(__file__).resolve().parent
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))
APPS_DIR = SATELLITE_DIR / "apps"
if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from apps.run_starlink_shell_experiment import (  # noqa: E402
    _append_forwarding_relevant_lookahead_boundary,
    _assert_operational_clean_host_apply_route,
    _bpf_backend_opted_in,
    _bpf_active_base_generation_record_for_preload,
    _bpf_map_capacity_generation_window_count,
    _bpf_generation_slot_width_for_horizon_budget,
    _bpf_triple_buffer_generation_slot_width,
    _bpf_triple_buffer_physical_generation_id,
    _advance_link_state_for_bpf_owned_parameter_skip,
    _merge_horizon_maintenance_handoff_preloaded_records,
    _merge_bpf_owned_route_identity_evidence,
    _merge_prepared_attach_capability_readiness,
    _maintenance_horizon_ids,
    _retired_bpf_generation_cleanup_candidate_horizon_status,
    _retired_cleanup_candidate_lifecycle_transition,
    _stage_bpf_owned_readiness_for_preloaded_apply,
    _bpf_owned_host_plan_without_parameter_updates,
    _compact_epoch_execution_record,
    _compact_preloaded_generation_record,
    _rolling_horizon_deferred_bpf_generation_record,
    _rolling_horizon_process_preload_work_item,
    _rolling_horizon_queued_bpf_generation_record,
    _compose_service_metadata,
    _compose_startup_wall_time_attribution,
    _compose_startup_progress_contract,
    _compose_startup_stages,
    _compose_stage_recovery_timeout_seconds,
    _compose_stage_retry_batches,
    _compose_stage_timeout_seconds,
    _copy_artifact,
    _continuous_operation_summary,
    _continuous_total_horizon_seconds,
    _delta_scoped_network_mutation_plan,
    _experiment_summary_payload,
    _finalize_runtime_status_fields,
    _freeze_prepared_update_bundle,
    _ground_endpoint_startup_summary,
    _horizon_id_for_queue_entry_from_plan,
    _materialized_lookahead_attach_budget_expansion,
    _horizon_lifecycle_queue_contract,
    _input_profile_config_hash,
    _prepare_replenished_host_plan_payloads,
    _select_prepared_replenished_host_plan_payloads,
    _prepare_frozen_rolling_horizon_segments,
    _preview_summary_to_segment,
    _retired_bpf_generation_cleanup_candidate_horizon_status,
    _run_replenishment_preview,
    _normal_runtime_inputs_with_bpf_generation_slots,
    _normal_runtime_inputs_with_bpf_resident_attach_budget,
    _thaw_normal_runtime_initial_segment,
    _bundle_backed_live_execution_source_gate,
    _bundle_backed_minimal_helper_consumption_proof,
    _bundle_backed_runtime_source_binding_gate,
    _frozen_bundle_normal_runtime_inputs_gate,
    _load_frozen_prepared_bundle_gate,
    _precompose_bpf_role_union_gate_from_reports,
    _precompose_horizon_queue_gate,
    _prepared_bundle_with_externalized_executable_inputs,
    _prepared_attach_capability_readiness_for_preload,
    _resource_scope_delta_by_slot,
    _verify_frozen_prepared_bundle,
    _write_json_breaking_hardlink,
    _live_timing_summary,
    _runtime_failure_context_summary,
    _run_live_temporal_runtime,
    _runtime_queue_bpf_route_identity_contexts,
    _stage_ready_service_names,
    _temporal_execution_queue,
    _wait_for_compose_containers,
)
from contracts import (  # noqa: E402
    NetworkMutationPlan,
    RuntimeUpdatePlan,
    TopologySnapshot,
    RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
    TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
)
from linux_mpls_runtime import (  # noqa: E402
    DirectGatewayExperimentContext,
    MplsPathSegment,
    _bpf_owned_prepared_attach_capability_contract,
)


def _direct_gateway_context(flow_id: str = "flow-a") -> DirectGatewayExperimentContext:
    segment = MplsPathSegment(
        link_id="link-a",
        source_node_id="stl-a",
        source_node_type="satellite",
        source_interface="sat-a",
        source_ip="10.0.0.1",
        destination_node_id="gw-a",
        destination_node_type="gateway",
        destination_interface="gw-a",
        destination_ip="10.0.0.2",
        label=100,
    )
    return DirectGatewayExperimentContext(
        flow_id=flow_id,
        ut_id="ut-a",
        zone_id="zone-a",
        serving_satellite_id="stl-a",
        selected_gateway_id="gw-a",
        selected_gateway_satellite_id="stl-a",
        zone_return_prefixes=("10.90.0.2/32",),
        ping_target_ip="192.0.2.10",
        target_host_node_id="target",
        policy_table_id=101,
        policy_rule_priority=101,
        satellite_path=("stl-a",),
        forward_labels=(100,),
        reverse_labels=(200,),
        ut_access_interface="ut-a",
        ut_access_ip="10.90.0.2",
        ut_access_peer_ip="10.90.0.1",
        gateway_access_interface="gw-a",
        gateway_access_ip="10.91.0.2",
        gateway_access_peer_ip="10.91.0.1",
        gateway_nat_interface="gw-nat",
        gateway_nat_source_ip="198.51.100.2",
        forward_segments=(segment,),
        reverse_segments=(segment,),
        validation_targets=(),
    )


def _temporal_plan() -> dict:
    return {
        "orbit_samples": [
            {
                "sample_id": "sample-1",
                "sample_index": 1,
                "slice_id": "slice-1",
                "relative_time_seconds": 0.0,
                "absolute_time_utc": "2026-06-21T00:00:00Z",
            },
            {
                "sample_id": "sample-2",
                "sample_index": 2,
                "slice_id": "slice-2",
                "relative_time_seconds": 10.0,
                "absolute_time_utc": "2026-06-21T00:00:10Z",
            },
            {
                "sample_id": "sample-3",
                "sample_index": 3,
                "slice_id": "slice-3",
                "relative_time_seconds": 20.0,
                "absolute_time_utc": "2026-06-21T00:00:20Z",
            },
        ],
        "topology_snapshot_refs": [
            {"slice_id": "slice-1", "snapshot_id": "snap-1"},
            {"slice_id": "slice-2", "snapshot_id": "snap-2"},
            {"slice_id": "slice-3", "snapshot_id": "snap-3"},
        ],
        "runtime_epochs": [
            {
                "epoch_id": "runtime-1",
                "sample_id": "sample-1",
                "slice_id": "slice-1",
                "snapshot_id": "snap-1",
                "activation_relative_time_seconds": 0.0,
                "activation_time_utc": "2026-06-21T00:00:00Z",
                "activation_group_id": "activation-000",
                "deadline_policy": "execute_not_later_than_sample_boundary",
                "trigger_reasons": ["initial_runtime_epoch"],
                "active_link_count": 3,
                "changed_link_ids": ["link-a"],
            }
        ],
        "parameter_epochs": [
            {
                "epoch_id": "parameter-1",
                "sample_id": "sample-3",
                "slice_id": "slice-3",
                "snapshot_id": "snap-3",
                "activation_relative_time_seconds": 20.0,
                "activation_time_utc": "2026-06-21T00:00:20Z",
                "activation_group_id": "activation-020",
                "update_policy": "threshold_or_interval_refresh_without_topology_mutation",
                "trigger_reasons": ["delay_threshold_exceeded"],
                "active_link_count": 3,
                "changed_link_ids": ["link-a"],
            }
        ],
        "event_queue": [
            {
                "event_id": "event-runtime-1",
                "event_type": "runtime_epoch",
                "source_sample_id": "sample-1",
                "slice_id": "slice-1",
                "snapshot_id": "snap-1",
                "activation_relative_time_seconds": 0.0,
                "activation_time_utc": "2026-06-21T00:00:00Z",
                "activation_group_id": "activation-000",
                "execution_policy": "execute_not_later_than_sample_boundary",
                "trigger_reasons": ["initial_runtime_epoch"],
                "linked_epoch_id": "runtime-1",
                "metadata": {"active_link_count": 3, "changed_link_count": 1},
            },
            {
                "event_id": "event-parameter-1",
                "event_type": "parameter_epoch",
                "source_sample_id": "sample-3",
                "slice_id": "slice-3",
                "snapshot_id": "snap-3",
                "activation_relative_time_seconds": 20.0,
                "activation_time_utc": "2026-06-21T00:00:20Z",
                "activation_group_id": "activation-020",
                "execution_policy": "threshold_or_interval_refresh_without_topology_mutation",
                "trigger_reasons": ["delay_threshold_exceeded"],
                "linked_epoch_id": "parameter-1",
                "metadata": {"active_link_count": 3, "changed_link_count": 1},
            },
        ],
        "no_op_sample_count": 1,
        "preparation_plan": {
            "continuous_operation_prepared": {
                "rolling_horizon_replenishment": "prepared_not_yet_live",
                "runtime_epoch_batching_policy": "same_activation_group_only",
            }
        },
        "event_time_summary": {
            "mode": "event_timeline_queue",
            "event_timeline_projection": {
                "event_time_accuracy": {
                    "sample_aligned_event_count": 2,
                    "refined_event_count": 0,
                }
            },
        },
        "preview_update_counters": {
            "physical_sample_count": 3,
            "runtime_transaction_count": 1,
            "parameter_update_batch_count": 1,
            "skipped_no_op_sample_count": 1,
            "sample_aligned_event_count": 2,
            "refined_event_count": 0,
        },
    }


def _summary_args(**overrides: object) -> SimpleNamespace:
    values = {
        "run_mode": "real-runtime",
        "continuous": False,
        "max_satellites": 3,
        "ut_catalog": None,
        "gateway_catalog": None,
        "zone_catalog": None,
        "ut_ids": "",
        "gateway_ids": "",
        "max_host_workers": 0,
        "runtime_mode": "operational_clean",
        "forwarding_backend": "bpf_owned",
        "bpf_dataplane_canary": False,
        "bpf_backend": "off",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _summary_runtime_report(
    *,
    aggregate_runtime_succeeded: bool = True,
    failure_reason: str | None = None,
    timing_summary: dict | None = None,
) -> dict:
    return {
        "finalized": True,
        "mode": "real-runtime",
        "timing_summary": dict(timing_summary or {}),
        "temporal_audit": {"source": "event_timeline"},
        "iterations": [],
        "prepared_bundle_runtime": {
            "source": "event_timeline",
            "physical_sample_count": 3,
            "runtime_transaction_count": 1,
            "parameter_update_batch_count": 1,
            "skipped_noop_sample_count": 1,
            "sample_aligned_event_count": 2,
            "refined_event_count": 0,
            "epoch_execution_queue": [],
        },
        "outcome": {
            "real_apply_success": True,
            "ping_success": True,
            "rollback_completed": True,
            "event_timeline_execution_success": True,
            "pre_rollback_connectivity_success": True,
            "rollback_success": True,
            "post_run_preflight_clean": True,
            "aggregate_runtime_succeeded": aggregate_runtime_succeeded,
            "failure_reason": failure_reason,
            "linux_gz_fallback_enabled": False,
            "linux_gz_fallback_command_count": 0,
            "eligible_core_fallback_to_linux": 0,
            "bpf_classifier_miss_count": 0,
            "bpf_ping_path_hit_count": 2,
            "bpf_return_handoff_hit_count": 1,
        },
    }


def _summary_payload_for_test(
    *,
    args: SimpleNamespace | None = None,
    runtime_report: dict | None = None,
    runtime_blocker: dict | None = None,
    timing_analysis: dict | None = None,
    bpf_dataplane_canary: dict | None = None,
) -> dict:
    temporal_plan = _temporal_plan()
    return _experiment_summary_payload(
        args=args or _summary_args(),
        run_id="run-1",
        run_dir=Path("/tmp/run-1"),
        preview_summary={},
        temporal_payload=temporal_plan,
        selected_runtime_slice_ids=["slice-1"],
        selected_parameter_slice_ids=["slice-2"],
        preview_gate={},
        runtime_report=runtime_report,
        runtime_blocker=runtime_blocker,
        watcher_summary={},
        phase_timing={},
        timing_analysis=timing_analysis or {},
        compose_audit={},
        startup_strategy=None,
        mini_internet_summary={},
        continuous_operation_summary={},
        slot_capacity_audit={},
        data_provenance={},
        artifacts={"real_runtime_report_json": "/tmp/run-1/real_runtime_report.json"},
        preflight_report=None,
        host_prepare_report=None,
        post_run_preflight_report={"clean_after_run": True},
        bpf_dataplane_canary=bpf_dataplane_canary,
    )


class EventTimelineRuntimeQueueTests(unittest.TestCase):
    def test_attach_capability_handoff_preserves_resident_union(self) -> None:
        existing = {
            "attach_plan": {"netns:sat-a:if0": ["ingress"]},
            "attached_interfaces": ["netns:sat-a:if0"],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:sat-a:if0",
                    "ifindex": 101,
                    "mac_address": "02:00:00:00:00:01",
                    "expected_roles": ["g_to_z_classifier"],
                    "attach_directions": ["ingress"],
                    "control_plane_ipv4_addrs": [],
                }
            ],
        }
        handoff = {
            "ok": True,
            "attach_plan": {
                "netns:sat-a:if0": ["egress"],
                "netns:sat-b:if1": ["ingress"],
            },
            "attached_interfaces": [
                "netns:sat-a:if0",
                "netns:sat-b:if1",
            ],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:sat-a:if0",
                    "ifindex": 101,
                    "mac_address": "02:00:00:00:00:01",
                    "expected_roles": ["z_to_g_zone_handoff"],
                    "attach_directions": ["egress"],
                    "control_plane_ipv4_addrs": ["10.0.0.1"],
                },
                {
                    "interface_ref": "netns:sat-b:if1",
                    "ifindex": 202,
                    "mac_address": "02:00:00:00:00:02",
                    "expected_roles": ["g_to_z_classifier"],
                    "attach_directions": ["ingress"],
                    "control_plane_ipv4_addrs": [],
                },
            ],
        }

        merged = _merge_prepared_attach_capability_readiness(existing, handoff)

        self.assertEqual(merged["prepared_bpf_attach_interface_count"], 2)
        self.assertEqual(
            merged["attach_plan"]["netns:sat-a:if0"],
            ["egress", "ingress"],
        )
        self.assertEqual(
            merged["attached_interface_details"][0]["expected_roles"],
            ["g_to_z_classifier", "z_to_g_zone_handoff"],
        )
        self.assertTrue(merged["resident_attach_capability_union"])
        self.assertFalse(
            merged["frozen_interface_binding_cache"]["live_discovery_allowed"]
        )

    def test_attach_capability_handoff_binding_conflict_fails_closed(self) -> None:
        detail = {
            "interface_ref": "netns:sat-a:if0",
            "ifindex": 101,
            "mac_address": "02:00:00:00:00:01",
        }
        with self.assertRaisesRegex(
            RuntimeError,
            "horizon_maintenance_attach_capability_ifindex_conflict",
        ):
            _merge_prepared_attach_capability_readiness(
                {
                    "attached_interfaces": [detail["interface_ref"]],
                    "attached_interface_details": [detail],
                },
                {
                    "attached_interfaces": [detail["interface_ref"]],
                    "attached_interface_details": [{**detail, "ifindex": 202}],
                },
            )

    def test_retired_cleanup_candidates_wait_then_promote_full_horizon(self) -> None:
        pending: dict[str, dict[int, dict]] = {}
        for generation_id in range(25):
            transition = _retired_cleanup_candidate_lifecycle_transition(
                pending_by_horizon=pending,
                candidate={
                    "generation_id": generation_id,
                    "horizon_id": "H0",
                    "compiled_runtime_inputs": {
                        "schema_version": (
                            "skyseed-bpf-owned-compiled-runtime-inputs-v2"
                        ),
                        "active_generation": {"value": generation_id},
                        "ingress_classifier_map": [],
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
                        "core_lfib_map": [],
                        "egress_handoff_map": [],
                            "zone_edge_delivery_map": [],
                        "generation_base_map": [],
                    },
                },
                readiness={"current_horizon_id": "H0"},
            )
            self.assertTrue(transition["pending"])
        promoted = _retired_cleanup_candidate_lifecycle_transition(
            pending_by_horizon=pending,
            candidate={
                "generation_id": 24,
                "horizon_id": "H0",
                "compiled_runtime_inputs": {
                    "schema_version": (
                        "skyseed-bpf-owned-compiled-runtime-inputs-v2"
                    ),
                    "active_generation": {"value": 24},
                    "ingress_classifier_map": [],
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
                    "core_lfib_map": [],
                    "egress_handoff_map": [],
                            "zone_edge_delivery_map": [],
                    "generation_base_map": [],
                },
            },
            readiness={"current_horizon_id": "H1"},
        )

        self.assertFalse(promoted["pending"])
        self.assertEqual(promoted["promoted_candidate_count"], 25)
        self.assertEqual(pending, {})

    def test_staged_horizon_does_not_relabel_active_horizon(self) -> None:
        staged = _stage_bpf_owned_readiness_for_preloaded_apply(
            {"current_horizon_id": "H0", "current_source_state_id": "state-h0"},
            target_horizon_id="H1",
            source_state_id="state-h1-source",
        )

        self.assertEqual(staged["current_horizon_id"], "H0")
        self.assertEqual(staged["current_source_state_id"], "state-h0")
        self.assertEqual(staged["staged_target_horizon_id"], "H1")

    def test_route_identity_handoff_merges_previous_and_worker_evidence(self) -> None:
        merged = _merge_bpf_owned_route_identity_evidence(
            {
                "ut_default_routes": [
                    {"key": ["ut1", "ua0", "10.0.0.1", "10.0.0.2"]}
                ],
                "gateway_local_return_routes": [],
            },
            {
                "ut_default_routes": [],
                "gateway_local_return_routes": [
                    {
                        "key": [
                            "gw1",
                            "ga0",
                            "10.1.0.1",
                            "10.1.0.2",
                            "10.0.0.2",
                        ]
                    }
                ],
            },
        )

        self.assertEqual(merged["ut_default_route_count"], 1)
        self.assertEqual(merged["gateway_local_return_route_count"], 1)
        self.assertEqual(len(merged["ut_default_routes"]), 1)
        self.assertEqual(len(merged["gateway_local_return_routes"]), 1)

    def test_live_runtime_does_not_reach_back_to_normal_runtime_inputs(self) -> None:
        source = inspect.getsource(_run_live_temporal_runtime)

        self.assertNotIn("normal_runtime_inputs", source)

    def test_pre_runtime_route_contexts_use_live_runtime_queue_contexts(self) -> None:
        context = _direct_gateway_context("flow-a")
        queue = [
            {
                "classification": "runtime_epoch",
                "segment_epoch_key": "initial:slice-a",
                "slice_id": "slice-a",
                "epoch_id": "runtime-a",
                "runtime_apply_allowed": True,
            }
        ]
        context_by_key = {
            "initial:slice-a": {
                "snapshot": object(),
                "runtime_update_plan": object(),
                "topology_path": "/tmp/slice-a-topology.json",
            }
        }

        with patch(
            "apps.run_starlink_shell_experiment._realization_contexts",
            return_value=(context,),
        ) as realization_contexts:
            result = _runtime_queue_bpf_route_identity_contexts(
                entries=queue,
                context_by_key=context_by_key,
                default_topology_path=Path("/tmp/default-topology.json"),
            )

        self.assertEqual(result, (context,))
        realization_contexts.assert_called_once()
        self.assertEqual(
            realization_contexts.call_args.kwargs["topology_path"],
            Path("/tmp/slice-a-topology.json"),
        )

    def test_pre_runtime_route_contexts_fail_closed_on_missing_queue_context(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "runtime queue route context missing",
        ):
            _runtime_queue_bpf_route_identity_contexts(
                entries=[
                    {
                        "classification": "runtime_epoch",
                        "segment_epoch_key": "initial:slice-a",
                        "slice_id": "slice-a",
                        "epoch_id": "runtime-a",
                        "runtime_apply_allowed": True,
                    }
                ],
                context_by_key={},
                default_topology_path=Path("/tmp/default-topology.json"),
            )

    def test_bpf_triple_buffer_physical_generation_uses_local_horizon_slots(self) -> None:
        slot_width = _bpf_triple_buffer_generation_slot_width(7)

        self.assertEqual(slot_width, 9)
        self.assertEqual(
            _bpf_triple_buffer_physical_generation_id(
                horizon_sequence_index=0,
                local_generation_id=6,
                generation_slot_width=slot_width,
            ),
            6,
        )
        self.assertEqual(
            _bpf_triple_buffer_physical_generation_id(
                horizon_sequence_index=1,
                local_generation_id=6,
                generation_slot_width=slot_width,
            ),
            15,
        )
        self.assertEqual(
            _bpf_triple_buffer_physical_generation_id(
                horizon_sequence_index=3,
                local_generation_id=6,
                generation_slot_width=slot_width,
            ),
            6,
        )

    def test_bpf_triple_buffer_slot_capacity_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "bpf_rolling_generation_slot_capacity_exceeded",
        ):
            _bpf_triple_buffer_physical_generation_id(
                horizon_sequence_index=1,
                local_generation_id=9,
                generation_slot_width=9,
            )

    def test_four_horizon_maintenance_cleanup_follows_retired_horizon(self) -> None:
        records = []
        expected = [
            ("H0", ""),
            ("H1", "H0"),
            ("H2", "H1"),
            ("H3", "H2"),
        ]
        observed = []
        for index, horizon_id in enumerate(("H0", "H1", "H2", "H3")):
            records.append({"horizon_id": horizon_id, "segment_index": index})
            current, previous = _maintenance_horizon_ids(
                current_horizon_id=horizon_id,
                segment_records=records,
            )
            observed.append((current, previous))
        self.assertEqual(observed, expected)

        self.assertFalse(
            _retired_bpf_generation_cleanup_candidate_horizon_status(
                candidate={"horizon_id": "H1", "generation_id": 32},
                readiness={"current_horizon_id": "H1"},
            )["retired"]
        )
        self.assertTrue(
            _retired_bpf_generation_cleanup_candidate_horizon_status(
                candidate={"horizon_id": "H0", "generation_id": 0},
                readiness={"current_horizon_id": "H1"},
            )["retired"]
        )

    def test_bpf_generation_slot_width_uses_horizon_budget(self) -> None:
        self.assertEqual(
            _bpf_generation_slot_width_for_horizon_budget(
                horizon_seconds=720.0,
                sample_interval_seconds=120.0,
                observed_generation_count=3,
            ),
            9,
        )

    def test_bpf_map_capacity_reserves_previous_current_next_slots(self) -> None:
        slot_width = _bpf_triple_buffer_generation_slot_width(25)

        self.assertEqual(slot_width, 32)
        self.assertEqual(
            _bpf_generation_slot_width_for_horizon_budget(
                horizon_seconds=240.0,
                sample_interval_seconds=10.0,
                observed_generation_count=25,
            ),
            32,
        )
        self.assertEqual(
            _bpf_map_capacity_generation_window_count(
                generation_slot_width=slot_width,
                observed_generation_count=25,
            ),
            96,
        )

    def test_horizon_maintenance_handoff_merges_full_preloaded_records(self) -> None:
        compiled_inputs = {
            "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
            "active_generation": {"value": 32},
            "ingress_classifier_map": [],
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
                            "zone_edge_delivery_map": [],
        }
        result = _merge_horizon_maintenance_handoff_preloaded_records(
            readiness={
                "preloaded_generation_records": [
                    {
                        "record_id": "h0-gen",
                        "ok": True,
                        "horizon_id": "H0",
                        "generation_id": 24,
                        "physical_horizon_generation_key": "H0:generation-24",
                        "inactive_generation_written": True,
                        "compiled_runtime_inputs": compiled_inputs,
                    }
                ],
                "prepared_generation_ids": [24],
            },
            target_horizon_id="H1",
            handoff_records=[
                {
                    "record_id": "h1-gen",
                    "ok": True,
                    "horizon_id": "H1",
                    "generation_id": 32,
                    "physical_horizon_generation_key": "H1:generation-32",
                    "inactive_generation_written": True,
                    "retain_inactive_generations": [24, 32],
                    "compiled_runtime_inputs": compiled_inputs,
                }
            ],
            expected_record_count=1,
        )

        readiness = result["readiness"]
        self.assertEqual(result["handoff_preloaded_generation_record_count"], 1)
        self.assertEqual(result["merged_preloaded_generation_record_count"], 2)
        self.assertEqual(readiness["prepared_generation_ids"], [24, 32])
        self.assertTrue(readiness["horizon_maintenance_handoff_consumed"])
        self.assertEqual(
            readiness["preloaded_generation_records"][1]["compiled_runtime_inputs"],
            compiled_inputs,
        )

    def test_horizon_maintenance_handoff_requires_full_compiled_inputs(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "horizon_maintenance_handoff_preloaded_records_missing",
        ):
            _merge_horizon_maintenance_handoff_preloaded_records(
                readiness={},
                target_horizon_id="H1",
                handoff_records=[
                    {
                        "record_id": "compact-h1-gen",
                        "ok": True,
                        "horizon_id": "H1",
                        "generation_id": 32,
                        "physical_horizon_generation_key": "H1:generation-32",
                        "inactive_generation_written": True,
                        "compiled_runtime_inputs_summary": {"present": True},
                    }
                ],
                expected_record_count=1,
            )

    def test_continuous_total_horizon_uses_loop_count(self) -> None:
        args = SimpleNamespace(
            continuous=True,
            horizon_seconds=240.0,
            step_seconds=10.0,
            sample_interval=10.0,
            slice_count=0,
            loop_count=3,
        )

        self.assertEqual(_continuous_total_horizon_seconds(args=args), 720.0)

    def test_frozen_rolling_horizon_segments_prepare_loop_window(self) -> None:
        args = SimpleNamespace(
            continuous=True,
            horizon_seconds=240.0,
            step_seconds=10.0,
            sample_interval=10.0,
            slice_count=0,
            loop_count=3,
            replenish_threshold_seconds=120.0,
        )
        initial_segment = {
            "segment_id": "initial",
            "start_time_utc": "2026-07-04T00:00:00Z",
            "end_time_utc": "2026-07-04T00:03:50Z",
            "current_execution_queue": [],
            "lookahead_preload_queue": [],
            "selected_epoch_queue": [],
        }

        def replenishment(*, segment_index, start_time_utc, horizon_seconds, **_):
            end_seconds = 240 * (segment_index + 1) - 10
            return {
                "segment_id": f"replenished-{segment_index:03d}",
                "start_time_utc": start_time_utc,
                "end_time_utc": (
                    f"2026-07-04T00:{end_seconds // 60:02d}:"
                    f"{end_seconds % 60:02d}Z"
                ),
                "horizon_seconds": horizon_seconds,
                "current_execution_queue": [],
                "lookahead_preload_queue": [],
                "selected_epoch_queue": [],
            }

        with patch(
            "apps.run_starlink_shell_experiment._run_replenishment_preview",
            side_effect=replenishment,
        ) as preview:
            segments = _prepare_frozen_rolling_horizon_segments(
                args=args,
                run_dir=Path("run"),
                initial_segment=initial_segment,
                topology_path=Path("topology.json"),
                link_state_path=Path("link_state.json"),
                run_start_time_utc="2026-07-04T00:00:00Z",
            )

        self.assertEqual([segment["segment_id"] for segment in segments], ["H0", "H1", "H2"])
        self.assertEqual(preview.call_count, 2)
        self.assertEqual(
            [call.kwargs["horizon_seconds"] for call in preview.call_args_list],
            [240.0, 240.0],
        )

    def test_normal_runtime_inputs_carry_bpf_physical_generation_slots(self) -> None:
        def plan_payload(plan_id: str, snapshot_id: str) -> dict:
            return {
                "schema_version": "satellite-runtime-update-plan-v1alpha1",
                "plan_id": plan_id,
                "target_snapshot": {"snapshot_id": snapshot_id},
                "planner": {"planner_name": "test"},
                "apply_model": {
                    "state_model": "full_state_intent",
                    "action_model": "node_local_installer_action_v1",
                },
                "node_subplans": {},
                "replacement_semantics": {},
                "validation_expectations": {},
            }

        current_entries = [
            {
                "segment_epoch_key": "current-horizon:slice-1",
                "horizon_id": "current-horizon",
                "slice_id": "slice-1",
                "classification": "runtime_epoch",
            },
            {
                "segment_epoch_key": "current-horizon:slice-2",
                "horizon_id": "current-horizon",
                "slice_id": "slice-2",
                "classification": "parameter_epoch",
            },
        ]
        replenished_entries = [
            {
                "segment_epoch_key": "replenished-001:slice-3",
                "horizon_id": "replenished-001",
                "slice_id": "slice-3",
                "classification": "runtime_epoch",
            }
        ]
        current_contexts = {
            "slice-1": {"runtime_update_plan": plan_payload("plan-1", "snap-1")},
            "slice-2": {"runtime_update_plan": plan_payload("plan-2", "snap-2")},
        }
        replenished_contexts = {
            "slice-3": {"runtime_update_plan": plan_payload("plan-3", "snap-3")}
        }
        normal_inputs = {
            "initial_segment": {
                "segment_id": "current-horizon",
                "current_execution_queue": current_entries,
            },
            "prepared_horizon_segments": [
                {
                    "segment_id": "current-horizon",
                    "current_execution_queue": current_entries,
                },
                {
                    "segment_id": "replenished-001",
                    "current_execution_queue": replenished_entries,
                },
            ],
            "context_by_slice": dict(current_contexts),
            "context_by_slice_by_segment": {
                "current-horizon": dict(current_contexts),
                "replenished-001": dict(replenished_contexts),
            },
            "context_by_segment_key": {
                "current-horizon:slice-1": {
                    "runtime_update_plan": plan_payload("plan-1", "snap-1")
                },
                "current-horizon:slice-2": {
                    "runtime_update_plan": plan_payload("plan-2", "snap-2")
                },
                "replenished-001:slice-3": {
                    "runtime_update_plan": plan_payload("plan-3", "snap-3")
                },
            },
            "runtime_update_plan_by_segment_key": {
                "current-horizon:slice-1": plan_payload("plan-1", "snap-1"),
                "current-horizon:slice-2": plan_payload("plan-2", "snap-2"),
                "replenished-001:slice-3": plan_payload("plan-3", "snap-3"),
            },
        }
        args = SimpleNamespace(
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
            continuous=True,
            sample_interval=10.0,
            step_seconds=120.0,
            horizon_seconds=240.0,
            replenish_threshold_seconds=120.0,
        )

        patched = _normal_runtime_inputs_with_bpf_generation_slots(
            normal_runtime_inputs=normal_inputs,
            args=args,
        )

        slot_width = _bpf_generation_slot_width_for_horizon_budget(
            horizon_seconds=240.0,
            sample_interval_seconds=10.0,
            observed_generation_count=3,
        )
        self.assertEqual(
            patched["context_by_slice_by_segment"]["current-horizon"]["slice-1"][
                "runtime_update_plan"
            ]["target_snapshot"]["bpf_physical_generation_id"],
            0,
        )
        self.assertEqual(
            patched["context_by_slice_by_segment"]["current-horizon"]["slice-2"][
                "runtime_update_plan"
            ]["target_snapshot"]["bpf_physical_generation_id"],
            1,
        )
        self.assertEqual(
            patched["context_by_slice_by_segment"]["replenished-001"]["slice-3"][
                "runtime_update_plan"
            ]["target_snapshot"]["bpf_physical_generation_id"],
            slot_width,
        )
        self.assertEqual(
            patched["context_by_slice_by_segment"]["replenished-001"]["slice-3"][
                "runtime_update_plan"
            ][
                "target_snapshot"
            ]["bpf_generation_buffer_index"],
            1,
        )
        self.assertNotIn("runtime_update_plan_by_segment_key", patched)
        self.assertNotIn("context_by_segment_key", patched)
        self.assertEqual(
            patched["bpf_generation_slot_allocator"]["assignment_count"],
            3,
        )

    def test_queue_entry_horizon_id_prefers_declared_horizon_before_plan_ids(
        self,
    ) -> None:
        entry = {
            "event_id": "reused-runtime-event",
            "epoch_id": "reused-runtime-event",
            "segment_epoch_key": "H1:slice-001",
            "horizon_id": "H1",
            "slice_id": "slice-001",
        }
        horizon_plan_entries = [
            {
                "horizon_id": "H0",
                "current_event_ids": ["reused-runtime-event"],
            },
            {
                "horizon_id": "H1",
                "current_event_ids": ["reused-runtime-event"],
            },
        ]

        horizon_id = _horizon_id_for_queue_entry_from_plan(
            entry,
            horizon_plan_entries=horizon_plan_entries,
            segment_queue=[entry],
        )

        self.assertEqual(horizon_id, "H1")

    def test_write_json_breaking_hardlink_preserves_source_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "frozen_prepared_update_bundle.json"
            alias_path = Path(tmpdir) / "prepared_update_bundle.json"
            _write_json_breaking_hardlink(source_path, {"bundle_id": "frozen"})
            alias_path.hardlink_to(source_path)

            _write_json_breaking_hardlink(alias_path, {"bundle_id": "runtime"})

            self.assertEqual(json.loads(source_path.read_text())["bundle_id"], "frozen")
            self.assertEqual(json.loads(alias_path.read_text())["bundle_id"], "runtime")
            self.assertNotEqual(source_path.stat().st_ino, alias_path.stat().st_ino)

    def test_immutable_artifact_copy_is_a_hardlink_without_copy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "source.json"
            destination_path = Path(tmpdir) / "config" / "source.json"
            source_path.write_text('{"payload":"frozen"}\n', encoding="utf-8")

            _copy_artifact(source_path, destination_path)

            self.assertEqual(source_path.stat().st_ino, destination_path.stat().st_ino)
            self.assertEqual(source_path.stat().st_nlink, 2)

    def test_materialized_lookahead_attach_budget_expands_only_safe_scope(
        self,
    ) -> None:
        expansion = _materialized_lookahead_attach_budget_expansion(
            attach_prepare_report={
                "reason": "bpf_resident_attach_coverage_budget_exceeded",
                "future_map_write_allowed": False,
                "active_generation_flip_allowed": False,
                "resident_attach_coverage_budget": {
                    "schema_version": "skyseed-bpf-resident-attach-budget-v1",
                    "budget_source": "official_precompose_bpf_role_union_gate",
                    "max_attach_interfaces": 2,
                    "max_resident_attach_interfaces": 2,
                    "prepared_bpf_attach_capability_count": 4,
                },
                "prepare_steps": {
                    "preactivation_attach_prepare": {
                        "future_non_materialized_capacity_only_interfaces": [],
                        "attach_cardinality_budget": {
                            "attach_eligible_count": 3,
                            "attached_count": 3,
                            "max_attach_interfaces": 2,
                        },
                    }
                },
                "attach_plan": {
                    "netns:sat-a:if0": {},
                    "netns:sat-b:if0": {},
                    "netns:sat-c:if0": {},
                },
            },
            rolling_host_additive_prepare={
                "ok": True,
                "bridge_created_count": 0,
                "preconnected_endpoint_created_count": 0,
                "future_host_link_state_apply_allowed": False,
                "preapply_qdisc_parameter_updates_allowed": False,
            },
        )

        self.assertTrue(expansion["allowed"])
        self.assertEqual(expansion["observed_attach_scope_count"], 3)
        self.assertEqual(expansion["budget"]["max_attach_interfaces"], 4)
        self.assertIn(
            "materialized_lookahead_attach_scope",
            expansion["budget"]["budget_source"],
        )

    def test_bpf_owned_host_plan_splits_parameter_updates_by_owner(
        self,
    ) -> None:
        plan = NetworkMutationPlan(
            schema_version="network-mutation-plan-v1",
            mutation_id="plan-parameter-heavy",
            source_snapshot={"source_state_id": "state-0"},
            target_scope={"managed_slot_names": ["slot-a", "slot-b"]},
            execution_boundary={},
            operations=[
                {
                    "operation": "update_slot_parameters",
                    "slot_name": "slot-a",
                    "slot_type": "ut_access",
                    "source_runtime_parameters": {"delay_ms": 1.0},
                    "target_runtime_parameters": {"delay_ms": 5.0},
                },
                {
                    "operation": "update_slot_parameters",
                    "slot_name": "slot-loss",
                    "slot_type": "ut_access",
                    "source_runtime_parameters": {"loss_percent": 0.0},
                    "target_runtime_parameters": {"loss_percent": 1.0},
                    "changed_runtime_parameter_fields": ["loss_percent"],
                },
                {
                    "operation": "rebind_slot_attachment",
                    "slot_name": "slot-b",
                    "slot_type": "ut_access",
                    "target_binding": {"satellite_id": "sat-1"},
                },
            ],
            validation_expectations={},
        )

        filtered, summary = _bpf_owned_host_plan_without_parameter_updates(plan)

        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["skipped_parameter_update_count"], 1)
        self.assertEqual(summary["retained_qdisc_required_update_count"], 1)
        self.assertEqual(summary["skipped_qdisc_required_count"], 0)
        self.assertEqual(
            [op["operation"] for op in filtered.operations],
            ["update_slot_parameters", "rebind_slot_attachment"],
        )
        self.assertEqual(
            filtered.operations[0]["bpf_owned_host_qdisc_runtime_parameters"],
            {"delay_ms": 5.0},
        )
        self.assertTrue(
            filtered.execution_boundary["bpf_owned_host_parameter_updates_skipped"]
        )
        self.assertEqual(
            filtered.target_scope["managed_slot_names"],
            ["slot-a", "slot-b"],
        )
        self.assertTrue(filtered.target_scope["delta_first_target_scope"])

    def test_delta_scoped_network_mutation_plan_removes_full_managed_scope(self) -> None:
        plan = NetworkMutationPlan(
            schema_version="network-mutation-plan-v1",
            mutation_id="plan-full-scope",
            source_snapshot={"source_state_id": "state-0"},
            target_scope={"managed_slot_names": ["slot-a", "slot-b", "slot-c"]},
            execution_boundary={},
            operations=[
                {
                    "operation": "rebind_slot_attachment",
                    "slot_name": "slot-b",
                    "slot_type": "ut_access",
                }
            ],
            validation_expectations={},
        )

        scoped = _delta_scoped_network_mutation_plan(plan)

        self.assertEqual(scoped.target_scope["managed_slot_names"], ["slot-b"])
        self.assertEqual(scoped.target_scope["original_managed_slot_count"], 3)
        self.assertEqual(scoped.target_scope["delta_managed_slot_count"], 1)
        self.assertFalse(
            scoped.execution_boundary["hot_path_full_scope_compare_allowed"]
        )

    def test_bpf_owned_parameter_skip_advances_link_state_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            link_state_path = Path(tmpdir) / "link_state.json"
            link_state_path.write_text(
                json.dumps(
                    {
                        "runtime_initialized": True,
                        "slots": [
                            {
                                "slot_name": "slot-a",
                                "occupied": True,
                                "binding": {"satellite_id": "sat-1"},
                                "runtime_parameters": {"delay_ms": 1.0},
                                "dynamic_endpoints": [{"interface_name": "if0"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = _advance_link_state_for_bpf_owned_parameter_skip(
                link_state_path=link_state_path,
                skipped_operations=[
                    {
                        "operation": "update_slot_parameters",
                        "slot_name": "slot-a",
                        "target_runtime_parameters": {"delay_ms": 5.0},
                    }
                ],
            )

            updated = json.loads(link_state_path.read_text(encoding="utf-8"))
            slot = updated["slots"][0]
            self.assertTrue(summary["enabled"])
            self.assertEqual(summary["advanced_slot_count"], 1)
            self.assertEqual(slot["runtime_parameters"], {"delay_ms": 5.0})
            self.assertEqual(slot["binding"], {"satellite_id": "sat-1"})
            self.assertEqual(slot["dynamic_endpoints"], [{"interface_name": "if0"}])
            self.assertTrue(updated["bpf_owned_parameter_metadata_advanced"])

    def test_operational_clean_host_route_blocks_full_sweep_reconcile(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "OPERATIONAL_CLEAN_HOST_ROUTE_FALLBACK_BLOCKER",
        ):
            _assert_operational_clean_host_apply_route(
                segment_key="H1:starlink-shell-slice-0001",
                host_apply={
                    "outcome": {
                        "preparation_summary": {
                            "reconcile_summary": {
                                "full_sweep": True,
                                "scope": "full_sweep",
                                "reconciled_slot_count": 200,
                            }
                        }
                    }
                },
            )

    def test_operational_clean_host_route_allows_dirty_scope(self) -> None:
        _assert_operational_clean_host_apply_route(
            segment_key="H1:starlink-shell-slice-0001",
            host_apply={
                "outcome": {
                    "preparation_summary": {
                        "reconcile_summary": {
                            "full_sweep": False,
                            "scope": "trusted_cached_state_skip",
                            "reconciled_slot_count": 2,
                        }
                    },
                    "normal_path_scope_contract": {
                        "hidden_repair_blocked": False,
                    },
                }
            },
        )

    def test_rolling_horizon_deferred_generation_record_is_compact_and_not_written(
        self,
    ) -> None:
        record = _rolling_horizon_deferred_bpf_generation_record(
            generation_item={
                "segment_epoch_key": "H1:slice-0001",
                "generation_id": 32,
                "logical_generation_id": 0,
                "physical_generation_id": 32,
                "generation_slot_width": 25,
                "horizon_sequence_index": 1,
                "intent_hash": "intent-hash",
            },
            entry={
                "epoch_id": "epoch-1",
                "slice_id": "slice-0001",
                "segment_id": "H1",
            },
            state_contract={
                "source_state_id": "live_state:snap-0:epoch-0",
                "target_state_id": "live_state:snap-1:epoch-1",
                "host_plan_mutation_id": "netmut-1",
            },
            bpf_generation_slot_width=25,
            retain_generation_ids=[0, 1, 32],
        )

        self.assertTrue(record["preload_deferred"])
        self.assertFalse(record["inactive_generation_written"])
        self.assertTrue(record["active_generation_flip_skipped"])
        self.assertTrue(record["runtime_apply_skipped"])
        self.assertEqual(record["delta_attach_count"], 0)
        self.assertEqual(record["runtime_full_generation_reload_count"], 0)
        self.assertNotIn("compiled_runtime_inputs", record)
        self.assertNotIn("preload_report", record)
        self.assertEqual(record["compiled_runtime_inputs_summary"]["kind"], "deferred")

    def test_compact_preloaded_generation_record_preserves_parallel_preload_summary(
        self,
    ) -> None:
        compact = _compact_preloaded_generation_record(
            {
                "record_id": "record-1",
                "generation_id": 32,
                "inactive_generation_written": True,
                "compiled_runtime_inputs": {"large": ["payload"] * 4},
                "preload_report": {
                    "ok": True,
                    "inactive_generation_written": True,
                    "runtime_apply_skipped": True,
                    "interface_role_map_updates_skipped": True,
                    "interface_role_map_skipped_update_count": 52,
                    "interface_role_map_update_skip_reason": (
                        "roles_prepared_by_attach_capability"
                    ),
                    "parallel_map_write_enabled": True,
                    "parallel_map_write_worker_count": 8,
                    "parallel_map_write_scheduled_update_count": 180,
                    "prepare_steps": {
                        "real_backend_prepare": {
                            "ok": True,
                            "parallel_map_write_enabled": True,
                            "parallel_map_write_worker_count": 8,
                            "parallel_map_write_scheduled_update_count": 180,
                        }
                    },
                },
            }
        )

        self.assertNotIn("compiled_runtime_inputs", compact)
        self.assertNotIn("preload_report", compact)
        self.assertEqual(compact["compiled_runtime_inputs_summary"]["kind"], "mapping")
        summary = compact["preload_report_summary"]
        self.assertTrue(summary["parallel_map_write_enabled"])
        self.assertEqual(summary["parallel_map_write_worker_count"], 8)
        self.assertEqual(summary["parallel_map_write_scheduled_update_count"], 180)
        self.assertTrue(summary["interface_role_map_updates_skipped"])
        nested = summary["real_backend_prepare_summary"]
        self.assertTrue(nested["parallel_map_write_enabled"])

    def test_bpf_preload_base_resolver_rejects_compact_summary(self) -> None:
        record = _bpf_active_base_generation_record_for_preload(
            {
                "active_generation": 42,
                "active_physical_horizon_generation_key": "H0:generation-42",
                "compiled_runtime_inputs_summary": {
                    "kind": "mapping",
                    "present": True,
                },
                "prepare_steps": {
                    "real_backend_prepare": {
                        "compiled_runtime_inputs_summary": {
                            "kind": "mapping",
                            "present": True,
                        }
                    }
                },
            }
        )

        self.assertFalse(record["ok"])
        self.assertEqual(record["reason"], "base_compiled_runtime_inputs_missing")
        self.assertEqual(record["compiled_runtime_inputs"], {})
        self.assertTrue(
            all(
                checked["manifest_ready"] is False
                for checked in record["checked_sources"]
            )
        )

    def test_bpf_preload_base_resolver_builds_process_work_item(
        self,
    ) -> None:
        compiled_inputs = {
            "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
            "active_generation": {"value": 42},
            "ingress_classifier_map": [],
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
                            "zone_edge_delivery_map": [],
            "bpf_generation_overlay_manifest": {
                "manifest_only_loader_required": True,
                "batch_map_write_required": True,
            },
            "bpf_map_write_manifest_only": True,
            "batch_map_write_required": True,
        }
        base_record = _bpf_active_base_generation_record_for_preload(
            {
                "active_generation": 42,
                "active_physical_horizon_generation_key": "H0:generation-42",
                "active_generation_compiled_runtime_inputs": compiled_inputs,
            }
        )
        generation_item = {
            "segment_epoch_key": "H1:slice-0001",
            "generation_id": 67,
            "logical_generation_id": 0,
            "physical_generation_id": 67,
            "intent_hash": "intent-67",
        }
        self.assertTrue(base_record["ok"])
        self.assertEqual(
            base_record["source"],
            "readiness.active_generation_compiled_runtime_inputs",
        )
        work_item = _rolling_horizon_process_preload_work_item(
            generation_item=generation_item,
            entry={"epoch_id": "epoch-1", "slice_id": "slice-0001", "segment_id": "H1"},
            state_contract={
                "source_state_id": "live_state:h0",
                "target_state_id": "live_state:h1",
            },
            bpf_generation_slot_width=25,
            retain_generation_ids=[42, 67],
            base_generation_record=base_record,
        )
        queued_record = _rolling_horizon_queued_bpf_generation_record(
            generation_item=generation_item,
            entry={"epoch_id": "epoch-1", "slice_id": "slice-0001", "segment_id": "H1"},
            state_contract={
                "source_state_id": "live_state:h0",
                "target_state_id": "live_state:h1",
            },
            bpf_generation_slot_width=25,
            retain_generation_ids=[42, 67],
        )

        self.assertEqual(work_item["base_generation_id"], 42)
        self.assertEqual(
            work_item["frozen_compile_owner"],
            "horizon_maintenance_process_worker_after_additive_prepare",
        )
        self.assertEqual(
            work_item["context_ref"]["segment_epoch_key"],
            "H1:slice-0001",
        )
        self.assertNotIn("base_generation_compiled_inputs", work_item)
        self.assertTrue(work_item["queued_record"]["preload_queued"])
        self.assertTrue(queued_record["preload_queued"])
        self.assertTrue(queued_record["queued_preload_audit"]["batch_map_write_required"])
        self.assertTrue(
            queued_record["queued_preload_audit"]["manifest_only_loader_required"]
        )

        compact_completed = _compact_preloaded_generation_record(
            {
                "record_id": "queuepregeneration-1",
                "generation_id": 67,
                "inactive_generation_written": True,
                "compiled_runtime_inputs": compiled_inputs,
                "manifest_only_loader": True,
                "manifest_only_map_write_used": True,
                "batch_map_write_required": True,
                "batch_map_write_used": True,
                "batch_map_write_update_count": 3,
                "actual_data_write_count": 3,
            }
        )
        self.assertTrue(compact_completed["manifest_only_loader"])
        self.assertTrue(compact_completed["manifest_only_map_write_used"])
        self.assertTrue(compact_completed["batch_map_write_used"])
        self.assertEqual(compact_completed["batch_map_write_update_count"], 3)

    def test_prepared_attach_capability_readiness_compacts_attach_proof(
        self,
    ) -> None:
        readiness = {
            "ok": True,
            "attach_plan": {
                "netns:r1:eth0": ["ingress"],
                "netns:r2:eth0": ["egress"],
            },
            "attached_interfaces": ["netns:r1:eth0", "netns:r2:eth0"],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:r1:eth0",
                    "attach_directions": ["ingress"],
                    "expected_roles": ["g_to_z_classifier"],
                    "ifindex": 101,
                    "mac_address": "02:00:00:00:00:01",
                },
                {
                    "interface_ref": "netns:r2:eth0",
                    "attach_directions": ["egress"],
                    "expected_roles": ["egress_handoff"],
                    "ifindex": 102,
                    "mac_address": "02:00:00:00:00:02",
                },
            ],
            "mpls_map_entry_counts": {"ingress_classifier_map": 3},
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
            "map_capacities": {"classifiers": 128},
            "preactivation_attach_prepare": {
                "ok": True,
                "runtime_apply_skipped": True,
                "active_generation_set": False,
                "map_ready": False,
                "runtime_full_generation_reload_count": 0,
                "full_attach_count": 0,
                "delta_attach_count": 0,
            },
            "preactivation_attach_prepare_records": [
                {"ok": True, "artifact_path": "/tmp/h1-attach.json"}
            ],
        }
        proof = _prepared_attach_capability_readiness_for_preload(readiness)
        self.assertEqual(
            proof["preactivation_attach_prepare_records"][0]["artifact_path"],
            "/tmp/h1-attach.json",
        )
        self.assertEqual(
            proof["attach_plan"],
            {
                "netns:r1:eth0": ["ingress"],
                "netns:r2:eth0": ["egress"],
            },
        )
        self.assertEqual(proof["prepared_bpf_attach_capability_count"], 2)

    def test_initial_full_attach_projects_frozen_capability_for_h0_preload(
        self,
    ) -> None:
        detail = {
            "interface_ref": "netns:r1:eth0",
            "attach_directions": ["ingress"],
            "expected_roles": ["g_to_z_classifier"],
            "ifindex": 101,
            "mac_address": "02:00:00:00:00:01",
        }
        readiness = {
            "ready": True,
            "ok": True,
            "attach_plan": {"netns:r1:eth0": ["ingress"]},
            "attached_interfaces": ["netns:r1:eth0"],
            "attached_interface_details": [detail],
            "prepare_steps": {
                "real_backend_prepare": {
                    "ok": True,
                    "attach_plan": {"netns:r1:eth0": ["ingress"]},
                    "attached_interfaces": ["netns:r1:eth0"],
                    "attached_interface_details": [detail],
                    "compiled_mpls_map_counts": {"ingress_classifier_map": 1},
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
                    "map_capacities": {"classifiers": 128},
                }
            },
        }

        proof = _prepared_attach_capability_readiness_for_preload(readiness)
        contract = _bpf_owned_prepared_attach_capability_contract(proof)

        self.assertTrue(contract["ok"])
        self.assertEqual(
            proof["preactivation_attach_prepare"]["source"],
            "initial_full_attach_result_projection",
        )
        self.assertTrue(
            proof["preactivation_attach_prepare"]["source_runtime_apply_occurred"]
        )
        self.assertTrue(
            proof["preactivation_attach_prepare"]["runtime_apply_skipped"]
        )
        self.assertFalse(
            proof["preactivation_attach_prepare"]["active_generation_set"]
        )
        self.assertEqual(proof["prepared_bpf_attach_capability_count"], 1)

    def test_process_preload_work_item_uses_immutable_context_reference(
        self,
    ) -> None:
        work_item = _rolling_horizon_process_preload_work_item(
            generation_item={
                "segment_epoch_key": "H1:slice-0001",
                "generation_id": 67,
                "logical_generation_id": 0,
                "physical_generation_id": 67,
                "intent_hash": "intent-67",
            },
            entry={"epoch_id": "epoch-1", "slice_id": "slice-0001"},
            state_contract={
                "source_state_id": "live_state:h0",
                "target_state_id": "live_state:h1",
            },
            bpf_generation_slot_width=25,
            retain_generation_ids=[42, 67],
            base_generation_record={
                "generation_id": 42,
                "physical_generation_id": 42,
                "compiled_runtime_inputs": {
                    "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
                    "active_generation": {"value": 42},
                    "ingress_classifier_map": [],
                            "ut_gateway_policy_map": [],
                            "gateway_selector_ingress_map": [],
                    "core_lfib_map": [],
                    "egress_handoff_map": [],
                            "zone_edge_delivery_map": [],
                },
            },
        )

        self.assertNotIn("frozen_compiled_runtime_inputs", work_item)
        self.assertEqual(
            work_item["frozen_compile_owner"],
            "horizon_maintenance_process_worker_after_additive_prepare",
        )
        self.assertNotIn("context", work_item)
        self.assertEqual(
            work_item["context_ref"]["segment_epoch_key"],
            "H1:slice-0001",
        )
        self.assertTrue(work_item["queued_record"]["preload_queued"])

    def test_process_preload_work_item_does_not_copy_typed_contract_context(self) -> None:
        snapshot = TopologySnapshot(
            schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
            snapshot_id="slice-typed-0001",
            time_slice={"slice_id": "slice-typed-0001", "state_model": "full_state"},
            provenance={"source": "typed-r127-replay"},
            nodes={},
            active_links=[],
            zones={},
            ut_zone_membership={},
        )
        plan = RuntimeUpdatePlan(
            schema_version=RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
            plan_id="plan-typed-0001",
            target_snapshot={"snapshot_id": snapshot.snapshot_id},
            planner={"planner_name": "typed-r127-replay"},
            apply_model={
                "state_model": "full_state_intent",
                "action_model": "runtime-action-v1",
            },
            node_subplans={},
            replacement_semantics={},
            validation_expectations={},
        )
        work_item = _rolling_horizon_process_preload_work_item(
            generation_item={
                "segment_epoch_key": "H1:slice-typed-0001",
                "generation_id": 67,
                "logical_generation_id": 0,
                "physical_generation_id": 67,
                "intent_hash": "intent-typed-67",
            },
            entry={"epoch_id": "epoch-typed-1", "slice_id": snapshot.snapshot_id},
            state_contract={},
            bpf_generation_slot_width=25,
            retain_generation_ids=[42, 67],
            base_generation_record={"generation_id": 42},
        )

        self.assertNotIn("context", work_item)
        self.assertEqual(
            work_item["context_ref"]["segment_epoch_key"],
            "H1:slice-typed-0001",
        )

    def test_process_preload_work_item_rejects_missing_context_reference(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "process_worker_preload_context_ref_missing",
        ):
            _rolling_horizon_process_preload_work_item(
                generation_item={
                    "segment_epoch_key": "",
                    "generation_id": 67,
                    "logical_generation_id": 0,
                    "physical_generation_id": 67,
                    "intent_hash": "intent-67",
                },
                entry={"epoch_id": "epoch-1", "slice_id": "slice-0001"},
                state_contract={},
                bpf_generation_slot_width=25,
                retain_generation_ids=[42, 67],
                base_generation_record={"generation_id": 42},
            )

    def test_process_preload_work_item_assigns_freeze_to_worker(self) -> None:
        work_item = _rolling_horizon_process_preload_work_item(
            generation_item={
                "segment_epoch_key": "H1:slice-0001",
                "generation_id": 67,
                "logical_generation_id": 0,
                "physical_generation_id": 67,
                "intent_hash": "intent-67",
            },
            entry={"epoch_id": "epoch-1", "slice_id": "slice-0001"},
            state_contract={
                "source_state_id": "live_state:h0",
                "target_state_id": "live_state:h1",
            },
            bpf_generation_slot_width=25,
            retain_generation_ids=[42, 67],
            base_generation_record={"generation_id": 42},
        )

        self.assertEqual(
            work_item["frozen_compile_owner"],
            "horizon_maintenance_process_worker_after_additive_prepare",
        )
        self.assertEqual(
            work_item["context_ref"]["segment_epoch_key"],
            "H1:slice-0001",
        )
        self.assertNotIn("frozen_compiled_runtime_inputs", work_item)
        self.assertTrue(work_item["queued_record"]["preload_queued"])

    def test_retired_cleanup_candidate_must_be_from_retired_horizon(self) -> None:
        current_horizon = _retired_bpf_generation_cleanup_candidate_horizon_status(
            candidate={
                "generation_id": 1,
                "horizon_id": "H0",
                "horizon_generation_key": "H0:generation-1",
            },
            readiness={
                "current_horizon_id": "H0",
                "active_generation": 2,
            },
        )
        retired_horizon = _retired_bpf_generation_cleanup_candidate_horizon_status(
            candidate={
                "generation_id": 24,
                "horizon_id": "H0",
                "horizon_generation_key": "H0:generation-24",
            },
            readiness={
                "current_horizon_id": "H1",
                "active_generation": 32,
            },
        )

        self.assertFalse(current_horizon["retired"])
        self.assertEqual(
            current_horizon["reason"],
            "candidate_horizon_is_current_active_horizon",
        )
        self.assertTrue(retired_horizon["retired"])

    def test_normal_runtime_inputs_carry_resident_attach_budget(self) -> None:
        plan = {
            "schema_version": "satellite-runtime-update-plan-v1alpha1",
            "plan_id": "plan-1",
            "target_snapshot": {"snapshot_id": "snap-1"},
            "planner": {"planner_name": "test"},
            "apply_model": {
                "state_model": "full_state_intent",
                "action_model": "node_local_installer_action_v1",
            },
            "node_subplans": {},
            "replacement_semantics": {},
            "validation_expectations": {},
        }
        normal_inputs = {
            "initial_segment": {
                "segment_id": "initial",
                "current_execution_queue": [],
            },
            "context_by_slice": {"slice-1": {"runtime_update_plan": dict(plan)}},
            "context_by_segment_key": {
                "initial:slice-1": {"runtime_update_plan": dict(plan)}
            },
            "context_by_slice_by_segment": {
                "initial": {"slice-1": {"runtime_update_plan": dict(plan)}}
            },
            "runtime_update_plan_by_segment_key": {
                "initial:slice-1": dict(plan),
            },
        }

        patched = _normal_runtime_inputs_with_bpf_resident_attach_budget(
            normal_runtime_inputs=normal_inputs,
            precompose_bpf_role_union_gate={
                "required_bpf_interface_ref_count": 266,
                "bootstrap_pre_runtime_full_attach_count": 266,
                "prepared_bpf_attach_capability": {
                    f"netns:sat-{index}:if0": ["ingress"]
                    for index in range(266)
                },
                "prepared_bpf_attach_capability_count": 266,
            },
        )

        budget = patched["resident_attach_coverage_budget"]
        self.assertEqual(
            budget["budget_source"],
            "official_precompose_bpf_role_union_gate",
        )
        self.assertEqual(budget["max_attach_interfaces"], 266)
        self.assertEqual(budget["slack_attach_interfaces"], 0)
        self.assertNotIn(
            "resident_attach_coverage_budget",
            patched["runtime_update_plan_by_segment_key"]["initial:slice-1"][
                "target_snapshot"
            ],
        )
        self.assertNotIn(
            "resident_attach_coverage_budget",
            patched["context_by_slice_by_segment"]["initial"]["slice-1"][
                "runtime_update_plan"
            ]["target_snapshot"],
        )
        self.assertIn("resident_attach_coverage_budget_hash", patched["hashes"])

        thawed = _thaw_normal_runtime_initial_segment(
            {
                "bundle_id": "bundle-budget-test",
                "executable_runtime_inputs": {
                    "normal_runtime_inputs": patched,
                    "normal_runtime_thaw_adapter": {
                        "adapter": "canonical_normal_runtime_inputs_v1",
                    },
                },
            }
        )
        self.assertEqual(
            thawed["context_by_slice"]["slice-1"]["runtime_update_plan"].target_snapshot[
                "resident_attach_coverage_budget"
            ],
            budget,
        )

    def test_normal_runtime_inputs_accept_explicit_rolling_attach_budget(self) -> None:
        rolling_budget = {
            "schema_version": "skyseed-bpf-resident-attach-budget-v1",
            "budget_source": "official_precompose+prepared_horizon_offline_role_union",
            "max_attach_interfaces": 512,
            "max_resident_attach_interfaces": 512,
            "slack_attach_interfaces": 0,
        }

        patched = _normal_runtime_inputs_with_bpf_resident_attach_budget(
            normal_runtime_inputs={
                "initial_segment": {
                    "segment_id": "initial",
                    "current_execution_queue": [],
                },
                "hashes": {},
            },
            precompose_bpf_role_union_gate={
                "required_bpf_interface_ref_count": 12,
                "prepared_bpf_attach_capability": {
                    "netns:sat-a:if0": ["ingress"]
                },
            },
            resident_attach_budget=rolling_budget,
        )

        self.assertEqual(
            patched["resident_attach_coverage_budget"]["max_attach_interfaces"],
            512,
        )
        self.assertIn("resident_attach_coverage_budget_hash", patched["hashes"])

    def test_resource_scope_delta_only_includes_unprepared_required_resources(self) -> None:
        required_scope = {
            "slot-a": ["netns:a:if0", "netns:b:if0"],
            "slot-b": ["netns:c:if0"],
            "slot-c": ["netns:d:if0"],
        }
        prepared_scope = {
            "slot-a": ["netns:a:if0"],
            "slot-b": ["netns:c:if0"],
            "slot-extra": ["netns:z:if0"],
        }

        delta = _resource_scope_delta_by_slot(
            required_resource_scope_by_slot=required_scope,
            prepared_resource_scope_by_slot=prepared_scope,
        )

        self.assertEqual(
            delta,
            {
                "slot-a": ["netns:b:if0"],
                "slot-c": ["netns:d:if0"],
            },
        )

    def test_lifecycle_queue_keeps_forwarding_boundary_as_lookahead_preload(self) -> None:
        temporal_plan = {
            "orbit_samples": [
                {
                    "sample_id": f"sample-{index}",
                    "sample_index": index,
                    "slice_id": f"slice-{index}",
                    "relative_time_seconds": float((index - 1) * 10),
                    "absolute_time_utc": f"2026-06-21T00:00:{(index - 1) * 10:02d}Z",
                }
                for index in range(1, 5)
            ],
            "runtime_epochs": [
                {
                    "epoch_id": f"runtime-{index}",
                    "sample_id": f"sample-{index}",
                    "slice_id": f"slice-{index}",
                    "activation_relative_time_seconds": float((index - 1) * 10),
                    "activation_time_utc": f"2026-06-21T00:00:{(index - 1) * 10:02d}Z",
                    "activation_group_id": f"activation-{index}",
                    "trigger_reasons": ["structural_link_change"],
                }
                for index in range(1, 4)
            ],
            "parameter_epochs": [
                {
                    "epoch_id": "parameter-1",
                    "sample_id": "sample-4",
                    "slice_id": "slice-4",
                    "activation_relative_time_seconds": 30.0,
                    "activation_time_utc": "2026-06-21T00:00:30Z",
                    "activation_group_id": "activation-4",
                    "trigger_reasons": ["bandwidth_threshold_exceeded"],
                }
            ],
        }

        _, selected_queue, _, _ = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=2,
            max_parameter_epochs=1,
        )
        _, candidate_queue, _, _ = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )

        lifecycle_queue = _append_forwarding_relevant_lookahead_boundary(
            selected_epoch_queue=selected_queue,
            candidate_epoch_queue=candidate_queue,
            max_runtime_epochs=2,
            max_parameter_epochs=1,
        )
        contract = _horizon_lifecycle_queue_contract(
            selected_epoch_queue=lifecycle_queue,
            max_runtime_epochs=2,
            max_parameter_epochs=1,
        )

        self.assertEqual(len(selected_queue), 3)
        self.assertEqual(contract["selected_event_count"], 4)
        self.assertEqual(contract["current_execution_event_count"], 3)
        self.assertEqual(contract["current_runtime_applicable_event_count"], 3)
        self.assertEqual(contract["lookahead_preload_event_count"], 1)
        self.assertEqual(contract["preload_only_boundary_event_count"], 1)
        self.assertEqual(contract["preload_required_event_count"], 1)
        self.assertEqual(contract["duplicate_event_count"], 0)
        self.assertFalse(contract["current_execution_queue_contains_preload_only"])
        self.assertTrue(contract["selected_current_lookahead_accounting_consistent"])
        self.assertEqual(
            contract["lookahead_preload_queue"][0]["epoch_id"],
            "runtime-3",
        )
        self.assertEqual(
            contract["lookahead_preload_queue"][0]["lookahead_selection_policy"],
            "first_forwarding_relevant_event_after_current_window_by_event_queue_order",
        )
        self.assertTrue(contract["lookahead_preload_queue"][0]["preload_only"])
        self.assertFalse(
            contract["lookahead_preload_queue"][0]["runtime_apply_allowed"]
        )
        self.assertEqual(
            contract["lookahead_preload_queue"][0]["activation_contract"],
            "preload_only_write_inactive_generation_no_flip",
        )
        gate = _precompose_horizon_queue_gate(
            requested_lifecycle_aware_1p1=True,
            candidate_epoch_queue=candidate_queue,
            horizon_lifecycle_queue_contract=contract,
            prepared_update_bundle={
                "selected_epoch_queue": lifecycle_queue,
                "current_execution_queue": contract["current_execution_queue"],
                "lookahead_preload_queue": contract["lookahead_preload_queue"],
            },
        )
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["boundary_event_id"], "runtime-3")
        self.assertEqual(gate["boundary_event_role"], "lookahead_preload_only")
        self.assertFalse(gate["boundary_event_runtime_apply_allowed"])
        self.assertTrue(gate["boundary_event_preload_required"])
        self.assertEqual(
            gate["prepared_update_bundle_counts"],
            {"selected": 4, "current": 3, "lookahead": 1},
        )

    def test_lifecycle_queue_selects_exactly_one_lookahead_boundary(self) -> None:
        selected_epoch_queue = [
            {
                "epoch_id": "runtime-1",
                "slice_id": "slice-1",
                "classification": "runtime_epoch",
                "trigger_reasons": ["initial_runtime_epoch"],
            },
            {
                "epoch_id": "runtime-2",
                "slice_id": "slice-2",
                "classification": "runtime_epoch",
                "trigger_reasons": ["structural_link_change"],
            },
            {
                "epoch_id": "runtime-3",
                "slice_id": "slice-3",
                "classification": "runtime_epoch",
                "trigger_reasons": ["structural_link_change"],
            },
            {
                "epoch_id": "runtime-4",
                "slice_id": "slice-4",
                "classification": "runtime_epoch",
                "trigger_reasons": ["structural_link_change"],
            },
        ]

        contract = _horizon_lifecycle_queue_contract(
            selected_epoch_queue=selected_epoch_queue,
            max_runtime_epochs=2,
            max_parameter_epochs=0,
        )

        self.assertEqual(contract["current_execution_event_count"], 2)
        self.assertEqual(contract["lookahead_preload_event_count"], 1)
        self.assertEqual(contract["preload_required_event_count"], 1)
        self.assertEqual(contract["not_selected_event_count"], 1)
        self.assertEqual(
            contract["lookahead_preload_queue_epoch_ids"],
            ["runtime-3"],
        )
        self.assertEqual(
            contract["event_reports"][3]["horizon_execution_role"],
            "not_selected_for_current_execution",
        )

    def test_lifecycle_queue_exposes_rolling_sliding_window_contract(self) -> None:
        selected_epoch_queue = [
            {
                "epoch_id": "runtime-1",
                "slice_id": "slice-1",
                "classification": "runtime_epoch",
                "segment_id": "segment-0",
                "segment_epoch_key": "segment-0:slice-1",
                "trigger_reasons": ["initial_runtime_epoch"],
            },
            {
                "epoch_id": "runtime-2",
                "slice_id": "slice-2",
                "classification": "runtime_epoch",
                "segment_id": "segment-0",
                "segment_epoch_key": "segment-0:slice-2",
                "trigger_reasons": ["structural_link_change"],
            },
            {
                "epoch_id": "runtime-3",
                "slice_id": "slice-3",
                "classification": "runtime_epoch",
                "segment_id": "segment-1",
                "segment_epoch_key": "segment-1:slice-3",
                "trigger_reasons": ["structural_link_change"],
            },
            {
                "epoch_id": "runtime-4",
                "slice_id": "slice-4",
                "classification": "runtime_epoch",
                "segment_id": "segment-2",
                "segment_epoch_key": "segment-2:slice-4",
                "trigger_reasons": ["structural_link_change"],
            },
        ]

        contract = _horizon_lifecycle_queue_contract(
            selected_epoch_queue=selected_epoch_queue,
            max_runtime_epochs=2,
            max_parameter_epochs=0,
        )
        sliding = contract["sliding_window_contract"]

        self.assertEqual(
            sliding["schema_version"],
            "skyseed-sliding-horizon-window-contract-v1",
        )
        self.assertEqual(sliding["window_model"], "rolling_horizon_window")
        self.assertFalse(sliding["test_horizon_count_hardcoded"])
        self.assertEqual(sliding["startup_prepare_policy"], "current_window_only")
        self.assertEqual(sliding["windows"]["current"]["horizon_id"], "segment-0")
        self.assertEqual(sliding["windows"]["next"]["horizon_id"], "segment-1")
        self.assertEqual(
            sliding["windows"]["next"]["status"],
            "boundary_preload_pending",
        )
        self.assertIn(
            "cleanup_previous_horizon_after_boundary",
            sliding["maintenance_allowed_operations"],
        )
        self.assertIn(
            "preapply_qdisc_parameter_updates",
            sliding["maintenance_forbidden_operations"],
        )
        self.assertIn(
            "runtime_recompute_network_mutation_plan",
            sliding["runtime_forbidden_operations"],
        )

    def test_replenished_host_plan_preparation_chains_from_remaining_window(
        self,
    ) -> None:
        def snapshot(snapshot_id: str, slice_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                snapshot_id=snapshot_id,
                time_slice={"slice_id": slice_id},
            )

        def plan(
            mutation_id: str,
            source_state_id: str,
            slot_name: str,
        ) -> NetworkMutationPlan:
            return NetworkMutationPlan(
                schema_version="test-plan-v1",
                mutation_id=mutation_id,
                source_snapshot={"source_state_id": source_state_id},
                target_scope={},
                execution_boundary={},
                operations=[
                    {
                        "operation": "activate_slot_attachment",
                        "slot_name": slot_name,
                        "slot_type": "same_plane_isl",
                        "target_occupied": True,
                        "target_binding": {"link": slot_name},
                        "target_endpoints": [],
                        "target_runtime_parameters": {"delay_ms": 1.0},
                    }
                ],
                validation_expectations={},
            )

        remaining_entry = {
            "segment_epoch_key": "initial:slice-002",
            "segment_id": "initial",
            "horizon_id": "initial",
            "slice_id": "slice-002",
            "epoch_id": "epoch-h0-002",
        }
        accepted_entry = {
            "segment_epoch_key": "replenished-001:slice-001",
            "segment_id": "replenished-001",
            "horizon_id": "replenished-001",
            "slice_id": "slice-001",
            "epoch_id": "epoch-h1-001",
            "classification": "runtime_epoch",
        }
        current_state_id = "live_state:snap-h0-001:epoch-h0-001"
        existing_plan = plan("mut-h0-002", current_state_id, "slot-h0-002")

        prepared = _prepare_replenished_host_plan_payloads(
            remaining_queue=[remaining_entry],
            accepted_entries=[accepted_entry],
            context_by_key={
                "initial:slice-002": {
                    "snapshot": snapshot("snap-h0-002", "slice-002")
                }
            },
            replenished_context_by_key={
                "replenished-001:slice-001": {
                    "snapshot": snapshot("snap-h1-001", "slice-001")
                }
            },
            current_dynamic_state={},
            current_state_id=current_state_id,
            resolve_existing_host_plan=(
                lambda _entry, _context, _state, _state_id: existing_plan
            ),
            build_host_plan=lambda _entry, _context, _state, state_id: plan(
                "mut-h1-001",
                state_id,
                "slot-h1-001",
            ),
        )

        self.assertEqual(prepared["prepared_host_plan_count"], 1)
        h1_plan = prepared["prepared_host_plan_by_segment_key"][
            "replenished-001:slice-001"
        ]
        self.assertEqual(
            h1_plan["source_snapshot"]["source_state_id"],
            "live_state:snap-h0-002:epoch-h0-002",
        )
        self.assertEqual(
            prepared["source_state_chain"][-1]["source_state_id"],
            "live_state:snap-h0-002:epoch-h0-002",
        )

    def test_replenished_host_plan_preparation_fails_without_remaining_context(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "rolling_horizon_existing_queue_missing_context:initial:slice-002",
        ):
            _prepare_replenished_host_plan_payloads(
                remaining_queue=[
                    {
                        "segment_epoch_key": "initial:slice-002",
                        "slice_id": "slice-002",
                        "epoch_id": "epoch-h0-002",
                    }
                ],
                accepted_entries=[],
                context_by_key={},
                replenished_context_by_key={},
                current_dynamic_state={},
                current_state_id="live_state:snap-h0-001:epoch-h0-001",
                resolve_existing_host_plan=lambda *_args: None,  # type: ignore[arg-type,return-value]
                build_host_plan=lambda *_args: None,  # type: ignore[arg-type,return-value]
            )

    def test_frozen_replenished_host_plan_selection_does_not_replay_state(self) -> None:
        segment_key = "replenished-001:slice-001"
        plan = NetworkMutationPlan(
            schema_version="test-plan-v1",
            mutation_id="mut-h1-001",
            source_snapshot={"source_state_id": "prepared-source"},
            target_scope={},
            execution_boundary={},
            operations=[],
            validation_expectations={},
        )

        selected = _select_prepared_replenished_host_plan_payloads(
            accepted_entries=[{"segment_epoch_key": segment_key}],
            prepared_host_plan_by_segment_key={segment_key: plan.to_dict()},
            prepared_source_state_chain=[
                {
                    "segment_epoch_key": segment_key,
                    "source_state_id": "prepared-source",
                    "target_state_id": "prepared-target",
                    "host_plan_mutation_id": "mut-h1-001",
                }
            ],
            current_state_id="live-current-state",
        )

        self.assertTrue(selected["selection_only"])
        self.assertFalse(selected["live_future_state_chain_replay"])
        self.assertEqual(selected["prepared_host_plan_count"], 1)
        self.assertEqual(
            selected["source_state_chain"][0]["selection_source"],
            "frozen_reference",
        )

    def test_frozen_replenished_host_plan_selection_fails_on_identity_mismatch(
        self,
    ) -> None:
        segment_key = "replenished-001:slice-001"
        plan = NetworkMutationPlan(
            schema_version="test-plan-v1",
            mutation_id="mut-h1-001",
            source_snapshot={"source_state_id": "prepared-source"},
            target_scope={},
            execution_boundary={},
            operations=[],
            validation_expectations={},
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "frozen_bundle_prepared_host_plan_reference_mismatch",
        ):
            _select_prepared_replenished_host_plan_payloads(
                accepted_entries=[{"segment_epoch_key": segment_key}],
                prepared_host_plan_by_segment_key={segment_key: plan.to_dict()},
                prepared_source_state_chain=[
                    {
                        "segment_epoch_key": segment_key,
                        "host_plan_mutation_id": "wrong-mutation",
                    }
                ],
                current_state_id="live-current-state",
            )

    def test_replenishment_preview_uses_replenished_artifact_source_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            segment_topology = tmp_path / "segment-topology.json"
            segment_link_state = tmp_path / "segment-link-state.json"
            segment_topology.write_text("{}", encoding="utf-8")
            segment_link_state.write_text("{}", encoding="utf-8")
            args = SimpleNamespace(sample_interval=10)

            with (
                patch(
                    "apps.run_starlink_shell_experiment._preview_command",
                    return_value=["preview"],
                ),
                patch(
                    "apps.run_starlink_shell_experiment.subprocess.run",
                    return_value=CompletedProcess(
                        args=["preview"],
                        returncode=0,
                        stdout="{}",
                        stderr="",
                    ),
                ),
                patch(
                    "apps.run_starlink_shell_experiment._load_temporal_artifacts",
                    return_value={
                        "artifacts": {
                            "topology_json": str(segment_topology),
                            "link_state_json": str(segment_link_state),
                        },
                        "temporal_plan": {},
                        "planner_preview": {},
                        "snapshot_preview": {},
                        "shell_manifest": {},
                    },
                ),
                patch(
                    "apps.run_starlink_shell_experiment._preview_summary_to_segment",
                    return_value={
                        "segment_id": "replenished-001",
                        "summary": {},
                    },
                ) as preview_to_segment,
            ):
                _run_replenishment_preview(
                    args=args,
                    run_dir=run_dir,
                    segment_index=1,
                    start_time_utc="2026-06-21T00:00:00Z",
                    horizon_seconds=120.0,
                    topology_path=tmp_path / "initial-topology.json",
                    link_state_path=tmp_path / "initial-link-state.json",
                    run_start_time_utc="2026-06-21T00:00:00Z",
                )

            kwargs = preview_to_segment.call_args.kwargs
            self.assertEqual(kwargs["topology_path"], segment_topology.resolve())
            self.assertEqual(kwargs["link_state_path"], segment_link_state.resolve())

    def test_preview_segment_contexts_record_their_source_artifact_paths(self) -> None:
        topology_path = Path("/tmp/segment-topology.json")
        link_state_path = Path("/tmp/segment-link-state.json")
        snapshot = SimpleNamespace(time_slice={"slice_id": "slice-001"})
        queue = [
            {
                "slice_id": "slice-001",
                "epoch_id": "runtime-001",
                "classification": "runtime_epoch",
                "activation_time_utc": "2026-06-21T00:00:00Z",
            }
        ]

        with (
            patch(
                "apps.run_starlink_shell_experiment._temporal_execution_queue",
                return_value=([], list(queue), ["slice-001"], []),
            ),
            patch(
                "apps.run_starlink_shell_experiment._annotate_epoch_queue_runtime_update_counts",
                side_effect=lambda selected_epoch_queue, planner_preview: list(
                    selected_epoch_queue
                ),
            ),
            patch(
                "apps.run_starlink_shell_experiment._append_forwarding_relevant_lookahead_boundary",
                side_effect=lambda selected_epoch_queue, **_kwargs: list(
                    selected_epoch_queue
                ),
            ),
            patch("apps.run_starlink_shell_experiment._load_snapshots", return_value=[]),
            patch(
                "apps.run_starlink_shell_experiment._filter_snapshots_by_slice_ids",
                return_value=[snapshot],
            ),
            patch(
                "apps.run_starlink_shell_experiment._build_slice_contexts",
                return_value=[
                    {
                        "snapshot": snapshot,
                        "runtime_update_plan": SimpleNamespace(),
                    }
                ],
            ),
            patch(
                "apps.run_starlink_shell_experiment._read_json",
                return_value={"slot_inventory": {}},
            ),
            patch(
                "apps.run_starlink_shell_experiment._selected_access_prewarm_resource_scope_by_slot",
                return_value={"ut2001-access-slot0": ["ut-access-resource"]},
            ),
            patch(
                "apps.run_starlink_shell_experiment._static_inventory_materialization_resource_scope_by_slot",
                return_value={"cross-slot0": ["cross-resource"]},
            ),
            patch(
                "apps.run_starlink_shell_experiment._run_window_resource_coverage_plan",
                return_value={
                    "schema_version": "skyseed-run-window-resource-coverage-plan-v1",
                    "unresolved_count": 0,
                    "prepared_resource_scope_by_slot": {
                        "ut2001-access-slot0": ["ut-access-resource"],
                        "cross-slot0": ["cross-resource"],
                    },
                },
            ),
        ):
            segment = _preview_summary_to_segment(
                segment_id="replenished-001",
                preview_summary={"temporal_plan": {"horizon_seconds": 120.0}},
                temporal_artifacts={
                    "temporal_plan": {
                        "orbit_samples": [
                            {
                                "absolute_time_utc": "2026-06-21T00:00:00Z",
                                "slice_id": "slice-001",
                            }
                        ],
                    },
                    "artifacts": {
                        "snapshot_preview_json": "/tmp/snapshot-preview.json",
                    },
                    "planner_preview": {},
                },
                topology_path=topology_path,
                link_state_path=link_state_path,
                run_start_time_utc="2026-06-21T00:00:00Z",
                max_epochs=None,
                max_runtime_epochs=None,
                max_parameter_epochs=None,
            )

        context = segment["context_by_slice"]["slice-001"]
        self.assertEqual(context["topology_path"], str(topology_path))
        self.assertEqual(context["link_state_path"], str(link_state_path))
        self.assertEqual(segment["topology_path"], str(topology_path))
        self.assertEqual(segment["link_state_path"], str(link_state_path))
        self.assertEqual(
            segment["prepared_resource_scope_by_slot"]["ut2001-access-slot0"],
            ["ut-access-resource"],
        )
        self.assertEqual(
            segment["run_window_resource_coverage_plan"]["unresolved_count"],
            0,
        )

    def test_precompose_horizon_gate_blocks_exactly_one_lookahead_violation(
        self,
    ) -> None:
        current_queue = [
            {
                "epoch_id": "runtime-1",
                "horizon_execution_role": "current_execution",
                "runtime_apply_allowed": True,
            }
        ]
        lookahead_queue = [
            {
                "epoch_id": "runtime-2",
                "horizon_execution_role": "lookahead_preload_only",
                "runtime_apply_allowed": False,
                "preload_required": True,
                "preload_only": True,
                "classification": "runtime_epoch",
                "trigger_reasons": ["structural_link_change"],
                "lookahead_selection_policy": (
                    "first_forwarding_relevant_event_after_current_window_by_event_queue_order"
                ),
            },
            {
                "epoch_id": "runtime-3",
                "horizon_execution_role": "lookahead_preload_only",
                "runtime_apply_allowed": False,
                "preload_required": True,
                "preload_only": True,
                "classification": "runtime_epoch",
                "trigger_reasons": ["structural_link_change"],
                "lookahead_selection_policy": (
                    "first_forwarding_relevant_event_after_current_window_by_event_queue_order"
                ),
            },
        ]
        contract = {
            "selected_event_count": 3,
            "current_execution_event_count": 1,
            "current_runtime_applicable_event_count": 1,
            "lookahead_preload_event_count": 2,
            "preload_required_event_count": 2,
            "preload_only_boundary_event_count": 2,
            "not_selected_event_count": 0,
            "duplicate_event_keys": [],
            "current_execution_queue": current_queue,
            "lookahead_preload_queue": lookahead_queue,
            "event_reports": [
                {"epoch_id": "runtime-1"},
                {"epoch_id": "runtime-2"},
                {"epoch_id": "runtime-3"},
            ],
        }

        gate = _precompose_horizon_queue_gate(
            requested_lifecycle_aware_1p1=True,
            candidate_epoch_queue=[*current_queue, *lookahead_queue],
            horizon_lifecycle_queue_contract=contract,
            prepared_update_bundle={
                "selected_epoch_queue": [*current_queue, *lookahead_queue],
                "current_execution_queue": current_queue,
                "lookahead_preload_queue": lookahead_queue,
            },
        )

        self.assertFalse(gate["passed"])
        self.assertFalse(gate["compose_up_allowed"])
        blockers = {blocker["blocker"] for blocker in gate["blockers"]}
        self.assertIn("exactly_one_lookahead_preload_event_required", blockers)
        self.assertIn("exactly_one_preload_required_event_required", blockers)

    def test_precompose_horizon_gate_accepts_full_current_horizon_without_boundary(
        self,
    ) -> None:
        current_queue = [
            {
                "epoch_id": "runtime-1",
                "horizon_execution_role": "current_execution",
                "runtime_apply_allowed": True,
                "classification": "runtime_epoch",
                "trigger_reasons": ["initial_runtime_epoch"],
            },
            {
                "epoch_id": "parameter-1",
                "horizon_execution_role": "current_execution",
                "runtime_apply_allowed": True,
                "classification": "parameter_epoch",
                "trigger_reasons": ["delay_threshold_exceeded"],
            },
        ]
        contract = {
            "selected_event_count": 2,
            "current_execution_event_count": 2,
            "current_runtime_applicable_event_count": 2,
            "lookahead_preload_event_count": 0,
            "preload_required_event_count": 0,
            "preload_only_boundary_event_count": 0,
            "not_selected_event_count": 0,
            "duplicate_event_keys": [],
            "current_execution_queue": current_queue,
            "lookahead_preload_queue": [],
            "event_reports": [
                {"epoch_id": "runtime-1"},
                {"epoch_id": "parameter-1"},
            ],
        }

        gate = _precompose_horizon_queue_gate(
            requested_lifecycle_aware_1p1=True,
            candidate_epoch_queue=current_queue,
            horizon_lifecycle_queue_contract=contract,
            prepared_update_bundle={
                "selected_epoch_queue": current_queue,
                "current_execution_queue": current_queue,
                "lookahead_preload_queue": [],
            },
        )

        self.assertTrue(gate["passed"])
        self.assertTrue(gate["compose_up_allowed"])
        self.assertEqual(gate["queue_gate_mode"], "full_current_horizon")
        self.assertFalse(gate["one_plus_one_boundary_required"])

    def test_precompose_bpf_role_union_gate_covers_lookahead_roles(self) -> None:
        gate = _precompose_bpf_role_union_gate_from_reports(
            current_event_role_reports=[
                {
                    "epoch_id": "runtime-1",
                    "slice_id": "slice-1",
                    "generation_id": 0,
                    "attach_plan": {"netns:sat-a:if0": ["ingress"]},
                    "role_by_interface": {
                        "netns:sat-a:if0": ["g_to_z_classifier"],
                    },
                    "interface_details": {
                        "netns:sat-a:if0": {
                            "interface_ref": "netns:sat-a:if0",
                            "interface_name": "netns:sat-a:if0",
                            "ifindex": 100,
                            "netns": "sat-a",
                        }
                    },
                }
            ],
            lookahead_event_role_reports=[
                {
                    "epoch_id": "runtime-2",
                    "slice_id": "slice-2",
                    "generation_id": 1,
                    "attach_plan": {"netns:sat-a:if0": ["ingress"]},
                    "role_by_interface": {
                        "netns:sat-a:if0": [
                            "g_to_z_classifier",
                            "z_to_g_core_lfib",
                        ],
                    },
                    "interface_details": {
                        "netns:sat-a:if0": {
                            "interface_ref": "netns:sat-a:if0",
                            "interface_name": "netns:sat-a:if0",
                            "ifindex": 100,
                            "netns": "sat-a",
                        }
                    },
                }
            ],
        )

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["required_interface_role_union_count"], 2)
        self.assertEqual(gate["prepared_bpf_attach_capability_count"], 2)
        self.assertEqual(gate["interface_role_mismatch_count"], 0)
        self.assertEqual(gate["attach_reuse_eligible_count"], 1)
        self.assertEqual(gate["attach_reuse_ineligible_count"], 0)
        self.assertEqual(gate["future_non_materialized_capacity_only_interface_count"], 0)
        self.assertFalse(gate["runtime_attach_allowed"])
        self.assertEqual(gate["delta_attach_count"], 0)
        self.assertEqual(gate["full_attach_count"], 0)
        self.assertTrue(gate["preload_report_written"])
        self.assertIn("skyseed_details", gate)
        self.assertEqual(
            gate["resident_attach_coverage_budget"]["budget_source"],
            "official_precompose_bpf_role_union_gate",
        )
        self.assertEqual(
            gate["resident_attach_coverage_budget"]["max_attach_interfaces"],
            1,
        )
        self.assertEqual(gate["resident_attach_coverage_budget"]["slack_attach_interfaces"], 0)
        self.assertEqual(
            gate["required_interface_role_union_by_role"],
            {"g_to_z_classifier": 1, "z_to_g_core_lfib": 1},
        )

    def test_precompose_bpf_binding_coverage_requires_fixed_shell_inventory(
        self,
    ) -> None:
        topology = {
            "slot_inventory": {
                "same_plane_isl": [
                    {
                        "slot_name": "fixed-shell-0150",
                        "slot_type": "same_plane_isl",
                        "endpoint_a_template": {
                            "node_id": "stl-940150",
                            "interface_name": "fs0150",
                        },
                        "endpoint_b_template": {
                            "node_id": "stl-940151",
                            "interface_name": "fs0150",
                        },
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "stl-940150",
                                "satellite_b_id": "stl-940151",
                            }
                        ],
                    }
                ]
            }
        }
        role_report = {
            "epoch_id": "runtime-1",
            "slice_id": "slice-1",
            "generation_id": 0,
            "attach_plan": {"netns:stl-940150:fs0150": ["ingress"]},
            "role_by_interface": {
                "netns:stl-940150:fs0150": ["g_to_z_core_lfib"],
            },
            "interface_details": {
                "netns:stl-940150:fs0150": {
                    "interface_ref": "netns:stl-940150:fs0150",
                    "expected_roles": ["g_to_z_core_lfib"],
                }
            },
        }

        missing_gate = _precompose_bpf_role_union_gate_from_reports(
            current_event_role_reports=[role_report],
            lookahead_event_role_reports=[],
            resource_coverage_plan={"prepared_resource_scope_by_slot": {}},
            topology=topology,
        )
        self.assertFalse(missing_gate["passed"])
        self.assertEqual(
            missing_gate["unresolved_bpf_interface_binding_count"],
            1,
        )

        covered_gate = _precompose_bpf_role_union_gate_from_reports(
            current_event_role_reports=[role_report],
            lookahead_event_role_reports=[],
            resource_coverage_plan={
                "prepared_resource_scope_by_slot": {
                    "fixed-shell-0150": ["same_plane_isl:stl-940150:stl-940151"]
                }
            },
            topology=topology,
        )
        self.assertTrue(covered_gate["passed"])
        self.assertEqual(
            covered_gate["unresolved_bpf_interface_binding_count"],
            0,
        )
        self.assertTrue(covered_gate["fixed_shell_0150_resolved"])

    def test_helper_compose_stage_timeout_scales_for_network_creation_and_s4_helper_count(self) -> None:
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="helper",
                service_count=2,
            ),
            90.0,
        )
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="helper",
                service_count=34,
            ),
            234.0,
        )
        self.assertEqual(
            _compose_stage_recovery_timeout_seconds(
                stage_type="helper",
                service_count=29,
            ),
            194.0,
        )

    def test_endpoint_compose_stage_timeout_scales_with_endpoint_count(self) -> None:
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="endpoint",
                service_count=8,
            ),
            60.0,
        )
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="endpoint",
                service_count=44,
            ),
            240.0,
        )
        self.assertEqual(
            _compose_stage_recovery_timeout_seconds(
                stage_type="endpoint",
                service_count=19,
            ),
            129.0,
        )

    def test_progress_contract_allows_high_progress_grace(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=895.0,
            completed_batches=80,
            total_batches=81,
            seconds_since_progress=2.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertEqual(contract["classification"], "high_progress_grace")
        self.assertEqual(contract["outcome_label"], "SLOW_BUT_PROGRESSING_CONTINUE")
        self.assertTrue(contract["base_budget_pressure"])
        self.assertGreater(contract["remaining_contract_seconds"], 0.0)

    def test_compose_startup_wall_time_attribution_splits_known_costs(self) -> None:
        attribution = _compose_startup_wall_time_attribution(
            stage_results=[
                {
                    "stage_id": "helpers",
                    "wall_seconds": 10.0,
                    "inter_stage_backoff_seconds": 3.0,
                },
                {
                    "stage_id": "satellites",
                    "wall_seconds": 20.0,
                    "retry_after_timeout": {
                        "retry_results": [
                            {"wall_seconds": 4.0},
                            {"wall_seconds": 5.0},
                        ]
                    },
                },
            ],
            total_wall_seconds=45.0,
        )

        self.assertEqual(attribution["compose_up_command_wall_seconds"], 30.0)
        self.assertEqual(attribution["compose_up_retry_command_wall_seconds"], 9.0)
        self.assertEqual(
            attribution["compose_up_inter_stage_backoff_wall_seconds"],
            3.0,
        )
        self.assertEqual(
            attribution["compose_up_orchestration_overhead_wall_seconds"],
            3.0,
        )
        self.assertEqual(attribution["retry_command_count"], 2)

    def test_progress_contract_blocks_no_progress_startup_stall(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=100.0,
            completed_batches=0,
            total_batches=81,
            seconds_since_progress=130.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
        )

        self.assertFalse(contract["allow_continue"])
        self.assertEqual(contract["classification"], "no_progress_stall")
        self.assertEqual(contract["outcome_label"], "NO_PROGRESS_STARTUP_STALL")

    def test_progressing_startup_near_end_continues_after_soft_budget(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=1090.0,
            completed_batches=83,
            total_batches=84,
            seconds_since_progress=1.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=0.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=True,
            current_stage_changed=True,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertEqual(contract["classification"], "high_progress_continue")
        self.assertTrue(contract["soft_budget_exceeded"])
        self.assertEqual(contract["outcome_label"], "SLOW_BUT_PROGRESSING_CONTINUE")
        self.assertTrue(contract["high_progress_continue"])
        self.assertTrue(contract["progress_observed"])
        self.assertFalse(contract["no_progress_stall"])
        self.assertFalse(contract["absolute_max_exceeded"])
        self.assertGreaterEqual(contract["remaining_contract_seconds"], 400.0)
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="satellite_batch",
                service_count=12,
            ),
            90.0,
        )

    def test_progress_contract_continues_past_absolute_cap_with_recent_progress(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=1501.0,
            completed_batches=71,
            total_batches=72,
            seconds_since_progress=1.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=True,
            current_stage_changed=True,
            docker_responsive=True,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertEqual(contract["classification"], "high_progress_continue")
        self.assertEqual(
            contract["outcome_label"],
            "SLOW_BUT_PROGRESSING_CONTINUE",
        )
        self.assertTrue(contract["absolute_max_exceeded"])
        self.assertFalse(contract["hard_cap_exceeded"])
        self.assertTrue(contract["absolute_max_nonblocking_due_to_progress"])
        self.assertTrue(contract["recent_progress_observed"])
        self.assertIsNone(contract["kill_reason"])

    def test_progress_contract_blocks_absolute_startup_cap_without_recent_progress(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=1501.0,
            completed_batches=71,
            total_batches=72,
            seconds_since_progress=130.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=False,
            current_stage_changed=False,
            docker_responsive=True,
        )

        self.assertFalse(contract["allow_continue"])
        self.assertEqual(contract["classification"], "no_progress_stall")
        self.assertEqual(contract["outcome_label"], "NO_PROGRESS_STARTUP_STALL")
        self.assertTrue(contract["absolute_max_exceeded"])
        self.assertTrue(contract["hard_cap_exceeded"])
        self.assertEqual(contract["kill_reason"], "no_progress_stall")

    def test_soft_budget_exceeded_with_recent_progress_records_observation_only(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=930.0,
            completed_batches=12,
            total_batches=84,
            seconds_since_progress=3.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=0.0,
            slow_progress_grace_seconds=0.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=True,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertTrue(contract["soft_budget_exceeded"])
        self.assertFalse(contract["soft_budget_warning"])
        self.assertTrue(contract["progress_observed"])
        self.assertEqual(contract["classification"], "high_progress_continue")
        self.assertEqual(contract["outcome_label"], "SLOW_BUT_PROGRESSING_CONTINUE")
        self.assertIsNone(contract["kill_reason"])

    def test_elapsed_budget_without_progress_is_observation_not_warning_or_kill(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=930.0,
            completed_batches=0,
            total_batches=84,
            seconds_since_progress=3.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=0.0,
            slow_progress_grace_seconds=0.0,
            absolute_max_seconds=0.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=False,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertEqual(contract["classification"], "startup_elapsed_budget_observed")
        self.assertIsNone(contract["outcome_label"])
        self.assertFalse(contract["soft_budget_warning"])
        self.assertIsNone(contract["kill_reason"])

    def test_endpoint_stage_retry_uses_single_service_batches(self) -> None:
        self.assertEqual(
            _compose_stage_retry_batches(
                service_names=[f"ut{i}" for i in range(1, 4)],
                stage_type="endpoint",
            ),
            [
                ["ut1"],
                ["ut2"],
                ["ut3"],
            ],
        )

    def test_helper_stage_retry_uses_parallel_batches(self) -> None:
        self.assertEqual(
            _compose_stage_retry_batches(
                service_names=[f"helper{i}" for i in range(1, 7)],
                stage_type="helper",
            ),
            [
                ["helper1", "helper2", "helper3", "helper4"],
                ["helper5", "helper6"],
            ],
        )

    def test_large_compose_splits_helpers_and_ground_endpoints_into_batches(self) -> None:
        compose_audit = {
            "service_names_by_type": {
                "helper": [
                    "0123456789abcdef0123456789abcdef",
                    *[f"ut{i}-placeholder-rtr" for i in range(1, 33)],
                ],
                "ix": [],
                "mini_internet": [],
                "gateway": [f"gw{i}" for i in range(1, 13)],
                "ut": [f"ut{i}" for i in range(1, 33)],
                "satellite": [],
            }
        }
        service_name_by_container = {
            "seed_output_0123456789abcdef0123456789abcdef_1": (
                "0123456789abcdef0123456789abcdef"
            ),
            **{
                f"ut{i}-placeholder-rtr": f"rnode_250_ut{i}-placeholder-rtr"
                for i in range(1, 33)
            },
            **{f"gw{i}": f"gw-service-{i}" for i in range(1, 13)},
            **{f"ut{i}": f"ut-service-{i}" for i in range(1, 33)},
        }

        stages = _compose_startup_stages(
            compose_audit=compose_audit,
            service_name_by_container=service_name_by_container,
            mode="infra-then-sat-batches",
            satellite_batch_size=24,
        )

        self.assertEqual(stages[0]["stage_id"], "helpers-one-shot")
        helper_stages = [
            stage
            for stage in stages
            if str(stage.get("stage_id")).startswith("helpers-placeholder")
        ]
        self.assertEqual(
            [len(stage["service_names"]) for stage in helper_stages],
            [8, 8, 8, 8],
        )
        gateway_stages = [
            stage
            for stage in stages
            if str(stage.get("stage_id")).startswith("ground-gateways")
        ]
        self.assertEqual(len(gateway_stages), 12)
        self.assertTrue(all(stage["endpoint_kind"] == "gateway" for stage in gateway_stages))
        self.assertTrue(all(len(stage["service_names"]) == 1 for stage in gateway_stages))
        ut_stages = [
            stage for stage in stages if str(stage.get("stage_id")).startswith("ground-uts")
        ]
        self.assertEqual(len(ut_stages), 32)
        self.assertTrue(all(len(stage["service_names"]) == 1 for stage in ut_stages))

    def test_satellite_startup_batches_respect_compose_parallel_limit(self) -> None:
        stages = _compose_startup_stages(
            compose_audit={
                "service_names_by_type": {
                    "helper": [],
                    "ix": [],
                    "mini_internet": [],
                    "gateway": [],
                    "ut": [],
                    "satellite": [f"stl-{index:06d}" for index in range(1, 25)],
                }
            },
            service_name_by_container={},
            mode="infra-then-sat-batches",
            satellite_batch_size=24,
            compose_parallel_limit=8,
        )

        self.assertEqual(
            [len(stage["service_names"]) for stage in stages],
            [8, 8, 8],
        )

    def test_helper_stage_accepts_successful_seed_one_shot(self) -> None:
        snapshot = {
            "containers": {
                "seed_output_0123456789abcdef0123456789abcdef_1": {
                    "state": "exited",
                    "exit_code": 0,
                    "error": "",
                },
                "ut2001-placeholder-rtr": {
                    "state": "running",
                    "exit_code": None,
                    "error": "",
                },
                "ut2002-placeholder-rtr": {
                    "state": "exited",
                    "exit_code": 0,
                    "error": "",
                },
                "seed_output_fedcba9876543210fedcba9876543210_1": {
                    "state": "exited",
                    "exit_code": 1,
                    "error": "",
                },
            }
        }

        ready = _stage_ready_service_names(
            compose_service_snapshot=snapshot,
            stage_services=[
                "helper-one-shot",
                "rnode_250_ut2001-placeholder-rtr",
                "rnode_250_ut2002-placeholder-rtr",
                "failed-helper-one-shot",
            ],
            service_name_by_container={
                "seed_output_0123456789abcdef0123456789abcdef_1": "helper-one-shot",
                "ut2001-placeholder-rtr": "rnode_250_ut2001-placeholder-rtr",
                "ut2002-placeholder-rtr": "rnode_250_ut2002-placeholder-rtr",
                "seed_output_fedcba9876543210fedcba9876543210_1": "failed-helper-one-shot",
            },
        )

        self.assertEqual(
            ready,
            ["helper-one-shot", "rnode_250_ut2001-placeholder-rtr"],
        )

    def test_endpoint_stage_rejects_successful_one_shot_container(self) -> None:
        snapshot = {
            "containers": {
                "seed_output_0123456789abcdef0123456789abcdef_1": {
                    "state": "exited",
                    "exit_code": 0,
                    "error": "",
                }
            }
        }

        ready = _stage_ready_service_names(
            compose_service_snapshot=snapshot,
            stage_services=["hnode_250_ut2001"],
            service_name_by_container={
                "seed_output_0123456789abcdef0123456789abcdef_1": (
                    "hnode_250_ut2001"
                ),
            },
            helper_service_names=[],
        )

        self.assertEqual(ready, [])

    def test_ground_endpoint_summary_reports_missing_and_recovery_attempts(self) -> None:
        snapshot = {
            "containers": {
                "ut2001": {
                    "state": "running",
                    "status": "running",
                    "created_at": "2026-06-22T10:00:00.000000000Z",
                    "started_at": "2026-06-22T10:00:04.500000000Z",
                },
                "ut2002": {
                    "state": "missing",
                },
            }
        }

        with patch(
            "apps.run_starlink_shell_experiment._compose_service_logs_tail",
            return_value={"returncode": 1, "stdout": "", "stderr": "missing"},
        ):
            summary = _ground_endpoint_startup_summary(
                output_dir=Path("/tmp/no-such-compose"),
                stage_services=["hnode_250_ut2001", "hnode_250_ut2002"],
                compose_service_snapshot=snapshot,
                service_name_by_container={
                    "ut2001": "hnode_250_ut2001",
                    "ut2002": "hnode_250_ut2002",
                },
                initially_ready_service_names=[],
                retry_results=[
                    {
                        "service_names": ["hnode_250_ut2001", "hnode_250_ut2002"],
                        "timed_out": True,
                        "timeout_seconds": 30.0,
                    }
                ],
            )

        self.assertEqual(summary["ground_endpoint_expected_count"], 2)
        self.assertEqual(summary["ground_endpoint_up_count"], 1)
        self.assertEqual(summary["ground_endpoint_missing_count"], 1)
        self.assertEqual(summary["ground_endpoint_recovered_count"], 1)
        self.assertEqual(summary["readiness_timeout_reason"], "missing_long_running_endpoints")
        self.assertEqual(
            summary["per_service_startup_seconds"]["hnode_250_ut2001"],
            4.5,
        )

    def test_container_readiness_accepts_successful_seed_one_shot(self) -> None:
        one_shot = "seed_output_0123456789abcdef0123456789abcdef_1"

        def fake_run(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        "ut2001\tUp 1 second\n"
                        f"{one_shot}\tExited (0) 1 second ago\n"
                    ),
                    stderr="",
                )
            raise AssertionError(command)

        with patch("apps.run_starlink_shell_experiment.subprocess.run", side_effect=fake_run):
            readiness = _wait_for_compose_containers(
                container_names=["ut2001", one_shot],
                timeout_seconds=0.0,
            )

        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["attempts"][0]["missing_containers"], [])
        self.assertEqual(readiness["attempts"][0]["ready_one_shot_containers"], [one_shot])

    def test_container_readiness_requires_explicit_one_shot_helper_name(self) -> None:
        one_shot = "seed_output_0123456789abcdef0123456789abcdef_1"

        def fake_run(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        "ut2001\tUp 1 second\n"
                        f"{one_shot}\tExited (0) 1 second ago\n"
                    ),
                    stderr="",
                )
            raise AssertionError(command)

        with patch("apps.run_starlink_shell_experiment.subprocess.run", side_effect=fake_run):
            readiness = _wait_for_compose_containers(
                container_names=["ut2001", one_shot],
                timeout_seconds=0.01,
                poll_interval_seconds=0.0,
                helper_container_names=[],
            )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["attempts"][-1]["missing_containers"], [one_shot])

    def test_compose_metadata_tracks_generated_container_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run-specific-output"
            output_dir.mkdir()
            (output_dir / ".env").write_text(
                "COMPOSE_PROJECT_NAME=seed_output\n",
                encoding="utf-8",
            )
            (output_dir / "docker-compose.yml").write_text(
                "\n".join(
                    [
                        "version: '2.4'",
                        "services:",
                        "  0123456789abcdef0123456789abcdef:",
                        "    image: helper-image",
                        "  explicit-service:",
                        "    image: explicit-image",
                        "    container_name: explicit-container",
                        "networks: {}",
                    ]
                ),
                encoding="utf-8",
            )

            metadata = _compose_service_metadata(output_dir)

        self.assertIn(
            "seed_output_0123456789abcdef0123456789abcdef_1",
            metadata["container_names"],
        )
        self.assertIn("explicit-container", metadata["container_names"])
        self.assertEqual(
            metadata["service_name_by_container"][
                "seed_output_0123456789abcdef0123456789abcdef_1"
            ],
            "0123456789abcdef0123456789abcdef",
        )

    def test_real_runtime_queue_is_sourced_from_event_timeline(self) -> None:
        temporal_plan = _temporal_plan()

        frames, queue, runtime_slices, parameter_slices = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )

        self.assertEqual(runtime_slices, ["slice-1"])
        self.assertEqual(parameter_slices, ["slice-3"])
        self.assertEqual(len(frames), 3)
        self.assertEqual(len(queue), 2)
        self.assertTrue(all(entry["source"] == "event_timeline" for entry in queue))
        self.assertEqual(queue[0]["event_timeline_type"], "runtime_transaction")
        self.assertEqual(queue[1]["event_timeline_type"], "parameter_update_batch")
        self.assertEqual(queue[0]["physical_sample_count"], 3)
        self.assertEqual(queue[0]["runtime_transaction_count"], 1)
        self.assertEqual(queue[0]["parameter_update_batch_count"], 1)
        self.assertEqual(queue[0]["skipped_noop_sample_count"], 1)
        self.assertEqual(queue[0]["sample_aligned_event_count"], 2)
        self.assertEqual(queue[0]["refined_event_count"], 0)

    def test_continuous_operation_summary_reports_event_timeline_counts(self) -> None:
        temporal_plan = _temporal_plan()
        _, queue, runtime_slices, parameter_slices = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )

        summary = _continuous_operation_summary(
            temporal_plan=temporal_plan,
            all_sample_frames=[],
            selected_epoch_queue=queue,
            selected_runtime_slice_ids=runtime_slices,
            selected_parameter_slice_ids=parameter_slices,
        )

        self.assertEqual(summary["source"], "event_timeline")
        self.assertEqual(summary["physical_sample_count"], 3)
        self.assertEqual(summary["runtime_transaction_count"], 1)
        self.assertEqual(summary["parameter_update_batch_count"], 1)
        self.assertEqual(summary["skipped_noop_sample_count"], 1)

    def test_run_manifest_summary_exposes_event_timeline_counts_at_top_level(self) -> None:
        temporal_plan = _temporal_plan()
        _, queue, runtime_slices, parameter_slices = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )
        args = _summary_args()
        runtime_report = {
            "finalized": True,
            "mode": "real-runtime",
            "timing_summary": {},
            "temporal_audit": {"source": "event_timeline"},
            "iterations": [],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "physical_sample_count": 3,
                "runtime_transaction_count": 1,
                "parameter_update_batch_count": 1,
                "skipped_noop_sample_count": 1,
                "sample_aligned_event_count": 2,
                "refined_event_count": 0,
                "epoch_execution_queue": queue,
            },
            "outcome": {
                "event_timeline_execution_success": True,
                "pre_rollback_connectivity_success": True,
                "rollback_success": True,
                "post_run_preflight_clean": True,
                "aggregate_runtime_succeeded": True,
                "linux_gz_fallback_enabled": False,
                "linux_gz_fallback_command_count": 0,
                "eligible_core_fallback_to_linux": 0,
                "bpf_classifier_miss_count": 0,
                "bpf_ping_path_hit_count": 2,
                "bpf_return_handoff_hit_count": 1,
            },
        }

        manifest = _experiment_summary_payload(
            args=args,
            run_id="run-1",
            run_dir=Path("/tmp/run-1"),
            preview_summary={},
            temporal_payload=temporal_plan,
            selected_runtime_slice_ids=runtime_slices,
            selected_parameter_slice_ids=parameter_slices,
            preview_gate={},
            runtime_report=runtime_report,
            runtime_blocker=None,
            watcher_summary={},
            phase_timing={},
            timing_analysis={},
            compose_audit={},
            startup_strategy=None,
            mini_internet_summary={},
            continuous_operation_summary={},
            slot_capacity_audit={},
            data_provenance={},
            artifacts={"real_runtime_report_json": "/tmp/run-1/real_runtime_report.json"},
            preflight_report=None,
            host_prepare_report=None,
            post_run_preflight_report={"clean_after_run": True},
        )

        self.assertEqual(manifest["source"], "event_timeline")
        self.assertEqual(manifest["physical_sample_count"], 3)
        self.assertEqual(manifest["runtime_transaction_count"], 1)
        self.assertEqual(manifest["parameter_update_batch_count"], 1)
        self.assertEqual(manifest["skipped_noop_sample_count"], 1)
        self.assertEqual(manifest["sample_aligned_event_count"], 2)
        self.assertEqual(manifest["refined_event_count"], 0)
        self.assertEqual(manifest["temporal_summary"]["source"], "event_timeline")

    def test_compact_execution_record_preserves_event_timeline_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            detail_jsonl = Path(temp_dir) / "details.jsonl"
            compact = _compact_epoch_execution_record(
                {
                    "sequence_index": 1,
                    "source": "event_timeline",
                    "event_timeline_type": "runtime_transaction",
                    "source_event_id": "event-runtime-1",
                    "source_event_type": "runtime_epoch",
                    "physical_sample_count": 3,
                    "runtime_transaction_count": 1,
                    "parameter_update_batch_count": 1,
                    "skipped_noop_sample_count": 1,
                    "sample_aligned_event_count": 2,
                    "refined_event_count": 0,
                    "event_timeline_execution_success": True,
                    "pre_rollback_connectivity_success": True,
                    "rollback_success": True,
                    "post_run_preflight_clean": True,
                    "aggregate_runtime_succeeded": True,
                    "linux_gz_fallback_enabled": False,
                    "linux_gz_fallback_command_count": 0,
                    "eligible_core_fallback_to_linux": 0,
                    "bpf_classifier_miss_count": 0,
                    "bpf_ping_path_hit_count": 4,
                    "bpf_return_handoff_hit_count": 2,
                    "epoch_id": "runtime-1",
                    "epoch_type": "runtime_epoch",
                    "timing": {},
                    "outcome": {"success": True},
                },
                detail_jsonl=detail_jsonl,
            )

        self.assertEqual(compact["source"], "event_timeline")
        self.assertEqual(compact["event_timeline_type"], "runtime_transaction")
        self.assertEqual(compact["physical_sample_count"], 3)
        self.assertEqual(compact["runtime_transaction_count"], 1)
        self.assertEqual(compact["parameter_update_batch_count"], 1)
        self.assertIs(compact["event_timeline_execution_success"], True)
        self.assertIs(compact["pre_rollback_connectivity_success"], True)
        self.assertIs(compact["rollback_success"], True)
        self.assertIs(compact["post_run_preflight_clean"], True)
        self.assertIs(compact["aggregate_runtime_succeeded"], True)
        self.assertIs(compact["linux_gz_fallback_enabled"], False)
        self.assertEqual(compact["linux_gz_fallback_command_count"], 0)
        self.assertEqual(compact["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(compact["bpf_classifier_miss_count"], 0)
        self.assertEqual(compact["bpf_ping_path_hit_count"], 4)
        self.assertEqual(compact["bpf_return_handoff_hit_count"], 2)

    def test_finalized_runtime_status_fields_propagate_to_artifacts(self) -> None:
        runtime_report = {
            "finalized": True,
            "final_cleanup": {"all_cleanup_success": True},
            "final_runtime_connectivity_validation": {
                "validation_phase": "pre_rollback",
                "validation_scope": "reapplied_gateway_class_sample",
                "primary_only": True,
                "ping_success": True,
            },
            "iterations": [
                {
                    "sequence_index": 1,
                    "source": "event_timeline",
                    "event_timeline_type": "runtime_transaction",
                    "outcome": {"success": True},
                    "linux_gz_fallback_enabled": False,
                    "linux_gz_fallback_command_count": 0,
                    "eligible_core_fallback_to_linux": 0,
                    "bpf_classifier_miss_count": 0,
                    "bpf_ping_path_hit_count": 2,
                    "bpf_return_handoff_hit_count": 1,
                }
            ],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "epoch_execution_queue": [
                    {
                        "sequence_index": 1,
                        "source": "event_timeline",
                        "event_timeline_type": "runtime_transaction",
                        "outcome": {"success": True},
                        "linux_gz_fallback_enabled": False,
                        "linux_gz_fallback_command_count": 0,
                        "eligible_core_fallback_to_linux": 0,
                        "bpf_classifier_miss_count": 0,
                        "bpf_ping_path_hit_count": 2,
                        "bpf_return_handoff_hit_count": 1,
                    }
                ],
            },
            "temporal_audit": {"source": "event_timeline"},
            "outcome": {"failure_reason": None, "rollback_completed": True},
        }

        finalized = _finalize_runtime_status_fields(
            runtime_report,
            {"clean_after_run": True},
        )

        for container in (
            finalized,
            finalized["outcome"],
            finalized["temporal_audit"],
            finalized["iterations"][0],
            finalized["prepared_bundle_runtime"]["epoch_execution_queue"][0],
        ):
            self.assertIs(container["event_timeline_execution_success"], True)
            self.assertIs(container["pre_rollback_connectivity_success"], True)
            self.assertIs(container["rollback_success"], True)
            self.assertIs(container["post_run_preflight_clean"], True)
            self.assertIs(container["aggregate_runtime_succeeded"], True)
            self.assertIs(container["linux_gz_fallback_enabled"], False)
            self.assertEqual(container["linux_gz_fallback_command_count"], 0)
            self.assertEqual(container["eligible_core_fallback_to_linux"], 0)
            self.assertEqual(container["bpf_classifier_miss_count"], 0)
            self.assertEqual(container["bpf_ping_path_hit_count"], 2)
            self.assertEqual(container["bpf_return_handoff_hit_count"], 1)

    def test_finalized_runtime_status_preserves_strict_violation_counters(self) -> None:
        failure_reason = (
            "bpf_owned_strict_contract_violation:"
            "eligible G/Z scope must be BPF-owned with no Linux fallback; "
            "readiness_reason=bpf_mpls_map_key_conflict; "
            "eligible_core_fallback_to_linux=902; "
            "linux_gz_fallback_command_count=902; "
            "backend_apply_fallback_to_linux=902"
        )
        runtime_report = {
            "finalized": True,
            "final_cleanup": {"all_cleanup_success": True},
            "final_runtime_connectivity_validation": {},
            "iterations": [],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "epoch_execution_queue": [],
            },
            "temporal_audit": {"source": "event_timeline"},
            "failure_context": {
                "exception": {
                    "message": failure_reason,
                    "skyseed_details": {
                        "backend_apply_contract": {
                            "linux_gz_fallback_command_count": 902,
                            "eligible_core_fallback_to_linux": 902,
                        }
                    },
                }
            },
            "outcome": {"failure_reason": failure_reason, "rollback_completed": True},
        }

        finalized = _finalize_runtime_status_fields(
            runtime_report,
            {"clean_after_run": True},
        )

        for container in (
            finalized,
            finalized["outcome"],
            finalized["temporal_audit"],
        ):
            self.assertIs(container["linux_gz_fallback_enabled"], True)
            self.assertEqual(container["linux_gz_fallback_command_count"], 902)
            self.assertEqual(container["eligible_core_fallback_to_linux"], 902)
            self.assertIs(container["aggregate_runtime_succeeded"], False)

    def test_finalized_runtime_status_preserves_failure_miss_drop_counters(self) -> None:
        failure_reason = (
            "bpf_owned_epoch_validation_miss_drop; "
            "backend_apply_skipped_by_bpf=592; "
            "backend_apply_fallback_to_linux=0; "
            "backend_apply_requested_commands=782"
        )
        runtime_report = {
            "finalized": True,
            "final_cleanup": {"all_cleanup_success": True},
            "final_runtime_connectivity_validation": {},
            "iterations": [],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "epoch_execution_queue": [],
            },
            "temporal_audit": {"source": "event_timeline"},
            "failure_context": {
                "exception": {
                    "message": failure_reason,
                    "skyseed_details": {
                        "backend_apply_contract": {
                            "linux_gz_fallback_command_count": 0,
                            "eligible_core_fallback_to_linux": 0,
                        },
                        "bpf_owned_packet_path_diagnostics": {
                            "bpf_classifier_miss_count": 146,
                            "bpf_ping_path_hit_count": 12,
                            "bpf_return_handoff_hit_count": 12,
                            "miss_drop_counters": {
                                "miss": 73,
                                "drop": 73,
                                "drop_missing_label": 0,
                                "drop_missing_generation": 0,
                                "drop_bad_packet": 0,
                            },
                        },
                    },
                }
            },
            "outcome": {"failure_reason": failure_reason, "rollback_completed": True},
        }

        finalized = _finalize_runtime_status_fields(
            runtime_report,
            {"clean_after_run": True},
        )

        for container in (
            finalized,
            finalized["outcome"],
            finalized["temporal_audit"],
        ):
            self.assertIs(container["linux_gz_fallback_enabled"], False)
            self.assertEqual(container["linux_gz_fallback_command_count"], 0)
            self.assertEqual(container["eligible_core_fallback_to_linux"], 0)
            self.assertEqual(container["bpf_classifier_miss_count"], 146)
            self.assertEqual(container["bpf_ping_path_hit_count"], 12)
            self.assertEqual(container["bpf_return_handoff_hit_count"], 12)
            self.assertEqual(
                container["bpf_miss_drop_counters"],
                {
                    "miss": 73,
                    "drop": 73,
                    "drop_missing_label": 0,
                    "drop_missing_generation": 0,
                    "drop_bad_packet": 0,
                },
            )
            self.assertIs(container["aggregate_runtime_succeeded"], False)

    def test_runtime_failure_context_summary_preserves_first_failure_bpf_counters(self) -> None:
        runtime_report = {
            "failure_context": {
                "exception": {
                    "message": "bpf_owned_epoch_validation_miss_drop; cleanup_success=True",
                    "skyseed_details": {
                        "bpf_owned_packet_path_diagnostics": {
                            "classifier_hit": 12,
                            "label_push": 12,
                            "egress_handoff_hit": 12,
                            "label_pop": 12,
                            "core_lfib_hit": 0,
                            "label_swap": 0,
                            "bpf_classifier_miss_count": 272,
                            "miss_drop_counters": {
                                "miss": 136,
                                "drop": 136,
                            },
                        }
                    },
                }
            },
            "outcome": {"failure_reason": None},
        }

        summary = _runtime_failure_context_summary(runtime_report)

        self.assertEqual(
            summary["classified_blocker"],
            "bpf_owned_epoch_validation_miss_drop",
        )
        self.assertEqual(
            summary["bpf_first_failure_counters"]["bpf_classifier_miss_count"],
            272,
        )
        self.assertEqual(
            summary["bpf_first_failure_counters"]["miss_drop_counters"],
            {"miss": 136, "drop": 136},
        )

    def test_operational_clean_cost_sanity_flags_steady_full_attach(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "initial",
                    "timing": {"semantic_breakdown": {"network_update_seconds": 5.0}},
                },
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "steady",
                    "bpf_attach_reuse": {
                        "available": True,
                        "reuse_eligible": False,
                        "reuse_rejected_reason": "map_reload_failed_before_full_prepare",
                        "attach_strategy": "dirty_recovery_reset",
                        "full_attach_count": 12,
                        "delta_attach_count": 0,
                        "reused_attach_count": 0,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": False,
                    },
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "post_apply_overhead_seconds": 1.0,
                            "unknown_blocking_seconds": 0.0,
                        }
                    },
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        cost = summary["cost_sanity"]
        self.assertFalse(cost["passed"])
        self.assertEqual(cost["conclusion"], "STEADY_UPDATE_COST_ANOMALY")
        self.assertTrue(cost["broad_rerun_blocked"])

    def test_operational_clean_cost_sanity_flags_cleanup_cost(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "initial",
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "runtime_incremental_cleanup_seconds": 0.0,
                        }
                    },
                },
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "steady",
                    "bpf_attach_reuse": {
                        "available": True,
                        "reuse_eligible": True,
                        "attach_strategy": "reuse_attached_bpf_map_reload",
                        "full_attach_count": 0,
                        "delta_attach_count": 0,
                        "reused_attach_count": 10,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": True,
                    },
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "runtime_incremental_cleanup_seconds": 400.0,
                            "post_apply_overhead_seconds": 1.0,
                            "unknown_blocking_seconds": 0.0,
                        }
                    },
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertEqual(summary["cost_sanity"]["conclusion"], "CLEANUP_COST_ANOMALY")

    def test_operational_clean_cost_sanity_flags_command_cardinality(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "initial",
                    "timing": {"semantic_breakdown": {"network_update_seconds": 5.0}},
                },
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "steady",
                    "bpf_attach_reuse": {
                        "available": True,
                        "reuse_eligible": True,
                        "attach_strategy": "reuse_attached_bpf_map_reload",
                        "full_attach_count": 0,
                        "delta_attach_count": 0,
                        "reused_attach_count": 10,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": True,
                    },
                    "host_operation_timing_summary": {
                        "operation_counts": {"qdisc_delete": 9},
                    },
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "post_apply_overhead_seconds": 1.0,
                            "unknown_blocking_seconds": 0.0,
                        }
                    },
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertEqual(
            summary["cost_sanity"]["conclusion"],
            "COMMAND_CARDINALITY_ANOMALY",
        )

    def test_experiment_summary_cost_sanity_blocks_runtime_pass(self) -> None:
        runtime_report = _summary_runtime_report(
            timing_summary={
                "cost_sanity": {
                    "schema_version": "skyseed-cost-sanity-v1",
                    "applicable": True,
                    "passed": False,
                    "conclusion": "COMMAND_CARDINALITY_ANOMALY",
                    "anomaly_count": 1,
                    "broad_rerun_blocked": True,
                    "anomaly_table": [
                        {
                            "phase": "command_cardinality",
                            "expected_order": "steady map-only update",
                            "actual": {"steady_qdisc_delete_count": 9},
                            "anomaly": True,
                            "suspected_module": "command planner",
                            "next_action": "inspect command cardinality",
                            "conclusion": "COMMAND_CARDINALITY_ANOMALY",
                        }
                    ],
                }
            },
        )

        manifest = _summary_payload_for_test(runtime_report=runtime_report)

        self.assertIs(manifest["aggregate_runtime_succeeded"], False)
        self.assertEqual(
            manifest["failure_reason"],
            "cost_sanity_failed:COMMAND_CARDINALITY_ANOMALY",
        )
        self.assertEqual(
            manifest["runtime_blocker"]["classified_blocker"],
            "COMMAND_CARDINALITY_ANOMALY",
        )
        self.assertEqual(
            manifest["cost_sanity_blocker"]["stage"],
            "cost_sanity",
        )
        self.assertTrue(manifest["broad_rerun_blocked"])

    def test_experiment_summary_warns_startup_cost_anomaly_without_failing_runtime(
        self,
    ) -> None:
        runtime_report = _summary_runtime_report()
        manifest = _summary_payload_for_test(
            runtime_report=runtime_report,
            timing_analysis={
                "startup_preparation": {
                    "cold_start_before_first_epoch_execution_wall_seconds": 1200.0,
                    "ready_to_execute_epochs_wall_seconds": 1190.0,
                    "compose_up_wall_seconds": 900.0,
                    "container_readiness_wall_seconds": 200.0,
                }
            },
        )

        self.assertIs(manifest["aggregate_runtime_succeeded"], True)
        self.assertEqual(
            manifest["cost_sanity_conclusion"],
            "STARTUP_COST_ANOMALY",
        )
        self.assertIsNone(manifest["runtime_blocker"])
        self.assertIsNone(manifest["cost_sanity_blocker"])
        self.assertTrue(manifest["broad_rerun_blocked"])
        self.assertEqual(manifest["cost_sanity"]["blocking_anomaly_count"], 0)
        self.assertEqual(manifest["cost_sanity"]["warning_only_anomaly_count"], 1)
        self.assertFalse(
            manifest["cost_sanity"]["policy"][
                "startup_cost_anomaly_blocks_runtime_pass"
            ]
        )

    def test_experiment_summary_preserves_semantic_blocker_when_cost_fails(self) -> None:
        runtime_report = _summary_runtime_report(
            aggregate_runtime_succeeded=False,
            failure_reason="operational_clean_bpf_map_reload_failed",
            timing_summary={
                "cost_sanity": {
                    "schema_version": "skyseed-cost-sanity-v1",
                    "applicable": True,
                    "passed": False,
                    "conclusion": "CLEANUP_COST_ANOMALY",
                    "anomaly_count": 1,
                    "broad_rerun_blocked": True,
                    "anomaly_table": [
                        {
                            "phase": "cleanup",
                            "expected_order": "bounded cleanup",
                            "actual": {"total_seconds": 400.0},
                            "anomaly": True,
                            "suspected_module": "cleanup",
                            "next_action": "inspect cleanup",
                            "conclusion": "CLEANUP_COST_ANOMALY",
                        }
                    ],
                }
            },
        )
        semantic_blocker = {
            "stage": "bpf_hotpath_map_reload",
            "classified_blocker": "operational_clean_bpf_map_reload_failed",
        }

        manifest = _summary_payload_for_test(
            runtime_report=runtime_report,
            runtime_blocker=semantic_blocker,
        )

        self.assertEqual(
            manifest["runtime_blocker"]["classified_blocker"],
            "operational_clean_bpf_map_reload_failed",
        )
        self.assertEqual(
            manifest["cost_sanity_blocker"]["classified_blocker"],
            "CLEANUP_COST_ANOMALY",
        )
        self.assertTrue(manifest["broad_rerun_blocked"])

    def test_bpf_experimental_backend_is_explicit_diagnostic_only(self) -> None:
        self.assertFalse(_bpf_backend_opted_in(_summary_args()))
        self.assertTrue(_bpf_backend_opted_in(_summary_args(bpf_dataplane_canary=True)))
        self.assertTrue(_bpf_backend_opted_in(_summary_args(bpf_backend="experimental")))

        manifest = _summary_payload_for_test(
            args=_summary_args(bpf_dataplane_canary=True),
            runtime_report=_summary_runtime_report(),
            bpf_dataplane_canary={"ok": True, "metrics": {}},
        )

        self.assertEqual(
            manifest["bpf_experimental_backend_contract"],
            {
                "diagnostic_only": True,
                "authoritative_for_runtime_pass": False,
                "explicit_opt_in": True,
                "executed": True,
            },
        )
        self.assertIs(manifest["aggregate_runtime_succeeded"], True)

    def test_bpf_experimental_success_cannot_make_strict_runtime_pass(self) -> None:
        manifest = _summary_payload_for_test(
            args=_summary_args(bpf_dataplane_canary=True),
            runtime_report=_summary_runtime_report(
                aggregate_runtime_succeeded=False,
                failure_reason="operational_clean_bpf_map_reload_failed",
            ),
            bpf_dataplane_canary={"ok": True, "metrics": {"loaded_gz_fec_count": 8}},
        )

        self.assertIs(manifest["aggregate_runtime_succeeded"], False)
        self.assertEqual(
            manifest["failure_reason"],
            "operational_clean_bpf_map_reload_failed",
        )
        self.assertFalse(
            manifest["bpf_experimental_backend_contract"][
                "authoritative_for_runtime_pass"
            ]
        )


class FrozenPreparedBundleContractTests(unittest.TestCase):
    def _args(self, **overrides: object) -> SimpleNamespace:
        payload = {
            "run_mode": "real-runtime",
            "orbit_input_mode": "planned-shell",
            "planned_shell_id": "S4",
            "max_satellites": 24,
            "start_time": "2026-06-21T00:00:00Z",
            "runtime_mode": "operational_clean",
            "forwarding_backend": "bpf_owned",
            "horizon_seconds": 120,
            "sample_interval": 10,
            "step_seconds": 120,
            "slice_count": 13,
            "continuous": False,
            "replenish_threshold_seconds": 60,
            "loop_count": 1,
            "interval": 0,
            "strict_activation_deadlines": False,
            "max_runtime_epochs": 4,
            "max_parameter_epochs": 1,
            "run_id": "ignored",
            "run_root": "/tmp/ignored",
            "format": "json",
            "load_frozen_prepared_bundle": None,
            "prepared_bundle_diagnostic_recompute_attempted": False,
        }
        payload.update(overrides)
        return SimpleNamespace(**payload)

    def test_frozen_source_hash_ignores_preview_to_real_runtime_transition(
        self,
    ) -> None:
        preview_args = self._args(run_mode="preview")
        real_args = self._args(run_mode="real-runtime")

        self.assertEqual(
            _input_profile_config_hash(preview_args),
            _input_profile_config_hash(real_args),
        )
        self.assertNotEqual(
            _input_profile_config_hash(preview_args, include_run_mode=True),
            _input_profile_config_hash(real_args, include_run_mode=True),
        )

    def _frozen_bundle(self, **arg_overrides: object) -> dict:
        start_time = str(
            arg_overrides.get("start_time") or "2026-06-21T00:00:00Z"
        )
        prepared = {
            "selected_epoch_queue": [
                {"event_id": "runtime-1", "absolute_time_utc": start_time},
                {"event_id": "parameter-1", "absolute_time_utc": "2026-06-21T00:00:10Z"},
                {"event_id": "runtime-2", "absolute_time_utc": "2026-06-21T00:00:20Z"},
            ],
            "current_execution_queue": [
                {"event_id": "runtime-1"},
                {"event_id": "parameter-1"},
            ],
            "lookahead_preload_queue": [
                {
                    "event_id": "runtime-2",
                    "execution_role": "lookahead_preload_only",
                    "runtime_apply_allowed": False,
                    "preload_required": True,
                }
            ],
            "precompose_horizon_queue_gate": {
                "preload_required_event_count": 1,
            },
            "prepared_artifacts": {},
        }
        return _freeze_prepared_update_bundle(
            prepared_update_bundle=prepared,
            args=self._args(**arg_overrides),
            prepared_resource_scope_by_slot={
                "slot-a": {"netns:node-a:if-a", "link:isl-a"}
            },
            precompose_bpf_role_union_gate={
                "required_interface_role_union": {
                    "netns:node-a:if-a": ["ingress_classifier", "core_lfib"]
                },
                "preload_reports": [{"event_id": "runtime-2", "status": "success"}],
                "preload_report_summary": {"success_count": 1},
            },
        )

    def _rolling_normal_runtime_inputs(self) -> dict:
        def _entry(segment_id: str, index: int) -> dict:
            return {
                "event_id": f"runtime-{index}",
                "epoch_id": f"epoch-{index}",
                "slice_id": f"slice-{index}",
                "segment_id": segment_id,
                "segment_epoch_key": f"{segment_id}:slice-{index}",
                "horizon_id": segment_id,
                "classification": "runtime_epoch",
                "runtime_apply_allowed": True,
                "activation_time_utc": f"2026-06-21T00:0{index}:00Z",
                "activation_relative_time_seconds": float(index * 60),
            }

        def _context(index: int) -> dict:
            snapshot = {
                "schema_version": "satellite-topology-snapshot-v1alpha1",
                "snapshot_id": f"snap-{index}",
                "time_slice": {"slice_id": f"slice-{index}", "state_model": "full_state"},
                "provenance": {"source": "test"},
                "nodes": {},
                "active_links": [],
                "zones": {},
                "ut_zone_membership": {},
                "planner_hints": {},
            }
            runtime_plan = {
                "schema_version": "satellite-runtime-update-plan-v1alpha1",
                "plan_id": f"plan-{index}",
                "target_snapshot": {"snapshot_id": f"snap-{index}"},
                "planner": {"planner_name": "test"},
                "apply_model": {
                    "state_model": "full_state_intent",
                    "action_model": "node_local_installer_action_v1",
                },
                "node_subplans": {},
                "replacement_semantics": {},
                "validation_expectations": {},
            }
            distribution = {
                "schema_version": "satellite-runtime-distribution-store-v1alpha1",
                "distribution_id": f"dist-{index}",
                "source_envelope_batch": {
                    "schema_version": "satellite-agent-envelope-batch-v1alpha1",
                    "envelope_batch_id": f"batch-{index}",
                },
                "distributor": {"distributor_name": "test"},
                "store_contract": {
                    "transport_binding": "transport_agnostic",
                    "dry_run_mode": True,
                },
                "node_delivery_views": {},
                "validation_expectations": {},
            }
            return {
                "snapshot": snapshot,
                "runtime_update_plan": runtime_plan,
                "distribution_batch": distribution,
                "slice_report": {},
            }

        segments = []
        context_by_slice_by_segment = {}
        context_by_segment_key = {}
        runtime_update_plan_by_key = {}
        distribution_by_key = {}
        snapshot_by_key = {}
        host_plan_by_key = {}
        for index, segment_id in enumerate(("H0", "H1", "H2")):
            entry = _entry(segment_id, index)
            ctx = _context(index)
            segment = {
                "segment_id": segment_id,
                "horizon_id": segment_id,
                "current_execution_queue": [entry],
                "lookahead_preload_queue": [],
                "selected_epoch_queue": [entry],
                "summary": {"source": "event_timeline", "runtime_epoch_count": 1},
                "prepared_resource_scope_by_slot": {
                    "slot-a": ["netns:node-a:if-a", "link:isl-a"]
                },
                "run_window_resource_coverage_plan": {
                    "unresolved_count": 0,
                    "required_resource_union_count": 2,
                },
            }
            segments.append(segment)
            context_by_slice_by_segment[segment_id] = {f"slice-{index}": ctx}
            key = entry["segment_epoch_key"]
            context_by_segment_key[key] = ctx
            runtime_update_plan_by_key[key] = ctx["runtime_update_plan"]
            distribution_by_key[key] = ctx["distribution_batch"]
            snapshot_by_key[key] = ctx["snapshot"]
            host_plan_by_key[key] = {
                "schema_version": "skyseed-network-mutation-plan-v1",
                "mutation_id": f"mutation-{index}",
                "source_state_id": f"state-{index}",
                "operations": [],
            }
        return {
            "schema_version": "skyseed-normal-runtime-inputs-v1",
            "source": "prepared_update_bundle",
            "normal_runtime_thaw_adapter": {
                "adapter": "canonical_normal_runtime_inputs_v1"
            },
            "initial_segment": segments[0],
            "prepared_horizon_segments": segments,
            "prepared_horizon_segment_count": len(segments),
            "context_by_slice": context_by_slice_by_segment["H0"],
            "context_by_slice_by_segment": context_by_slice_by_segment,
            "context_by_segment_key": context_by_segment_key,
            "runtime_update_plan_by_segment_key": runtime_update_plan_by_key,
            "distribution_batch_by_segment_key": distribution_by_key,
            "topology_snapshot_by_segment_key": snapshot_by_key,
            "host_network_mutation_plan_by_segment_key": host_plan_by_key,
            "topology_path": "/tmp/topology.json",
            "link_state_path": "/tmp/link_state.json",
        }

    def test_frozen_bundle_verifies_id_hash_and_queue_ids(self) -> None:
        bundle = self._frozen_bundle()

        gate = _verify_frozen_prepared_bundle(bundle)

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["execution_source"], "prepared_update_bundle")
        self.assertEqual(gate["loaded_bundle_id"], bundle["bundle_id"])
        self.assertEqual(gate["loaded_bundle_hash"], bundle["bundle_hash"])
        self.assertFalse(gate["planner_recomputed"])
        self.assertTrue(gate["live_used_frozen_bundle"])
        self.assertEqual(gate["loaded_current_execution_ids"], ["runtime-1", "parameter-1"])
        self.assertEqual(gate["loaded_lookahead_preload_ids"], ["runtime-2"])
        self.assertEqual(gate["queue_accounting"]["selected_total"], 3)
        self.assertEqual(gate["queue_accounting"]["current_runtime_applicable"], 2)
        self.assertEqual(gate["queue_accounting"]["lookahead_preload_required"], 1)
        self.assertIn("executable_runtime_inputs", bundle)
        self.assertNotIn("executable_runtime_inputs", bundle["bundle_hash_payload"])
        self.assertIn(
            "executable_runtime_inputs_manifest",
            bundle["bundle_hash_payload"],
        )
        self.assertEqual(
            bundle["bundle_hash_payload"]["executable_runtime_inputs_manifest"][
                "executable_runtime_inputs_hash"
            ],
            bundle["executable_runtime_inputs"]["hashes"][
                "executable_runtime_inputs_hash"
            ],
        )
        self.assertIn(
            "executable_runtime_inputs_hash",
            bundle["executable_runtime_inputs"]["hashes"],
        )
        sliding = bundle["executable_runtime_inputs"]["sliding_window_contract"]
        self.assertEqual(sliding["window_model"], "rolling_horizon_window")
        self.assertFalse(sliding["test_horizon_count_hardcoded"])
        self.assertEqual(
            sliding["windows"]["current"]["horizon_id"],
            "prepared-bundle-current-horizon-0",
        )
        self.assertEqual(
            sliding["windows"]["next"]["horizon_id"],
            "prepared-bundle-current-horizon-0:next",
        )
        self.assertIn(
            "sliding_window_contract_hash",
            bundle["executable_runtime_inputs"]["hashes"],
        )

    def test_externalized_executable_runtime_inputs_keep_bundle_verifiable(self) -> None:
        normal_inputs = self._rolling_normal_runtime_inputs()
        prepared = {
            "selected_epoch_queue": [
                entry
                for segment in normal_inputs["prepared_horizon_segments"]
                for entry in segment["current_execution_queue"]
            ],
            "current_execution_queue": normal_inputs["prepared_horizon_segments"][0][
                "current_execution_queue"
            ],
            "lookahead_preload_queue": [],
            "precompose_horizon_queue_gate": {
                "preload_required_event_count": 0,
            },
            "prepared_artifacts": {},
        }
        bundle = _freeze_prepared_update_bundle(
            prepared_update_bundle=prepared,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
            prepared_resource_scope_by_slot={
                "slot-a": {"netns:node-a:if-a", "link:isl-a"}
            },
            precompose_bpf_role_union_gate={
                "required_interface_role_union": {
                    "netns:node-a:if-a": ["ingress_classifier"]
                },
            },
            normal_runtime_inputs=normal_inputs,
        )
        with tempfile.TemporaryDirectory() as tmp:
            compact_bundle = _prepared_bundle_with_externalized_executable_inputs(
                prepared_update_bundle=bundle,
                artifact_path=Path(tmp) / "executable_runtime_inputs.json",
            )

            self.assertNotIn("executable_runtime_inputs", compact_bundle)
            self.assertTrue(compact_bundle["executable_runtime_inputs_externalized"])
            executable_path = Path(compact_bundle["executable_runtime_inputs_json"])
            self.assertTrue(executable_path.exists())
            executable_text = executable_path.read_text(encoding="utf-8")
            self.assertTrue(executable_text.startswith('{"'))
            self.assertNotIn('\n  "', executable_text)
            self.assertNotIn('"normal_runtime_inputs":', executable_text)
            normal_path = Path(
                compact_bundle["prepared_artifacts"]["normal_runtime_inputs_json"]
            )
            self.assertTrue(normal_path.exists())
            self.assertIn('"normal_runtime_inputs_ref":', executable_text)

            gate = _verify_frozen_prepared_bundle(compact_bundle)
            self.assertTrue(gate["passed"])
            thawed = _thaw_normal_runtime_initial_segment(compact_bundle)
            self.assertEqual(thawed["horizon_id"], "H0")

    def test_normal_runtime_inputs_do_not_persist_derived_context_indexes(
        self,
    ) -> None:
        normal_inputs = self._rolling_normal_runtime_inputs()

        patched = _normal_runtime_inputs_with_bpf_generation_slots(
            normal_runtime_inputs=normal_inputs,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
        )

        for field in (
            "context_by_slice",
            "context_by_segment_key",
            "runtime_update_plan_by_segment_key",
            "distribution_batch_by_segment_key",
            "topology_snapshot_by_segment_key",
        ):
            self.assertNotIn(field, patched)
            self.assertNotIn(f"{field}_hash", patched.get("hashes", {}))
        self.assertIn("context_by_slice_by_segment", patched)
        self.assertIn("host_network_mutation_plan_by_segment_key", patched)
        h1_context = patched["context_by_slice_by_segment"]["H1"]["slice-1"]
        self.assertIn(
            "bpf_physical_generation_id",
            h1_context["runtime_update_plan"]["target_snapshot"],
        )

    def test_externalized_bundle_accepts_normal_inputs_without_derived_indexes(
        self,
    ) -> None:
        normal_inputs = self._rolling_normal_runtime_inputs()
        for field in (
            "context_by_slice",
            "context_by_segment_key",
            "runtime_update_plan_by_segment_key",
            "distribution_batch_by_segment_key",
            "topology_snapshot_by_segment_key",
        ):
            normal_inputs.pop(field, None)
        normal_inputs["hashes"] = {}
        prepared = {
            "selected_epoch_queue": [
                entry
                for segment in normal_inputs["prepared_horizon_segments"]
                for entry in segment["current_execution_queue"]
            ],
            "current_execution_queue": normal_inputs["prepared_horizon_segments"][0][
                "current_execution_queue"
            ],
            "lookahead_preload_queue": [],
            "precompose_horizon_queue_gate": {
                "preload_required_event_count": 0,
            },
            "prepared_artifacts": {},
        }
        bundle = _freeze_prepared_update_bundle(
            prepared_update_bundle=prepared,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
            prepared_resource_scope_by_slot={
                "slot-a": {"netns:node-a:if-a", "link:isl-a"}
            },
            precompose_bpf_role_union_gate={
                "required_interface_role_union": {
                    "netns:node-a:if-a": ["ingress_classifier"]
                },
            },
            normal_runtime_inputs=normal_inputs,
        )
        with tempfile.TemporaryDirectory() as tmp:
            compact_bundle = _prepared_bundle_with_externalized_executable_inputs(
                prepared_update_bundle=bundle,
                artifact_path=Path(tmp) / "executable_runtime_inputs.json",
            )
            normal_path = Path(
                compact_bundle["prepared_artifacts"]["normal_runtime_inputs_json"]
            )
            normal_text = normal_path.read_text(encoding="utf-8")

            for field in (
                "context_by_slice",
                "context_by_segment_key",
                "runtime_update_plan_by_segment_key",
                "distribution_batch_by_segment_key",
                "topology_snapshot_by_segment_key",
            ):
                self.assertNotIn(f'"{field}"', normal_text)
            self.assertTrue(_verify_frozen_prepared_bundle(compact_bundle)["passed"])
            thawed = _thaw_normal_runtime_initial_segment(compact_bundle)
            self.assertEqual(thawed["prepared_horizon_segment_count"], 3)
            source_gate = _bundle_backed_live_execution_source_gate(
                load_gate={
                    "passed": True,
                    "loaded_bundle_id": compact_bundle["bundle_id"],
                    "loaded_bundle_hash": compact_bundle["bundle_hash"],
                },
            )
            normal_gate = _frozen_bundle_normal_runtime_inputs_gate(
                prepared_update_bundle=compact_bundle,
                runtime_source_gate=source_gate,
            )
            self.assertTrue(normal_gate["passed"])


    def test_frozen_bundle_horizon_plan_uses_prepared_rolling_segments(self) -> None:
        normal_inputs = self._rolling_normal_runtime_inputs()
        prepared = {
            "selected_epoch_queue": [
                entry
                for segment in normal_inputs["prepared_horizon_segments"]
                for entry in segment["current_execution_queue"]
            ],
            "current_execution_queue": normal_inputs["prepared_horizon_segments"][0][
                "current_execution_queue"
            ],
            "lookahead_preload_queue": [],
            "precompose_horizon_queue_gate": {
                "preload_required_event_count": 0,
            },
            "prepared_artifacts": {},
        }
        bundle = _freeze_prepared_update_bundle(
            prepared_update_bundle=prepared,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
            prepared_resource_scope_by_slot={
                "slot-a": {"netns:node-a:if-a", "link:isl-a"}
            },
            precompose_bpf_role_union_gate={
                "required_interface_role_union": {
                    "netns:node-a:if-a": ["ingress_classifier"]
                },
            },
            normal_runtime_inputs=normal_inputs,
        )

        plan = bundle["executable_runtime_inputs"]["horizon_execution_plan"]
        self.assertEqual(plan["prepared_horizon_count"], 3)
        self.assertEqual(
            [horizon["horizon_id"] for horizon in plan["horizons"]],
            ["H0", "H1", "H2"],
        )
        self.assertFalse(plan["runtime_replenishment_preview_allowed"])
        self.assertFalse(plan["runtime_build_network_mutation_plan_allowed"])
        gate = _verify_frozen_prepared_bundle(
            bundle,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
        )
        self.assertTrue(gate["passed"])
        thawed = _thaw_normal_runtime_initial_segment(bundle)
        self.assertEqual(thawed["prepared_horizon_segment_count"], 3)
        self.assertEqual(
            [
                segment["current_execution_queue"][0]["segment_epoch_key"]
                for segment in thawed["prepared_horizon_segments"]
            ],
            ["H0:slice-0", "H1:slice-1", "H2:slice-2"],
        )

    def test_thaw_uses_initial_segment_scoped_contexts_for_repeated_slice_ids(
        self,
    ) -> None:
        normal_inputs = self._rolling_normal_runtime_inputs()
        h2_context = copy.deepcopy(
            normal_inputs["context_by_slice_by_segment"]["H2"]["slice-2"]
        )
        h2_context["snapshot"]["snapshot_id"] = "wrong-h2-context"
        normal_inputs["context_by_slice"] = {"slice-0": h2_context}
        prepared = {
            "selected_epoch_queue": [
                entry
                for segment in normal_inputs["prepared_horizon_segments"]
                for entry in segment["current_execution_queue"]
            ],
            "current_execution_queue": normal_inputs["prepared_horizon_segments"][0][
                "current_execution_queue"
            ],
            "lookahead_preload_queue": [],
            "precompose_horizon_queue_gate": {
                "preload_required_event_count": 0,
            },
            "prepared_artifacts": {},
        }
        bundle = _freeze_prepared_update_bundle(
            prepared_update_bundle=prepared,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
            prepared_resource_scope_by_slot={
                "slot-a": {"netns:node-a:if-a", "link:isl-a"}
            },
            precompose_bpf_role_union_gate={
                "required_interface_role_union": {
                    "netns:node-a:if-a": ["ingress_classifier"]
                },
            },
            normal_runtime_inputs=normal_inputs,
        )

        thawed = _thaw_normal_runtime_initial_segment(bundle)

        self.assertEqual(
            thawed["context_by_slice"]["slice-0"]["snapshot"].snapshot_id,
            "snap-0",
        )

    def test_thaw_fails_closed_when_scoped_initial_contexts_missing(self) -> None:
        normal_inputs = self._rolling_normal_runtime_inputs()
        del normal_inputs["context_by_slice_by_segment"]["H0"]
        prepared = {
            "selected_epoch_queue": [
                entry
                for segment in normal_inputs["prepared_horizon_segments"]
                for entry in segment["current_execution_queue"]
            ],
            "current_execution_queue": normal_inputs["prepared_horizon_segments"][0][
                "current_execution_queue"
            ],
            "lookahead_preload_queue": [],
            "precompose_horizon_queue_gate": {
                "preload_required_event_count": 0,
            },
            "prepared_artifacts": {},
        }
        bundle = _freeze_prepared_update_bundle(
            prepared_update_bundle=prepared,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
            prepared_resource_scope_by_slot={
                "slot-a": {"netns:node-a:if-a", "link:isl-a"}
            },
            precompose_bpf_role_union_gate={
                "required_interface_role_union": {
                    "netns:node-a:if-a": ["ingress_classifier"]
                },
            },
            normal_runtime_inputs=normal_inputs,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "frozen_bundle_missing_initial_segment_scoped_contexts:H0",
        ):
            _thaw_normal_runtime_initial_segment(bundle)

    def test_continuous_frozen_bundle_fails_when_prepared_horizons_missing(
        self,
    ) -> None:
        bundle = self._frozen_bundle()

        gate = _verify_frozen_prepared_bundle(
            bundle,
            args=self._args(
                continuous=True,
                horizon_seconds=720,
                sample_interval=10,
                replenish_threshold_seconds=120,
            ),
        )

        blockers = {blocker["blocker"] for blocker in gate["blockers"]}
        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_missing_prepared_rolling_horizons",
            blockers,
        )
        self.assertIn(
            "frozen_prepared_bundle_horizon_execution_plan_incomplete",
            blockers,
        )

    def test_bundle_backed_live_execution_source_gate_allows_normal_runtime(self) -> None:
        load_gate = _verify_frozen_prepared_bundle(self._frozen_bundle())

        source_gate = _bundle_backed_live_execution_source_gate(
            load_gate=load_gate,
        )

        self.assertTrue(source_gate["passed"])
        self.assertTrue(source_gate["live_used_frozen_bundle"])
        self.assertEqual(source_gate["execution_source"], "prepared_update_bundle")
        self.assertFalse(source_gate["planner_recomputed"])
        self.assertFalse(source_gate["actual_s4_runtime_proof"])
        self.assertFalse(source_gate["compose_started"])
        self.assertTrue(source_gate["compose_up_allowed"])

    def test_bundle_backed_runtime_source_binding_uses_bundle_for_all_sources(self) -> None:
        bundle = self._frozen_bundle()
        source_gate = _bundle_backed_live_execution_source_gate(
            load_gate=_verify_frozen_prepared_bundle(bundle),
        )

        runtime_gate = _bundle_backed_runtime_source_binding_gate(
            prepared_update_bundle=bundle,
            source_gate=source_gate,
        )

        self.assertTrue(runtime_gate["passed"])
        for key in (
            "execution_source",
            "runtime_execution_source",
            "resource_inventory_source",
            "bpf_plan_source",
            "role_union_source",
            "generation_plan_source",
            "preload_plan_source",
            "cleanup_scope_source",
        ):
            self.assertEqual(runtime_gate[key], "prepared_update_bundle")
        self.assertFalse(runtime_gate["runtime_helper_reached"])
        self.assertFalse(runtime_gate["planner_callsite_reached"])
        self.assertFalse(runtime_gate["runtime_planner_recomputed"])
        self.assertFalse(runtime_gate["compose_started"])

    def test_bundle_backed_runtime_source_binding_fails_closed_on_missing_inputs(self) -> None:
        bundle = self._frozen_bundle()
        bundle["current_execution_queue"] = []
        bundle["resource_refs"] = []
        bundle["bpf_required_interface_refs"] = []
        bundle["cleanup_owned_scope"] = {"resource_count": 0}
        source_gate = _bundle_backed_live_execution_source_gate(
            load_gate=_verify_frozen_prepared_bundle(bundle),
        )

        runtime_gate = _bundle_backed_runtime_source_binding_gate(
            prepared_update_bundle=bundle,
            source_gate=source_gate,
        )

        blockers = {blocker["blocker"] for blocker in runtime_gate["blockers"]}
        self.assertFalse(runtime_gate["passed"])
        self.assertIn("bundle_runtime_current_execution_queue_missing", blockers)
        self.assertIn("bundle_runtime_resource_inventory_missing", blockers)
        self.assertIn("bundle_runtime_bpf_required_refs_missing", blockers)
        self.assertIn("bundle_runtime_cleanup_owned_scope_missing", blockers)

    def test_minimal_helper_consumption_reaches_helpers_from_bundle(self) -> None:
        bundle = self._frozen_bundle()
        source_gate = _bundle_backed_runtime_source_binding_gate(
            prepared_update_bundle=bundle,
            source_gate=_bundle_backed_live_execution_source_gate(
                load_gate=_verify_frozen_prepared_bundle(bundle),
            ),
        )

        proof = _bundle_backed_minimal_helper_consumption_proof(
            prepared_update_bundle=bundle,
            runtime_source_gate=source_gate,
        )

        self.assertTrue(proof["passed"])
        self.assertTrue(proof["runtime_helper_reached"])
        self.assertTrue(proof["preload_helper_reached"])
        self.assertTrue(proof["generation_helper_reached"])
        self.assertEqual(proof["runtime_helper_input_source"], "prepared_update_bundle")
        self.assertEqual(proof["preload_helper_input_source"], "prepared_update_bundle")
        self.assertEqual(proof["generation_helper_input_source"], "prepared_update_bundle")
        self.assertFalse(proof["network_mutation_executed"])
        self.assertFalse(proof["runtime_started"])

    def test_minimal_helper_consumption_fails_on_authoritative_recompute_attempts(self) -> None:
        bundle = self._frozen_bundle()
        source_gate = _bundle_backed_runtime_source_binding_gate(
            prepared_update_bundle=bundle,
            source_gate=_bundle_backed_live_execution_source_gate(
                load_gate=_verify_frozen_prepared_bundle(bundle),
            ),
        )

        proof = _bundle_backed_minimal_helper_consumption_proof(
            prepared_update_bundle=bundle,
            runtime_source_gate=source_gate,
            helper_recompute_attempts={
                "queue_split": True,
                "resource_resolve": True,
                "role_union_build": True,
                "bpf_plan_build": True,
                "generation_plan_rebuild": True,
                "consume_diagnostic_recompute_result": True,
            },
        )

        blockers = {blocker["blocker"] for blocker in proof["blockers"]}
        self.assertFalse(proof["passed"])
        self.assertIn("helper_attempted_authoritative_queue_recompute", blockers)
        self.assertIn("helper_attempted_live_resource_resolution", blockers)
        self.assertIn("helper_attempted_authoritative_role_union_rebuild", blockers)
        self.assertIn("helper_attempted_authoritative_bpf_plan_rebuild", blockers)
        self.assertIn("helper_attempted_generation_preload_plan_rebuild", blockers)
        self.assertIn(
            "helper_attempted_to_consume_diagnostic_recompute_result",
            blockers,
        )














    def test_queue_accounting_non_mutually_exclusive_fails_before_compose(self) -> None:
        bundle = self._frozen_bundle()
        bundle["queue_accounting"] = {
            "mutually_exclusive": False,
            "selected_total": 3,
            "breakdown_sum": 4,
            "breakdown_matches_selected_total": False,
        }
        bundle["bundle_hash_payload"]["queue_accounting"] = bundle["queue_accounting"]

        gate = _verify_frozen_prepared_bundle(bundle)

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_queue_accounting_not_mutually_exclusive",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_hash_mismatch_fails_before_compose(self) -> None:
        bundle = self._frozen_bundle()
        bundle["bundle_hash_payload"]["lookahead_preload_ids"] = ["runtime-drifted"]

        gate = _verify_frozen_prepared_bundle(bundle)

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_hash_mismatch",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_queue_id_drift_fails_before_compose(self) -> None:
        bundle = self._frozen_bundle()
        bundle["current_execution_queue"][1]["event_id"] = "parameter-drifted"

        gate = _verify_frozen_prepared_bundle(bundle)

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_queue_id_drift",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_input_hash_mismatch_fails_before_compose(self) -> None:
        bundle = self._frozen_bundle()

        gate = _verify_frozen_prepared_bundle(
            bundle,
            args=self._args(max_satellites=48),
        )

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_input_profile_config_hash_mismatch",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_allows_continuous_runtime_orchestration_window(self) -> None:
        bundle = self._frozen_bundle()

        gate = _verify_frozen_prepared_bundle(
            bundle,
            args=self._args(
                horizon_seconds=360,
                continuous=True,
                replenish_threshold_seconds=60,
                loop_count=1,
            ),
        )

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_missing_prepared_rolling_horizons",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_continuous_runtime_still_fails_source_input_drift(self) -> None:
        bundle = self._frozen_bundle()

        gate = _verify_frozen_prepared_bundle(
            bundle,
            args=self._args(
                horizon_seconds=360,
                continuous=True,
                replenish_threshold_seconds=60,
                max_satellites=48,
            ),
        )

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_input_profile_config_hash_mismatch",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_unresolved_resource_refs_fail_before_compose(self) -> None:
        bundle = self._frozen_bundle()
        bundle["precompose_run_window_resource_coverage_plan"] = {
            "unresolved_count": 1,
            "unresolved_examples": ["slot-a:missing"],
        }

        gate = _verify_frozen_prepared_bundle(bundle)

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_resource_refs_unresolved",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_unresolved_bpf_refs_fail_before_compose(self) -> None:
        bundle = self._frozen_bundle()
        bundle["precompose_bpf_role_union_gate"] = {
            "unresolved_bpf_interface_binding_count": 1,
            "unresolved_bpf_interface_binding_examples": ["netns:node-a:if-a"],
        }

        gate = _verify_frozen_prepared_bundle(bundle)

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_bpf_required_interface_refs_unresolved",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_frozen_bundle_missing_generation_or_cleanup_fails_before_compose(self) -> None:
        bundle = self._frozen_bundle()
        bundle["generation_preload_plan"] = {}
        bundle["cleanup_owned_scope"] = {"resource_count": 0}

        gate = _verify_frozen_prepared_bundle(bundle)

        blockers = {blocker["blocker"] for blocker in gate["blockers"]}
        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_generation_preload_plan_missing",
            blockers,
        )
        self.assertIn("frozen_prepared_bundle_cleanup_owned_scope_missing", blockers)

    def test_frozen_bundle_full_current_horizon_allows_empty_lookahead_preload_plan(
        self,
    ) -> None:
        bundle = self._frozen_bundle()
        bundle["generation_preload_plan"] = {
            "lookahead_preload_ids": [],
            "preload_required_event_count": 0,
            "preload_reports": [],
            "preload_report_summary": {"success_count": 0},
        }

        gate = _verify_frozen_prepared_bundle(bundle)

        blockers = {blocker["blocker"] for blocker in gate["blockers"]}
        self.assertTrue(gate["passed"])
        self.assertNotIn(
            "frozen_prepared_bundle_generation_preload_plan_missing",
            blockers,
        )

    def test_runtime_source_binding_accepts_full_current_horizon_without_lookahead(
        self,
    ) -> None:
        bundle = self._frozen_bundle()
        bundle["lookahead_preload_queue"] = []
        bundle["generation_preload_plan"] = {
            "lookahead_preload_ids": [],
            "preload_required_event_count": 0,
        }
        bundle["precompose_horizon_queue_gate"] = {
            "passed": True,
            "queue_gate_mode": "full_current_horizon",
            "full_current_horizon_mode": True,
        }

        gate = _bundle_backed_runtime_source_binding_gate(
            prepared_update_bundle=bundle,
            source_gate={
                "passed": True,
                "blockers": [],
                "loaded_bundle_id": bundle["bundle_id"],
                "loaded_bundle_hash": bundle["bundle_hash"],
            },
        )

        blockers = {blocker["blocker"] for blocker in gate["blockers"]}
        self.assertTrue(gate["passed"])
        self.assertNotIn("bundle_runtime_lookahead_preload_queue_missing", blockers)

    def test_missing_frozen_bundle_fails_before_compose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            gate = _load_frozen_prepared_bundle_gate(
                bundle_path=Path(tmpdir) / "missing.json"
            )

        self.assertFalse(gate["passed"])
        self.assertIn(
            "frozen_prepared_bundle_missing",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_live_recompute_attempt_with_frozen_bundle_is_blocker(self) -> None:
        gate = _verify_frozen_prepared_bundle(
            self._frozen_bundle(),
            planner_recompute_attempted=True,
        )

        self.assertFalse(gate["passed"])
        self.assertIn(
            "planner_recompute_attempted_with_frozen_bundle",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_normal_live_source_gate_allows_bundle_then_normal_input_gate_blocks_missing_inputs(
        self,
    ) -> None:
        source_gate = _bundle_backed_live_execution_source_gate(
            load_gate=_verify_frozen_prepared_bundle(self._frozen_bundle()),
        )

        self.assertTrue(source_gate["passed"])
        self.assertTrue(source_gate["compose_up_allowed"])
        self.assertEqual(source_gate["execution_source"], "prepared_update_bundle")

        normal_gate = _frozen_bundle_normal_runtime_inputs_gate(
            prepared_update_bundle=self._frozen_bundle(),
            runtime_source_gate=source_gate,
        )

        self.assertFalse(normal_gate["passed"])
        self.assertFalse(normal_gate["compose_started"])
        self.assertFalse(normal_gate["compose_up_allowed"])
        self.assertIn(
            "frozen_bundle_missing_normal_runtime_inputs",
            {blocker["blocker"] for blocker in normal_gate["blockers"]},
        )
        self.assertIn(
            "executable_runtime_inputs.normal_runtime_inputs.initial_segment",
            normal_gate["missing_normal_runtime_inputs"],
        )
        self.assertIn(
            "_preview_summary_to_segment",
            normal_gate["unsafe_recompute_callsites_blocked"],
        )

    def test_normal_runtime_inputs_gate_passes_with_typed_thawable_inputs(self) -> None:
        bundle = self._frozen_bundle()
        snapshot_payload = {
            "schema_version": "satellite-topology-snapshot-v1alpha1",
            "snapshot_id": "snap-1",
            "time_slice": {"slice_id": "slice-1", "state_model": "full_state"},
            "provenance": {"source": "test"},
            "nodes": {},
            "active_links": [],
            "zones": {},
            "ut_zone_membership": {},
            "planner_hints": {},
        }
        runtime_plan_payload = {
            "schema_version": "satellite-runtime-update-plan-v1alpha1",
            "plan_id": "plan-1",
            "target_snapshot": {"snapshot_id": "snap-1"},
            "planner": {"planner_name": "test"},
            "apply_model": {
                "state_model": "full_state_intent",
                "action_model": "node_local_installer_action_v1",
            },
            "node_subplans": {},
            "replacement_semantics": {},
            "validation_expectations": {},
        }
        distribution_payload = {
            "schema_version": "satellite-runtime-distribution-store-v1alpha1",
            "distribution_id": "dist-1",
            "source_envelope_batch": {
                "schema_version": "satellite-agent-envelope-batch-v1alpha1",
                "envelope_batch_id": "batch-1",
            },
            "distributor": {"distributor_name": "test"},
            "store_contract": {
                "transport_binding": "transport_agnostic",
                "dry_run_mode": True,
            },
            "node_delivery_views": {},
            "validation_expectations": {},
        }
        context_payload = {
            "snapshot": snapshot_payload,
            "runtime_update_plan": runtime_plan_payload,
            "distribution_batch": distribution_payload,
            "slice_report": {},
        }
        bundle["executable_runtime_inputs"]["normal_runtime_inputs"] = {
            "initial_segment": {
                "segment_id": "segment-1",
                "current_execution_queue": [
                    {"segment_epoch_key": "segment-1:slice-1", "slice_id": "slice-1"}
                ],
            },
            "context_by_slice": {"slice-1": context_payload},
            "runtime_update_plan_by_segment_key": {
                "segment-1:slice-1": runtime_plan_payload
            },
            "distribution_batch_by_segment_key": {
                "segment-1:slice-1": distribution_payload
            },
            "topology_snapshot_by_segment_key": {
                "segment-1:slice-1": snapshot_payload
            },
            "host_network_mutation_plan_by_segment_key": {
                "segment-1:slice-1": {"mutation_id": "mut-1"}
            },
        }
        bundle["executable_runtime_inputs"]["normal_runtime_thaw_adapter"] = {
            "adapter": "canonical_normal_runtime_inputs_v1"
        }
        source_gate = _bundle_backed_live_execution_source_gate(
            load_gate={
                "passed": True,
                "loaded_bundle_id": bundle["bundle_id"],
                "loaded_bundle_hash": bundle["bundle_hash"],
            },
        )

        normal_gate = _frozen_bundle_normal_runtime_inputs_gate(
            prepared_update_bundle=bundle,
            runtime_source_gate=source_gate,
        )

        self.assertTrue(normal_gate["passed"])
        self.assertTrue(normal_gate["compose_up_allowed"])
        self.assertEqual(
            normal_gate["runtime_execution_source"],
            "prepared_update_bundle",
        )
        self.assertFalse(normal_gate["planner_recomputed"])

    def test_replay_recompute_used_as_proof_is_invalid(self) -> None:
        gate = _verify_frozen_prepared_bundle(
            self._frozen_bundle(),
            replay_recompute_used_as_proof=True,
        )

        self.assertFalse(gate["passed"])
        self.assertIn(
            "artifact_replay_recompute_used_as_proof_invalid",
            {blocker["blocker"] for blocker in gate["blockers"]},
        )

    def test_no_compose_load_path_reads_same_bundle_id_hash(self) -> None:
        bundle = self._frozen_bundle()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "frozen_prepared_update_bundle.json"
            path.write_text(json.dumps(bundle), encoding="utf-8")

            gate = _load_frozen_prepared_bundle_gate(bundle_path=path)

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["loaded_bundle_id"], bundle["bundle_id"])
        self.assertEqual(gate["loaded_bundle_hash"], bundle["bundle_hash"])
        self.assertFalse(gate["planner_recomputed"])

    def test_no_compose_load_path_blocks_contaminated_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "frozen_prepared_update_bundle.json"
            path.write_text("{}", encoding="utf-8")
            (Path(tmpdir) / "CONTAMINATED_DO_NOT_REUSE.json").write_text(
                json.dumps({"reason": "hardlink_pollution"}),
                encoding="utf-8",
            )

            gate = _load_frozen_prepared_bundle_gate(bundle_path=path)

        self.assertFalse(gate["passed"])
        self.assertEqual(
            gate["blockers"][0]["blocker"],
            "frozen_prepared_bundle_marked_contaminated",
        )

    def test_variant_start_time_profile_like_fixture_passes_same_contract(self) -> None:
        bundle = self._frozen_bundle(
            start_time="2026-06-21T00:05:00Z",
            max_runtime_epochs=5,
        )

        gate = _verify_frozen_prepared_bundle(
            bundle,
            args=self._args(
                start_time="2026-06-21T00:05:00Z",
                max_runtime_epochs=5,
            ),
        )

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["loaded_bundle_id"], bundle["bundle_id"])


if __name__ == "__main__":
    unittest.main()
