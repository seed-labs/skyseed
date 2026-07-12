#!/usr/bin/env python3
# encoding: utf-8

"""Temporal planning helpers for dense Starlink shell sampling."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from skyseed_v1.core.ir.contracts import RuntimeUpdatePlan, TopologySnapshot
from skyseed_v1.planning.mutations import (
    build_network_mutation_plan,
    dynamic_slot_state_from_link_state,
    dynamic_slot_state_from_snapshot,
    make_runtime_parameter_mutation_policy,
)
from skyseed_v1.topology.shell import PropagatedOrbitSlice


SPEED_OF_LIGHT_KM_PER_MS = 299.792458
DEFAULT_ROUTING_COST_DELTA_THRESHOLD = 5
DEFAULT_RUNTIME_PARAMETER_MODEL_NAME = "distance_elevation_runtime_parameter_model_v1"
DEFAULT_BPF_GENERATION_PRELOAD_SUPPORT = "current_generation_only"
DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS = 1.0
REFINABLE_RUNTIME_EVENT_REASONS = (
    "coverage_invalidation",
    "ut_serving_satellite_change",
    "zone_active_serving_satellite_change",
    "gw_selection_change",
    "structural_link_change",
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _parse_utc_timestamp(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc_timestamp(value: datetime) -> str:
    rendered = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    rendered = rendered.replace("+00:00", "Z")
    if rendered.endswith(".000Z"):
        return rendered.replace(".000Z", "Z")
    return rendered


def _interpolated_utc_timestamp(
    *,
    previous_time_utc: str,
    current_time_utc: str,
    previous_relative_time: float,
    current_relative_time: float,
    target_relative_time: float,
) -> str:
    previous_dt = _parse_utc_timestamp(previous_time_utc)
    current_dt = _parse_utc_timestamp(current_time_utc)
    if previous_dt is None or current_dt is None:
        return str(current_time_utc)
    span = float(current_relative_time) - float(previous_relative_time)
    if span <= 0.0:
        return str(current_time_utc)
    fraction = (
        float(target_relative_time) - float(previous_relative_time)
    ) / span
    fraction = min(1.0, max(0.0, fraction))
    return _format_utc_timestamp(previous_dt + (current_dt - previous_dt) * fraction)


def _activation_group_id(relative_time_seconds: float) -> str:
    return f"activation-{float(relative_time_seconds):012.3f}"


def _refinable_runtime_reason(trigger_reasons: Sequence[str]) -> Optional[str]:
    reasons = {str(reason) for reason in trigger_reasons}
    for reason in REFINABLE_RUNTIME_EVENT_REASONS:
        if reason in reasons:
            return reason
    return None


def _bounded_bisection_refined_relative_time(
    *,
    previous_relative_time: float,
    current_relative_time: float,
    precision_seconds: float,
    transition_fraction: float = 0.5,
) -> Tuple[float, Dict[str, Any]]:
    start = float(previous_relative_time)
    end = float(current_relative_time)
    if end <= start:
        return end, {
            "refinement_iteration_count": 0,
            "refinement_search_lower_relative_time_seconds": start,
            "refinement_search_upper_relative_time_seconds": end,
            "refinement_target_fraction": float(transition_fraction),
        }
    precision = max(0.001, float(precision_seconds))
    target_fraction = min(1.0, max(0.0, float(transition_fraction)))
    target_time = start + ((end - start) * target_fraction)
    lower = start
    upper = end
    iteration_count = 0
    max_iterations = max(1, int(math.ceil(math.log2(max((end - start) / precision, 1.0)))) + 1)
    while (upper - lower) > precision and iteration_count < max_iterations:
        midpoint = lower + ((upper - lower) / 2.0)
        iteration_count += 1
        if midpoint < target_time:
            lower = midpoint
        else:
            upper = midpoint
    refined_time = lower + ((upper - lower) / 2.0)
    quantized = round(refined_time / precision) * precision
    refined_time = min(end, max(start, quantized))
    return refined_time, {
        "refinement_iteration_count": iteration_count,
        "refinement_search_lower_relative_time_seconds": float(lower),
        "refinement_search_upper_relative_time_seconds": float(upper),
        "refinement_target_fraction": float(target_fraction),
    }


def _sample_aligned_refinement_metadata(
    *,
    sample: OrbitSample,
    status: str,
    reason: str,
    precision_seconds: float,
    supported: bool = False,
    previous_sample: Optional[OrbitSample] = None,
) -> Dict[str, Any]:
    previous_relative_time = (
        float(previous_sample.relative_time_seconds) if previous_sample is not None else None
    )
    previous_time_utc = (
        str(previous_sample.absolute_time_utc) if previous_sample is not None else None
    )
    return {
        "event_time_source": "physical_sample_boundary",
        "event_time_accuracy": "sample_aligned",
        "refinement_status": str(status),
        "refinement_method": "sample_boundary_fallback",
        "refinement_reason": str(reason),
        "refinement_supported": bool(supported),
        "sample_aligned_relative_time_seconds": float(sample.relative_time_seconds),
        "sample_aligned_time_utc": str(sample.absolute_time_utc),
        "refinement_precision_seconds": float(precision_seconds),
        "candidate_interval_start_relative_time_seconds": previous_relative_time,
        "candidate_interval_end_relative_time_seconds": float(sample.relative_time_seconds),
        "candidate_interval_start_time_utc": previous_time_utc,
        "candidate_interval_end_time_utc": str(sample.absolute_time_utc),
        "refinement_iteration_count": 0,
        "refinement_search_lower_relative_time_seconds": previous_relative_time,
        "refinement_search_upper_relative_time_seconds": float(sample.relative_time_seconds),
        "refinement_target_fraction": None,
    }


def _runtime_event_refinement_metadata(
    *,
    previous_sample: Optional[OrbitSample],
    current_sample: OrbitSample,
    trigger_reasons: Sequence[str],
    precision_seconds: float,
) -> Dict[str, Any]:
    if previous_sample is None:
        return _sample_aligned_refinement_metadata(
            sample=current_sample,
            status="not_applicable",
            reason="initial_materialization",
            precision_seconds=precision_seconds,
        )
    supporting_reason = _refinable_runtime_reason(trigger_reasons)
    if supporting_reason is None:
        primary_reason = str(trigger_reasons[0]) if trigger_reasons else "unknown_runtime_reason"
        return _sample_aligned_refinement_metadata(
            sample=current_sample,
            previous_sample=previous_sample,
            status="unsupported",
            reason=f"unsupported_runtime_reason:{primary_reason}",
            precision_seconds=precision_seconds,
        )
    start = float(previous_sample.relative_time_seconds)
    end = float(current_sample.relative_time_seconds)
    if end <= start:
        return _sample_aligned_refinement_metadata(
            sample=current_sample,
            previous_sample=previous_sample,
            status="refinement_failed",
            reason="non_positive_candidate_interval",
            precision_seconds=precision_seconds,
            supported=True,
        )
    refined_relative_time, search_metadata = _bounded_bisection_refined_relative_time(
        previous_relative_time=start,
        current_relative_time=end,
        precision_seconds=precision_seconds,
    )
    refined_time_utc = _interpolated_utc_timestamp(
        previous_time_utc=str(previous_sample.absolute_time_utc),
        current_time_utc=str(current_sample.absolute_time_utc),
        previous_relative_time=start,
        current_relative_time=end,
        target_relative_time=refined_relative_time,
    )
    return {
        "event_time_source": "event_timeline_refinement",
        "event_time_accuracy": "refined_bounded_interval_estimate",
        "refinement_status": "refined",
        "refinement_method": "bounded_local_bisection",
        "refinement_reason": supporting_reason,
        "refinement_supported": True,
        "sample_aligned_relative_time_seconds": float(current_sample.relative_time_seconds),
        "sample_aligned_time_utc": str(current_sample.absolute_time_utc),
        "refinement_precision_seconds": float(precision_seconds),
        "candidate_interval_start_relative_time_seconds": start,
        "candidate_interval_end_relative_time_seconds": end,
        "candidate_interval_start_time_utc": str(previous_sample.absolute_time_utc),
        "candidate_interval_end_time_utc": str(current_sample.absolute_time_utc),
        "refined_relative_time_seconds": float(refined_relative_time),
        "refined_time_utc": refined_time_utc,
        **search_metadata,
    }


def _parameter_event_refinement_metadata(
    *,
    previous_sample: Optional[OrbitSample],
    current_sample: OrbitSample,
    precision_seconds: float,
) -> Dict[str, Any]:
    return _sample_aligned_refinement_metadata(
        sample=current_sample,
        previous_sample=previous_sample,
        status="unsupported",
        reason="parameter_threshold_batch_discrete_update",
        precision_seconds=precision_seconds,
    )


def _refinement_counter_summary(
    epochs: Sequence[RuntimeEpoch | ParameterEpoch],
) -> Dict[str, Any]:
    refined_event_count = 0
    sample_aligned_event_count = 0
    supported_event_count = 0
    failed_event_count = 0
    unsupported_event_count = 0
    reason_breakdown: Dict[str, int] = {}
    for epoch in epochs:
        status = str(getattr(epoch, "refinement_status", "not_recorded"))
        reason = str(getattr(epoch, "refinement_reason", "not_recorded"))
        reason_breakdown[reason] = int(reason_breakdown.get(reason, 0)) + 1
        if bool(getattr(epoch, "refinement_supported", False)):
            supported_event_count += 1
        if str(getattr(epoch, "event_time_accuracy", "")) == "refined_bounded_interval_estimate":
            refined_event_count += 1
        else:
            sample_aligned_event_count += 1
        if status == "refinement_failed":
            failed_event_count += 1
        elif status == "unsupported":
            unsupported_event_count += 1
    return {
        "refined_event_count": refined_event_count,
        "sample_aligned_event_count": sample_aligned_event_count,
        "refinement_supported_event_count": supported_event_count,
        "refinement_failed_event_count": failed_event_count,
        "refinement_unsupported_event_count": unsupported_event_count,
        "refinement_reason_breakdown": dict(sorted(reason_breakdown.items())),
    }


def _epoch_refinement_metadata(epoch: RuntimeEpoch | ParameterEpoch) -> Dict[str, Any]:
    return {
        "event_time_source": str(epoch.event_time_source),
        "event_time_accuracy": str(epoch.event_time_accuracy),
        "refinement_status": str(epoch.refinement_status),
        "refinement_method": str(epoch.refinement_method),
        "refinement_reason": str(epoch.refinement_reason),
        "refinement_supported": bool(epoch.refinement_supported),
        "sample_aligned_relative_time_seconds": float(
            epoch.sample_aligned_relative_time_seconds
        ),
        "sample_aligned_time_utc": str(epoch.sample_aligned_time_utc),
        "refinement_precision_seconds": float(epoch.refinement_precision_seconds),
        "candidate_interval_start_relative_time_seconds": (
            None
            if epoch.candidate_interval_start_relative_time_seconds is None
            else float(epoch.candidate_interval_start_relative_time_seconds)
        ),
        "candidate_interval_end_relative_time_seconds": (
            None
            if epoch.candidate_interval_end_relative_time_seconds is None
            else float(epoch.candidate_interval_end_relative_time_seconds)
        ),
        "candidate_interval_start_time_utc": epoch.candidate_interval_start_time_utc,
        "candidate_interval_end_time_utc": epoch.candidate_interval_end_time_utc,
        "refinement_iteration_count": int(epoch.refinement_iteration_count),
        "refinement_search_lower_relative_time_seconds": (
            None
            if epoch.refinement_search_lower_relative_time_seconds is None
            else float(epoch.refinement_search_lower_relative_time_seconds)
        ),
        "refinement_search_upper_relative_time_seconds": (
            None
            if epoch.refinement_search_upper_relative_time_seconds is None
            else float(epoch.refinement_search_upper_relative_time_seconds)
        ),
        "refinement_target_fraction": (
            None
            if epoch.refinement_target_fraction is None
            else float(epoch.refinement_target_fraction)
        ),
    }


def _epoch_refinement_init_fields(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "event_time_source": str(metadata.get("event_time_source", "physical_sample_boundary")),
        "event_time_accuracy": str(metadata.get("event_time_accuracy", "sample_aligned")),
        "refinement_status": str(metadata.get("refinement_status", "not_recorded")),
        "refinement_method": str(metadata.get("refinement_method", "not_recorded")),
        "refinement_reason": str(metadata.get("refinement_reason", "not_recorded")),
        "refinement_supported": bool(metadata.get("refinement_supported", False)),
        "sample_aligned_relative_time_seconds": float(
            metadata.get("sample_aligned_relative_time_seconds", 0.0)
        ),
        "sample_aligned_time_utc": str(metadata.get("sample_aligned_time_utc", "")),
        "refinement_precision_seconds": float(
            metadata.get(
                "refinement_precision_seconds",
                DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS,
            )
        ),
        "candidate_interval_start_relative_time_seconds": metadata.get(
            "candidate_interval_start_relative_time_seconds"
        ),
        "candidate_interval_end_relative_time_seconds": metadata.get(
            "candidate_interval_end_relative_time_seconds"
        ),
        "candidate_interval_start_time_utc": metadata.get("candidate_interval_start_time_utc"),
        "candidate_interval_end_time_utc": metadata.get("candidate_interval_end_time_utc"),
        "refinement_iteration_count": int(metadata.get("refinement_iteration_count", 0) or 0),
        "refinement_search_lower_relative_time_seconds": metadata.get(
            "refinement_search_lower_relative_time_seconds"
        ),
        "refinement_search_upper_relative_time_seconds": metadata.get(
            "refinement_search_upper_relative_time_seconds"
        ),
        "refinement_target_fraction": metadata.get("refinement_target_fraction"),
    }


def _activation_from_refinement(metadata: Mapping[str, Any]) -> Tuple[float, str]:
    relative_time = metadata.get(
        "refined_relative_time_seconds",
        metadata.get("sample_aligned_relative_time_seconds", 0.0),
    )
    time_utc = metadata.get(
        "refined_time_utc",
        metadata.get("sample_aligned_time_utc", ""),
    )
    return float(relative_time), str(time_utc)


def _runtime_plan_dict(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(runtime_update_plan, dict):
        return dict(runtime_update_plan)
    return {
        "schema_version": runtime_update_plan.schema_version,
        "plan_id": runtime_update_plan.plan_id,
        "target_snapshot": dict(runtime_update_plan.target_snapshot),
        "planner": dict(runtime_update_plan.planner),
        "apply_model": dict(runtime_update_plan.apply_model),
        "node_subplans": {
            str(node_id): dict(subplan)
            for node_id, subplan in runtime_update_plan.node_subplans.items()
        },
        "replacement_semantics": dict(runtime_update_plan.replacement_semantics),
        "validation_expectations": dict(
            runtime_update_plan.validation_expectations
        ),
    }


def _node_position(snapshot: TopologySnapshot, node_id: str) -> Optional[Tuple[float, float, float]]:
    node = snapshot.nodes.get(str(node_id))
    if not isinstance(node, dict):
        return None
    position_state = dict(node.get("position_state", {}))
    if not position_state:
        return None
    return (
        float(position_state.get("x_km", 0.0)),
        float(position_state.get("y_km", 0.0)),
        float(position_state.get("z_km", 0.0)),
    )


def _distance_km(first: Tuple[float, float, float], second: Tuple[float, float, float]) -> float:
    return math.sqrt(
        (first[0] - second[0]) ** 2
        + (first[1] - second[1]) ** 2
        + (first[2] - second[2]) ** 2
    )


def _link_distance_km(snapshot: TopologySnapshot, link: Dict[str, Any]) -> Optional[float]:
    endpoints = [dict(endpoint) for endpoint in link.get("endpoints", [])]
    if len(endpoints) < 2:
        return None
    first_position = _node_position(snapshot, str(endpoints[0].get("node_id")))
    second_position = _node_position(snapshot, str(endpoints[1].get("node_id")))
    if first_position is None or second_position is None:
        return None
    return _distance_km(first_position, second_position)


class LinkRuntimeParameterModel(Protocol):
    model_name: str

    def derive_parameters(
        self,
        *,
        snapshot: TopologySnapshot,
        link: Dict[str, Any],
    ) -> Dict[str, Any]:
        ...


@dataclass(frozen=True)
class DistanceElevationRuntimeParameterModel:
    model_name: str = DEFAULT_RUNTIME_PARAMETER_MODEL_NAME
    speed_of_light_km_per_ms: float = SPEED_OF_LIGHT_KM_PER_MS
    routing_cost_scale: float = 100.0
    access_min_bandwidth_mbps: float = 120.0
    access_base_bandwidth_mbps: float = 220.0
    access_elevation_bandwidth_gain_mbps: float = 780.0
    access_min_loss_percent: float = 0.005
    access_elevation_loss_span_percent: float = 0.18
    same_plane_min_bandwidth_mbps: float = 900.0
    same_plane_base_bandwidth_mbps: float = 2200.0
    same_plane_bandwidth_distance_penalty: float = 0.08
    same_plane_base_loss_percent: float = 0.004
    same_plane_loss_distance_start_km: float = 900.0
    same_plane_loss_distance_slope: float = 0.00001
    same_plane_max_loss_percent: float = 0.08
    cross_plane_min_bandwidth_mbps: float = 650.0
    cross_plane_base_bandwidth_mbps: float = 1800.0
    cross_plane_bandwidth_distance_penalty: float = 0.09
    cross_plane_base_loss_percent: float = 0.008
    cross_plane_loss_distance_start_km: float = 1100.0
    cross_plane_loss_distance_slope: float = 0.000015
    cross_plane_max_loss_percent: float = 0.15

    def _routing_cost(self, delay_ms: float) -> int:
        return int(round(delay_ms * self.routing_cost_scale))

    def _access_link_parameters(
        self,
        *,
        distance_km: float,
        elevation_deg: float,
    ) -> Dict[str, Any]:
        delay_ms = distance_km / self.speed_of_light_km_per_ms
        normalized_elevation = max(0.0, min(float(elevation_deg), 90.0)) / 90.0
        bandwidth_mbps = max(
            self.access_min_bandwidth_mbps,
            self.access_base_bandwidth_mbps
            + (normalized_elevation * self.access_elevation_bandwidth_gain_mbps),
        )
        loss_percent = max(
            self.access_min_loss_percent,
            (1.0 - normalized_elevation) * self.access_elevation_loss_span_percent,
        )
        return {
            "delay_ms": round(delay_ms, 6),
            "loss_percent": round(loss_percent, 6),
            "bandwidth_mbps": round(bandwidth_mbps, 6),
            "routing_cost": self._routing_cost(delay_ms),
        }

    def _isl_link_parameters(
        self,
        *,
        distance_km: float,
        slot_type: str,
    ) -> Dict[str, Any]:
        delay_ms = distance_km / self.speed_of_light_km_per_ms
        if slot_type in {"same_plane_isl", "fixed_isl"}:
            bandwidth_mbps = max(
                self.same_plane_min_bandwidth_mbps,
                self.same_plane_base_bandwidth_mbps
                - (distance_km * self.same_plane_bandwidth_distance_penalty),
            )
            loss_percent = min(
                self.same_plane_max_loss_percent,
                self.same_plane_base_loss_percent
                + max(0.0, distance_km - self.same_plane_loss_distance_start_km)
                * self.same_plane_loss_distance_slope,
            )
        else:
            bandwidth_mbps = max(
                self.cross_plane_min_bandwidth_mbps,
                self.cross_plane_base_bandwidth_mbps
                - (distance_km * self.cross_plane_bandwidth_distance_penalty),
            )
            loss_percent = min(
                self.cross_plane_max_loss_percent,
                self.cross_plane_base_loss_percent
                + max(0.0, distance_km - self.cross_plane_loss_distance_start_km)
                * self.cross_plane_loss_distance_slope,
            )
        return {
            "delay_ms": round(delay_ms, 6),
            "loss_percent": round(loss_percent, 6),
            "bandwidth_mbps": round(bandwidth_mbps, 6),
            "routing_cost": self._routing_cost(delay_ms),
        }

    def derive_parameters(
        self,
        *,
        snapshot: TopologySnapshot,
        link: Dict[str, Any],
    ) -> Dict[str, Any]:
        slot_type = str(link.get("slot_type"))
        distance_km = _link_distance_km(snapshot, link)
        if distance_km is None:
            return {}
        if slot_type in {"ut_access", "gw_access"}:
            elevation_deg = float(link.get("visibility_elevation_deg", 45.0))
            return {
                **self._access_link_parameters(
                    distance_km=distance_km,
                    elevation_deg=elevation_deg,
                ),
                "slot_type": slot_type,
                "distance_km": round(distance_km, 6),
                "visibility_elevation_deg": round(elevation_deg, 6),
            }
        if slot_type in {"same_plane_isl", "fixed_isl", "cross_plane_isl"}:
            return {
                **self._isl_link_parameters(distance_km=distance_km, slot_type=slot_type),
                "slot_type": slot_type,
                "distance_km": round(distance_km, 6),
            }
        return {
            "slot_type": slot_type,
            "distance_km": round(distance_km, 6),
        }


def default_link_runtime_parameter_model() -> LinkRuntimeParameterModel:
    return DistanceElevationRuntimeParameterModel()


def derive_link_runtime_parameters(
    snapshot: TopologySnapshot,
    link: Dict[str, Any],
    *,
    parameter_model: Optional[LinkRuntimeParameterModel] = None,
) -> Dict[str, Any]:
    model = parameter_model or default_link_runtime_parameter_model()
    return model.derive_parameters(snapshot=snapshot, link=link)


def enrich_snapshot_runtime_parameters(
    snapshot: TopologySnapshot,
    *,
    parameter_model: Optional[LinkRuntimeParameterModel] = None,
) -> TopologySnapshot:
    model = parameter_model or default_link_runtime_parameter_model()
    enriched_links: List[Dict[str, Any]] = []
    for link in snapshot.active_links:
        link_copy = dict(link)
        link_copy["runtime_parameters"] = derive_link_runtime_parameters(
            snapshot,
            link_copy,
            parameter_model=model,
        )
        enriched_links.append(link_copy)
    provenance = dict(snapshot.provenance)
    provenance["runtime_parameter_model"] = {
        "model_name": str(model.model_name),
    }
    return TopologySnapshot(
        schema_version=snapshot.schema_version,
        snapshot_id=snapshot.snapshot_id,
        time_slice=dict(snapshot.time_slice),
        provenance=provenance,
        nodes={node_id: dict(node) for node_id, node in snapshot.nodes.items()},
        active_links=enriched_links,
        zones={zone_id: dict(zone) for zone_id, zone in snapshot.zones.items()},
        ut_zone_membership=dict(snapshot.ut_zone_membership),
        planner_hints=dict(snapshot.planner_hints),
    )


def _flow_contexts_from_plan(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    payload = _runtime_plan_dict(runtime_update_plan)
    flow_contexts: Dict[str, Dict[str, Any]] = {}
    node_subplans = dict(payload.get("node_subplans", {}))
    for subplan in node_subplans.values():
        for action in list(subplan.get("actions", [])):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            match = dict(action.get("intent", {}).get("match", {}))
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            local_role = str(outcome.get("local_role", match.get("node_role", "")))
            if local_role == "ut_ingress":
                # Immutable UT edge policy is never a source of core FEC state.
                continue
            flow_context = dict(outcome.get("flow_context", {}))
            flow_id = str(flow_context.get("flow_id", match.get("flow_id", "")))
            if not flow_id:
                continue
            flow_contexts.setdefault(flow_id, {}).update(flow_context)
    return {
        flow_id: flow_contexts[flow_id]
        for flow_id in sorted(flow_contexts)
    }


def flow_path_summaries(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for flow_id, context in _flow_contexts_from_plan(runtime_update_plan).items():
        summaries.append(
            {
                "flow_id": flow_id,
                "ut_id": context.get("ut_id"),
                "serving_satellite_id": context.get("serving_satellite_id"),
                "selected_gateway_id": context.get("selected_gateway_id"),
                "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
                "satellite_path": list(context.get("satellite_path", [])),
                "forward_link_ids": list(context.get("forward_link_ids", [])),
                "reverse_link_ids": list(context.get("reverse_link_ids", [])),
                "forward_labels": list(context.get("forward_labels", [])),
                "reverse_labels": list(context.get("reverse_labels", [])),
            }
        )
    return summaries


def gateway_exit_summary(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    per_ut: Dict[str, Dict[str, Any]] = {}
    for context in _flow_contexts_from_plan(runtime_update_plan).values():
        ut_id = str(context.get("ut_id", "")).strip()
        selected_gateway_id = str(context.get("selected_gateway_id", "")).strip()
        if not ut_id or not selected_gateway_id:
            continue
        per_ut[ut_id] = {
            "selected_gateway_id": selected_gateway_id,
            "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
            "serving_satellite_id": context.get("serving_satellite_id"),
            "target_host_node_id": context.get("target_host_node_id"),
        }
    return per_ut


def _sorted_unique(values: Iterable[Any]) -> List[str]:
    normalized: set[str] = set()
    for value in values:
        if value is None:
            continue
        rendered = str(value).strip()
        if not rendered or rendered.lower() == "none":
            continue
        normalized.add(rendered)
    return sorted(normalized)


def _route_group_nodes(group: Dict[str, Any]) -> List[str]:
    return _sorted_unique(
        [
            group.get("selected_gateway_id"),
            group.get("selected_gateway_satellite_id"),
            group.get("serving_satellite_id"),
            group.get("zone_serving_satellite_id"),
            *list(group.get("satellite_path", [])),
        ]
    )


def _fec_group_signature(group: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "group_kind": group.get("group_kind"),
        "gateway_class_id": group.get("gateway_class_id"),
        "selected_gateway_id": group.get("selected_gateway_id"),
        "selected_gateway_satellite_id": group.get("selected_gateway_satellite_id"),
        "zone_id": group.get("zone_id"),
        "serving_satellite_id": group.get("serving_satellite_id"),
        "zone_serving_satellite_id": group.get("zone_serving_satellite_id"),
        "satellite_path": list(group.get("satellite_path", [])),
        "forward_link_ids": list(group.get("forward_link_ids", [])),
        "reverse_link_ids": list(group.get("reverse_link_ids", [])),
        "forward_labels": list(group.get("forward_labels", [])),
        "reverse_labels": list(group.get("reverse_labels", [])),
        "return_prefixes": list(group.get("return_prefixes", [])),
        "pop_point": group.get("pop_point"),
    }


def _estimate_group_operation_items(group: Dict[str, Any]) -> int:
    link_count = len(list(group.get("forward_link_ids", []))) + len(list(group.get("reverse_link_ids", [])))
    prefix_count = len(list(group.get("return_prefixes", [])))
    return max(1, link_count + prefix_count + 1)


def gz_fec_plan(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> Dict[str, Any]:
    payload = _runtime_plan_dict(runtime_update_plan)
    flow_contexts = _flow_contexts_from_plan(payload)
    forward_fecs: Dict[str, Dict[str, Any]] = {}
    return_fecs: Dict[str, Dict[str, Any]] = {}
    for flow_id, context in flow_contexts.items():
        zone_id = str(context.get("zone_id") or "")
        selected_gateway_id = str(context.get("selected_gateway_id") or "")
        serving_satellite_id = str(context.get("serving_satellite_id") or "")
        gateway_class_id = str(
            context.get("gateway_class_id")
            or (
                f"{zone_id}:default-gateway:{selected_gateway_id}"
                if zone_id and selected_gateway_id
                else selected_gateway_id
            )
        )
        forward_links = [str(link_id) for link_id in list(context.get("forward_link_ids", []))]
        forward_labels = [int(label) for label in list(context.get("forward_labels", []))]
        satellite_path = [str(node_id) for node_id in list(context.get("satellite_path", []))]
        forward_key = (
            f"{gateway_class_id}:{serving_satellite_id}:"
            f"{_fingerprint({'forward_link_ids': forward_links, 'forward_labels': forward_labels})}"
        )
        forward_group = forward_fecs.setdefault(
            forward_key,
            {
                "group_kind": "g_forward_fec",
                "fec_id": forward_key,
                "fec_identity_basis": "generation_zone_gateway_class_zone_anchor_path",
                "gateway_class_id": gateway_class_id,
                "selected_gateway_id": selected_gateway_id,
                "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
                "serving_satellite_id": serving_satellite_id,
                "zone_anchor_satellite_id": (
                    context.get("zone_anchor_satellite_id") or serving_satellite_id
                ),
                "zone_anchor_source": "topology_snapshot_zone_active_serving_satellite_id",
                "zone_id": zone_id,
                "satellite_path": satellite_path,
                "forward_link_ids": forward_links,
                "forward_labels": forward_labels,
                "flow_ids": [],
                "ut_ids": [],
            },
        )
        forward_group["flow_ids"].append(flow_id)
        forward_group["ut_ids"].append(str(context.get("ut_id") or ""))

        return_intent = dict(context.get("return_path_intent", {}))
        shared_target = dict(return_intent.get("shared_backbone_return_target", {}))
        return_key = str(
            shared_target.get("target_key")
            or (
                f"{selected_gateway_id}:{zone_id}:{serving_satellite_id}"
                if selected_gateway_id and zone_id and serving_satellite_id
                else ""
            )
        )
        if not return_key:
            continue
        reverse_links = [str(link_id) for link_id in list(context.get("reverse_link_ids", []))]
        reverse_labels = [int(label) for label in list(context.get("reverse_labels", []))]
        candidate_zone_route = dict(return_intent.get("candidate_shared_zone_return_route", {}))
        return_group = return_fecs.setdefault(
            return_key,
            {
                "group_kind": "gz_return_fec",
                "fec_id": return_key,
                "fec_identity_basis": "generation_zone_gateway_class_zone_anchor_path",
                "target_key": return_key,
                "gateway_class_id": gateway_class_id,
                "selected_gateway_id": selected_gateway_id,
                "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
                "zone_id": zone_id,
                "zone_serving_satellite_id": (
                    shared_target.get("zone_serving_satellite_id")
                    or return_intent.get("zone_serving_satellite_id")
                    or serving_satellite_id
                ),
                "zone_anchor_satellite_id": (
                    shared_target.get("zone_serving_satellite_id")
                    or return_intent.get("zone_serving_satellite_id")
                    or context.get("zone_anchor_satellite_id")
                    or serving_satellite_id
                ),
                "zone_anchor_source": "topology_snapshot_zone_active_serving_satellite_id",
                "satellite_path": satellite_path,
                "reverse_link_ids": reverse_links,
                "reverse_labels": reverse_labels,
                "return_prefixes": [
                    str(prefix)
                    for prefix in (
                        candidate_zone_route.get("route_prefixes")
                        or context.get("zone_return_prefixes", [])
                    )
                    if str(prefix)
                ],
                "pop_point": (
                    return_intent.get("zone_serving_satellite_id")
                    or serving_satellite_id
                ),
                "state_model": "shared_gateway_zone_or_zone_return_reachability",
                "flow_ids": [],
                "ut_ids": [],
            },
        )
        return_group["flow_ids"].append(flow_id)
        return_group["ut_ids"].append(str(context.get("ut_id") or ""))
    for group in list(forward_fecs.values()) + list(return_fecs.values()):
        group["flow_ids"] = _sorted_unique(group.get("flow_ids", []))
        group["ut_ids"] = _sorted_unique(group.get("ut_ids", []))
        group["affected_nodes"] = _route_group_nodes(group)
    return {
        "plan_id": payload.get("plan_id"),
        "target_snapshot": dict(payload.get("target_snapshot", {})),
        "model": "gz_fec_plan_v1",
        "planning_scope": {
            "forward_fec_basis": "generation + zone + gateway class + zone anchor + path",
            "return_fec_basis": "(selected_gateway, zone, responsible_satellite)",
            "backbone_state_scales_with_ut_count": False,
            "ut_or_flow_in_core_gz_fec_identity": False,
        },
        "flow_count": len(flow_contexts),
        "forward_fec_count": len(forward_fecs),
        "return_fec_count": len(return_fecs),
        "forward_fecs": [
            dict(group)
            for _, group in sorted(forward_fecs.items())
        ],
        "return_fecs": [
            dict(group)
            for _, group in sorted(return_fecs.items())
        ],
    }


def diff_gz_fec_plans(
    previous_plan: Dict[str, Any] | None,
    current_plan: Dict[str, Any],
) -> Dict[str, Any]:
    previous_groups = {} if previous_plan is None else {
        str(group.get("fec_id")): dict(group)
        for group in (
            list(previous_plan.get("forward_fecs", []))
            + list(previous_plan.get("return_fecs", []))
        )
    }
    current_groups = {
        str(group.get("fec_id")): dict(group)
        for group in (
            list(current_plan.get("forward_fecs", []))
            + list(current_plan.get("return_fecs", []))
        )
    }
    added = sorted(set(current_groups) - set(previous_groups))
    removed = sorted(set(previous_groups) - set(current_groups))
    changed = sorted(
        group_id
        for group_id in set(previous_groups) & set(current_groups)
        if _fec_group_signature(previous_groups[group_id])
        != _fec_group_signature(current_groups[group_id])
    )
    unchanged = sorted(
        group_id
        for group_id in set(previous_groups) & set(current_groups)
        if group_id not in set(changed)
    )
    changed_group_ids = added + removed + changed
    changed_group_payloads = [
        current_groups.get(group_id) or previous_groups[group_id]
        for group_id in changed_group_ids
    ]
    affected_nodes = _sorted_unique(
        node_id
        for group in changed_group_payloads
        for node_id in list(group.get("affected_nodes", []))
    )
    changed_zones = _sorted_unique(
        group.get("zone_id")
        for group in changed_group_payloads
        if group.get("zone_id")
    )
    operation_estimate = sum(
        _estimate_group_operation_items(group)
        for group in changed_group_payloads
    )
    return {
        "model": "gz_fec_update_diff_v1",
        "previous_plan_id": None if previous_plan is None else previous_plan.get("plan_id"),
        "current_plan_id": current_plan.get("plan_id"),
        "current_snapshot": dict(current_plan.get("target_snapshot", {})),
        "changed_zones": changed_zones,
        "changed_fec_group_count": len(changed_group_ids),
        "changed_gz_return_group_count": sum(
            1
            for group_id in changed_group_ids
            if str((current_groups.get(group_id) or previous_groups[group_id]).get("group_kind"))
            == "gz_return_fec"
        ),
        "added_fec_groups": added,
        "removed_fec_groups": removed,
        "changed_fec_groups": changed,
        "skipped_unchanged_fec_group_count": len(unchanged),
        "skipped_unchanged_fec_groups": unchanged[:50],
        "affected_node_count": len(affected_nodes),
        "affected_nodes": affected_nodes,
        "estimated_lfib_route_operation_items": operation_estimate,
        "diff_scope": "changed_fec_zone_path_groups_only",
    }


def _intervals_from_zone_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    ordered_rows = sorted(
        rows,
        key=lambda row: float(row.get("relative_time_seconds") or 0.0),
    )
    intervals: List[Dict[str, Any]] = []
    start_row: Dict[str, Any] | None = None
    previous_row: Dict[str, Any] | None = None
    step_seconds = 0.0
    if len(ordered_rows) > 1:
        step_seconds = max(
            0.0,
            float(ordered_rows[1].get("relative_time_seconds") or 0.0)
            - float(ordered_rows[0].get("relative_time_seconds") or 0.0),
        )
    for row in ordered_rows:
        resolved = bool(row.get("responsible_satellite_id"))
        if resolved and start_row is None:
            start_row = row
        if not resolved and start_row is not None:
            end_row = previous_row or row
            start = float(start_row.get("relative_time_seconds") or 0.0)
            end = float(end_row.get("relative_time_seconds") or 0.0)
            intervals.append(
                {
                    "start_relative_time_seconds": start,
                    "end_relative_time_seconds": end,
                    "duration_seconds": round(end - start + step_seconds, 6),
                    "start_slice_id": start_row.get("slice_id"),
                    "end_slice_id": end_row.get("slice_id"),
                }
            )
            start_row = None
        previous_row = row
    if start_row is not None:
        end_row = previous_row or start_row
        start = float(start_row.get("relative_time_seconds") or 0.0)
        end = float(end_row.get("relative_time_seconds") or 0.0)
        intervals.append(
            {
                "start_relative_time_seconds": start,
                "end_relative_time_seconds": end,
                "duration_seconds": round(end - start + step_seconds, 6),
                "start_slice_id": start_row.get("slice_id"),
                "end_slice_id": end_row.get("slice_id"),
            }
        )
    return intervals


def _dwell_runs_from_zone_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    ordered_rows = sorted(
        rows,
        key=lambda row: float(row.get("relative_time_seconds") or 0.0),
    )
    step_seconds = 0.0
    if len(ordered_rows) > 1:
        step_seconds = max(
            0.0,
            float(ordered_rows[1].get("relative_time_seconds") or 0.0)
            - float(ordered_rows[0].get("relative_time_seconds") or 0.0),
        )
    runs: List[Dict[str, Any]] = []
    start_row = ordered_rows[0]
    previous_row = ordered_rows[0]
    for row in ordered_rows[1:]:
        if row.get("responsible_satellite_id") != previous_row.get("responsible_satellite_id"):
            start = float(start_row.get("relative_time_seconds") or 0.0)
            end = float(previous_row.get("relative_time_seconds") or 0.0)
            runs.append(
                {
                    "responsible_satellite_id": start_row.get("responsible_satellite_id"),
                    "start_relative_time_seconds": start,
                    "end_relative_time_seconds": end,
                    "duration_seconds": round(end - start + step_seconds, 6),
                    "start_slice_id": start_row.get("slice_id"),
                    "end_slice_id": previous_row.get("slice_id"),
                }
            )
            start_row = row
        previous_row = row
    start = float(start_row.get("relative_time_seconds") or 0.0)
    end = float(previous_row.get("relative_time_seconds") or 0.0)
    runs.append(
        {
            "responsible_satellite_id": start_row.get("responsible_satellite_id"),
            "start_relative_time_seconds": start,
            "end_relative_time_seconds": end,
            "duration_seconds": round(end - start + step_seconds, 6),
            "start_slice_id": start_row.get("slice_id"),
            "end_slice_id": previous_row.get("slice_id"),
        }
    )
    return runs


def _numeric_distribution(values: Sequence[float]) -> Dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "avg": None, "p50": None, "p95": None, "max": None}
    p95_index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "count": len(ordered),
        "avg": round(sum(ordered) / len(ordered), 6),
        "p50": ordered[len(ordered) // 2],
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def build_zone_availability_analysis(
    *,
    anchor_rows: Sequence[Dict[str, Any]],
    zones_by_id: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    rows_by_zone: Dict[str, List[Dict[str, Any]]] = {}
    rows_by_time: Dict[float, Dict[str, Dict[str, Any]]] = {}
    for row in anchor_rows:
        zone_id = str(row.get("zone_id"))
        rows_by_zone.setdefault(zone_id, []).append(dict(row))
        relative_time = float(row.get("relative_time_seconds") or 0.0)
        rows_by_time.setdefault(relative_time, {})[zone_id] = dict(row)
    required_zone_ids = sorted(
        zone_id
        for zone_id, zone in zones_by_id.items()
        if str(zone.get("service_role", "required_service")) == "required_service"
        and bool(zone.get("active_service", True))
        and bool(zone.get("required_for_runtime", True))
    )
    zone_summaries: List[Dict[str, Any]] = []
    role_counts: Dict[str, int] = {}
    unresolved_by_role: Dict[str, int] = {}
    for zone_id, rows in sorted(rows_by_zone.items()):
        zone = dict(zones_by_id.get(zone_id, {}))
        role = str(zone.get("service_role", "required_service"))
        role_counts[role] = int(role_counts.get(role, 0)) + 1
        resolved_count = sum(
            1
            for row in rows
            if bool(row.get("zone_service_resolved", row.get("responsible_satellite_id")))
        )
        unresolved_count = len(rows) - resolved_count
        zone_anchor_resolved_count = sum(
            1
            for row in rows
            if bool(row.get("zone_anchor_resolved", row.get("responsible_satellite_id")))
        )
        ut_attach_resolved_count = sum(
            1
            for row in rows
            if bool(row.get("ut_attach_resolved", True))
        )
        unresolved_reasons: Dict[str, int] = {}
        for row in rows:
            if bool(row.get("zone_service_resolved", row.get("responsible_satellite_id"))):
                continue
            reason = str(row.get("zone_service_resolution_state") or row.get("resolution_state") or "unresolved")
            unresolved_reasons[reason] = int(unresolved_reasons.get(reason, 0)) + 1
        if unresolved_count:
            unresolved_by_role[role] = int(unresolved_by_role.get(role, 0)) + unresolved_count
        intervals = _intervals_from_zone_rows(rows)
        dwell_runs = [
            run for run in _dwell_runs_from_zone_rows(rows)
            if run.get("responsible_satellite_id")
        ]
        zone_summaries.append(
            {
                "zone_id": zone_id,
                "service_role": role,
                "active_service": bool(zone.get("active_service", True)),
                "required_for_runtime": bool(zone.get("required_for_runtime", role == "required_service")),
                "sample_count": len(rows),
                "resolved_count": resolved_count,
                "unresolved_count": unresolved_count,
                "zone_anchor_resolved_count": zone_anchor_resolved_count,
                "ut_attach_resolved_count": ut_attach_resolved_count,
                "zone_service_resolved_count": resolved_count,
                "unresolved_reasons": unresolved_reasons,
                "resolved_fraction": round(resolved_count / len(rows), 6) if rows else None,
                "longest_resolved_interval": (
                    max(intervals, key=lambda interval: float(interval["duration_seconds"]))
                    if intervals
                    else None
                ),
                "dwell_time_seconds": _numeric_distribution(
                    [
                        float(run.get("duration_seconds", 0.0))
                        for run in dwell_runs
                    ]
                ),
                "short_dwell_under_30s_count": sum(
                    1
                    for run in dwell_runs
                    if float(run.get("duration_seconds", 0.0)) < 30.0
                ),
            }
        )
    all_required_rows = []
    for relative_time in sorted(rows_by_time):
        zone_rows = rows_by_time[relative_time]
        unresolved_required = [
            zone_id
            for zone_id in required_zone_ids
            if not bool(
                zone_rows.get(zone_id, {}).get(
                    "zone_service_resolved",
                    zone_rows.get(zone_id, {}).get("responsible_satellite_id"),
                )
            )
        ]
        all_required_rows.append(
            {
                "relative_time_seconds": relative_time,
                "responsible_satellite_id": None if unresolved_required else "all_required_resolved",
                "unresolved_required_zones": unresolved_required,
            }
        )
    required_intervals = _intervals_from_zone_rows(all_required_rows)
    return {
        "model": "zone_availability_analysis_v1",
        "required_zone_ids": required_zone_ids,
        "role_counts": role_counts,
        "unresolved_sample_count_by_role": unresolved_by_role,
        "zones": zone_summaries,
        "longest_all_required_resolved_interval": (
            max(required_intervals, key=lambda interval: float(interval["duration_seconds"]))
            if required_intervals
            else None
        ),
        "all_required_resolved_intervals_top10": sorted(
            required_intervals,
            key=lambda interval: float(interval["duration_seconds"]),
            reverse=True,
        )[:10],
    }


def build_gz_planning_artifacts(
    *,
    snapshots: Sequence[TopologySnapshot],
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    fec_plans = [gz_fec_plan(plan) for plan in runtime_update_plans]
    diffs = [
        diff_gz_fec_plans(
            fec_plans[index - 1] if index > 0 else None,
            fec_plan,
        )
        for index, fec_plan in enumerate(fec_plans)
    ]
    zones_by_id: Dict[str, Dict[str, Any]] = {}
    for snapshot in snapshots:
        for zone_id, zone in snapshot.zones.items():
            zones_by_id.setdefault(
                str(zone_id),
                {
                    "zone_id": str(zone_id),
                    "geometry": dict(zone.get("fixed_coordinates", {})),
                    "position_model": dict(zone.get("position_state", {})).get("model"),
                    "partition_policy": zone.get("partition_policy"),
                    "membership_mode": zone.get("membership_mode"),
                    "source": zone.get("source"),
                    "service_domain_source": zone.get("service_domain_source"),
                    "service_domain": dict(zone.get("service_domain", {})),
                    "service_role": zone.get("service_role", "required_service"),
                    "active_service": bool(zone.get("active_service", True)),
                    "required_for_runtime": bool(
                        zone.get(
                            "required_for_runtime",
                            str(zone.get("service_role", "required_service"))
                            == "required_service",
                        )
                    ),
                    "radius_km": zone.get("radius_km"),
                    "coverage_margin_deg": zone.get("coverage_margin_deg"),
                    "default_gateway": zone.get("default_gateway"),
                    "preferred_gateway_ids": list(zone.get("preferred_gateway_ids", [])),
                    "prefix_overrides": [
                        dict(override)
                        for override in list(zone.get("prefix_overrides", []))
                    ],
                    "ut_ids": list(zone.get("ut_ids", [])),
                    "return_prefixes": list(zone.get("return_prefixes", [])),
                    "active_policy": dict(
                        snapshot.planner_hints.get(
                            "zone_default_gateway_preferences",
                            {},
                        )
                    ).get(str(zone_id), {}),
                },
            )
    anchor_rows: List[Dict[str, Any]] = []
    quality_by_zone: Dict[str, Dict[str, Any]] = {}
    previous_anchor_by_zone: Dict[str, str] = {}
    for snapshot in snapshots:
        for zone_id, zone in sorted(snapshot.zones.items()):
            anchor = dict(zone.get("anchor_selection", {}))
            selected = str(zone.get("active_serving_satellite_id") or "")
            previous = previous_anchor_by_zone.get(str(zone_id))
            changed = bool(previous is not None and previous != selected)
            if selected:
                previous_anchor_by_zone[str(zone_id)] = selected
            row = {
                "slice_id": snapshot.time_slice.get("slice_id"),
                "absolute_time_utc": snapshot.time_slice.get("absolute_time_utc"),
                "relative_time_seconds": snapshot.time_slice.get("relative_time_seconds"),
                "zone_id": str(zone_id),
                "responsible_satellite_id": selected or None,
                "service_role": zone.get("service_role", "required_service"),
                "active_service": bool(zone.get("active_service", True)),
                "required_for_runtime": bool(
                    zone.get(
                        "required_for_runtime",
                        str(zone.get("service_role", "required_service"))
                        == "required_service",
                    )
                ),
                "quality_score": anchor.get("quality_score"),
                "coverage_margin_deg": anchor.get("coverage_margin_deg"),
                "gateway_path_hops": anchor.get("gateway_path_hops"),
                "change_reason": anchor.get("change_reason"),
                "anchor_changed": changed,
                "resolution_state": zone.get("resolution_state"),
                "zone_anchor_resolved": bool(
                    zone.get("zone_anchor_resolved", bool(selected))
                ),
                "ut_attach_resolved": bool(
                    zone.get("ut_attach_resolved", True)
                ),
                "zone_service_resolved": bool(
                    zone.get("zone_service_resolved", bool(selected))
                ),
                "zone_service_resolution_state": zone.get(
                    "zone_service_resolution_state",
                    zone.get("resolution_state"),
                ),
                "ut_visibility_audit": dict(zone.get("ut_visibility_audit", {})),
            }
            anchor_rows.append(row)
            zone_summary = quality_by_zone.setdefault(
                str(zone_id),
                {
                    "zone_id": str(zone_id),
                    "sample_count": 0,
                    "handover_count": 0,
                    "unresolved_count": 0,
                    "zone_anchor_unresolved_count": 0,
                    "ut_attach_unresolved_count": 0,
                    "zone_service_unresolved_count": 0,
                    "unresolved_reasons": {},
                    "coverage_margin_deg_min": None,
                    "coverage_margin_deg_sum": 0.0,
                    "coverage_margin_sample_count": 0,
                    "change_reasons": {},
                },
            )
            zone_summary["sample_count"] += 1
            if changed:
                zone_summary["handover_count"] += 1
            if not bool(row.get("zone_anchor_resolved")):
                zone_summary["zone_anchor_unresolved_count"] += 1
            if not bool(row.get("ut_attach_resolved")):
                zone_summary["ut_attach_unresolved_count"] += 1
            if not bool(row.get("zone_service_resolved")):
                zone_summary["zone_service_unresolved_count"] += 1
            if not bool(row.get("zone_service_resolved")):
                zone_summary["unresolved_count"] += 1
                service_reason = str(
                    row.get("zone_service_resolution_state") or "unresolved"
                )
                zone_summary["unresolved_reasons"][service_reason] = int(
                    zone_summary["unresolved_reasons"].get(service_reason, 0)
                ) + 1
            reason = str(anchor.get("change_reason") or "unknown")
            zone_summary["change_reasons"][reason] = int(zone_summary["change_reasons"].get(reason, 0)) + 1
            if anchor.get("coverage_margin_deg") is not None:
                margin = float(anchor["coverage_margin_deg"])
                zone_summary["coverage_margin_deg_sum"] += margin
                zone_summary["coverage_margin_sample_count"] += 1
                if zone_summary["coverage_margin_deg_min"] is None:
                    zone_summary["coverage_margin_deg_min"] = margin
                else:
                    zone_summary["coverage_margin_deg_min"] = min(
                        float(zone_summary["coverage_margin_deg_min"]),
                        margin,
                    )
    for zone_summary in quality_by_zone.values():
        margin_sample_count = int(zone_summary.get("coverage_margin_sample_count", 0))
        margin_sum = float(zone_summary.pop("coverage_margin_deg_sum", 0.0))
        zone_summary["coverage_margin_deg_avg"] = (
            round(margin_sum / float(margin_sample_count), 6)
            if margin_sample_count > 0
            else None
        )
    return {
        "zone_model": {
            "model": "zone_model_v1",
            "zone_count": len(zones_by_id),
            "zones": [
                dict(zone)
                for _, zone in sorted(zones_by_id.items())
            ],
        },
        "zone_anchor_schedule": {
            "model": "zone_anchor_schedule_v1",
            "selection_algorithm": "zone_anchor_quality_generator_v1",
            "rows": anchor_rows,
        },
        "zone_anchor_quality_summary": {
            "model": "zone_anchor_quality_summary_v1",
            "zones": [
                dict(summary)
                for _, summary in sorted(quality_by_zone.items())
            ],
        },
        "gz_fec_plan": {
            "model": "gz_fec_plan_sequence_v1",
            "snapshot_count": len(fec_plans),
            "plans": fec_plans,
        },
        "gz_update_diff_summary": {
            "model": "gz_update_diff_summary_v1",
            "diff_count": len(diffs),
            "diffs": diffs,
        },
        "zone_availability_analysis": build_zone_availability_analysis(
            anchor_rows=anchor_rows,
            zones_by_id=zones_by_id,
        ),
    }


def active_serving_satellite_ids(snapshot: TopologySnapshot) -> Dict[str, Any]:
    return {
        zone_id: zone.get("active_serving_satellite_id")
        for zone_id, zone in sorted(snapshot.zones.items())
    }


def _link_topology_signature(link: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slot_type": link.get("slot_type"),
        "binding": dict(link.get("binding", {})),
        "endpoints": [
            {
                "node_id": endpoint.get("node_id"),
                "node_type": endpoint.get("node_type"),
                "interface_name": endpoint.get("interface_name"),
                "ip_address": endpoint.get("ip_address"),
            }
            for endpoint in link.get("endpoints", [])
        ],
    }


def _link_parameter_map(snapshot: TopologySnapshot) -> Dict[str, Dict[str, Any]]:
    return {
        str(link.get("link_id")): dict(link.get("runtime_parameters", {}))
        for link in snapshot.active_links
    }


def _link_topology_map(snapshot: TopologySnapshot) -> Dict[str, Dict[str, Any]]:
    return {
        str(link.get("link_id")): _link_topology_signature(link)
        for link in snapshot.active_links
    }


def _planner_signature(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    signatures: Dict[str, Dict[str, Any]] = {}
    for flow_id, context in _flow_contexts_from_plan(runtime_update_plan).items():
        signatures[flow_id] = {
            "ut_id": context.get("ut_id"),
            "serving_satellite_id": context.get("serving_satellite_id"),
            "selected_gateway_id": context.get("selected_gateway_id"),
            "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
            "satellite_path": list(context.get("satellite_path", [])),
            "forward_link_ids": list(context.get("forward_link_ids", [])),
            "reverse_link_ids": list(context.get("reverse_link_ids", [])),
            "forward_labels": list(context.get("forward_labels", [])),
            "reverse_labels": list(context.get("reverse_labels", [])),
            "ut_access_link_id": context.get("ut_access_link_id"),
            "gateway_access_link_id": context.get("gateway_access_link_id"),
        }
    return signatures


def _semantic_flow_signature(runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    signatures: Dict[str, Dict[str, Any]] = {}
    for flow_id, context in _flow_contexts_from_plan(runtime_update_plan).items():
        return_intent = dict(context.get("return_path_intent", {}))
        shared_target = dict(return_intent.get("shared_backbone_return_target", {}))
        candidate_zone_route = dict(return_intent.get("candidate_shared_zone_return_route", {}))
        signatures[flow_id] = {
            "ut_id": context.get("ut_id"),
            "zone_id": context.get("zone_id"),
            "gateway_class_id": context.get("gateway_class_id"),
            "serving_satellite_id": context.get("serving_satellite_id"),
            "selected_gateway_id": context.get("selected_gateway_id"),
            "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
            "satellite_path": list(context.get("satellite_path", [])),
            "forward_link_ids": list(context.get("forward_link_ids", [])),
            "reverse_link_ids": list(context.get("reverse_link_ids", [])),
            "ut_access_link_id": context.get("ut_access_link_id"),
            "gateway_access_link_id": context.get("gateway_access_link_id"),
            "return_target_key": shared_target.get("target_key"),
            "zone_serving_satellite_id": (
                shared_target.get("zone_serving_satellite_id")
                or return_intent.get("zone_serving_satellite_id")
                or context.get("serving_satellite_id")
            ),
            "return_prefixes": list(
                candidate_zone_route.get("route_prefixes")
                or context.get("zone_return_prefixes", [])
            ),
        }
    return signatures


def _semantic_fec_groups(
    runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    g_to_z: Dict[str, Dict[str, Any]] = {}
    z_to_g: Dict[str, Dict[str, Any]] = {}
    for flow_id, context in _flow_contexts_from_plan(runtime_update_plan).items():
        zone_id = str(context.get("zone_id") or "")
        selected_gateway_id = str(context.get("selected_gateway_id") or "")
        serving_satellite_id = str(context.get("serving_satellite_id") or "")
        gateway_class_id = str(
            context.get("gateway_class_id")
            or (
                f"{zone_id}:default-gateway:{selected_gateway_id}"
                if zone_id and selected_gateway_id
                else selected_gateway_id
            )
        )
        forward_signature = {
            "gateway_class_id": gateway_class_id,
            "selected_gateway_id": selected_gateway_id,
            "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
            "serving_satellite_id": serving_satellite_id,
            "zone_anchor_satellite_id": (
                context.get("zone_anchor_satellite_id") or serving_satellite_id
            ),
            "zone_id": zone_id,
            "satellite_path": list(context.get("satellite_path", [])),
            "forward_link_ids": list(context.get("forward_link_ids", [])),
        }
        forward_key = (
            f"{gateway_class_id}:{serving_satellite_id}:"
            f"{_fingerprint(forward_signature)}"
        )
        g_to_z.setdefault(
            forward_key,
            {
                **forward_signature,
                "fec_kind": "G",
                "flow_ids": [],
                "ut_ids": [],
            },
        )
        g_to_z[forward_key]["flow_ids"].append(flow_id)
        g_to_z[forward_key]["ut_ids"].append(str(context.get("ut_id") or ""))

        return_intent = dict(context.get("return_path_intent", {}))
        shared_target = dict(return_intent.get("shared_backbone_return_target", {}))
        candidate_zone_route = dict(return_intent.get("candidate_shared_zone_return_route", {}))
        return_key = str(
            shared_target.get("target_key")
            or (
                f"{selected_gateway_id}:{zone_id}:{serving_satellite_id}"
                if selected_gateway_id and zone_id and serving_satellite_id
                else ""
            )
        )
        if return_key:
            return_signature = {
                "target_key": return_key,
                "gateway_class_id": gateway_class_id,
                "selected_gateway_id": selected_gateway_id,
                "selected_gateway_satellite_id": context.get("selected_gateway_satellite_id"),
                "zone_id": zone_id,
                "zone_serving_satellite_id": (
                    shared_target.get("zone_serving_satellite_id")
                    or return_intent.get("zone_serving_satellite_id")
                    or serving_satellite_id
                ),
                "zone_anchor_satellite_id": (
                    shared_target.get("zone_serving_satellite_id")
                    or return_intent.get("zone_serving_satellite_id")
                    or context.get("zone_anchor_satellite_id")
                    or serving_satellite_id
                ),
                "satellite_path": list(context.get("satellite_path", [])),
                "reverse_link_ids": list(context.get("reverse_link_ids", [])),
                "return_prefixes": list(
                    candidate_zone_route.get("route_prefixes")
                    or context.get("zone_return_prefixes", [])
                ),
            }
            z_to_g.setdefault(
                return_key,
                {
                    **return_signature,
                    "fec_kind": "GZ",
                    "flow_ids": [],
                    "ut_ids": [],
                },
            )
            z_to_g[return_key]["flow_ids"].append(flow_id)
            z_to_g[return_key]["ut_ids"].append(str(context.get("ut_id") or ""))
    for group in list(g_to_z.values()) + list(z_to_g.values()):
        group["flow_ids"] = _sorted_unique(group.get("flow_ids", []))
        group["ut_ids"] = _sorted_unique(group.get("ut_ids", []))
    return {"g_to_z": g_to_z, "z_to_g": z_to_g}


def _diff_group_maps(
    previous: Mapping[str, Dict[str, Any]],
    current: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    changed = sorted(
        key
        for key in set(previous) & set(current)
        if previous.get(key) != current.get(key)
    )
    unchanged = sorted(
        key
        for key in set(previous) & set(current)
        if key not in set(changed)
    )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "change_count": len(added) + len(removed) + len(changed),
    }


def _semantic_fec_diff(
    previous_plan: RuntimeUpdatePlan | Dict[str, Any],
    current_plan: RuntimeUpdatePlan | Dict[str, Any],
) -> Dict[str, Any]:
    previous_groups = _semantic_fec_groups(previous_plan)
    current_groups = _semantic_fec_groups(current_plan)
    g_diff = _diff_group_maps(previous_groups["g_to_z"], current_groups["g_to_z"])
    gz_diff = _diff_group_maps(previous_groups["z_to_g"], current_groups["z_to_g"])
    return {
        "g_to_z": g_diff,
        "z_to_g": gz_diff,
        "change_count": int(g_diff["change_count"]) + int(gz_diff["change_count"]),
    }


def _changed_flow_ids(
    *,
    previous_signature: Mapping[str, Dict[str, Any]],
    current_signature: Mapping[str, Dict[str, Any]],
    fields: Sequence[str],
) -> List[str]:
    changed: List[str] = []
    for flow_id in sorted(set(previous_signature) | set(current_signature)):
        previous = previous_signature.get(flow_id, {})
        current = current_signature.get(flow_id, {})
        if any(previous.get(field) != current.get(field) for field in fields):
            changed.append(flow_id)
    return changed


def _zone_anchor_changes(
    previous_snapshot: TopologySnapshot,
    current_snapshot: TopologySnapshot,
) -> List[str]:
    previous = active_serving_satellite_ids(previous_snapshot)
    current = active_serving_satellite_ids(current_snapshot)
    return [
        zone_id
        for zone_id in sorted(set(previous) | set(current))
        if previous.get(zone_id) != current.get(zone_id)
    ]


def _coverage_invalidation_zone_ids(
    previous_snapshot: TopologySnapshot,
    current_snapshot: TopologySnapshot,
) -> List[str]:
    invalidated: List[str] = []
    for zone_id in sorted(set(previous_snapshot.zones) | set(current_snapshot.zones)):
        previous_zone = dict(previous_snapshot.zones.get(zone_id, {}))
        current_zone = dict(current_snapshot.zones.get(zone_id, {}))
        previous_resolved = (
            str(previous_zone.get("resolution_state", "resolved")) == "resolved"
            and bool(previous_zone.get("active_serving_satellite_id"))
        )
        current_resolved = (
            str(current_zone.get("resolution_state", "resolved")) == "resolved"
            and bool(current_zone.get("active_serving_satellite_id"))
        )
        required = bool(
            current_zone.get(
                "required_for_runtime",
                str(current_zone.get("service_role", "required_service")) == "required_service",
            )
        )
        active_service = bool(current_zone.get("active_service", True))
        if previous_resolved and not current_resolved and required and active_service:
            invalidated.append(str(zone_id))
    return invalidated


def _runtime_semantic_diff(
    *,
    previous_snapshot: TopologySnapshot,
    current_snapshot: TopologySnapshot,
    previous_plan: RuntimeUpdatePlan | Dict[str, Any],
    current_plan: RuntimeUpdatePlan | Dict[str, Any],
) -> Dict[str, Any]:
    changed_links = _changed_link_ids(
        previous_map=_link_topology_map(previous_snapshot),
        current_map=_link_topology_map(current_snapshot),
    )
    previous_signature = _semantic_flow_signature(previous_plan)
    current_signature = _semantic_flow_signature(current_plan)
    fec_diff = _semantic_fec_diff(previous_plan, current_plan)
    zone_changes = _zone_anchor_changes(previous_snapshot, current_snapshot)
    coverage_invalidations = _coverage_invalidation_zone_ids(
        previous_snapshot,
        current_snapshot,
    )
    ut_serving_changes = _changed_flow_ids(
        previous_signature=previous_signature,
        current_signature=current_signature,
        fields=("serving_satellite_id", "ut_access_link_id"),
    )
    gateway_changes = _changed_flow_ids(
        previous_signature=previous_signature,
        current_signature=current_signature,
        fields=(
            "selected_gateway_id",
            "selected_gateway_satellite_id",
            "gateway_class_id",
            "gateway_access_link_id",
        ),
    )
    g_to_z_changes = _changed_flow_ids(
        previous_signature=previous_signature,
        current_signature=current_signature,
        fields=("satellite_path", "forward_link_ids"),
    )
    z_to_g_changes = _changed_flow_ids(
        previous_signature=previous_signature,
        current_signature=current_signature,
        fields=("reverse_link_ids", "return_target_key", "return_prefixes"),
    )
    reasons: List[str] = []
    if int(fec_diff["change_count"]) > 0:
        reasons.append("gz_fec_path_change")
        reasons.append("bpf_generation_content_change")
    if int(fec_diff["g_to_z"]["change_count"]) > 0 or g_to_z_changes:
        reasons.append("g_to_z_path_change")
    if int(fec_diff["z_to_g"]["change_count"]) > 0 or z_to_g_changes:
        reasons.append("z_to_g_path_change")
    if ut_serving_changes:
        reasons.append("ut_serving_satellite_change")
    if zone_changes:
        reasons.append("zone_active_serving_satellite_change")
    if gateway_changes:
        reasons.append("gw_selection_change")
    if changed_links:
        reasons.append("structural_link_change")
    if coverage_invalidations:
        reasons.append("coverage_invalidation")
    return {
        "trigger_reasons": reasons,
        "changed_link_ids": changed_links,
        "fec_diff": fec_diff,
        "changed_flow_ids": {
            "ut_serving_satellite_change": ut_serving_changes,
            "gw_selection_change": gateway_changes,
            "g_to_z_path_change": g_to_z_changes,
            "z_to_g_path_change": z_to_g_changes,
        },
        "changed_zone_ids": zone_changes,
        "coverage_invalidation_zone_ids": coverage_invalidations,
    }


def _changed_link_ids(
    *,
    previous_map: Mapping[str, Dict[str, Any]],
    current_map: Mapping[str, Dict[str, Any]],
) -> List[str]:
    return [
        link_id
        for link_id in sorted(set(previous_map) | set(current_map))
        if previous_map.get(link_id) != current_map.get(link_id)
    ]


def _parameter_delta(
    *,
    previous: Dict[str, Any],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    previous_delay = float(previous.get("delay_ms", 0.0))
    current_delay = float(current.get("delay_ms", 0.0))
    previous_loss = float(previous.get("loss_percent", 0.0))
    current_loss = float(current.get("loss_percent", 0.0))
    previous_bandwidth = float(previous.get("bandwidth_mbps", 0.0))
    current_bandwidth = float(current.get("bandwidth_mbps", 0.0))
    bandwidth_delta_pct = 0.0
    if previous_bandwidth > 0.0:
        bandwidth_delta_pct = abs(current_bandwidth - previous_bandwidth) / previous_bandwidth * 100.0
    elif previous_bandwidth != current_bandwidth:
        bandwidth_delta_pct = 100.0
    return {
        "delay_delta_ms": abs(current_delay - previous_delay),
        "loss_delta_pct": abs(current_loss - previous_loss),
        "bandwidth_delta_pct": bandwidth_delta_pct,
        "routing_cost_delta": abs(
            int(current.get("routing_cost", 0)) - int(previous.get("routing_cost", 0))
        ),
    }


@dataclass(frozen=True)
class OrbitSample:
    sample_id: str
    sample_index: int
    slice_id: str
    relative_time_seconds: float
    absolute_time_utc: str
    satellite_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservationFrame:
    frame_id: str
    sample_id: str
    slice_id: str
    snapshot_id: str
    activation_relative_time_seconds: float
    activation_time_utc: str
    activation_group_id: str
    observation_policy: str
    classification: str
    active_link_count: int
    active_serving_satellite_ids: Dict[str, Any]
    gateway_exit_summary: Dict[str, Dict[str, Any]]
    flow_path_summaries: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeEpoch:
    epoch_id: str
    sample_id: str
    slice_id: str
    snapshot_id: str
    activation_relative_time_seconds: float
    activation_time_utc: str
    activation_group_id: str
    deadline_policy: str
    trigger_reasons: Tuple[str, ...]
    active_link_count: int
    changed_link_ids: Tuple[str, ...]
    network_mutation_operation_count: int
    network_mutation_operation_counts: Dict[str, int]
    samples_since_previous_runtime_epoch: int
    event_time_source: str = "physical_sample_boundary"
    event_time_accuracy: str = "sample_aligned"
    refinement_status: str = "not_recorded"
    refinement_method: str = "not_recorded"
    refinement_reason: str = "not_recorded"
    refinement_supported: bool = False
    sample_aligned_relative_time_seconds: float = 0.0
    sample_aligned_time_utc: str = ""
    refinement_precision_seconds: float = DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS
    candidate_interval_start_relative_time_seconds: Optional[float] = None
    candidate_interval_end_relative_time_seconds: Optional[float] = None
    candidate_interval_start_time_utc: Optional[str] = None
    candidate_interval_end_time_utc: Optional[str] = None
    refinement_iteration_count: int = 0
    refinement_search_lower_relative_time_seconds: Optional[float] = None
    refinement_search_upper_relative_time_seconds: Optional[float] = None
    refinement_target_fraction: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterEpoch:
    epoch_id: str
    sample_id: str
    slice_id: str
    snapshot_id: str
    activation_relative_time_seconds: float
    activation_time_utc: str
    activation_group_id: str
    update_policy: str
    trigger_reasons: Tuple[str, ...]
    active_link_count: int
    changed_link_ids: Tuple[str, ...]
    changed_parameter_counts: Dict[str, int]
    samples_since_previous_parameter_epoch: int
    event_time_source: str = "physical_sample_boundary"
    event_time_accuracy: str = "sample_aligned"
    refinement_status: str = "not_recorded"
    refinement_method: str = "not_recorded"
    refinement_reason: str = "not_recorded"
    refinement_supported: bool = False
    sample_aligned_relative_time_seconds: float = 0.0
    sample_aligned_time_utc: str = ""
    refinement_precision_seconds: float = DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS
    candidate_interval_start_relative_time_seconds: Optional[float] = None
    candidate_interval_end_relative_time_seconds: Optional[float] = None
    candidate_interval_start_time_utc: Optional[str] = None
    candidate_interval_end_time_utc: Optional[str] = None
    refinement_iteration_count: int = 0
    refinement_search_lower_relative_time_seconds: Optional[float] = None
    refinement_search_upper_relative_time_seconds: Optional[float] = None
    refinement_target_fraction: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TemporalEvent:
    event_id: str
    event_type: str
    source_sample_id: str
    slice_id: str
    snapshot_id: str
    activation_relative_time_seconds: float
    activation_time_utc: str
    activation_group_id: str
    detected_relative_time_seconds: float
    detected_time_utc: str
    execution_policy: str
    trigger_reasons: Tuple[str, ...]
    conflict_owner_keys: Tuple[str, ...]
    linked_epoch_id: Optional[str]
    linked_frame_id: Optional[str]
    scheduling_delay_seconds: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TemporalPlan:
    orbit_samples: Tuple[OrbitSample, ...]
    runtime_epochs: Tuple[RuntimeEpoch, ...]
    parameter_epochs: Tuple[ParameterEpoch, ...]
    observation_frames: Tuple[ObservationFrame, ...]
    event_queue: Tuple[TemporalEvent, ...]
    no_op_sample_count: int
    resource_budget: Dict[str, Any]
    preparation_plan: Dict[str, Any]
    event_time_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "orbit_samples": [sample.to_dict() for sample in self.orbit_samples],
            "topology_snapshot_refs": [
                {
                    "sample_id": sample.sample_id,
                    "slice_id": frame.slice_id,
                    "snapshot_id": frame.snapshot_id,
                    "classification": frame.classification,
                    "relative_time_seconds": sample.relative_time_seconds,
                    "absolute_time_utc": sample.absolute_time_utc,
                }
                for sample, frame in zip(self.orbit_samples, self.observation_frames)
            ],
            "runtime_epochs": [epoch.to_dict() for epoch in self.runtime_epochs],
            "parameter_epochs": [epoch.to_dict() for epoch in self.parameter_epochs],
            "observation_frames": [frame.to_dict() for frame in self.observation_frames],
            "event_queue": [event.to_dict() for event in self.event_queue],
            "no_op_sample_count": self.no_op_sample_count,
            "resource_budget": dict(self.resource_budget),
            "preparation_plan": dict(self.preparation_plan),
            "event_time_summary": dict(self.event_time_summary),
            "preview_update_counters": dict(
                self.event_time_summary.get("preview_update_counters", {})
            ),
            "compression_summary": {
                "orbit_sample_count": len(self.orbit_samples),
                "topology_snapshot_count": len(self.orbit_samples),
                "runtime_epoch_count": len(self.runtime_epochs),
                "parameter_epoch_count": len(self.parameter_epochs),
                "observation_event_count": len(self.observation_frames),
                "event_queue_count": len(self.event_queue),
                "no_op_sample_count": self.no_op_sample_count,
                "samples_compressed_out_of_runtime_updates": (
                    len(self.orbit_samples) - len(self.runtime_epochs)
                ),
            },
        }


def _link_event_owner_keys(snapshot: TopologySnapshot, link_id: str) -> List[str]:
    for link in snapshot.active_links:
        if str(link.get("link_id")) != str(link_id):
            continue
        keys = {f"link:{link_id}"}
        binding = dict(link.get("binding", {}))
        slot_name = str(binding.get("slot_name", "")).strip()
        if slot_name:
            keys.add(f"slot:{slot_name}")
        for endpoint in list(link.get("endpoints", [])):
            endpoint = dict(endpoint)
            node_id = str(endpoint.get("node_id", "")).strip()
            interface_name = str(endpoint.get("interface_name", "")).strip()
            node_type = str(endpoint.get("node_type", "")).strip()
            if node_id and interface_name:
                keys.add(f"interface:{node_id}:{interface_name}")
            if node_id and node_type:
                keys.add(f"node:{node_type}:{node_id}")
        return sorted(keys)
    return [f"link:{link_id}"]


def _epoch_conflict_owner_keys(
    *,
    snapshot: TopologySnapshot,
    changed_link_ids: Sequence[str],
) -> Tuple[str, ...]:
    keys = set()
    for link_id in changed_link_ids:
        keys.update(_link_event_owner_keys(snapshot, str(link_id)))
    return tuple(sorted(keys))


def _observation_sample_index_set(
    orbit_samples: Sequence[OrbitSample],
    *,
    observation_interval_seconds: Optional[float],
) -> set[int]:
    if not orbit_samples:
        return set()
    if observation_interval_seconds is None or observation_interval_seconds <= 0.0:
        return set(range(len(orbit_samples)))
    selected: set[int] = {0, len(orbit_samples) - 1}
    next_target = 0.0
    tolerance = max(0.001, float(observation_interval_seconds) * 0.001)
    for index, sample in enumerate(orbit_samples):
        relative_time = float(sample.relative_time_seconds)
        if relative_time + tolerance < next_target:
            continue
        if abs(relative_time - next_target) <= tolerance or relative_time >= next_target:
            selected.add(index)
            while next_target <= relative_time + tolerance:
                next_target += float(observation_interval_seconds)
    return selected


def _sorted_temporal_events(events: Sequence[TemporalEvent]) -> List[TemporalEvent]:
    event_priority = {
        "runtime_epoch": 0,
        "parameter_epoch": 1,
        "observation_event": 2,
    }
    return sorted(
        events,
        key=lambda event: (
            float(event.activation_relative_time_seconds),
            int(event_priority.get(str(event.event_type), 99)),
            str(event.event_id),
        ),
    )


def _runtime_trigger_reasons(
    *,
    previous_snapshot: TopologySnapshot,
    current_snapshot: TopologySnapshot,
    previous_plan: RuntimeUpdatePlan | Dict[str, Any],
    current_plan: RuntimeUpdatePlan | Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    analysis = _runtime_semantic_diff(
        previous_snapshot=previous_snapshot,
        current_snapshot=current_snapshot,
        previous_plan=previous_plan,
        current_plan=current_plan,
    )
    return list(analysis["trigger_reasons"]), list(analysis["changed_link_ids"])


def _parameter_trigger_analysis(
    *,
    previous_snapshot: TopologySnapshot,
    current_snapshot: TopologySnapshot,
    previous_epoch_relative_time: float,
    current_relative_time: float,
    parameter_max_interval: Optional[float],
    delay_threshold_ms: float,
    loss_threshold_pct: float,
    bandwidth_threshold_pct: float,
    routing_cost_threshold: int,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    reasons: List[str] = []
    changed_links: Dict[str, Dict[str, Any]] = {}
    previous_parameters = _link_parameter_map(previous_snapshot)
    current_parameters = _link_parameter_map(current_snapshot)
    for link_id in sorted(set(previous_parameters) ^ set(current_parameters)):
        changed_links[link_id] = {
            "delay_delta_ms": None,
            "loss_delta_pct": None,
            "bandwidth_delta_pct": None,
            "routing_cost_delta": None,
            "trigger_reasons": ["active_state_changed"],
        }
    for link_id in sorted(set(previous_parameters) & set(current_parameters)):
        delta = _parameter_delta(
            previous=previous_parameters[link_id],
            current=current_parameters[link_id],
        )
        parameter_reasons: List[str] = []
        if delta["delay_delta_ms"] >= float(delay_threshold_ms):
            parameter_reasons.append("delay_threshold_exceeded")
        if delta["loss_delta_pct"] >= float(loss_threshold_pct):
            parameter_reasons.append("loss_threshold_exceeded")
        previous_drop = float(previous_parameters[link_id].get("loss_percent", 0.0)) >= 100.0
        current_drop = float(current_parameters[link_id].get("loss_percent", 0.0)) >= 100.0
        if previous_drop != current_drop:
            parameter_reasons.append("drop_state_changed")
        if delta["bandwidth_delta_pct"] >= float(bandwidth_threshold_pct):
            parameter_reasons.append("bandwidth_threshold_exceeded")
        if int(delta["routing_cost_delta"]) >= int(routing_cost_threshold):
            parameter_reasons.append("routing_cost_changed")
        if parameter_reasons:
            changed_links[link_id] = {
                **delta,
                "trigger_reasons": parameter_reasons,
            }
    elapsed = current_relative_time - previous_epoch_relative_time
    if (
        parameter_max_interval is not None
        and parameter_max_interval > 0.0
        and elapsed >= float(parameter_max_interval)
        and changed_links
    ):
        reasons.append("parameter_max_interval_elapsed")
    for link_change in changed_links.values():
        for reason in link_change["trigger_reasons"]:
            if reason not in reasons:
                reasons.append(reason)
    return reasons, changed_links


def _runtime_plan_for_slice(
    *,
    snapshots: Sequence[TopologySnapshot],
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    slice_id: str,
) -> RuntimeUpdatePlan | Dict[str, Any] | None:
    for snapshot, plan in zip(snapshots, runtime_update_plans):
        if str(snapshot.time_slice.get("slice_id")) == str(slice_id):
            return plan
    return None


def _runtime_snapshot_for_slice(
    *,
    snapshots: Sequence[TopologySnapshot],
    slice_id: str,
) -> TopologySnapshot | None:
    for snapshot in snapshots:
        if str(snapshot.time_slice.get("slice_id")) == str(slice_id):
            return snapshot
    return None


def _empty_distribution_entry(identifier: str) -> Dict[str, Any]:
    return {
        "id": identifier,
        "runtime_epoch_count": 0,
        "parameter_epoch_count": 0,
        "gz_fec_path_change_count": 0,
        "g_to_z_change_count": 0,
        "z_to_g_change_count": 0,
        "gw_selection_change_count": 0,
        "ut_serving_satellite_change_count": 0,
        "zone_active_satellite_change_count": 0,
        "qdisc_parameter_threshold_crossing_count": 0,
    }


def _bump_distribution(
    distribution: Dict[str, Dict[str, Any]],
    identifier: Any,
    field_name: str,
    amount: int = 1,
) -> None:
    key = str(identifier or "").strip()
    if not key:
        return
    entry = distribution.setdefault(key, _empty_distribution_entry(key))
    entry[field_name] = int(entry.get(field_name, 0)) + int(amount)


def _flow_ids_to_contexts(
    runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any] | None,
) -> Dict[str, Dict[str, Any]]:
    if runtime_update_plan is None:
        return {}
    return _flow_contexts_from_plan(runtime_update_plan)


def _runtime_epoch_fec_scope_count(
    runtime_update_plan: RuntimeUpdatePlan | Dict[str, Any] | None,
) -> int:
    if runtime_update_plan is None:
        return 0
    groups = _semantic_fec_groups(runtime_update_plan)
    return len(groups["g_to_z"]) + len(groups["z_to_g"])


def _trigger_reason_counts(epochs: Sequence[RuntimeEpoch | ParameterEpoch]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for epoch in epochs:
        for reason in epoch.trigger_reasons:
            counts[str(reason)] = int(counts.get(str(reason), 0)) + 1
    return counts


def _runtime_operation_count(
    epochs: Sequence[RuntimeEpoch],
    *,
    exclude_parameter_updates: bool = False,
) -> int:
    total = 0
    for epoch in epochs:
        for operation, count in epoch.network_mutation_operation_counts.items():
            if exclude_parameter_updates and str(operation) == "update_slot_parameters":
                continue
            total += int(count)
    return total


def _span_count_for_sampled_signatures(
    rows: Sequence[Tuple[float, str, Dict[str, Any]]],
) -> int:
    last_by_id: Dict[str, Dict[str, Any]] = {}
    span_count = 0
    for _, identifier, signature in sorted(rows, key=lambda row: (row[0], row[1])):
        previous = last_by_id.get(identifier)
        if previous != signature:
            span_count += 1
            last_by_id[identifier] = dict(signature)
    return span_count


def _link_span_count(snapshots: Sequence[TopologySnapshot]) -> int:
    rows: List[Tuple[float, str, Dict[str, Any]]] = []
    for snapshot in snapshots:
        relative_time = float(snapshot.time_slice.get("relative_time_seconds", 0.0))
        for link_id, signature in _link_topology_map(snapshot).items():
            rows.append((relative_time, link_id, signature))
    return _span_count_for_sampled_signatures(rows)


def _attachment_span_count(
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    snapshots: Sequence[TopologySnapshot],
) -> int:
    rows: List[Tuple[float, str, Dict[str, Any]]] = []
    for snapshot, plan in zip(snapshots, runtime_update_plans):
        relative_time = float(snapshot.time_slice.get("relative_time_seconds", 0.0))
        for flow_id, context in _flow_contexts_from_plan(plan).items():
            ut_id = str(context.get("ut_id") or flow_id)
            rows.append(
                (
                    relative_time,
                    ut_id,
                    {
                        "zone_id": context.get("zone_id"),
                        "serving_satellite_id": context.get("serving_satellite_id"),
                        "selected_gateway_id": context.get("selected_gateway_id"),
                        "selected_gateway_satellite_id": context.get(
                            "selected_gateway_satellite_id"
                        ),
                    },
                )
            )
    return _span_count_for_sampled_signatures(rows)


def _fec_path_span_count(
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    snapshots: Sequence[TopologySnapshot],
) -> int:
    rows: List[Tuple[float, str, Dict[str, Any]]] = []
    for snapshot, plan in zip(snapshots, runtime_update_plans):
        relative_time = float(snapshot.time_slice.get("relative_time_seconds", 0.0))
        groups = _semantic_fec_groups(plan)
        for group_id, group in {
            **groups["g_to_z"],
            **groups["z_to_g"],
        }.items():
            signature = dict(group)
            signature.pop("flow_ids", None)
            signature.pop("ut_ids", None)
            rows.append((relative_time, group_id, signature))
    return _span_count_for_sampled_signatures(rows)


def _parameter_timeline_point_count(snapshots: Sequence[TopologySnapshot]) -> int:
    return sum(len(_link_parameter_map(snapshot)) for snapshot in snapshots)


def _zone_anchor_policy_counts(snapshots: Sequence[TopologySnapshot]) -> Dict[str, int]:
    counts = {
        "active_still_valid_no_change_count": 0,
        "dwell_blocked_change_count": 0,
        "hysteresis_blocked_change_count": 0,
        "hysteresis_approved_better_candidate_count": 0,
    }
    for snapshot in snapshots:
        for zone in snapshot.zones.values():
            reason = str(dict(zone.get("anchor_selection", {})).get("change_reason") or "")
            if reason == "stable_existing_anchor":
                counts["active_still_valid_no_change_count"] += 1
            elif reason == "min_dwell_keep_current_anchor":
                counts["dwell_blocked_change_count"] += 1
            elif reason == "hysteresis_keep_current_anchor":
                counts["hysteresis_blocked_change_count"] += 1
            elif reason in {"challenger_exceeded_hysteresis", "best_zone_anchor_score"}:
                counts["hysteresis_approved_better_candidate_count"] += 1
    return counts


def _ignored_transition_counts(
    *,
    snapshots: Sequence[TopologySnapshot],
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    delay_threshold_ms: float,
    loss_threshold_pct: float,
    bandwidth_threshold_pct: float,
    routing_cost_threshold: int,
) -> Dict[str, int]:
    counts = {
        "candidate_event_count": 0,
        "ignored_cost_only_change_count": 0,
        "ignored_metadata_only_change_count": 0,
    }
    for index in range(1, min(len(snapshots), len(runtime_update_plans))):
        previous_snapshot = snapshots[index - 1]
        current_snapshot = snapshots[index]
        previous_plan = runtime_update_plans[index - 1]
        current_plan = runtime_update_plans[index]
        runtime_analysis = _runtime_semantic_diff(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
            previous_plan=previous_plan,
            current_plan=current_plan,
        )
        parameter_reasons, changed_parameter_links = _parameter_trigger_analysis(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
            previous_epoch_relative_time=float(
                previous_snapshot.time_slice.get("relative_time_seconds", 0.0)
            ),
            current_relative_time=float(
                current_snapshot.time_slice.get("relative_time_seconds", 0.0)
            ),
            parameter_max_interval=None,
            delay_threshold_ms=delay_threshold_ms,
            loss_threshold_pct=loss_threshold_pct,
            bandwidth_threshold_pct=bandwidth_threshold_pct,
            routing_cost_threshold=routing_cost_threshold,
        )
        planner_changed = _planner_signature(previous_plan) != _planner_signature(current_plan)
        semantic_changed = (
            _semantic_flow_signature(previous_plan) != _semantic_flow_signature(current_plan)
        )
        if planner_changed and not semantic_changed and not runtime_analysis["trigger_reasons"]:
            counts["ignored_metadata_only_change_count"] += 1

        cost_only_link_count = 0
        previous_parameters = _link_parameter_map(previous_snapshot)
        current_parameters = _link_parameter_map(current_snapshot)
        for link_id in sorted(set(previous_parameters) & set(current_parameters)):
            delta = _parameter_delta(
                previous=previous_parameters[link_id],
                current=current_parameters[link_id],
            )
            if (
                int(delta["routing_cost_delta"]) > 0
                and float(delta["delay_delta_ms"]) == 0.0
                and float(delta["loss_delta_pct"]) == 0.0
                and float(delta["bandwidth_delta_pct"]) == 0.0
                and int(delta["routing_cost_delta"]) < int(routing_cost_threshold)
            ):
                cost_only_link_count += 1
        if cost_only_link_count and not parameter_reasons:
            counts["ignored_cost_only_change_count"] += cost_only_link_count

        if (
            runtime_analysis["trigger_reasons"]
            or parameter_reasons
            or changed_parameter_links
            or cost_only_link_count
            or (planner_changed and not semantic_changed)
        ):
            counts["candidate_event_count"] += 1
    return counts


def build_preview_update_counters(
    *,
    snapshots: Sequence[TopologySnapshot],
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    runtime_epochs: Sequence[RuntimeEpoch],
    parameter_epochs: Sequence[ParameterEpoch],
    no_op_sample_count: int,
    sample_count: int,
    delay_threshold_ms: float,
    loss_threshold_pct: float,
    bandwidth_threshold_pct: float,
    routing_cost_threshold: int,
    bpf_generation_preload_support: str = DEFAULT_BPF_GENERATION_PRELOAD_SUPPORT,
    event_refinement_precision_seconds: float = DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS,
) -> Dict[str, Any]:
    per_zone: Dict[str, Dict[str, Any]] = {}
    per_ut: Dict[str, Dict[str, Any]] = {}
    runtime_reason_counts = _trigger_reason_counts(runtime_epochs)
    parameter_reason_counts = _trigger_reason_counts(parameter_epochs)

    for epoch in runtime_epochs:
        snapshot = _runtime_snapshot_for_slice(snapshots=snapshots, slice_id=epoch.slice_id)
        plan = _runtime_plan_for_slice(
            snapshots=snapshots,
            runtime_update_plans=runtime_update_plans,
            slice_id=epoch.slice_id,
        )
        flow_contexts = _flow_ids_to_contexts(plan)
        if snapshot is not None:
            for zone_id in sorted(snapshot.zones):
                _bump_distribution(per_zone, zone_id, "runtime_epoch_count")
                if "zone_active_serving_satellite_change" in epoch.trigger_reasons:
                    _bump_distribution(per_zone, zone_id, "zone_active_satellite_change_count")
        for context in flow_contexts.values():
            ut_id = context.get("ut_id")
            zone_id = context.get("zone_id")
            _bump_distribution(per_ut, ut_id, "runtime_epoch_count")
            _bump_distribution(per_zone, zone_id, "runtime_epoch_count", amount=0)
            if "gz_fec_path_change" in epoch.trigger_reasons:
                _bump_distribution(per_ut, ut_id, "gz_fec_path_change_count")
                _bump_distribution(per_zone, zone_id, "gz_fec_path_change_count")
            if "g_to_z_path_change" in epoch.trigger_reasons:
                _bump_distribution(per_ut, ut_id, "g_to_z_change_count")
                _bump_distribution(per_zone, zone_id, "g_to_z_change_count")
            if "z_to_g_path_change" in epoch.trigger_reasons:
                _bump_distribution(per_ut, ut_id, "z_to_g_change_count")
                _bump_distribution(per_zone, zone_id, "z_to_g_change_count")
            if "gw_selection_change" in epoch.trigger_reasons:
                _bump_distribution(per_ut, ut_id, "gw_selection_change_count")
                _bump_distribution(per_zone, zone_id, "gw_selection_change_count")
            if "ut_serving_satellite_change" in epoch.trigger_reasons:
                _bump_distribution(per_ut, ut_id, "ut_serving_satellite_change_count")

    qdisc_parameter_threshold_crossing_count = 0
    for epoch in parameter_epochs:
        qdisc_parameter_threshold_crossing_count += len(epoch.changed_link_ids)
        snapshot = _runtime_snapshot_for_slice(snapshots=snapshots, slice_id=epoch.slice_id)
        plan = _runtime_plan_for_slice(
            snapshots=snapshots,
            runtime_update_plans=runtime_update_plans,
            slice_id=epoch.slice_id,
        )
        flow_contexts = _flow_ids_to_contexts(plan)
        binding_by_link = {}
        if snapshot is not None:
            binding_by_link = {
                str(link.get("link_id")): dict(link.get("binding", {}))
                for link in snapshot.active_links
            }
        for link_id in epoch.changed_link_ids:
            binding = dict(binding_by_link.get(str(link_id), {}))
            ut_id = binding.get("ut_id")
            affected_zone_ids: set[str] = set()
            affected_ut_ids: set[str] = set()
            if ut_id:
                affected_ut_ids.add(str(ut_id))
            if snapshot is not None and ut_id:
                zone_id = snapshot.ut_zone_membership.get(str(ut_id))
                if zone_id:
                    affected_zone_ids.add(str(zone_id))
            for context in flow_contexts.values():
                flow_link_ids = {
                    str(context.get("ut_access_link_id") or ""),
                    str(context.get("gateway_access_link_id") or ""),
                    *[str(value) for value in list(context.get("forward_link_ids", []))],
                    *[str(value) for value in list(context.get("reverse_link_ids", []))],
                }
                if str(link_id) in flow_link_ids and context.get("zone_id"):
                    affected_zone_ids.add(str(context.get("zone_id")))
                    if context.get("ut_id"):
                        affected_ut_ids.add(str(context.get("ut_id")))
            for affected_ut_id in sorted(affected_ut_ids):
                _bump_distribution(per_ut, affected_ut_id, "parameter_epoch_count")
                _bump_distribution(
                    per_ut,
                    affected_ut_id,
                    "qdisc_parameter_threshold_crossing_count",
                )
            for zone_id in sorted(affected_zone_ids):
                _bump_distribution(per_zone, zone_id, "parameter_epoch_count")
                _bump_distribution(per_zone, zone_id, "qdisc_parameter_threshold_crossing_count")

    bpf_generation_count = len(runtime_epochs)
    active_generation_switch_count = bpf_generation_count
    expected_bpf_owned_scope_count = sum(
        _runtime_epoch_fec_scope_count(
            _runtime_plan_for_slice(
                snapshots=snapshots,
                runtime_update_plans=runtime_update_plans,
                slice_id=epoch.slice_id,
            )
        )
        for epoch in runtime_epochs
    )
    expected_qdisc_command_count = sum(len(epoch.changed_link_ids) for epoch in parameter_epochs)
    delay_threshold_crossing_count = int(
        parameter_reason_counts.get("delay_threshold_exceeded", 0)
    )
    bandwidth_threshold_crossing_count = int(
        parameter_reason_counts.get("bandwidth_threshold_exceeded", 0)
    )
    loss_drop_threshold_crossing_count = int(
        parameter_reason_counts.get("loss_threshold_exceeded", 0)
    ) + int(parameter_reason_counts.get("drop_state_changed", 0))
    active_inactive_transition_count = int(
        parameter_reason_counts.get("active_state_changed", 0)
    )
    ignored_counts = _ignored_transition_counts(
        snapshots=snapshots,
        runtime_update_plans=runtime_update_plans,
        delay_threshold_ms=float(delay_threshold_ms),
        loss_threshold_pct=float(loss_threshold_pct),
        bandwidth_threshold_pct=float(bandwidth_threshold_pct),
        routing_cost_threshold=int(routing_cost_threshold),
    )
    stability_counts = _zone_anchor_policy_counts(snapshots)
    runtime_transaction_count = len(runtime_epochs)
    parameter_update_batch_count = len(parameter_epochs)
    refinement_summary = _refinement_counter_summary(
        [*list(runtime_epochs), *list(parameter_epochs)]
    )
    coalesced_event_count = sum(
        1
        for epoch in list(runtime_epochs) + list(parameter_epochs)
        if len(epoch.trigger_reasons) > 1
    )
    return {
        "schema_version": "skyseed-event-timeline-preview-counters-v1",
        "sample_count": int(sample_count),
        "physical_sample_count": int(sample_count),
        "candidate_event_count": int(ignored_counts["candidate_event_count"]),
        "refined_event_count": int(refinement_summary["refined_event_count"]),
        "sample_aligned_event_count": int(
            refinement_summary["sample_aligned_event_count"]
        ),
        "refinement_supported_event_count": int(
            refinement_summary["refinement_supported_event_count"]
        ),
        "refinement_failed_event_count": int(
            refinement_summary["refinement_failed_event_count"]
        ),
        "refinement_unsupported_event_count": int(
            refinement_summary["refinement_unsupported_event_count"]
        ),
        "refinement_precision_seconds": float(event_refinement_precision_seconds),
        "refinement_reason_breakdown": dict(
            refinement_summary["refinement_reason_breakdown"]
        ),
        "runtime_epoch_count": len(runtime_epochs),
        "parameter_epoch_count": len(parameter_epochs),
        "runtime_transaction_count": runtime_transaction_count,
        "parameter_update_batch_count": parameter_update_batch_count,
        "coalesced_event_count": coalesced_event_count,
        "skipped_no_op_sample_count": int(no_op_sample_count),
        "link_span_count": _link_span_count(snapshots),
        "attachment_span_count": _attachment_span_count(runtime_update_plans, snapshots),
        "fec_path_span_count": _fec_path_span_count(runtime_update_plans, snapshots),
        "parameter_timeline_point_count": _parameter_timeline_point_count(snapshots),
        "gz_fec_path_change_count": int(runtime_reason_counts.get("gz_fec_path_change", 0)),
        "g_to_z_change_count": int(runtime_reason_counts.get("g_to_z_path_change", 0)),
        "z_to_g_change_count": int(runtime_reason_counts.get("z_to_g_path_change", 0)),
        "g_z_fec_path_change_count": int(runtime_reason_counts.get("gz_fec_path_change", 0)),
        "g_to_z_path_change_count": int(runtime_reason_counts.get("g_to_z_path_change", 0)),
        "z_to_g_path_change_count": int(runtime_reason_counts.get("z_to_g_path_change", 0)),
        "ut_serving_satellite_change_count": int(
            runtime_reason_counts.get("ut_serving_satellite_change", 0)
        ),
        "zone_active_satellite_change_count": int(
            runtime_reason_counts.get("zone_active_serving_satellite_change", 0)
        ),
        "zone_anchor_change_count": int(
            runtime_reason_counts.get("zone_active_serving_satellite_change", 0)
        ),
        "gw_selection_change_count": int(runtime_reason_counts.get("gw_selection_change", 0)),
        "structural_link_change_count": int(runtime_reason_counts.get("structural_link_change", 0)),
        "structural_link_active_inactive_count": int(
            runtime_reason_counts.get("structural_link_change", 0)
        ),
        "coverage_invalidation_forced_change_count": int(
            runtime_reason_counts.get("coverage_invalidation", 0)
        ),
        "qdisc_parameter_threshold_crossing_count": qdisc_parameter_threshold_crossing_count,
        "delay_threshold_crossing_count": delay_threshold_crossing_count,
        "rate_bandwidth_threshold_crossing_count": bandwidth_threshold_crossing_count,
        "loss_drop_threshold_crossing_count": loss_drop_threshold_crossing_count,
        "active_inactive_transition_count": active_inactive_transition_count,
        "ignored_cost_only_change_count": int(ignored_counts["ignored_cost_only_change_count"]),
        "ignored_metadata_only_change_count": int(
            ignored_counts["ignored_metadata_only_change_count"]
        ),
        "active_still_valid_no_change_count": int(
            stability_counts["active_still_valid_no_change_count"]
        ),
        "dwell_blocked_change_count": int(stability_counts["dwell_blocked_change_count"]),
        "hysteresis_blocked_change_count": int(
            stability_counts["hysteresis_blocked_change_count"]
        ),
        "hysteresis_approved_better_candidate_count": int(
            stability_counts["hysteresis_approved_better_candidate_count"]
        ),
        "parameter_reason_counts": parameter_reason_counts,
        "runtime_reason_counts": runtime_reason_counts,
        "bpf_generation_readiness": {
            "supported_mode": str(bpf_generation_preload_support),
            "current_generation_supported": True,
            "next_generation_preload_supported": (
                str(bpf_generation_preload_support)
                in {"next_generation_preload", "multi_generation_horizon_preload"}
            ),
            "multi_generation_horizon_preload_supported": (
                str(bpf_generation_preload_support) == "multi_generation_horizon_preload"
            ),
            "design_ready_multi_generation_horizon_preload": True,
        },
        "bpf_generation_count": bpf_generation_count,
        "active_generation_switch_count": active_generation_switch_count,
        "bpf_generation_content_change_count": int(
            runtime_reason_counts.get("bpf_generation_content_change", 0)
        ),
        "bpf_generations_follow_real_forwarding_content_changes": (
            bpf_generation_count
            == 1 + int(runtime_reason_counts.get("bpf_generation_content_change", 0))
        ),
        "expected_bpf_owned_scope_count": expected_bpf_owned_scope_count,
        "expected_linux_mpls_lfib_elision_count": expected_bpf_owned_scope_count,
        "expected_qdisc_command_count": expected_qdisc_command_count,
        "expected_qdisc_update_count": expected_qdisc_command_count,
        "expected_lifecycle_link_operation_count": _runtime_operation_count(
            runtime_epochs,
            exclude_parameter_updates=True,
        ),
        "expected_lifecycle_and_parameter_operation_count": _runtime_operation_count(
            runtime_epochs,
            exclude_parameter_updates=False,
        )
        + expected_qdisc_command_count,
        "estimate_basis": {
            "bpf_owned_scope": "semantic G and GZ FEC groups per RuntimeEpoch",
            "linux_mpls_lfib_elision": "one estimated Linux/MPLS LFIB-route scope per BPF-owned semantic FEC group",
            "qdisc_command": "one expected qdisc update per changed ParameterEpoch link",
            "lifecycle_link_operation": "NetworkMutationPlan RuntimeEpoch operations excluding update_slot_parameters",
            "generation_preload": "current-generation live support; next and multi-generation preload are reported honestly as unsupported unless configured",
        },
        "per_zone_update_distribution": [
            dict(entry) for _, entry in sorted(per_zone.items())
        ],
        "per_ut_update_distribution": [
            dict(entry) for _, entry in sorted(per_ut.items())
        ],
    }


def _resource_budget(
    *,
    topology_inventory: Dict[str, Any],
    snapshots: Sequence[TopologySnapshot],
    ut_slot_margin: int,
    gateway_slot_margin: int,
    satellite_isl_slot_margin: int,
) -> Dict[str, Any]:
    slot_inventory = dict(topology_inventory.get("slot_inventory", {}))
    ut_counts: Dict[str, int] = {}
    gateway_counts: Dict[str, int] = {}
    same_plane_degree: Dict[str, int] = {}
    cross_plane_degree: Dict[str, int] = {}
    for snapshot in snapshots:
        sample_ut_counts: Dict[str, int] = {}
        sample_gateway_counts: Dict[str, int] = {}
        sample_cross_plane_degree: Dict[str, int] = {}
        sample_same_plane_degree: Dict[str, int] = {}
        for link in snapshot.active_links:
            slot_type = str(link.get("slot_type"))
            binding = dict(link.get("binding", {}))
            endpoints = [dict(endpoint) for endpoint in link.get("endpoints", [])]
            if slot_type == "ut_access":
                ut_id = str(binding.get("ut_id", ""))
                if ut_id:
                    sample_ut_counts[ut_id] = sample_ut_counts.get(ut_id, 0) + 1
            elif slot_type == "gw_access":
                gateway_id = str(binding.get("gateway_id", ""))
                if gateway_id:
                    sample_gateway_counts[gateway_id] = sample_gateway_counts.get(gateway_id, 0) + 1
            elif slot_type in {"same_plane_isl", "fixed_isl"}:
                for endpoint in endpoints:
                    sat_id = str(endpoint.get("node_id", ""))
                    if sat_id:
                        sample_same_plane_degree[sat_id] = sample_same_plane_degree.get(sat_id, 0) + 1
            elif slot_type == "cross_plane_isl":
                for endpoint in endpoints:
                    sat_id = str(endpoint.get("node_id", ""))
                    if sat_id:
                        sample_cross_plane_degree[sat_id] = sample_cross_plane_degree.get(sat_id, 0) + 1
        for ut_id, count in sample_ut_counts.items():
            ut_counts[ut_id] = max(ut_counts.get(ut_id, 0), count)
        for gateway_id, count in sample_gateway_counts.items():
            gateway_counts[gateway_id] = max(gateway_counts.get(gateway_id, 0), count)
        for sat_id, count in sample_same_plane_degree.items():
            same_plane_degree[sat_id] = max(same_plane_degree.get(sat_id, 0), count)
        for sat_id, count in sample_cross_plane_degree.items():
            cross_plane_degree[sat_id] = max(cross_plane_degree.get(sat_id, 0), count)

    per_ut = {
        ut_id: {
            "max_simultaneous_active_links": count,
            "budgeted_slots": count + int(ut_slot_margin),
        }
        for ut_id, count in sorted(ut_counts.items())
    }
    per_gateway = {
        gateway_id: {
            "max_simultaneous_active_links": count,
            "budgeted_slots": count + int(gateway_slot_margin),
        }
        for gateway_id, count in sorted(gateway_counts.items())
    }
    satellite_ids = sorted(
        str(entry.get("satellite_id"))
        for entry in topology_inventory.get("satellites", [])
        if entry.get("satellite_id")
    )
    per_satellite_isl = {}
    for satellite_id in satellite_ids:
        same_plane = int(same_plane_degree.get(satellite_id, 0))
        cross_plane = int(cross_plane_degree.get(satellite_id, 0))
        per_satellite_isl[satellite_id] = {
            "max_same_plane_degree": same_plane,
            "max_cross_plane_degree": cross_plane,
            "budgeted_total_degree": same_plane + cross_plane + int(satellite_isl_slot_margin),
        }
    return {
        "policy": {
            "ut_slot_margin": int(ut_slot_margin),
            "gateway_slot_margin": int(gateway_slot_margin),
            "satellite_isl_slot_margin": int(satellite_isl_slot_margin),
        },
        "ut_access": {
            "current_slot_inventory_count": len(slot_inventory.get("ut_access", [])),
            "recommended_total_slots": sum(
                int(entry["budgeted_slots"]) for entry in per_ut.values()
            ),
            "per_ut": per_ut,
        },
        "gateway_access": {
            "current_slot_inventory_count": len(slot_inventory.get("gateway_access", [])),
            "recommended_total_slots": sum(
                int(entry["budgeted_slots"]) for entry in per_gateway.values()
            ),
            "per_gateway": per_gateway,
        },
        "satellite_isl": {
            "current_same_plane_slot_inventory_count": len(
                slot_inventory.get("same_plane_isl", [])
            ),
            "current_cross_plane_slot_inventory_count": len(slot_inventory.get("cross_plane_isl", [])),
            "max_same_plane_degree": max(
                (int(entry["max_same_plane_degree"]) for entry in per_satellite_isl.values()),
                default=0,
            ),
            "max_cross_plane_degree": max(
                (int(entry["max_cross_plane_degree"]) for entry in per_satellite_isl.values()),
                default=0,
            ),
            "max_total_degree": max(
                (
                    int(entry["max_same_plane_degree"]) + int(entry["max_cross_plane_degree"])
                    for entry in per_satellite_isl.values()
                ),
                default=0,
            ),
            "per_satellite": per_satellite_isl,
        },
        "bounded_precreation": {
            "precreates_all_future_ut_sat_pairs": False,
            "precreates_all_future_gateway_sat_pairs": False,
            "precreates_all_future_sat_sat_pairs": False,
        },
    }


def build_sample_boundary_temporal_plan(
    *,
    topology_inventory: Dict[str, Any],
    link_state_path: Path,
    propagated_slices: Sequence[PropagatedOrbitSlice],
    snapshots: Sequence[TopologySnapshot],
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    parameter_max_interval: Optional[float],
    delay_threshold_ms: float,
    loss_threshold_pct: float,
    bandwidth_threshold_pct: float,
    routing_cost_threshold: int = DEFAULT_ROUTING_COST_DELTA_THRESHOLD,
    ut_slot_margin: int = 1,
    gateway_slot_margin: int = 1,
    satellite_isl_slot_margin: int = 1,
    runtime_parameter_model: Optional[LinkRuntimeParameterModel] = None,
    event_refinement_precision_seconds: float = DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS,
) -> TemporalPlan:
    parameter_model = runtime_parameter_model or default_link_runtime_parameter_model()
    refinement_precision = max(0.001, float(event_refinement_precision_seconds))
    runtime_parameter_mutation_policy = make_runtime_parameter_mutation_policy(
        delay_threshold_ms=float(delay_threshold_ms),
        loss_threshold_pct=float(loss_threshold_pct),
        bandwidth_threshold_pct=float(bandwidth_threshold_pct),
        routing_cost_threshold=int(routing_cost_threshold),
    )
    enriched_snapshots = [
        enrich_snapshot_runtime_parameters(
            snapshot,
            parameter_model=parameter_model,
        )
        for snapshot in snapshots
    ]
    orbit_samples: List[OrbitSample] = []
    runtime_epochs: List[RuntimeEpoch] = []
    parameter_epochs: List[ParameterEpoch] = []
    observation_frames: List[ObservationFrame] = []
    sample_classification: Dict[int, str] = {}

    runtime_source_state = dynamic_slot_state_from_link_state(
        json.loads(link_state_path.read_text(encoding="utf-8"))
    )
    runtime_source_state_id = f"link_state:{link_state_path.name}"
    last_runtime_index = 0
    last_parameter_index = 0

    for index, (orbit_slice, snapshot) in enumerate(zip(propagated_slices, enriched_snapshots), start=1):
        orbit_samples.append(
            OrbitSample(
                sample_id=f"orbitsample-{index:04d}",
                sample_index=index,
                slice_id=str(orbit_slice.slice_id),
                relative_time_seconds=float(orbit_slice.relative_time_seconds),
                absolute_time_utc=str(orbit_slice.timestamp_utc),
                satellite_count=len(orbit_slice.satellite_states),
            )
        )

        if index == 1:
            current_sample = orbit_samples[-1]
            refinement_metadata = _runtime_event_refinement_metadata(
                previous_sample=None,
                current_sample=current_sample,
                trigger_reasons=("initial_runtime_epoch",),
                precision_seconds=refinement_precision,
            )
            activation_relative_time, activation_time_utc = _activation_from_refinement(
                refinement_metadata
            )
            mutation_plan = build_network_mutation_plan(
                source_state=runtime_source_state,
                source_state_id=runtime_source_state_id,
                target_snapshot=snapshot,
                transition_name=f"{runtime_source_state_id}_to_{snapshot.time_slice.get('slice_id')}",
                runtime_parameter_mutation_policy=runtime_parameter_mutation_policy,
            )
            operation_counts: Dict[str, int] = {}
            for operation in mutation_plan.operations:
                operation_name = str(operation.get("operation"))
                operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
            runtime_epochs.append(
                RuntimeEpoch(
                    epoch_id=f"runtimeepoch-{_fingerprint({'snapshot_id': snapshot.snapshot_id, 'index': index})}",
                    sample_id=current_sample.sample_id,
                    slice_id=str(snapshot.time_slice.get("slice_id")),
                    snapshot_id=snapshot.snapshot_id,
                    activation_relative_time_seconds=activation_relative_time,
                    activation_time_utc=activation_time_utc,
                    activation_group_id=_activation_group_id(activation_relative_time),
                    deadline_policy="execute_not_later_than_sample_boundary",
                    trigger_reasons=("initial_runtime_epoch",),
                    active_link_count=len(snapshot.active_links),
                    changed_link_ids=tuple(sorted(str(link.get("link_id")) for link in snapshot.active_links)),
                    network_mutation_operation_count=len(mutation_plan.operations),
                    network_mutation_operation_counts=operation_counts,
                    samples_since_previous_runtime_epoch=1,
                    **_epoch_refinement_init_fields(refinement_metadata),
                )
            )
            sample_classification[index - 1] = "runtime_epoch"
            runtime_source_state = dynamic_slot_state_from_snapshot(snapshot)
            runtime_source_state_id = snapshot.snapshot_id
            last_runtime_index = index - 1
            last_parameter_index = index - 1
            continue

        previous_snapshot = enriched_snapshots[index - 2]
        previous_plan = runtime_update_plans[index - 2]
        current_plan = runtime_update_plans[index - 1]
        runtime_analysis = _runtime_semantic_diff(
            previous_snapshot=previous_snapshot,
            current_snapshot=snapshot,
            previous_plan=previous_plan,
            current_plan=current_plan,
        )
        runtime_reasons = list(runtime_analysis["trigger_reasons"])
        changed_links = list(runtime_analysis["changed_link_ids"])
        if runtime_reasons:
            previous_sample = orbit_samples[index - 2]
            current_sample = orbit_samples[-1]
            refinement_metadata = _runtime_event_refinement_metadata(
                previous_sample=previous_sample,
                current_sample=current_sample,
                trigger_reasons=runtime_reasons,
                precision_seconds=refinement_precision,
            )
            activation_relative_time, activation_time_utc = _activation_from_refinement(
                refinement_metadata
            )
            mutation_plan = build_network_mutation_plan(
                source_state=runtime_source_state,
                source_state_id=runtime_source_state_id,
                target_snapshot=snapshot,
                transition_name=f"{runtime_source_state_id}_to_{snapshot.time_slice.get('slice_id')}",
                runtime_parameter_mutation_policy=runtime_parameter_mutation_policy,
            )
            operation_counts: Dict[str, int] = {}
            for operation in mutation_plan.operations:
                operation_name = str(operation.get("operation"))
                operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
            runtime_epochs.append(
                RuntimeEpoch(
                    epoch_id=f"runtimeepoch-{_fingerprint({'snapshot_id': snapshot.snapshot_id, 'index': index})}",
                    sample_id=current_sample.sample_id,
                    slice_id=str(snapshot.time_slice.get("slice_id")),
                    snapshot_id=snapshot.snapshot_id,
                    activation_relative_time_seconds=activation_relative_time,
                    activation_time_utc=activation_time_utc,
                    activation_group_id=_activation_group_id(activation_relative_time),
                    deadline_policy="execute_not_later_than_sample_boundary",
                    trigger_reasons=tuple(runtime_reasons),
                    active_link_count=len(snapshot.active_links),
                    changed_link_ids=tuple(changed_links),
                    network_mutation_operation_count=len(mutation_plan.operations),
                    network_mutation_operation_counts=operation_counts,
                    samples_since_previous_runtime_epoch=index - last_runtime_index,
                    **_epoch_refinement_init_fields(refinement_metadata),
                )
            )
            sample_classification[index - 1] = "runtime_epoch"
            runtime_source_state = dynamic_slot_state_from_snapshot(snapshot)
            runtime_source_state_id = snapshot.snapshot_id
            last_runtime_index = index - 1
            last_parameter_index = index - 1
            continue

        parameter_reasons, changed_parameter_links = _parameter_trigger_analysis(
            previous_snapshot=enriched_snapshots[last_parameter_index],
            current_snapshot=snapshot,
            previous_epoch_relative_time=float(
                enriched_snapshots[last_parameter_index].time_slice.get("relative_time_seconds", 0.0)
            ),
            current_relative_time=float(snapshot.time_slice.get("relative_time_seconds", 0.0)),
            parameter_max_interval=parameter_max_interval,
            delay_threshold_ms=float(delay_threshold_ms),
            loss_threshold_pct=float(loss_threshold_pct),
            bandwidth_threshold_pct=float(bandwidth_threshold_pct),
            routing_cost_threshold=int(routing_cost_threshold),
        )
        if parameter_reasons:
            current_sample = orbit_samples[-1]
            previous_parameter_sample = orbit_samples[last_parameter_index]
            refinement_metadata = _parameter_event_refinement_metadata(
                previous_sample=previous_parameter_sample,
                current_sample=current_sample,
                precision_seconds=refinement_precision,
            )
            activation_relative_time, activation_time_utc = _activation_from_refinement(
                refinement_metadata
            )
            changed_counts = {
                "delay_threshold_exceeded": 0,
                "loss_threshold_exceeded": 0,
                "drop_state_changed": 0,
                "bandwidth_threshold_exceeded": 0,
                "routing_cost_changed": 0,
                "active_state_changed": 0,
            }
            for link_change in changed_parameter_links.values():
                for reason in link_change.get("trigger_reasons", []):
                    changed_counts[reason] = changed_counts.get(reason, 0) + 1
            parameter_epochs.append(
                ParameterEpoch(
                    epoch_id=f"parameterepoch-{_fingerprint({'snapshot_id': snapshot.snapshot_id, 'index': index})}",
                    sample_id=current_sample.sample_id,
                    slice_id=str(snapshot.time_slice.get("slice_id")),
                    snapshot_id=snapshot.snapshot_id,
                    activation_relative_time_seconds=activation_relative_time,
                    activation_time_utc=activation_time_utc,
                    activation_group_id=_activation_group_id(activation_relative_time),
                    update_policy="threshold_or_interval_refresh_without_topology_mutation",
                    trigger_reasons=tuple(parameter_reasons),
                    active_link_count=len(snapshot.active_links),
                    changed_link_ids=tuple(sorted(changed_parameter_links)),
                    changed_parameter_counts=changed_counts,
                    samples_since_previous_parameter_epoch=index - last_parameter_index,
                    **_epoch_refinement_init_fields(refinement_metadata),
                )
            )
            sample_classification[index - 1] = "parameter_epoch"
            last_parameter_index = index - 1
        else:
            sample_classification[index - 1] = "no_op"

    for index, (sample, snapshot, runtime_update_plan) in enumerate(
        zip(orbit_samples, enriched_snapshots, runtime_update_plans),
        start=1,
    ):
        observation_frames.append(
            ObservationFrame(
                frame_id=f"obsframe-{index:04d}",
                sample_id=sample.sample_id,
                slice_id=str(snapshot.time_slice.get("slice_id")),
                snapshot_id=snapshot.snapshot_id,
                activation_relative_time_seconds=float(sample.relative_time_seconds),
                activation_time_utc=str(sample.absolute_time_utc),
                activation_group_id=f"activation-{float(sample.relative_time_seconds):012.3f}",
                observation_policy="coarse_sample_frame",
                classification=sample_classification[index - 1],
                active_link_count=len(snapshot.active_links),
                active_serving_satellite_ids=active_serving_satellite_ids(snapshot),
                gateway_exit_summary=gateway_exit_summary(runtime_update_plan),
                flow_path_summaries=flow_path_summaries(runtime_update_plan),
            )
        )

    resource_budget = _resource_budget(
        topology_inventory=topology_inventory,
        snapshots=enriched_snapshots,
        ut_slot_margin=int(ut_slot_margin),
        gateway_slot_margin=int(gateway_slot_margin),
        satellite_isl_slot_margin=int(satellite_isl_slot_margin),
    )
    preparation_plan = {
        "strict_mode": True,
        "moved_to_preparation": [
            "dense_orbit_sample_propagation",
            "topology_snapshot_generation_per_sample",
            "runtime_update_plan_precomputation_per_sample",
            "runtime_epoch_trigger_detection",
            "parameter_epoch_trigger_detection",
            "runtime_delta_chain_precomputation",
            "parameter_delta_summary_precomputation",
            "bounded_resource_budget_computation",
        ],
        "runtime_parameter_model": {
            "model_name": str(parameter_model.model_name),
        },
        "prepared_artifacts": {
            "orbit_sample_count": len(orbit_samples),
            "topology_snapshot_count": len(enriched_snapshots),
            "runtime_epoch_count": len(runtime_epochs),
            "parameter_epoch_count": len(parameter_epochs),
        },
        "continuous_operation_prepared": {
            "initial_horizon_precomputed": True,
            "epoch_queue_prepared": True,
            "rolling_horizon_replenishment": "prepared_not_yet_live",
            "runtime_epoch_batching_policy": "same_activation_group_only",
            "parameter_epoch_execution_model": "lightweight_parameter_refresh_prepared",
        },
        "strict_mode_not_allowed": [
            "activate_future_links_early",
            "install_forwarding_state_usable_by_current_packets_early",
            "make_hidden_future_paths_usable",
        ],
    }
    preview_update_counters = build_preview_update_counters(
        snapshots=enriched_snapshots,
        runtime_update_plans=runtime_update_plans,
        runtime_epochs=runtime_epochs,
        parameter_epochs=parameter_epochs,
        no_op_sample_count=sum(1 for value in sample_classification.values() if value == "no_op"),
        sample_count=len(orbit_samples),
        delay_threshold_ms=float(delay_threshold_ms),
        loss_threshold_pct=float(loss_threshold_pct),
        bandwidth_threshold_pct=float(bandwidth_threshold_pct),
        routing_cost_threshold=int(routing_cost_threshold),
        event_refinement_precision_seconds=refinement_precision,
    )
    event_timeline_projection = {
        "schema_version": "skyseed-event-timeline-projection-v1",
        "physical_sample_model": "samples_discover_candidate_events_only",
        "event_time_accuracy": {
            "refinement_status": "enabled_bounded_local_refinement",
            "sample_aligned_event_count": int(
                preview_update_counters["sample_aligned_event_count"]
            ),
            "refined_event_count": int(preview_update_counters["refined_event_count"]),
            "refinement_supported_event_count": int(
                preview_update_counters["refinement_supported_event_count"]
            ),
            "refinement_failed_event_count": int(
                preview_update_counters["refinement_failed_event_count"]
            ),
            "refinement_unsupported_event_count": int(
                preview_update_counters["refinement_unsupported_event_count"]
            ),
            "refinement_precision_seconds": float(
                preview_update_counters["refinement_precision_seconds"]
            ),
            "refinement_reason_breakdown": dict(
                preview_update_counters["refinement_reason_breakdown"]
            ),
        },
        "compatibility_adapters": {
            "runtime_transaction_to_runtime_epoch": True,
            "parameter_update_batch_to_parameter_epoch": True,
        },
        "runtime_transaction_count": int(
            preview_update_counters["runtime_transaction_count"]
        ),
        "parameter_update_batch_count": int(
            preview_update_counters["parameter_update_batch_count"]
        ),
        "candidate_event_count": int(preview_update_counters["candidate_event_count"]),
        "span_counts": {
            "link_span_count": int(preview_update_counters["link_span_count"]),
            "attachment_span_count": int(preview_update_counters["attachment_span_count"]),
            "fec_path_span_count": int(preview_update_counters["fec_path_span_count"]),
            "parameter_timeline_point_count": int(
                preview_update_counters["parameter_timeline_point_count"]
            ),
        },
    }
    return TemporalPlan(
        orbit_samples=tuple(orbit_samples),
        runtime_epochs=tuple(runtime_epochs),
        parameter_epochs=tuple(parameter_epochs),
        observation_frames=tuple(observation_frames),
        event_queue=tuple(),
        no_op_sample_count=sum(1 for value in sample_classification.values() if value == "no_op"),
        resource_budget=resource_budget,
        preparation_plan=preparation_plan,
        event_time_summary={
            "mode": "event_timeline_adapter_to_sample_boundary_queue",
            "runtime_epoch_compatibility_adapter": "RuntimeTransaction -> RuntimeEpoch",
            "parameter_epoch_compatibility_adapter": "ParameterUpdateBatch -> ParameterEpoch",
            "event_timeline_projection": event_timeline_projection,
            "evaluation_sample_count": len(orbit_samples),
            "observation_event_count": len(observation_frames),
            "topology_event_count": len(runtime_epochs),
            "parameter_event_count": len(parameter_epochs),
            "periodic_refresh_event_count": sum(
                1
                for epoch in parameter_epochs
                if "parameter_max_interval_elapsed" in set(epoch.trigger_reasons)
            ),
            "threshold_parameter_event_count": sum(
                1
                for epoch in parameter_epochs
                if any(reason != "parameter_max_interval_elapsed" for reason in epoch.trigger_reasons)
            ),
            "maximum_event_scheduling_delay_seconds": 0.0,
            "preview_update_counters": preview_update_counters,
        },
    )


def build_temporal_plan(
    *,
    topology_inventory: Dict[str, Any],
    link_state_path: Path,
    propagated_slices: Sequence[PropagatedOrbitSlice],
    snapshots: Sequence[TopologySnapshot],
    runtime_update_plans: Sequence[RuntimeUpdatePlan | Dict[str, Any]],
    parameter_max_interval: Optional[float],
    delay_threshold_ms: float,
    loss_threshold_pct: float,
    bandwidth_threshold_pct: float,
    routing_cost_threshold: int = DEFAULT_ROUTING_COST_DELTA_THRESHOLD,
    ut_slot_margin: int = 1,
    gateway_slot_margin: int = 1,
    satellite_isl_slot_margin: int = 1,
    observation_interval_seconds: Optional[float] = None,
    event_evaluation_interval_seconds: Optional[float] = None,
    runtime_parameter_model: Optional[LinkRuntimeParameterModel] = None,
    event_refinement_precision_seconds: float = DEFAULT_EVENT_REFINEMENT_PRECISION_SECONDS,
) -> TemporalPlan:
    base_plan = build_sample_boundary_temporal_plan(
        topology_inventory=topology_inventory,
        link_state_path=link_state_path,
        propagated_slices=propagated_slices,
        snapshots=snapshots,
        runtime_update_plans=runtime_update_plans,
        parameter_max_interval=parameter_max_interval,
        delay_threshold_ms=delay_threshold_ms,
        loss_threshold_pct=loss_threshold_pct,
        bandwidth_threshold_pct=bandwidth_threshold_pct,
        routing_cost_threshold=routing_cost_threshold,
        ut_slot_margin=ut_slot_margin,
        gateway_slot_margin=gateway_slot_margin,
        satellite_isl_slot_margin=satellite_isl_slot_margin,
        runtime_parameter_model=runtime_parameter_model,
        event_refinement_precision_seconds=event_refinement_precision_seconds,
    )
    observation_indexes = _observation_sample_index_set(
        base_plan.orbit_samples,
        observation_interval_seconds=observation_interval_seconds,
    )
    filtered_observation_frames = tuple(
        frame
        for index, frame in enumerate(base_plan.observation_frames)
        if index in observation_indexes
    )

    snapshots_by_id = {
        str(snapshot.snapshot_id): snapshot
        for snapshot in snapshots
    }
    events: List[TemporalEvent] = []
    for epoch in base_plan.runtime_epochs:
        snapshot = snapshots_by_id.get(str(epoch.snapshot_id))
        owner_keys = _epoch_conflict_owner_keys(
            snapshot=snapshot if snapshot is not None else snapshots[-1],
            changed_link_ids=epoch.changed_link_ids,
        )
        events.append(
            TemporalEvent(
                event_id=f"tempevent-{epoch.epoch_id}",
                event_type="runtime_epoch",
                source_sample_id=epoch.sample_id,
                slice_id=epoch.slice_id,
                snapshot_id=epoch.snapshot_id,
                activation_relative_time_seconds=float(epoch.activation_relative_time_seconds),
                activation_time_utc=str(epoch.activation_time_utc),
                activation_group_id=str(epoch.activation_group_id),
                detected_relative_time_seconds=float(
                    epoch.sample_aligned_relative_time_seconds
                ),
                detected_time_utc=str(epoch.sample_aligned_time_utc),
                execution_policy=str(epoch.deadline_policy),
                trigger_reasons=tuple(epoch.trigger_reasons),
                conflict_owner_keys=owner_keys,
                linked_epoch_id=str(epoch.epoch_id),
                linked_frame_id=None,
                scheduling_delay_seconds=max(
                    0.0,
                    float(epoch.sample_aligned_relative_time_seconds)
                    - float(epoch.activation_relative_time_seconds),
                ),
                metadata={
                    "active_link_count": int(epoch.active_link_count),
                    "changed_link_count": len(epoch.changed_link_ids),
                    "network_mutation_operation_count": int(epoch.network_mutation_operation_count),
                    **_epoch_refinement_metadata(epoch),
                },
            )
        )
    for epoch in base_plan.parameter_epochs:
        snapshot = snapshots_by_id.get(str(epoch.snapshot_id))
        owner_keys = _epoch_conflict_owner_keys(
            snapshot=snapshot if snapshot is not None else snapshots[-1],
            changed_link_ids=epoch.changed_link_ids,
        )
        refresh_kind = (
            "periodic_refresh"
            if "parameter_max_interval_elapsed" in set(epoch.trigger_reasons)
            else "threshold_crossing"
        )
        events.append(
            TemporalEvent(
                event_id=f"tempevent-{epoch.epoch_id}",
                event_type="parameter_epoch",
                source_sample_id=epoch.sample_id,
                slice_id=epoch.slice_id,
                snapshot_id=epoch.snapshot_id,
                activation_relative_time_seconds=float(epoch.activation_relative_time_seconds),
                activation_time_utc=str(epoch.activation_time_utc),
                activation_group_id=str(epoch.activation_group_id),
                detected_relative_time_seconds=float(
                    epoch.sample_aligned_relative_time_seconds
                ),
                detected_time_utc=str(epoch.sample_aligned_time_utc),
                execution_policy=str(epoch.update_policy),
                trigger_reasons=tuple(epoch.trigger_reasons),
                conflict_owner_keys=owner_keys,
                linked_epoch_id=str(epoch.epoch_id),
                linked_frame_id=None,
                scheduling_delay_seconds=max(
                    0.0,
                    float(epoch.sample_aligned_relative_time_seconds)
                    - float(epoch.activation_relative_time_seconds),
                ),
                metadata={
                    "active_link_count": int(epoch.active_link_count),
                    "changed_link_count": len(epoch.changed_link_ids),
                    "changed_parameter_counts": dict(epoch.changed_parameter_counts),
                    "parameter_event_kind": refresh_kind,
                    **_epoch_refinement_metadata(epoch),
                },
            )
        )
    for frame in filtered_observation_frames:
        events.append(
            TemporalEvent(
                event_id=f"tempevent-{frame.frame_id}",
                event_type="observation_event",
                source_sample_id=frame.sample_id,
                slice_id=frame.slice_id,
                snapshot_id=frame.snapshot_id,
                activation_relative_time_seconds=float(frame.activation_relative_time_seconds),
                activation_time_utc=str(frame.activation_time_utc),
                activation_group_id=str(frame.activation_group_id),
                detected_relative_time_seconds=float(frame.activation_relative_time_seconds),
                detected_time_utc=str(frame.activation_time_utc),
                execution_policy=str(frame.observation_policy),
                trigger_reasons=(str(frame.classification),),
                conflict_owner_keys=tuple(),
                linked_epoch_id=None,
                linked_frame_id=str(frame.frame_id),
                scheduling_delay_seconds=0.0,
                metadata={
                    "active_link_count": int(frame.active_link_count),
                    "flow_path_count": len(frame.flow_path_summaries),
                },
            )
        )
    event_queue = tuple(_sorted_temporal_events(events))
    scheduling_delays = [float(event.scheduling_delay_seconds) for event in event_queue]
    maximum_event_scheduling_delay_seconds = (
        max(scheduling_delays) if scheduling_delays else 0.0
    )
    average_event_scheduling_delay_seconds = (
        sum(scheduling_delays) / len(scheduling_delays) if scheduling_delays else 0.0
    )
    updated_preparation_plan = dict(base_plan.preparation_plan)
    continuous_operation_prepared = dict(updated_preparation_plan.get("continuous_operation_prepared", {}))
    continuous_operation_prepared.update(
        {
            "event_time_queue_prepared": True,
            "observation_frames_scheduled_independently": True,
            "event_queue_owner_key_serialization": [
                "link_id",
                "slot_name",
                "endpoint_interface",
                "node_role",
            ],
        }
    )
    updated_preparation_plan["continuous_operation_prepared"] = continuous_operation_prepared
    return TemporalPlan(
        orbit_samples=base_plan.orbit_samples,
        runtime_epochs=base_plan.runtime_epochs,
        parameter_epochs=base_plan.parameter_epochs,
        observation_frames=filtered_observation_frames,
        event_queue=event_queue,
        no_op_sample_count=base_plan.no_op_sample_count,
        resource_budget=base_plan.resource_budget,
        preparation_plan=updated_preparation_plan,
        event_time_summary={
            "mode": "event_timeline_queue",
            "runtime_epoch_compatibility_adapter": "RuntimeTransaction -> RuntimeEpoch",
            "parameter_epoch_compatibility_adapter": "ParameterUpdateBatch -> ParameterEpoch",
            "event_timeline_projection": dict(
                base_plan.event_time_summary.get("event_timeline_projection", {})
            ),
            "evaluation_sample_count": len(base_plan.orbit_samples),
            "event_evaluation_interval_seconds": (
                float(event_evaluation_interval_seconds)
                if event_evaluation_interval_seconds is not None
                else None
            ),
            "observation_interval_seconds": (
                float(observation_interval_seconds)
                if observation_interval_seconds is not None
                else None
            ),
            "topology_event_count": len(base_plan.runtime_epochs),
            "parameter_event_count": len(base_plan.parameter_epochs),
            "periodic_refresh_event_count": sum(
                1
                for epoch in base_plan.parameter_epochs
                if "parameter_max_interval_elapsed" in set(epoch.trigger_reasons)
            ),
            "threshold_parameter_event_count": sum(
                1
                for epoch in base_plan.parameter_epochs
                if any(reason != "parameter_max_interval_elapsed" for reason in epoch.trigger_reasons)
            ),
            "observation_event_count": len(filtered_observation_frames),
            "maximum_event_scheduling_delay_seconds": maximum_event_scheduling_delay_seconds,
            "average_event_scheduling_delay_seconds": average_event_scheduling_delay_seconds,
            "preview_update_counters": dict(
                base_plan.event_time_summary.get("preview_update_counters", {})
            ),
        },
    )
