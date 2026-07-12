"""Resolve one human-authored experiment into the runner's effective profile."""

from __future__ import annotations

from typing import Any

from .models import ExperimentConfig


def resolve_profile(config: ExperimentConfig) -> dict[str, Any]:
    issues = config.validate()
    if issues:
        raise ValueError("invalid experiment configuration: " + ",".join(issues))
    shell = config.constellation
    ground = config.ground
    runtime = config.runtime
    satellite_count = shell.satellite_count
    expected_containers = satellite_count + (2 * len(ground.ut_ids)) + len(
        ground.gateway_ids
    )
    expected_batches = (expected_containers + 4) // 5
    dummy_components = {
        "satellite_runtime_management_networks": 1,
        "gateway_bootstrap_networks": len(ground.gateway_ids),
        "same_plane_isl_slot_networks": satellite_count,
        "cross_plane_isl_slot_networks": shell.cross_plane_slot_count,
        "ut_access_slot_networks": 2 * len(ground.ut_ids),
        "gateway_access_slot_networks": 2 * len(ground.gateway_ids),
    }
    required_dummy_networks = sum(dummy_components.values())
    pass_label = f"RICH_{shell.shell_id}_OPERATIONAL_CLEAN_PASS"
    failure_label = (
        f"RICH_{shell.shell_id}_OPERATIONAL_CLEAN_FAILED_FIRST_FAILURE_RECORDED"
    )
    runner_args = {
        "run_mode": "real-runtime",
        "orbit_input_mode": "planned-shell",
        "planned_shell_id": shell.shell_id,
        "max_satellites": satellite_count,
        "orbit_data": "data/orbits/planned_shell_orbit_data_s1_s5.json",
        "ut_catalog": ground.ut_catalog,
        "ut_gateway_policy_catalog": ground.ut_gateway_policy_catalog,
        "gateway_catalog": ground.gateway_catalog,
        "gateway_binding_catalog": ground.gateway_binding_catalog,
        "zone_catalog": ground.zone_catalog,
        "max_uts": len(ground.ut_ids),
        "max_gateways": len(ground.gateway_ids),
        "ground_placement": "catalog",
        "start_time": config.start_time_utc,
        "sample_interval": runtime.sample_interval_seconds,
        "event_evaluation_interval": runtime.event_interval_seconds,
        "horizon_seconds": runtime.horizon_seconds,
        "visualization_link_start_timestamp": config.start_time_utc,
        "visualization_link_interval": f"{runtime.sample_interval_seconds}s",
        "visualization_link_duration_seconds": runtime.duration_seconds,
        "runtime_mode": runtime.runtime_mode,
        "continuous": True,
        "loop_count": runtime.horizon_count,
        "interval": 0,
        "pre_transition_probe_interval": 0,
        "runtime_epoch_validation_interval": 0,
        "parameter_epoch_validation_interval": 0,
        "runtime_epoch_validation_scope": "reapplied_gateway_class_sample",
        "preflight_policy": "require-clean",
        "host_prepare_policy": "auto",
        "forwarding_backend": runtime.forwarding_backend,
        "bpftool_path": "/usr/lib/linux-tools/5.4.0-216-generic/bpftool",
        "bpf_backend": "off",
        "compose_build_policy": "if-needed",
        "compose_up_timeout_seconds": 0,
        "startup_high_progress_grace_ratio": 0.95,
        "startup_high_progress_grace_seconds": 420,
        "startup_slow_progress_grace_seconds": 180,
        "startup_absolute_max_seconds": 0,
        "compose_build_timeout_seconds": 0,
        "container_readiness_timeout_seconds": 600,
        "initial_runtime_readiness_timeout_seconds": 300,
        "progress_heartbeat_seconds": 15,
        "runtime_ping_observer_seconds": 30,
        "startup_stall_warning_seconds": 0,
        "epoch_stall_warning_seconds": 300,
        "docker_dummy_network_pool": "10.128.0.0/9",
        "docker_dummy_network_mask": 19,
        "format": "json",
    }
    return {
        "name": config.profile_name,
        "description": config.description,
        "runner": "starlink_shell_experiment",
        "planned_shell": shell.shell_id,
        "max_satellites": satellite_count,
        "catalog_contract": {"required": True},
        "validation_selector": {
            "ut": {"mode": "explicit", "ids": list(ground.ut_ids)},
            "gateway": {"mode": "explicit", "ids": list(ground.gateway_ids)},
            "zone": {"mode": "explicit", "ids": list(ground.zone_ids)},
        },
        "ground_qualification": {
            "start_time_utc": ground.qualification_start_time_utc,
            "duration_seconds": ground.qualification_duration_seconds,
            "experiment_duration_seconds": runtime.duration_seconds,
            "within_qualified_window": (
                ground.qualification_duration_seconds is None
                or runtime.duration_seconds <= ground.qualification_duration_seconds
            ),
            "periodicity_assumed": False,
        },
        "runner_args": runner_args,
        "docker_dummy_network_capacity": {
            "required_dummy_networks": required_dummy_networks,
            "minimum_addresses_per_subnet": 512,
            "formula": "available_subnets = 2^(docker_dummy_network_mask - pool_prefix_length) when docker_dummy_network_mask >= pool_prefix_length",
            "components": dummy_components,
        },
        "startup_materialization": {
            "contract_metadata": {
                "owner_module": "skyseed_v1.configuration.resolver",
                "scope": "resolved-example-specific",
                "lifetime": "full operational validation guard",
                "failure_action": "hard fail for stage failure, compose/docker failure, resource deadlock, or explicit no-progress evidence; total startup elapsed wall time is recorded but is not a gate",
                "not_ad_hoc_reason": "Cardinality is derived from the selected shell, ground component, and frozen physical-link capacity; gating is based on failure or progress evidence rather than a fixed total wall clock.",
                "focused_test": "skyseed_v1/tests/contracts/test_experiment_configuration.py",
            },
            "required": True,
            "expected_containers": expected_containers,
            "expected_docker_networks": len(ground.gateway_ids) + 1,
            "expected_direct_backend_host_interfaces_before_runtime": 0,
            "expected_dummy_networks": required_dummy_networks,
            "expected_compose_stage_batches": expected_batches,
            "expected_qdisc_root_objects": None,
            "expected_qdisc_filter_objects": None,
            "compose_up_timeout_seconds": 0,
            "startup_high_progress_grace_ratio": 0.95,
            "startup_high_progress_grace_seconds": 420,
            "startup_slow_progress_grace_seconds": 180,
            "startup_absolute_max_seconds": 0,
            "timeout_budget_justification": f"{shell.shell_id} resolves to {expected_containers} compose containers and {required_dummy_networks} dummy-network-backed topology networks, including {shell.cross_plane_slot_count} frozen cross-plane candidate slots. Total startup time is evidence, not a fixed kill gate.",
            "stall_no_progress_seconds": 120,
            "slow_progress_min_completed_ratio": 0.5,
            "outcome_labels": [],
        },
        "normal_failure_cleanup": {
            "enabled": True,
            "must_use_external_residue_detector": True,
            "success_label": "CLEANUP_BOUNDARY_PASS",
            "failure_label": "CLEANUP_BOUNDARY_STILL_INCOMPLETE",
        },
        "allowed_capabilities": {"compose": True, "runtime": True, "bpf": True},
        "runner_boolean_flags": {"require_visualization_links": True},
        "visualization_physical_links": {
            "required": True,
            "visibility_model": "visualization_frozen_physical_links_v1",
            "gateway_binding_catalog": ground.gateway_binding_catalog,
            "planned_orbit_data": runner_args["orbit_data"],
            "gateway_catalog": ground.gateway_catalog,
            "start_timestamp": config.start_time_utc,
            "interval": f"{runtime.sample_interval_seconds}s",
            "duration_seconds": runtime.duration_seconds,
            "selected_gateway_count": len(ground.gateway_ids),
        },
        "pass_outcome_label": pass_label,
        "failed_first_failure_outcome_label": failure_label,
        "expected_outcome_labels": [
            pass_label,
            "FIRST_FAILURE_EVIDENCE_PRESERVED",
            "CLEANUP_BOUNDARY_PASS",
            failure_label,
        ],
        "forbidden_behavior": {
            "strict_bpf_owned_no_canary_experimental": True,
            "linux_gz_mpls_lfib_fallback": True,
            "canary_experimental_pass_evidence": True,
            "full_attach_fallback_after_map_reload_failure": True,
            "dirty_recovery_reset_as_reuse": True,
        },
        "cleanup_requirements": {
            "direct_backend_host_interfaces": 0,
            "bpf_pins": 0,
            "runtime_locks": 0,
            "compose_containers": 0,
            "compose_network_residue": 0,
            "project_output_changed": False,
        },
        "cost_sanity": {
            "startup_materialization_seconds_warn": 0,
            "network_update_seconds_warn": 300,
            "post_apply_overhead_seconds_warn": 120,
            "cleanup_seconds_warn": 180,
            "unknown_blocking_seconds_warn": 60,
            "command_cardinality_warn": 200000,
            "anomaly_labels": ["COST_SANITY_BLOCKER", "UNKNOWN_BLOCKING_ANOMALY"],
        },
        "long_run_contract": {
            "required": True,
            "profile_kind": "full_horizon_boundary_validation",
            "default_horizon_count": runtime.horizon_count,
            "minimum_horizon_count": 2,
            "horizon_seconds": runtime.horizon_seconds,
            "forbid_max_runtime_epochs": True,
            "forbid_max_parameter_epochs": True,
            "forbid_max_epochs": True,
            "must_exercise_horizon_boundaries": True,
            "must_exercise_process_worker_handoff": True,
        },
    }
