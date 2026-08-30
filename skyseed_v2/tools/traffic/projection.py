"""Project durable traffic evidence into non-authoritative replay facts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from ipaddress import ip_interface
import re
from typing import Any

from .reader import TrafficReplayRead


_SATELLITE_NAME = re.compile(
    r"dynamic-shell-orbit-(?P<orbit>[0-9]+)-satellite-(?P<satellite>[0-9]+)$"
)


def project_traffic_replay(observation: TrafficReplayRead) -> dict[str, Any]:
    """Build endpoint, transaction, timeline, and display-only Position facts."""

    if not isinstance(observation, TrafficReplayRead):
        raise TypeError("observation must be a TrafficReplayRead")
    source = deepcopy(observation.snapshot)
    nodes = {int(value["node_slot"]): value for value in source["nodes"]}
    nodes_by_ipv4 = {value["runtime_ipv4"]: value for value in nodes.values()}
    dynamic_nodes = {value["node_id"]: value for value in source["dynamic_nodes"]}
    interfaces = {
        (int(value["generation"]), int(value["node_slot"]), int(value["ifindex"])): value
        for value in source["interfaces"]
    }
    networks = {
        int(value["generation"]): value for value in source["generation_networks"]
    }
    samples = tuple(
        (datetime.fromisoformat(value["timestamp"]), value)
        for value in source["position_samples"]
    )
    anchor = source["clock_anchor"]
    nominal_origin = datetime.fromisoformat(anchor["nominal_origin"])
    monotonic_origin_ns = int(anchor["monotonic_origin_ns"])
    zone_by_ipv4 = {
        str(ip_interface(value["packet_prefix"]).ip): value
        for value in source["compiler"]["zone_prefixes"]
    }
    loss_total = sum(int(value["lost_events"]) for value in source["loss_records"])
    recorder_end = source["recorder_end"]
    recorder_complete = bool(
        recorder_end is not None and recorder_end.get("complete") is True
    )
    inactive_ingress_associations = sum(
        packet.get("ingress_identity", {}).get("packet_identity_validity")
        == "unique-observed-physical-inactive-ingress"
        for packet in source["packets"]
    )

    grouped: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    unclassified = []
    for packet in source["packets"]:
        icmp = packet["icmp"]
        if (
            packet["protocol"] == 1
            and icmp.get("valid") is True
            and isinstance(icmp.get("identifier"), int)
            and isinstance(icmp.get("sequence"), int)
            and isinstance(icmp.get("type"), int)
        ):
            key = (
                packet["source_ipv4"],
                packet["destination_ipv4"],
                int(icmp["type"]),
                int(icmp["identifier"]),
                int(icmp["sequence"]),
            )
            grouped[key].append(packet)
        else:
            unclassified.append(
                _unclassified_packet(
                    packet,
                    nodes=nodes,
                    interfaces=interfaces,
                    nominal_origin=nominal_origin,
                    monotonic_origin_ns=monotonic_origin_ns,
                )
            )

    traffic_records = []
    for key, packets in sorted(
        grouped.items(),
        key=lambda item: min(int(value["kernel_monotonic_ns"]) for value in item[1]),
    ):
        ordered = sorted(packets, key=lambda value: int(value["kernel_monotonic_ns"]))
        source_endpoint = _resolve_endpoint(
            str(key[0]),
            int(ordered[0]["generation"]),
            nodes_by_ipv4=nodes_by_ipv4,
            zone_by_ipv4=zone_by_ipv4,
            networks=networks,
            dynamic_nodes=dynamic_nodes,
        )
        destination_endpoint = _resolve_endpoint(
            str(key[1]),
            int(ordered[-1]["generation"]),
            nodes_by_ipv4=nodes_by_ipv4,
            zone_by_ipv4=zone_by_ipv4,
            networks=networks,
            dynamic_nodes=dynamic_nodes,
        )
        if source_endpoint["endpoint_kind"] == "unknown" or destination_endpoint[
            "endpoint_kind"
        ] == "unknown":
            unclassified.append(
                _unclassified_icmp_group(
                    key,
                    ordered,
                    source_endpoint=source_endpoint,
                    destination_endpoint=destination_endpoint,
                    nodes=nodes,
                    interfaces=interfaces,
                    nominal_origin=nominal_origin,
                    monotonic_origin_ns=monotonic_origin_ns,
                )
            )
            continue
        traffic_records.append(
            _project_icmp_record(
                len(traffic_records) + 1,
                key,
                ordered,
                source_endpoint=source_endpoint,
                destination_endpoint=destination_endpoint,
                nodes=nodes,
                dynamic_nodes=dynamic_nodes,
                interfaces=interfaces,
                networks=networks,
                nominal_origin=nominal_origin,
                monotonic_origin_ns=monotonic_origin_ns,
                recorder_complete=recorder_complete,
                loss_total=loss_total,
            )
        )

    ping_transactions = _pair_ping_transactions(traffic_records)
    active_networks = tuple(
        {
            **network,
            "zone_services": tuple(
                {
                    **value,
                    "service_display_name": _display_name(
                        value["service_satellite"],
                        dynamic_nodes[value["service_satellite"]],
                    ),
                }
                for value in network["zone_services"]
            ),
        }
        for network in source["generation_networks"]
    )
    workload = source["workload"]
    if workload is None:
        ping_transactions = tuple(
            {**transaction, "series_id": None}
            for transaction in ping_transactions
        )
        traffic_lanes: tuple[dict[str, Any], ...] = ()
    else:
        ping_transactions, traffic_lanes = _project_workload_activity(
            ping_transactions,
            workload,
        )
    ping_transactions, observed_flows = _project_observed_flows(
        ping_transactions
    )
    timeline = _project_simulation_timeline(
        source["timeline"],
        active_networks,
        source["compiler"]["zone_events"],
        ping_transactions,
        dynamic_nodes,
    )
    paired = sum(value["pairing_status"] == "paired" for value in ping_transactions)
    workload_slots = 0 if workload is None else int(
        workload["inventory"]["scheduled_slots"]
    )
    workload_observed = sum(
        value["observed_transactions"] for value in traffic_lanes
    )
    workload_gaps = sum(value["observed_gap_slots"] for value in traffic_lanes)
    qualification = _project_probe_qualification(
        source["qualification"],
        ping_transactions=ping_transactions,
        telemetry_complete=recorder_complete,
        telemetry_losses=loss_total,
    )

    return {
        "format_version": 3,
        "tool": "traffic-path-replay",
        "authority": {
            "status": "non-authoritative-view",
            "traffic_source": "durable tc-BPF ingress telemetry",
            "active_network_source": (
                "packet generation plus exact-generation active interface identity; "
                "inactive-ingress drops may use one unambiguous observed physical "
                "identity without entering the active graph"
            ),
            "zone_source": "already-produced BPF Compiler inspection evidence",
            "position_source": "retained Slice / Difference Position facts",
            "workload_source": (
                None
                if workload is None
                else "optional example-owned planned traffic workload"
            ),
            "interpolation": (
                "Interpolated position is visualization-only and is not a "
                "retained Simulator Position fact."
            ),
            "runtime_control": False,
            "network_semantic_authority": False,
        },
        "overview": {
            "telemetry_complete": recorder_complete,
            "reported_losses": loss_total,
            "ping_transactions": len(ping_transactions),
            "paired_ping_transactions": paired,
            "directional_icmp_records": len(traffic_records),
            "grouped_packet_observations": sum(
                len(value["observed_path"]) for value in traffic_records
            ),
            "raw_unclassified_observations": len(unclassified),
            "node_identities": len(nodes),
            "interface_identities": len(interfaces),
            "inactive_ingress_physical_identity_associations": (
                inactive_ingress_associations
            ),
            "nominal_boundaries": len(active_networks),
            "foundation_token": source["header"]["foundation_token"],
            "observed_flows": len(observed_flows),
            "workload_present": workload is not None,
            "workload_series": len(traffic_lanes),
            "scheduled_slots": workload_slots,
            "observed_workload_transactions": workload_observed,
            "scheduled_slots_without_tc_bpf_observation": workload_gaps,
            "qualification_status": (
                None if qualification is None else qualification["summary"]["status"]
            ),
        },
        "clock": {
            **dict(anchor),
            "mapping": (
                "nominal_origin + (packet.kernel_monotonic_ns - "
                "monotonic_origin_ns)"
            ),
        },
        "position": {
            "sample_count": len(samples),
            "first_sample": samples[0][0].isoformat(),
            "last_sample": samples[-1][0].isoformat(),
            "retained_interval_seconds": source["timeline"].get(
                "slice_interval_seconds"
            ),
            "interpolation_notice": (
                "Interpolated position is visualization-only and is not a "
                "retained Simulator Position fact."
            ),
        },
        "visualization": {
            "recent_traffic_default_seconds": 5,
            "recent_traffic_options_seconds": (2, 5, 10),
            "packet_animation": "visual-time-expansion",
            "recorded_timing_authoritative": True,
            "persistence_clock": "simulation-time",
            "persistence_represents": (
                "real observations inside the selected recent simulation-time window"
            ),
            "persistence_does_not_represent": (
                "RTT",
                "throughput",
                "traffic volume",
                "queue occupancy",
            ),
        },
        "simulation_timeline": timeline,
        "active_networks": active_networks,
        "nodes": tuple(
            {
                **value,
                "display_name": _display_name(node_id, value),
            }
            for node_id, value in sorted(dynamic_nodes.items())
        ),
        "zones": tuple(
            _project_zone_geometry(value)
            for _zone_id, value in sorted(
                (
                    (value["zone_id"], value)
                    for value in source["dynamic_zones"]
                ),
                key=lambda item: item[0],
            )
        ),
        "position_samples": source["position_samples"],
        "workload": workload,
        "qualification": qualification,
        "traffic_lanes": traffic_lanes,
        "observed_flows": observed_flows,
        "ping_transactions": tuple(ping_transactions),
        "unclassified_observations": tuple(unclassified),
        "inventory": {
            "ping_transactions": len(ping_transactions),
            "paired_ping_transactions": paired,
            "directional_icmp_records": len(traffic_records),
            "packet_observations": len(source["packets"]),
            "unclassified_observations": len(unclassified),
            "reported_losses": loss_total,
            "position_samples": len(samples),
            "nominal_boundaries": len(active_networks),
            "zone_events": len(source["compiler"]["zone_events"]),
            "observed_flows": len(observed_flows),
            "workload_series": len(traffic_lanes),
            "scheduled_slots": workload_slots,
            "observed_workload_transactions": workload_observed,
            "observed_gap_slots": workload_gaps,
            "qualification_probe_identities": (
                0 if qualification is None else qualification["summary"]["probe_identities"]
            ),
            "qualification_episodes": (
                0 if qualification is None else qualification["summary"]["episodes"]
            ),
        },
        "raw_evidence": {
            "telemetry_header": source["header"],
            "simulation_clock_anchor": anchor,
            "compiler": source["compiler"],
            "dynamic_timeline": source["timeline"],
            "workload_authority": (
                None if workload is None else workload["authority"]
            ),
            "loss_records": source["loss_records"],
            "recorder_end": recorder_end,
            "probe_plan": (
                None if source["qualification"] is None else source["qualification"]["plan"]
            ),
            "probe_execution_records": (
                None
                if source["qualification"] is None
                else source["qualification"]["execution"]
            ),
            "runner_events": (
                None
                if source["qualification"] is None
                else source["qualification"]["runner_events"]
            ),
            "record_count": len(source["raw_records"]),
            "raw_record_artifact": "traffic-telemetry.jsonl",
        },
    }


def _project_probe_qualification(
    value: dict[str, Any] | None,
    *,
    ping_transactions: tuple[dict[str, Any], ...],
    telemetry_complete: bool,
    telemetry_losses: int,
) -> dict[str, Any] | None:
    if value is None:
        return None
    plan = value["plan"]
    execution = tuple(value["execution"])
    runner_events = tuple(value["runner_events"])
    horizon = plan["planning_horizon"]
    identities = {
        item["probe_id"]: dict(item) for item in plan["probe_identities"]
    }
    episodes = {item["episode_id"]: dict(item) for item in plan["episodes"]}
    records_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in execution[1:]:
        episode_id = record.get("episode_id")
        if episode_id in episodes:
            records_by_episode[str(episode_id)].append(record)
    executor_end = next(
        item for item in execution if item.get("record_type") == "executor_end"
    )
    execution_complete = bool(
        executor_end.get("complete") is True
        and executor_end.get("cleanup_complete") is True
        and int(executor_end.get("active_generators", -1)) == 0
    )
    evidence_complete = bool(
        execution_complete and telemetry_complete and telemetry_losses == 0
    )
    activations = tuple(
        item for item in runner_events if item.get("event") == "activation_end"
    )
    switches = tuple(
        (
            int(item["generation_switch_start_monotonic_ns"]),
            int(item["generation_switch_end_monotonic_ns"]),
            str(item["active_frontier"]),
        )
        for item in activations
    )
    actual_frontier = (
        None
        if not activations
        else max(datetime.fromisoformat(str(item["active_frontier"])) for item in activations)
    )
    telemetry_by_identifier: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for transaction in ping_transactions:
        telemetry_by_identifier[int(transaction["identifier"])].append(transaction)
    migration_records = tuple(
        dict(item)
        for item in execution
        if item.get("record_type") == "zone_source_migration"
    )
    episode_results = []
    activation_anomalies: dict[str, int] = defaultdict(int)
    for episode_id, episode in episodes.items():
        identity = identities[episode["probe_id"]]
        records = records_by_episode.get(episode_id, [])
        stops = [item for item in records if item.get("record_type") == "generator_stopped"]
        observations = [
            item for item in records if item.get("record_type") == "ping_observation"
        ]
        armed = any(item.get("record_type") == "episode_armed" for item in records)
        not_run = any(item.get("record_type") == "episode_not_run" for item in records)
        sent = sum(int(item.get("sent", 0)) for item in stops)
        received = sum(int(item.get("received", 0)) for item in stops)
        rtts = sorted(
            float(item["rtt_ms"])
            for item in observations
            if item.get("observation") == "reply" and item.get("rtt_ms") is not None
        )
        reply_times = sorted(
            int(item["host_monotonic_ns"])
            for item in observations
            if item.get("observation") == "reply"
        )
        longest_gap = max(
            (
                (current - previous) / 1_000_000_000
                for previous, current in zip(reply_times, reply_times[1:])
            ),
            default=0.0,
        )
        consecutive = 0
        consecutive_activation_associated = False
        longest_consecutive = 0
        recovered_isolated = False
        recovered_transient = False
        recovered_activation_transient = False
        final_reachable_run = 0
        final_activation_associated = False
        expected_unreachable_observations = 0
        migration_observations = 0
        for item in sorted(observations, key=lambda record: int(record["host_monotonic_ns"])):
            observation = item.get("observation")
            timestamp_ns = int(item["host_monotonic_ns"])
            in_migration = any(
                migration["episode_id"] == episode_id
                and int(migration["migration_start_monotonic_ns"])
                <= timestamp_ns
                <= int(migration["migration_end_monotonic_ns"])
                for migration in migration_records
            )
            if in_migration:
                migration_observations += 1
                continue
            if observation in {"timeout", "network-error"}:
                raw_published = item.get("published_active_frontier")
                if not isinstance(raw_published, str):
                    continue
                published_frontier = datetime.fromisoformat(raw_published)
                if (
                    actual_frontier is None
                    or published_frontier > actual_frontier
                ):
                    continue
                state = _planned_episode_state(episode, published_frontier)
                if state is not None and state.get("expected_reachable") is False:
                    expected_unreachable_observations += 1
                    continue
                consecutive += 1
                longest_consecutive = max(longest_consecutive, consecutive)
                final_reachable_run = consecutive
                correlated = _activation_for_timestamp(timestamp_ns, switches)
                if correlated is not None:
                    activation_anomalies[correlated] += 1
                    consecutive_activation_associated = True
                    final_activation_associated = True
            elif observation == "reply":
                if consecutive:
                    if consecutive_activation_associated:
                        recovered_activation_transient = True
                    elif consecutive == 1:
                        recovered_isolated = True
                    else:
                        recovered_transient = True
                consecutive = 0
                consecutive_activation_associated = False
                final_reachable_run = 0
                final_activation_associated = False
        longest_consecutive = max(longest_consecutive, consecutive)
        launch_records = [
            item for item in records if item.get("record_type") == "generator_launched"
        ]
        telemetry_transactions = sum(
            sum(
                _transaction_matches_generator(transaction, item)
                for transaction in telemetry_by_identifier.get(
                    int(item["icmp_identifier"]), ()
                )
            )
            for item in launch_records
        )
        cleanup_complete = all(
            item.get("cleanup_result") == "cleaned" for item in stops
        ) and len(stops) == len(launch_records)
        active_overlap = bool(
            actual_frontier is not None
            and datetime.fromisoformat(episode["start_time"]) <= actual_frontier
        )
        reachable_overlap = any(
            item.get("expected_reachable") is True
            and actual_frontier is not None
            and datetime.fromisoformat(item["start_time"]) <= actual_frontier
            for item in episode["state_intervals"]
        )
        infrastructure_failure = bool(
            active_overlap
            and (
                not evidence_complete
                or (armed and not cleanup_complete)
                or (reachable_overlap and armed and not launch_records)
            )
        )
        classifications = []
        if infrastructure_failure:
            classifications.append("probe-infrastructure-or-evidence-failure")
        if expected_unreachable_observations or (
            armed and not launch_records and active_overlap and not reachable_overlap
        ):
            classifications.append("expected-unreachable")
        if final_reachable_run >= 3:
            classifications.append("persistent-blackhole")
        elif final_reachable_run:
            classifications.append(
                "transient-activation-associated-disruption"
                if final_activation_associated
                else "transient-disruption"
            )
        if recovered_activation_transient:
            classifications.append("transient-activation-associated-disruption")
        if recovered_transient:
            classifications.append("transient-disruption")
        if recovered_isolated:
            classifications.append("isolated-loss")
        if not classifications:
            classifications.append(
                "not-executed-outside-active-horizon"
                if not_run and not active_overlap
                else "pass"
            )
        episode_results.append(
            {
                "episode_id": episode_id,
                "probe_id": episode["probe_id"],
                "direction": identity["direction"],
                "mode": episode["mode"],
                "path_stability": identity["path_stability"],
                "path_length_class": identity["path_length_class"],
                "selection_reasons": identity["selection_reasons"],
                "ground_station_id": identity["ground_station_id"],
                "zone_id": identity["zone_id"],
                "armed": armed,
                "active_published_overlap": active_overlap,
                "execution_disposition": _episode_execution_disposition(records),
                "rearmable": any(
                    item.get("record_type")
                    in {"episode_cancelled", "episode_not_run"}
                    and item.get("rearmable") is True
                    for item in records
                ),
                "sent": sent,
                "received": received,
                "loss": max(0, sent - received),
                "loss_percent": 0.0 if sent == 0 else (sent - received) * 100 / sent,
                "rtt_ms": _distribution(rtts),
                "longest_receive_gap_seconds": longest_gap,
                "longest_consecutive_loss": longest_consecutive,
                "expected_unreachable_observations": expected_unreachable_observations,
                "migration_observations_excluded": migration_observations,
                "generator_segments": len(launch_records),
                "cleanup_complete": cleanup_complete,
                "telemetry_transactions": telemetry_transactions,
                "packet_path_evidence_present": telemetry_transactions > 0,
                "classifications": classifications,
            }
        )
    sent = sum(item["sent"] for item in episode_results)
    received = sum(item["received"] for item in episode_results)
    rtts = sorted(
        float(item["rtt_ms"])
        for item in execution
        if item.get("record_type") == "ping_observation"
        and item.get("observation") == "reply"
        and item.get("rtt_ms") is not None
    )
    persistent_failures = [
        item
        for item in episode_results
        if "persistent-blackhole" in item["classifications"]
    ]
    infrastructure_failures = [
        item
        for item in episode_results
        if "probe-infrastructure-or-evidence-failure" in item["classifications"]
    ]
    worst_flow = max(
        episode_results,
        key=lambda item: (
            item["loss_percent"],
            item["longest_consecutive_loss"],
            item["episode_id"],
        ),
        default=None,
    )
    worst_activation = (
        None
        if not activation_anomalies
        else max(
            (
                {"active_frontier": frontier, "anomaly_count": count}
                for frontier, count in activation_anomalies.items()
            ),
            key=lambda item: (item["anomaly_count"], item["active_frontier"]),
        )
    )
    ready_durations = [
        float(item["duration_seconds"])
        for item in runner_events
        if item.get("event") == "maintenance_prepare_end"
        and item.get("duration_seconds") is not None
    ]
    main_durations = [float(item["duration_seconds"]) for item in activations]
    maintenance_durations = [
        float(item["duration_seconds"])
        for item in runner_events
        if str(item.get("event", "")).startswith("maintenance_")
        and str(item.get("event", "")).endswith("_end")
        and item.get("duration_seconds") is not None
    ]
    rss_values = [
        int(item["runner_rss_bytes"])
        for item in runner_events
        if item.get("event") == "resource_health_checkpoint"
        and item.get("runner_rss_bytes") is not None
    ]
    status = (
        "FAIL"
        if persistent_failures or infrastructure_failures or not evidence_complete
        else "PASS"
    )
    if status == "PASS" and any(
        any(value != "pass" and value != "not-executed-outside-active-horizon" and value != "expected-unreachable" for value in item["classifications"])
        for item in episode_results
    ):
        status = "WARN"
    return {
        "summary": {
            "status": status,
            "plan_revision_id": horizon["revision_id"],
            "probe_plan_construction_duration_seconds": plan["planning"][
                "duration_seconds"
            ],
            "probe_identities": len(identities),
            "episodes": len(episodes),
            "persistent_episodes": plan["planning"]["persistent_episode_count"],
            "rotating_background_episodes": plan["planning"].get(
                "rotating_background_episode_count", 0
            ),
            "activation_window_episodes": plan["planning"]["activation_window_episode_count"],
            "direction_counts": plan["planning"]["direction_counts"],
            "path_length_class_counts": plan["planning"]["path_length_class_counts"],
            "selection_reason_counts": plan["planning"]["selection_reason_counts"],
            "estimated_echo_requests": plan["planning"]["estimated_echo_requests"],
            "estimated_telemetry_events": plan["planning"]["estimated_telemetry_events"],
            "sent": sent,
            "received": received,
            "loss": max(0, sent - received),
            "loss_percent": 0.0 if sent == 0 else (sent - received) * 100 / sent,
            "rtt_ms": _distribution(rtts),
            "longest_receive_gap_seconds": max(
                (item["longest_receive_gap_seconds"] for item in episode_results),
                default=0.0,
            ),
            "longest_consecutive_loss": max(
                (item["longest_consecutive_loss"] for item in episode_results),
                default=0,
            ),
            "persistent_failure_count": len(persistent_failures),
            "infrastructure_failure_count": len(infrastructure_failures),
            "telemetry_complete": telemetry_complete,
            "telemetry_ring_loss": telemetry_losses,
            "executor_complete": execution_complete,
            "cleanup_complete": executor_end.get("cleanup_complete") is True,
            "global_evidence_complete": evidence_complete,
            "active_activation_count": len(activations),
            "active_frontier": None if actual_frontier is None else actual_frontier.isoformat(),
        },
        "runner_observer_effect": {
            "ready_seconds": _distribution(ready_durations),
            "main_seconds": _distribution(main_durations),
            "maintenance_max_seconds": max(maintenance_durations, default=0.0),
            "deadline_misses": sum(
                item.get("event") == "deadline_miss" for item in runner_events
            ),
            "readiness_wait_max_seconds": max(
                (
                    float(item.get("readiness_dependency_wait_seconds", 0.0))
                    for item in activations
                ),
                default=0.0,
            ),
            "runner_rss_max_bytes": max(rss_values, default=None),
        },
        "worst_flow": worst_flow,
        "worst_activation": worst_activation,
        "zone_source_migrations": migration_records,
        "episodes": tuple(episode_results),
    }


def _episode_execution_disposition(
    records: list[dict[str, Any]],
) -> str:
    record_types = {str(item.get("record_type")) for item in records}
    if "episode_completed" in record_types:
        return "completed"
    if "episode_cancelled" in record_types:
        return "cancelled-rearmable"
    if "episode_not_run" in record_types:
        return "not-run-rearmable"
    if "episode_armed" in record_types:
        return "armed-incomplete"
    return "not-observed"


def _transaction_matches_generator(
    transaction: Mapping[str, Any], launch: Mapping[str, Any]
) -> bool:
    for direction_name in ("request", "reply"):
        direction = transaction.get(direction_name)
        if (
            isinstance(direction, Mapping)
            and direction.get("source_ipv4") == launch.get("source_ipv4")
            and direction.get("destination_ipv4")
            == launch.get("destination_ipv4")
        ):
            return True
    return False


def _planned_episode_state(
    episode: Mapping[str, Any], nominal_time: datetime
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in episode["state_intervals"]
            if datetime.fromisoformat(item["start_time"])
            <= nominal_time
            < datetime.fromisoformat(item["end_time"])
        ),
        None,
    )


def _activation_for_timestamp(
    timestamp_ns: int,
    switches: tuple[tuple[int, int, str], ...],
) -> str | None:
    candidates = [
        (min(abs(timestamp_ns - start), abs(timestamp_ns - end)), frontier)
        for start, end, frontier in switches
        if start - 1_000_000_000 <= timestamp_ns <= end + 1_000_000_000
    ]
    return None if not candidates else min(candidates)[1]


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    return {
        "count": len(ordered),
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def _percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(len(values) - 1, lower + 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _resolve_endpoint(
    address: str,
    generation: int,
    *,
    nodes_by_ipv4: dict[str, dict[str, Any]],
    zone_by_ipv4: dict[str, dict[str, Any]],
    networks: dict[int, dict[str, Any]],
    dynamic_nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    node = nodes_by_ipv4.get(address)
    if node is not None:
        node_id = str(node["node_id"])
        return {
            "endpoint_kind": "node",
            "ipv4": address,
            "stable_id": node_id,
            "display_name": _display_name(node_id, dynamic_nodes[node_id]),
            "node_id": node_id,
            "node_kind": dynamic_nodes[node_id]["kind"],
            "physical_node_id": node_id,
            "physical_display_name": _display_name(node_id, dynamic_nodes[node_id]),
            "service_satellite_id": None,
            "service_satellite_display_name": None,
        }
    zone = zone_by_ipv4.get(address)
    if zone is not None:
        zone_id = str(zone["zone_id"])
        services = {
            value["zone_id"]: value["service_satellite"]
            for value in networks[generation]["zone_services"]
        }
        service = services.get(zone_id)
        display = (
            None
            if service is None
            else _display_name(service, dynamic_nodes[service])
        )
        return {
            "endpoint_kind": "zone",
            "ipv4": address,
            "packet_prefix": zone["packet_prefix"],
            "stable_id": zone_id,
            "display_name": zone_id,
            "zone_id": zone_id,
            "node_id": None,
            "node_kind": "zone",
            "physical_node_id": service,
            "physical_display_name": display,
            "service_satellite_id": service,
            "service_satellite_display_name": display,
        }
    return {
        "endpoint_kind": "unknown",
        "ipv4": address,
        "stable_id": None,
        "display_name": address,
        "node_id": None,
        "node_kind": "unknown",
        "physical_node_id": None,
        "physical_display_name": None,
        "service_satellite_id": None,
        "service_satellite_display_name": None,
    }


def _project_icmp_record(
    record_number: int,
    key: tuple[object, ...],
    ordered: list[dict[str, Any]],
    *,
    source_endpoint: dict[str, Any],
    destination_endpoint: dict[str, Any],
    nodes: dict[int, dict[str, Any]],
    dynamic_nodes: dict[str, dict[str, Any]],
    interfaces: dict[tuple[int, int, int], dict[str, Any]],
    networks: dict[int, dict[str, Any]],
    nominal_origin: datetime,
    monotonic_origin_ns: int,
    recorder_complete: bool,
    loss_total: int,
) -> dict[str, Any]:
    source_ipv4, destination_ipv4, icmp_type, identifier, sequence = key
    first_ns = int(ordered[0]["kernel_monotonic_ns"])
    first_simulation_time = _simulation_time(
        first_ns,
        nominal_origin=nominal_origin,
        monotonic_origin_ns=monotonic_origin_ns,
    )
    warnings: list[str] = []
    observed_path = []
    previous_node_id = str(source_endpoint["physical_node_id"])
    expected_from_redirect: str | None = None
    first_link_verified = False

    for index, packet in enumerate(ordered):
        slot = int(packet["node_slot"])
        generation = int(packet["generation"])
        node = nodes[slot]
        node_id = str(node["node_id"])
        ingress = _packet_ingress_identity(packet, interfaces)
        ingress_peer = str(ingress["peer_node_id"])
        if ingress_peer != previous_node_id:
            warnings.append(
                f"observation {index + 1} ingress peer does not continue the prior hop"
            )
        if expected_from_redirect is not None and expected_from_redirect != node_id:
            warnings.append(
                f"observation {index + 1} does not match the prior redirect peer"
            )
        if index == 0:
            first_link_verified = ingress_peer == source_endpoint["physical_node_id"]
            if not first_link_verified:
                warnings.append(
                    "inferred first link is not confirmed by ingress peer identity"
                )

        egress = None
        egress_ifindex = packet.get("selected_egress_ifindex")
        if egress_ifindex is not None:
            egress = interfaces[(generation, slot, int(egress_ifindex))]
        packet_ns = int(packet["kernel_monotonic_ns"])
        simulation_time = _simulation_time(
            packet_ns,
            nominal_origin=nominal_origin,
            monotonic_origin_ns=monotonic_origin_ns,
        )
        network = networks[generation]
        observed_path.append(
            {
                "step": index + 1,
                "evidence_kind": "observed-ingress",
                "node_id": node_id,
                "display_name": _display_name(node_id, dynamic_nodes[node_id]),
                "node_kind": dynamic_nodes[node_id]["kind"],
                "kernel_monotonic_ns": packet_ns,
                "simulation_timestamp": simulation_time.isoformat(),
                "recorded_elapsed_ms": (packet_ns - first_ns) / 1_000_000,
                "generation": generation,
                "active_boundary_timestamp": network["boundary_timestamp"],
                "ingress_ifindex": int(packet["ingress_ifindex"]),
                "ingress_peer_node_id": ingress_peer,
                "incoming_connection_id": ingress["connection_id"],
                "ingress_identity_validity": ingress.get(
                    "packet_identity_validity", "exact-generation"
                ),
                "ingress_identity_generations": tuple(
                    ingress.get("identity_generations", (generation,))
                ),
                "selected_egress_ifindex": egress_ifindex,
                "egress_peer_node_id": None if egress is None else egress["peer_node_id"],
                "outgoing_connection_id": (
                    None if egress is None else egress["connection_id"]
                ),
                "decision": packet["decision"],
                "reason": packet["reason"],
                "raw_record_index": packet["record_index"],
            }
        )
        previous_node_id = node_id
        expected_from_redirect = None if egress is None else str(egress["peer_node_id"])

    final = observed_path[-1]
    terminal = final["decision"] in {"local/pass", "drop"}
    if not terminal:
        warnings.append("the final observation is not terminal")
    if (
        final["decision"] == "local/pass"
        and final["node_id"] != destination_endpoint["physical_node_id"]
    ):
        warnings.append("local/pass terminal is not the endpoint physical realization")
    path_continuous = not warnings
    if not recorder_complete:
        warnings.append("durable telemetry recorder did not complete")
    if loss_total:
        warnings.append(f"collector reported {loss_total} lost telemetry event(s)")

    source_node_id = str(source_endpoint["physical_node_id"])
    first_generation = int(ordered[0]["generation"])
    inferred_source = {
        "evidence_kind": "inferred-source-realization",
        "node_id": source_node_id,
        "display_name": _display_name(source_node_id, dynamic_nodes[source_node_id]),
        "node_kind": dynamic_nodes[source_node_id]["kind"],
        "logical_endpoint": source_endpoint,
        "kernel_monotonic_ns": first_ns,
        "simulation_timestamp": first_simulation_time.isoformat(),
        "recorded_elapsed_ms": 0.0,
        "generation": first_generation,
        "active_boundary_timestamp": networks[first_generation][
            "boundary_timestamp"
        ],
    }
    return {
        "record_id": f"icmp-{record_number:04d}",
        "protocol": "ICMP",
        "icmp_type": int(icmp_type),
        "icmp_label": _icmp_label(int(icmp_type)),
        "identifier": int(identifier),
        "sequence": int(sequence),
        "source_ipv4": source_ipv4,
        "destination_ipv4": destination_ipv4,
        "source_endpoint": source_endpoint,
        "destination_endpoint": destination_endpoint,
        "source_display_name": source_endpoint["display_name"],
        "destination_display_name": destination_endpoint["display_name"],
        "physical_source_node_id": source_endpoint["physical_node_id"],
        "physical_destination_node_id": destination_endpoint["physical_node_id"],
        "first_kernel_monotonic_ns": first_ns,
        "last_kernel_monotonic_ns": int(ordered[-1]["kernel_monotonic_ns"]),
        "first_simulation_timestamp": first_simulation_time.isoformat(),
        "recorded_duration_ms": (
            int(ordered[-1]["kernel_monotonic_ns"]) - first_ns
        )
        / 1_000_000,
        "observed_hops": len(observed_path),
        "link_hops": len(observed_path),
        "final_decision": final["decision"],
        "final_reason": final["reason"],
        "first_link": {
            "evidence_kind": "inferred-from-first-observed-ingress",
            "source_node_id": source_endpoint["physical_node_id"],
            "target_node_id": observed_path[0]["node_id"],
            "connection_id": observed_path[0]["incoming_connection_id"],
            "peer_identity_verified": first_link_verified,
        },
        "inferred_source": inferred_source,
        "observed_path": tuple(observed_path),
        "telemetry_complete": recorder_complete,
        "reported_losses": loss_total,
        "path_continuous": path_continuous,
        "evidence_complete": recorder_complete and loss_total == 0 and path_continuous,
        "warnings": tuple(warnings),
    }


def _pair_ping_transactions(
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    requests = sorted(
        (value for value in records if value["icmp_type"] == 8),
        key=lambda value: value["first_kernel_monotonic_ns"],
    )
    replies = sorted(
        (value for value in records if value["icmp_type"] == 0),
        key=lambda value: value["first_kernel_monotonic_ns"],
    )
    used_replies: set[str] = set()
    transactions = []
    for request_index, request in enumerate(requests):
        candidates = [
            reply
            for reply in replies
            if reply["record_id"] not in used_replies
            and reply["source_ipv4"] == request["destination_ipv4"]
            and reply["destination_ipv4"] == request["source_ipv4"]
            and reply["identifier"] == request["identifier"]
            and reply["sequence"] == request["sequence"]
            and reply["first_kernel_monotonic_ns"]
            > request["last_kernel_monotonic_ns"]
        ]
        next_request = next(
            (
                value
                for value in requests[request_index + 1 :]
                if value["source_ipv4"] == request["source_ipv4"]
                and value["destination_ipv4"] == request["destination_ipv4"]
                and value["identifier"] == request["identifier"]
                and value["sequence"] == request["sequence"]
            ),
            None,
        )
        ambiguous = bool(
            candidates
            and next_request is not None
            and candidates[0]["first_kernel_monotonic_ns"]
            > next_request["first_kernel_monotonic_ns"]
        )
        if len(candidates) > 1 and (
            candidates[0]["first_kernel_monotonic_ns"]
            == candidates[1]["first_kernel_monotonic_ns"]
        ):
            ambiguous = True
        reply = None if ambiguous or not candidates else candidates[0]
        if reply is not None:
            used_replies.add(reply["record_id"])
        transactions.append(
            _transaction(
                len(transactions) + 1,
                request=request,
                reply=reply,
                status="ambiguous" if ambiguous else "paired" if reply else "unpaired",
            )
        )
    for reply in replies:
        if reply["record_id"] not in used_replies:
            transactions.append(
                _transaction(
                    len(transactions) + 1,
                    request=None,
                    reply=reply,
                    status="reply-only",
                )
            )
    return tuple(
        sorted(transactions, key=lambda value: value["first_kernel_monotonic_ns"])
    )


def _transaction(
    number: int,
    *,
    request: dict[str, Any] | None,
    reply: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    first = request if request is not None else reply
    assert first is not None
    last = reply if reply is not None else request
    assert last is not None
    directions = tuple(value for value in (request, reply) if value is not None)
    warnings = []
    if status == "ambiguous":
        warnings.append("request/reply identity collision is temporally ambiguous")
    elif status != "paired":
        warnings.append("ICMP request/reply transaction is incomplete")
    return {
        "transaction_id": f"ping-{number:04d}",
        "pairing_status": status,
        "identifier": first["identifier"],
        "sequence": first["sequence"],
        "source_endpoint": (
            request["source_endpoint"] if request is not None else reply["destination_endpoint"]
        ),
        "destination_endpoint": (
            request["destination_endpoint"] if request is not None else reply["source_endpoint"]
        ),
        "request": request,
        "reply": reply,
        "first_kernel_monotonic_ns": first["first_kernel_monotonic_ns"],
        "last_kernel_monotonic_ns": last["last_kernel_monotonic_ns"],
        "first_simulation_timestamp": first["first_simulation_timestamp"],
        "recorded_duration_ms": (
            last["last_kernel_monotonic_ns"] - first["first_kernel_monotonic_ns"]
        )
        / 1_000_000,
        "generations": tuple(
            dict.fromkeys(
                step["generation"]
                for direction in directions
                for step in direction["observed_path"]
            )
        ),
        "active_boundaries": tuple(
            dict.fromkeys(
                step["active_boundary_timestamp"]
                for direction in directions
                for step in direction["observed_path"]
            )
        ),
        "evidence_complete": bool(
            status == "paired" and all(value["evidence_complete"] for value in directions)
        ),
        "warnings": tuple(warnings),
    }


def _project_observed_flows(
    transactions: tuple[dict[str, Any], ...],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Group real Ping transactions by unordered logical endpoint pair."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    endpoints: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for transaction in transactions:
        source = transaction["source_endpoint"]
        destination = transaction["destination_endpoint"]
        source_id = str(source["stable_id"])
        destination_id = str(destination["stable_id"])
        key = tuple(sorted((source_id, destination_id)))
        grouped[key].append(transaction)
        endpoints.setdefault(key, {})[source_id] = source
        endpoints[key][destination_id] = destination

    ordered = sorted(
        grouped,
        key=lambda key: min(
            int(value["first_kernel_monotonic_ns"])
            for value in grouped[key]
        ),
    )
    transaction_flow: dict[str, str] = {}
    flows = []
    for number, key in enumerate(ordered, start=1):
        values = tuple(
            sorted(
                grouped[key],
                key=lambda value: int(value["first_kernel_monotonic_ns"]),
            )
        )
        flow_id = f"observed-flow-{number:04d}"
        endpoint_a = endpoints[key][key[0]]
        endpoint_b = endpoints[key][key[1]]
        for value in values:
            transaction_flow[value["transaction_id"]] = flow_id
        flows.append(
            {
                "flow_id": flow_id,
                "protocol": "ICMP",
                "traffic_kind": "ping",
                "display_label": (
                    f"{endpoint_a['display_name']} ↔ "
                    f"{endpoint_b['display_name']} · ICMP Ping"
                ),
                "endpoint_a": endpoint_a,
                "endpoint_b": endpoint_b,
                "observed_ping_count": len(values),
                "paired_ping_count": sum(
                    value["pairing_status"] == "paired" for value in values
                ),
                "incomplete_ping_count": sum(
                    value["pairing_status"] != "paired" for value in values
                ),
                "first_simulation_timestamp": values[0][
                    "first_simulation_timestamp"
                ],
                "last_simulation_timestamp": values[-1][
                    "first_simulation_timestamp"
                ],
                "transaction_ids": tuple(
                    value["transaction_id"] for value in values
                ),
            }
        )
    return (
        tuple(
            {
                **transaction,
                "observed_flow_id": transaction_flow[
                    transaction["transaction_id"]
                ],
            }
            for transaction in transactions
        ),
        tuple(flows),
    )


def _project_workload_activity(
    transactions: tuple[dict[str, Any], ...],
    workload: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    series_values = tuple(workload["series"])
    assigned: dict[str, list[dict[str, Any]]] = {
        value["series_id"]: [] for value in series_values
    }
    projected_transactions = []
    for transaction in transactions:
        matches = [
            value
            for value in series_values
            if _transaction_matches_series(transaction, value)
        ]
        if len(matches) > 1:
            raise ValueError("one Ping transaction ambiguously matches workload series")
        series_id = None if not matches else matches[0]["series_id"]
        projected = {**transaction, "series_id": series_id}
        projected_transactions.append(projected)
        if series_id is not None:
            assigned[series_id].append(projected)

    lanes = []
    for series in series_values:
        cadence = series["schedule"]
        slot_count = int(cadence["scheduled_slots"])
        assigned_slots = _match_transactions_to_scheduled_slots(
            assigned[series["series_id"]],
            cadence,
        )
        slot_transactions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        markers = []
        for transaction in assigned[series["series_id"]]:
            slot_index = assigned_slots.get(transaction["transaction_id"])
            if slot_index is not None:
                slot_transactions[slot_index].append(transaction)
            markers.append(
                {
                    "transaction_id": transaction["transaction_id"],
                    "timestamp": transaction["first_simulation_timestamp"],
                    "pairing_status": transaction["pairing_status"],
                    "slot_index": slot_index,
                    "recorded_duration_ms": transaction["recorded_duration_ms"],
                    "packet_observations": sum(
                        len(value["observed_path"])
                        for value in (transaction["request"], transaction["reply"])
                        if value is not None
                    ),
                }
            )
        observed_slots = tuple(sorted(slot_transactions))
        missing = tuple(
            index for index in range(slot_count) if index not in slot_transactions
        )
        lanes.append(
            {
                "series_id": series["series_id"],
                "display_label": series["display_label"],
                "direction": series["direction"],
                "ground_station": series["ground_station"],
                "zone": series["zone"],
                "schedule": cadence,
                "semantic_purpose": series["semantic_purpose"],
                "event_focus": series["event_focus"],
                "scheduled_slots": slot_count,
                "observed_transactions": len(markers),
                "observed_paired_transactions": sum(
                    value["pairing_status"] == "paired" for value in markers
                ),
                "observed_slots": observed_slots,
                "observed_gap_slots": len(missing),
                "observed_gap_runs": _gap_runs(missing, cadence),
                "transaction_markers": tuple(markers),
                "gap_qualification": (
                    "A scheduled slot without tc-BPF observation is an observed "
                    "traffic gap; schedule plus telemetry alone does not prove "
                    "command execution, timeout, packet loss, or failed Ping."
                ),
            }
        )
    return tuple(projected_transactions), tuple(lanes)


def _match_transactions_to_scheduled_slots(
    transactions: list[dict[str, Any]], cadence: dict[str, Any]
) -> dict[str, int]:
    """Align one Ping process cadence without accumulating timestamp drift."""

    if not transactions:
        return {}
    start = datetime.fromisoformat(cadence["start_time"])
    interval = float(cadence["interval_seconds"])
    slot_count = int(cadence["scheduled_slots"])
    by_process: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for transaction in transactions:
        source = transaction["source_endpoint"]
        destination = transaction["destination_endpoint"]
        by_process[
            (
                transaction.get("identifier", transaction["transaction_id"]),
                source.get("stable_id")
                or source.get("node_id")
                or source.get("zone_id"),
                destination.get("stable_id")
                or destination.get("node_id")
                or destination.get("zone_id"),
            )
        ].append(transaction)

    result: dict[str, int] = {}
    for values in by_process.values():
        ordered = sorted(
            values, key=lambda value: int(value["first_kernel_monotonic_ns"])
            if "first_kernel_monotonic_ns" in value
            else datetime.fromisoformat(
                value["first_simulation_timestamp"]
            ).timestamp()
        )
        if not all(isinstance(value.get("sequence"), int) for value in ordered):
            for transaction in ordered:
                timestamp = datetime.fromisoformat(
                    transaction["first_simulation_timestamp"]
                )
                slot = round((timestamp - start).total_seconds() / interval)
                if 0 <= slot < slot_count:
                    result[transaction["transaction_id"]] = slot
            continue
        segments: list[list[dict[str, Any]]] = []
        for value in ordered:
            if not segments or int(value["sequence"]) <= int(
                segments[-1][-1]["sequence"]
            ):
                segments.append([])
            segments[-1].append(value)
        for segment in segments:
            first = segment[0]
            first_time = datetime.fromisoformat(first["first_simulation_timestamp"])
            anchor_slot = round((first_time - start).total_seconds() / interval)
            first_sequence = int(first["sequence"])
            for transaction in segment:
                slot = anchor_slot + int(transaction["sequence"]) - first_sequence
                if 0 <= slot < slot_count:
                    result[transaction["transaction_id"]] = slot
    return result


def _transaction_matches_series(
    transaction: dict[str, Any], series: dict[str, Any]
) -> bool:
    timestamp = datetime.fromisoformat(transaction["first_simulation_timestamp"])
    schedule = series["schedule"]
    if not (
        datetime.fromisoformat(schedule["start_time"])
        <= timestamp
        < datetime.fromisoformat(schedule["end_time"])
    ):
        return False
    source = transaction["source_endpoint"]
    destination = transaction["destination_endpoint"]
    ground_id = series["ground_station"]["node_id"]
    zone_id = series["zone"]["zone_id"]
    if series["direction"] == "ground-station-to-zone":
        return (
            source["endpoint_kind"] == "node"
            and source["node_id"] == ground_id
            and destination["endpoint_kind"] == "zone"
            and destination["zone_id"] == zone_id
        )
    return (
        source["endpoint_kind"] == "zone"
        and source["zone_id"] == zone_id
        and destination["endpoint_kind"] == "node"
        and destination["node_id"] == ground_id
    )


def _gap_runs(
    missing: tuple[int, ...], cadence: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    if not missing:
        return ()
    start = datetime.fromisoformat(cadence["start_time"])
    end = datetime.fromisoformat(cadence["end_time"])
    interval = float(cadence["interval_seconds"])
    groups = []
    first = previous = missing[0]
    for index in missing[1:]:
        if index == previous + 1:
            previous = index
            continue
        groups.append((first, previous))
        first = previous = index
    groups.append((first, previous))
    return tuple(
        {
            "first_slot_index": lower,
            "last_slot_index": upper,
            "slot_count": upper - lower + 1,
            "start_time": (start + timedelta(seconds=lower * interval)).isoformat(),
            "end_time": (
                min(
                    end,
                    start + timedelta(seconds=(upper + 1) * interval),
                )
            ).isoformat(),
            "meaning": "scheduled slots without tc-BPF observation",
        }
        for lower, upper in groups
    )


def _project_simulation_timeline(
    timeline: dict[str, Any],
    networks: tuple[dict[str, Any], ...],
    zone_events: tuple[dict[str, Any], ...],
    transactions: tuple[dict[str, Any], ...],
    dynamic_nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    start = datetime.fromisoformat(timeline["start_time"])
    end = datetime.fromisoformat(timeline["end_time"])
    projected_zone_events = []
    for index, value in enumerate(zone_events):
        previous = value["previous_service_satellite"]
        service = value["service_satellite"]
        projected_zone_events.append(
            {
                **value,
                "event_id": f"zone-event-{index + 1:03d}",
                "previous_service_display_name": (
                    None
                    if previous is None
                    else _display_name(previous, dynamic_nodes[previous])
                ),
                "service_display_name": (
                    None if service is None else _display_name(service, dynamic_nodes[service])
                ),
            }
        )
    return {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "duration_seconds": (end - start).total_seconds(),
        "nominal_boundaries": tuple(
            {
                "generation": int(value["generation"]),
                "timestamp": value["boundary_timestamp"],
            }
            for value in networks
        ),
        "zone_events": tuple(projected_zone_events),
        "transaction_markers": tuple(
            {
                "transaction_id": value["transaction_id"],
                "observed_flow_id": value["observed_flow_id"],
                "timestamp": value["first_simulation_timestamp"],
                "pairing_status": value["pairing_status"],
                "source": value["source_endpoint"]["display_name"],
                "destination": value["destination_endpoint"]["display_name"],
            }
            for value in transactions
        ),
    }


def _project_zone_geometry(value: dict[str, Any]) -> dict[str, Any]:
    geometry = dict(value["geometry"])
    west = float(geometry["west_longitude_deg"])
    east = float(geometry["east_longitude_deg"])
    longitude_segments = (
        ((west, east),)
        if west <= east
        else ((west, 180.0), (-180.0, east))
    )
    return {
        **value,
        "geometry": {
            **geometry,
            "longitude_segments": tuple(
                {"west_longitude_deg": lower, "east_longitude_deg": upper}
                for lower, upper in longitude_segments
            ),
        },
        "packet_link_semantics": False,
    }


def _generation_at(
    networks: tuple[dict[str, Any], ...], timestamp: datetime
) -> int | None:
    """Return the latest exact generation boundary at or before timestamp."""

    boundaries = tuple(
        datetime.fromisoformat(value["boundary_timestamp"]) for value in networks
    )
    index = bisect_right(boundaries, timestamp) - 1
    return None if index < 0 else int(networks[index]["generation"])


def _unclassified_icmp_group(
    key: tuple[object, ...],
    packets: list[dict[str, Any]],
    *,
    source_endpoint: dict[str, Any],
    destination_endpoint: dict[str, Any],
    nodes: dict[int, dict[str, Any]],
    interfaces: dict[tuple[int, int, int], dict[str, Any]],
    nominal_origin: datetime,
    monotonic_origin_ns: int,
) -> dict[str, Any]:
    source_ipv4, destination_ipv4, icmp_type, identifier, sequence = key
    return {
        "classification": "raw/unclassified",
        "classification_reason": "ICMP endpoint IPv4 has no durable Node or Zone identity",
        "protocol": 1,
        "icmp_type": int(icmp_type),
        "identifier": int(identifier),
        "sequence": int(sequence),
        "source_ipv4": source_ipv4,
        "destination_ipv4": destination_ipv4,
        "source_endpoint": source_endpoint,
        "destination_endpoint": destination_endpoint,
        "observations": tuple(
            _raw_packet_observation(
                packet,
                nodes=nodes,
                interfaces=interfaces,
                nominal_origin=nominal_origin,
                monotonic_origin_ns=monotonic_origin_ns,
            )
            for packet in packets
        ),
    }


def _unclassified_packet(
    packet: dict[str, Any],
    *,
    nodes: dict[int, dict[str, Any]],
    interfaces: dict[tuple[int, int, int], dict[str, Any]],
    nominal_origin: datetime,
    monotonic_origin_ns: int,
) -> dict[str, Any]:
    icmp = packet["icmp"]
    if packet["protocol"] != 1:
        reason = "protocol has no v2 packet identity"
    elif icmp.get("valid") is not True:
        reason = "ICMP metadata is unavailable or invalid"
    else:
        reason = "ICMP grouping fields are incomplete"
    return {
        "classification": "raw/unclassified",
        "classification_reason": reason,
        "protocol": packet["protocol"],
        "source_ipv4": packet["source_ipv4"],
        "destination_ipv4": packet["destination_ipv4"],
        "observations": (
            _raw_packet_observation(
                packet,
                nodes=nodes,
                interfaces=interfaces,
                nominal_origin=nominal_origin,
                monotonic_origin_ns=monotonic_origin_ns,
            ),
        ),
    }


def _packet_ingress_identity(
    packet: Mapping[str, Any],
    interfaces: Mapping[tuple[int, int, int], Mapping[str, Any]],
) -> dict[str, Any]:
    resolved = packet.get("ingress_identity")
    if isinstance(resolved, Mapping):
        return dict(resolved)
    key = (
        int(packet["generation"]),
        int(packet["node_slot"]),
        int(packet["ingress_ifindex"]),
    )
    return dict(interfaces[key])


def _raw_packet_observation(
    packet: dict[str, Any],
    *,
    nodes: dict[int, dict[str, Any]],
    interfaces: dict[tuple[int, int, int], dict[str, Any]],
    nominal_origin: datetime,
    monotonic_origin_ns: int,
) -> dict[str, Any]:
    packet_ns = int(packet["kernel_monotonic_ns"])
    slot = int(packet["node_slot"])
    generation = int(packet["generation"])
    ingress = _packet_ingress_identity(packet, interfaces)
    egress_index = packet.get("selected_egress_ifindex")
    egress = None if egress_index is None else interfaces[
        (generation, slot, int(egress_index))
    ]
    return {
        "record_index": packet["record_index"],
        "node_id": nodes[slot]["node_id"],
        "kernel_monotonic_ns": packet_ns,
        "simulation_timestamp": _simulation_time(
            packet_ns,
            nominal_origin=nominal_origin,
            monotonic_origin_ns=monotonic_origin_ns,
        ).isoformat(),
        "generation": generation,
        "active_boundary_timestamp": packet.get(
            "active_boundary_timestamp", ingress["boundary_timestamp"]
        ),
        "ingress_ifindex": packet["ingress_ifindex"],
        "ingress_peer_node_id": ingress["peer_node_id"],
        "ingress_identity_validity": ingress.get(
            "packet_identity_validity", "exact-generation"
        ),
        "selected_egress_ifindex": egress_index,
        "egress_peer_node_id": None if egress is None else egress["peer_node_id"],
        "decision": packet["decision"],
        "reason": packet["reason"],
    }


def _simulation_time(
    kernel_monotonic_ns: int,
    *,
    nominal_origin: datetime,
    monotonic_origin_ns: int,
) -> datetime:
    return nominal_origin + timedelta(
        microseconds=(kernel_monotonic_ns - monotonic_origin_ns) / 1_000
    )


def _position_at(
    samples: tuple[tuple[datetime, dict[str, Any]], ...],
    node_id: str,
    timestamp: datetime,
) -> dict[str, Any] | None:
    times = tuple(value[0] for value in samples)
    index = bisect_left(times, timestamp)
    if index < len(samples) and times[index] == timestamp:
        value = samples[index][1]["positions"][node_id]
        return {
            "ecef_m": dict(value["ecef_m"]),
            "geographic": dict(value["geographic"]),
            "provenance": "retained-position",
            "retained_timestamp": timestamp.isoformat(),
            "visualization_only": False,
        }
    if index == 0 or index == len(samples):
        return None
    before_time, before_sample = samples[index - 1]
    after_time, after_sample = samples[index]
    span = (after_time - before_time).total_seconds()
    fraction = (timestamp - before_time).total_seconds() / span
    before = before_sample["positions"][node_id]["ecef_m"]
    after = after_sample["positions"][node_id]["ecef_m"]
    return {
        "ecef_m": {
            axis: float(before[axis])
            + (float(after[axis]) - float(before[axis])) * fraction
            for axis in ("x", "y", "z")
        },
        "geographic": None,
        "provenance": "linear-ecef-interpolation",
        "before_timestamp": before_time.isoformat(),
        "after_timestamp": after_time.isoformat(),
        "fraction": fraction,
        "visualization_only": True,
    }


def _display_name(node_id: str, metadata: dict[str, Any]) -> str:
    entity_id = node_id.rsplit("/", 1)[-1]
    satellite = _SATELLITE_NAME.fullmatch(entity_id)
    if satellite:
        return f"Orbit-{satellite.group('orbit')} Sat-{satellite.group('satellite')}"
    if metadata.get("kind") == "ground-station":
        words = entity_id.removeprefix("gs-").removesuffix("-reference").split("-")
        return " ".join(value.capitalize() for value in words)
    return entity_id


def _icmp_label(icmp_type: int) -> str:
    if icmp_type == 8:
        return "ICMP echo request"
    if icmp_type == 0:
        return "ICMP echo reply"
    return f"ICMP type {icmp_type}"


__all__ = ["project_traffic_replay"]
