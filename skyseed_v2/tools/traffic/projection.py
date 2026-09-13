"""Project detached traffic evidence against explicit planned reachability."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_interface
import json
import os
from math import isfinite
from numbers import Real
from typing import Any

from skyseed_v2.simulator import (
    PlannedOperationalState,
    ReachableRouteOutcome,
    apply_plan_transition,
    plan_transition_fingerprint,
    planned_operational_state_fingerprint,
)
from skyseed_v2.simulator.planning import (
    plan_transition_from_data,
    planned_operational_state_to_data,
)

from .reader import TrafficReplayRead
from ._probe_cases import _request_cases, _ping_report_ranges


def project_traffic_replay(observation: TrafficReplayRead) -> dict[str, Any]:
    """Bind telemetry transactions to formal physical and planned evidence."""

    return _project_traffic_replay(observation)


def _project_traffic_replay(observation, *, reader_validated=False):
    # Only the immediate reader-to-projection composition can reuse validation.
    # Public projections of detached mutable snapshots still validate semantics.
    if not isinstance(observation, TrafficReplayRead):
        raise TypeError("observation must be a TrafficReplayRead")
    source = observation.snapshot
    telemetry = source["telemetry"]
    planning = source["planning"]
    compiler = source["compiler"]
    nodes_by_address = {
        value["runtime_ipv4"]: value for value in telemetry["nodes"]
    }
    activation_times = tuple(
        datetime.fromisoformat(timestamp)
        for realization in compiler["realizations"]
        for timestamp in realization["activation_timestamps"]
    )
    _validate_compact_planning_structure(
        planning, physical_frames=source["physical_frames"]
    )
    zone_prefixes = _zone_prefixes(compiler["realizations"])
    transactions = _project_transactions(
        telemetry["ping_transactions"],
        planning=planning,
        nodes_by_address=nodes_by_address,
        zone_prefixes=zone_prefixes,
        activation_times=activation_times,
        reader_validated=reader_validated,
    )
    shell_nodes = source.get("shell_nodes", ())
    membership = {n["node_id"]: n["shell_id"] for n in shell_nodes if n.get("shell_id")}
    for transaction in transactions:
        route = transaction.get("route_outcome")
        path = [] if route is None or route["kind"] != "reachable" else [_node_id(n) for n in route["path"]]
        observed_nodes = [telemetry["packets"][i]["node_id"] for i in
                          (*transaction.get("request_packet_indices", ()), *transaction.get("reply_packet_indices", ()))]
        transaction["planned_shell_ids"] = sorted({membership[n] for n in path if n in membership})
        transaction["observed_shell_ids"] = sorted({membership[n] for n in observed_nodes if n in membership})
        transaction["shell_ids"] = sorted(set(transaction["planned_shell_ids"]) | set(transaction["observed_shell_ids"]))
    qualification = _project_probe_qualification(
        source["qualification"],
        ping_transactions=transactions,
        telemetry_complete=telemetry["telemetry_complete"],
        telemetry_losses=telemetry["telemetry_losses"],
    )
    shell_coverage = _runtime_shell_coverage(source.get("qualification"), transactions, membership)
    if qualification is not None:
        qualification["shell_coverage"] = shell_coverage
        qualification["summary"]["coverage_status"] = (
            "PASS" if shell_coverage and all(c["status"] in {"covered", "no-eligible-candidate"} for c in shell_coverage) else "NOT PROVEN")
        if qualification["summary"]["status"] == "PASS" and qualification["summary"]["coverage_status"] != "PASS":
            qualification["summary"]["status"] = "WARN"
    classifications: dict[str, int] = defaultdict(int)
    for value in transactions:
        classifications[str(value["classification"])] += 1
    inventory = {
        "physical_frames": len(source["physical_frames"]),
        "plans": len(planning["plans"]),
        "compiled_realizations": len(compiler["realizations"]),
        "formal_activation_boundaries": len(activation_times),
        "formal_planning_boundaries": len(planning["formal_transitions"]),
        "packet_observations": len(telemetry["packets"]),
        "ping_transactions": len(transactions),
        "uncertain_association_groups": sum(t["pairing_status"] == "ambiguous" for t in transactions),
        "endpoint_delivered_transactions": sum(t.get("reply_delivery") == "endpoint-observed" and not t.get("association_uncertainty") for t in transactions),
        "planned_reachable_transactions": sum(
            value["planned_reachability"] == "reachable"
            for value in transactions
        ),
        "planned_unreachable_transactions": sum(
            value["planned_reachability"] == "unreachable"
            for value in transactions
        ),
        "unclassified_observations": classifications.get("unclassified", 0),
        "telemetry_lost_events": telemetry["telemetry_losses"],
    }
    return {
        "format_version": 7,
        "tool": "traffic-path-replay",
        "authority": source["authority"],
        "overview": {
            "telemetry_complete": telemetry["telemetry_complete"],
            "physical_world_fingerprint": source[
                "physical_world_fingerprint"
            ],
            "compiler_evidence": compiler["source_kind"],
            "qualification_status": (
                None if qualification is None else qualification["summary"]["status"]
            ),
            "classification_counts": dict(sorted(classifications.items())),
        },
        "inventory": inventory,
        "shell_nodes": shell_nodes,
        "shell_coverage": shell_coverage,
        "physical_evidence": {
            "coordinate_system": "WGS84-ECEF-metres",
            "frames": source["physical_frames"],
        },
        "planned_evidence": {
            "plans": planning["plans"],
            "formal_transitions": planning["formal_transitions"],
            "final_state": planning["final_state"],
            "final_state_fingerprint": planning["final_state_fingerprint"],
        },
        "compiled_evidence": compiler,
        "transactions": transactions,
        "qualification": qualification,
        "workload": source["workload"],
        "telemetry": {
            "header": telemetry["header"],
            "nodes": telemetry["nodes"],
            "interfaces": telemetry["interfaces"],
            "boundaries": telemetry["boundaries"],
            "packets": telemetry["packets"],
            "loss_records": telemetry["loss_records"],
            "recorder_end": telemetry["recorder_end"],
            "raw_record_count": telemetry["raw_record_count"],
        },
    }


def _runtime_shell_coverage(qualification, transactions, membership):
    if qualification is None:
        return []
    plan = qualification["plan"]
    identities = {p["probe_id"]: p for p in plan["probe_identities"]}
    planned_coverage = {c["shell_id"]: c for c in plan.get("shell_coverage", ())}
    result = []
    for shell in sorted(set(membership.values())):
        episodes = [e for e in plan["episodes"] if any(i["expected_reachable"] and any(membership.get(n) == shell for n in i["path_node_ids"]) for i in e["state_intervals"])]
        tx = [t for t in transactions if shell in t.get("planned_shell_ids", ()) and t.get("request") is not None and t.get("generator") is not None and not t.get("association_uncertainty")]
        evidence = planned_coverage.get(shell)
        goals = [] if evidence is None else [dict(g) for g in evidence["goals"]]
        for goal in goals:
            supporting = {e["episode_id"]: e for e in episodes if identities[e["probe_id"]]["direction"] == goal["direction"] and any(c["shell_id"] == shell and c["case"] == goal["case"] for c in e.get("coverage_cases", ()))}
            goal["observed_requests"] = sum(
                _observes_coverage_window(t, supporting[t["generator"]["episode_id"]], goal["case"], shell, membership)
                for t in tx if t["generator"]["episode_id"] in supporting
                and t["generator"].get("probe_id") == supporting[t["generator"]["episode_id"]]["probe_id"])
            goal["observed"] = bool(goal["observed_requests"])
        directions = sorted({identities[t["generator"]["probe_id"]]["direction"] for t in tx if t["generator"].get("probe_id") in identities})
        status = ("eligibility-not-recorded" if evidence is None else "no-eligible-candidate" if not evidence["eligible"] else
                  "covered" if all(g["observed_requests"] or not g["eligible_candidate_observations"] for g in goals) else "coverage-gap")
        result.append({"shell_id": shell, "status": status, "planned_episode_count": len(episodes),
                       "planned_episode_ids": [e["episode_id"] for e in episodes], "observed_requests": len(tx),
                       "delivered_replies": sum(t.get("reply_delivery") in {"endpoint-observed", "executor-confirmed"} for t in tx),
                       "observed_directions": directions, "goals": goals,
                       "reason": "Historical selection did not record per-Shell eligibility; zero selected episodes do not prove no available path." if evidence is None else evidence.get("reason"),
                       "count_semantics": "overlapping planned-path coverage; not per-Shell executor packet totals"})
    return result


def _observes_coverage_window(transaction, episode, case, shell, membership):
    request = transaction["request"]
    value = request.get("simulation_timestamp")
    if value is None:
        return False
    at = datetime.fromisoformat(value)
    if not datetime.fromisoformat(episode["start_time"]) <= at < datetime.fromisoformat(episode["end_time"]):
        return False
    events = {datetime.fromisoformat(t) for t in episode.get("affected_activation_times", ())}
    for interval in episode["state_intervals"]:
        start, end = (datetime.fromisoformat(interval[k]) for k in ("start_time", "end_time"))
        if not (start <= at < end and interval["expected_reachable"]
                and any(membership.get(n) == shell for n in interval["path_node_ids"])):
            continue
        # A transition goal requires an observation after the relevant change,
        # within the bounded event window. An episode-wide label is eligibility
        # evidence only. Stable observations exclude these transition windows.
        relevant = start in events and bool(interval.get("boundary_reasons"))
        in_transition = relevant and at < min(end, start + timedelta(
            seconds=max(3.0, 2 * episode.get("interval_seconds", 1.0))))
        return in_transition if case == "transition" else not in_transition
    return False


def _validate_compact_planning_structure(
    planning: dict[str, Any],
    *,
    physical_frames: tuple[dict[str, Any], ...],
) -> None:
    expected = tuple(
        (plan_index, plan["plan_id"], transition_index, timestamp)
        for plan_index, plan in enumerate(planning["plans"])
        for transition_index, timestamp in enumerate(
            plan["activation_timestamps"]
        )
    )
    actual = tuple(
        (
            value["plan_index"],
            value["plan_id"],
            value["transition_index"],
            value["timestamp"],
        )
        for value in planning["formal_transitions"]
    )
    physical_timestamps = tuple(
        value["timestamp"] for value in physical_frames
    )
    if actual != expected or tuple(value[3] for value in actual) != (
        physical_timestamps
    ):
        raise ValueError(
            "Traffic compact replay previous/final boundary coverage differs"
        )


def _project_transactions(
    values: tuple[dict[str, Any], ...],
    *,
    planning: dict[str, Any],
    nodes_by_address: dict[str, dict[str, Any]],
    zone_prefixes: dict[str, str],
    activation_times: tuple[datetime, ...],
    reader_validated: bool = False,
) -> tuple[dict[str, object], ...]:
    cursor = _PlanningCursor(tuple(planning["formal_transitions"]),
                             reader_validated=reader_validated)
    results: list[dict[str, object] | None] = [None] * len(values)
    requested = []
    for index, value in enumerate(values):
        request = value["request"]
        published = None if request is None else (request.get("transition_boundary_timestamp") if request.get("transition_boundary_timestamp") is not None and request.get("generation_boundary_timestamp") == request.get("transition_boundary_timestamp") else request.get("formal_boundary_timestamp"))
        if published is None:
            results[index] = _project_transaction(
                value,
                boundary=None,
                zone_assignments={},
                route_outcomes=(),
                nodes_by_address=nodes_by_address,
                zone_prefixes=zone_prefixes,
                activation_times=activation_times,
            )
        else:
            requested.append(
                (
                    datetime.fromisoformat(published),
                    index,
                    value,
                )
            )
    for timestamp, index, value in sorted(requested):
        cursor.advance_through(timestamp)
        results[index] = _project_transaction(
            value,
            boundary=cursor.boundary,
            zone_assignments=cursor.zone_assignments,
            route_outcomes=cursor.route_outcomes.values(),
            nodes_by_address=nodes_by_address,
            zone_prefixes=zone_prefixes,
            activation_times=activation_times,
        )
    cursor.replay_remaining()
    cursor.validate_final(planning)
    assert all(value is not None for value in results)
    return tuple(value for value in results if value is not None)


@dataclass(slots=True)
class _PlanningCursor:
    records: tuple[dict[str, Any], ...]
    reader_validated: bool = False
    index: int = 0
    state: PlannedOperationalState | None = None
    boundary: dict[str, Any] | None = None
    zone_assignments: dict[str, dict[str, Any] | None] = field(
        default_factory=dict
    )
    route_outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def advance_through(self, timestamp: datetime) -> None:
        while self.index < len(self.records):
            record = self.records[self.index]
            if datetime.fromisoformat(record["timestamp"]) > timestamp:
                break
            self._advance_one(record)

    def replay_remaining(self) -> None:
        while self.index < len(self.records):
            self._advance_one(self.records[self.index])

    def validate_final(self, planning: dict[str, Any]) -> None:
        if self.reader_validated:
            return
        if self.state is None:
            raise ValueError("Traffic planning evidence contains no transition")
        if (
            planned_operational_state_fingerprint(self.state)
            != planning["final_state_fingerprint"]
        ):
            raise ValueError("Traffic compact replay final fingerprint differs")
        if planned_operational_state_to_data(self.state) != planning["final_state"]:
            raise ValueError("Traffic compact replay final state differs")

    def _advance_one(self, record: dict[str, Any]) -> None:
        transition_data = record["transition"]
        if not self.reader_validated:
            transition = plan_transition_from_data(transition_data)
            if transition.timestamp.isoformat() != record["timestamp"]:
                raise ValueError("Traffic transition timestamp metadata differs")
            if (
                plan_transition_fingerprint(transition)
                != record["transition_fingerprint"]
            ):
                raise ValueError("Traffic transition fingerprint differs")
            state = apply_plan_transition(self.state, transition)
            if (
                planned_operational_state_fingerprint(state)
                != record["state_fingerprint"]
            ):
                raise ValueError("Traffic transition state fingerprint differs")
            if _state_summary(state) != record["state_summary"]:
                raise ValueError("Traffic transition state summary differs")
            self.state = state
        _apply_compact_maps(
            transition_data,
            zone_assignments=self.zone_assignments,
            route_outcomes=self.route_outcomes,
        )
        self.boundary = record
        self.index += 1


def _apply_compact_maps(
    transition: dict[str, Any],
    *,
    zone_assignments: dict[str, dict[str, Any] | None],
    route_outcomes: dict[str, dict[str, Any]],
) -> None:
    missing = object()
    for change in transition["zone_service_transitions"]:
        previous = zone_assignments.get((change["network_scope"], change["zone_id"]), missing)
        if previous is missing:
            if change["previous_satellite_id"] is not None:
                raise ValueError("Traffic compact Zone previous value differs")
        elif previous != change["previous_satellite_id"]:
            raise ValueError("Traffic compact Zone previous value differs")
        zone_assignments[(change["network_scope"], change["zone_id"])] = change["current_satellite_id"]
    for change in transition["route_outcome_transitions"]:
        key = _demand_key(change["demand"])
        previous = route_outcomes.get(key)
        expected_previous = (
            None
            if change["previous"] is None
            else {"demand": change["demand"], **change["previous"]}
        )
        if previous != expected_previous:
            raise ValueError("Traffic compact Route previous value differs")
        if change["current"] is None:
            route_outcomes.pop(key, None)
        else:
            route_outcomes[key] = {
                "demand": change["demand"],
                **change["current"],
            }


def _state_summary(value: PlannedOperationalState) -> dict[str, int]:
    assignments = value.zone_service_assignments
    reachable = sum(
        isinstance(outcome, ReachableRouteOutcome)
        for outcome in value.route_outcomes
    )
    served = sum(
        assignment.satellite_id is not None for assignment in assignments
    )
    return {
        "operational_connection_count": len(value.operational_connections),
        "zone_assignment_count": len(assignments),
        "zones_served": served,
        "zones_unserved": len(assignments) - served,
        "reachable_route_count": reachable,
        "unreachable_route_count": len(value.route_outcomes) - reachable,
    }


def _demand_key(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _project_transaction(
    value: dict[str, Any],
    *,
    boundary: dict[str, Any] | None,
    zone_assignments: dict[str, dict[str, Any] | None],
    route_outcomes: Iterable[dict[str, Any]],
    nodes_by_address: dict[str, dict[str, Any]],
    zone_prefixes: dict[str, str],
    activation_times: tuple[datetime, ...],
) -> dict[str, object]:
    request = value["request"]
    result = dict(value)
    reply_confirmed = value.get("reply_delivery") in {"endpoint-observed", "executor-confirmed"} or ("reply_delivery" not in value and value["observed_reply"])
    if request is None:
        result.update(
            {
                "formal_boundary_timestamp": None,
                "route_outcome": None,
                "planned_reachability": "unknown",
                "planned_unreachable_reason": None,
                "activation_associated": False,
                "classification": "unclassified",
                "classification_reason": "no ICMP request observation",
            }
        )
        return result
    timestamp = datetime.fromisoformat(request["simulation_timestamp"])
    if boundary is None:
        result.update(
            {
                "formal_boundary_timestamp": None,
                "route_outcome": None,
                "planned_reachability": "unknown",
                "planned_unreachable_reason": None,
                "activation_associated": False,
                "classification": "unclassified",
                "classification_reason": (
                    "no published planned state at observation timestamp"
                ),
            }
        )
        return result
    source_node = nodes_by_address.get(request["source_ipv4"])
    source_id = None if source_node is None else source_node["node_id"]
    target = _target_for_address(
        request["destination_ipv4"], nodes_by_address, zone_prefixes
    )
    source_zone = _zone_for_address(request["source_ipv4"], zone_prefixes)
    if target is not None and target["kind"] == "ground-station":
        target["network_scope"] = "" if source_zone is None else source_zone[0]
    if source_id is None:
        if source_zone is not None:
            satellite_id = zone_assignments.get(source_zone)
            if satellite_id is not None:
                source_id = _node_id(satellite_id)
    if value.get("generator") and _zone_for_address(request["source_ipv4"], zone_prefixes) is not None:
        source_id = value["generator"].get("physical_source_node_id") or source_id
    route = _route_outcome(route_outcomes, source_id, target)
    activation_associated = request.get("transition_boundary_timestamp") is not None
    if route is None:
        reachability = "unknown"
        reason = None
        classification = "unclassified"
        classification_reason = "no matching explicit planned Route demand"
    elif route["kind"] == "unreachable":
        reachability = "unreachable"
        reason = route["reason"]
        classification = (
            "unexpected-reachable"
            if reply_confirmed
            else "expected-unreachable"
        )
        classification_reason = (
            "runtime reply contradicts explicit planned-unreachable outcome"
            if reply_confirmed
            else f"explicit planned-unreachable: {reason}"
        )
    else:
        reachability = "reachable"
        reason = None
        if reply_confirmed:
            classification = "observed-reachable"
            classification_reason = "reply observed for explicit reachable Route"
        elif activation_associated:
            classification = "activation-associated-loss"
            classification_reason = (
                "reachable loss observed during an actual Main transition"
            )
        else:
            classification = "unexplained-reachable-loss"
            classification_reason = (
                "explicit reachable Route had no observed reply away from activation"
            )
    if value.get("association_uncertainty"):
        classification = "unclassified"
        classification_reason = "; ".join(value["association_uncertainty"])
    elif value.get("observed_reply") and value.get("reply_delivery") == "unconfirmed":
        classification = "unclassified"
        classification_reason = "reply forwarding observed; delivery to source is unconfirmed"
    result.update(
        {
            "formal_boundary_timestamp": boundary["timestamp"],
            "route_outcome": route,
            "planned_reachability": reachability,
            "planned_unreachable_reason": reason,
            "activation_associated": activation_associated,
            "classification": classification,
            "classification_reason": classification_reason,
        }
    )
    return result


def _target_for_address(
    address: str,
    nodes_by_address: dict[str, dict[str, Any]],
    zone_prefixes: dict[str, str],
) -> dict[str, object] | None:
    node = nodes_by_address.get(address)
    if node is not None:
        parts = str(node["node_id"]).split("/", 2)
        if len(parts) == 3 and parts[1] == "ground-station":
            return {"kind": "ground-station", "node_id": node["node_id"]}
    zone_id = _zone_for_address(address, zone_prefixes)
    return None if zone_id is None else {"kind": "zone", "zone_id": zone_id[1], "network_scope": zone_id[0]}


def _zone_for_address(address: str, zone_prefixes: dict[str, str]) -> str | None:
    candidate = ip_address(address)
    for zone_id, prefix in sorted(zone_prefixes.items()):
        if candidate in ip_interface(prefix).network:
            return zone_id
    return None


def _route_outcome(
    outcomes: Iterable[dict[str, Any]],
    source_id: str | None,
    target: dict[str, object] | None,
) -> dict[str, Any] | None:
    if source_id is None or target is None:
        return None
    for outcome in outcomes:
        demand = outcome["demand"]
        if _node_id(demand["source"]) != source_id:
            continue
        planned_target = demand["target"]
        if target.get("network_scope", "") != planned_target.get("network_scope", ""):
            continue
        if target["kind"] == "zone" and planned_target["kind"] == "zone":
            if target["zone_id"] == planned_target["zone_id"]:
                return outcome
        if (
            target["kind"] == "ground-station"
            and planned_target["kind"] == "ground-station"
            and target["node_id"]
            == _node_id(planned_target["ground_station_id"])
        ):
            return outcome
    return None


def _zone_prefixes(
    realizations: tuple[dict[str, Any], ...]
) -> dict[str, str]:
    baseline = None
    for realization in realizations:
        if "zone_prefixes" not in realization:
            continue
        values = realization["zone_prefixes"]
        if not isinstance(values, (list, tuple)):
            raise TypeError("Compiler Zone prefixes must be a sequence")
        mapping = {}
        owners = {}
        for value in values:
            zone_id = value.get("zone_id")
            prefix = value.get("packet_prefix")
            if not isinstance(zone_id, str) or not zone_id:
                raise ValueError("Compiler Zone prefix has an invalid Zone ID")
            if not isinstance(prefix, str) or not prefix:
                raise ValueError("Compiler Zone prefix is invalid")
            zone_id = (value.get("network_scope", ""), zone_id)
            canonical = str(ip_interface(prefix))
            network = str(ip_interface(prefix).network)
            if zone_id in mapping or (
                network in owners and owners[network] != zone_id
            ):
                raise ValueError("Compiler Zone prefix identity is conflicting")
            mapping[zone_id] = canonical
            owners[network] = zone_id
        if baseline is None:
            baseline = mapping
        elif mapping != baseline:
            raise ValueError("Compiler Zone prefixes are unstable")
    return {} if baseline is None else baseline


def _node_id(value: dict[str, Any]) -> str:
    return f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"


def _project_probe_qualification(
    value: dict[str, Any] | None,
    *,
    ping_transactions: tuple[dict[str, Any], ...],
    telemetry_complete: bool,
    telemetry_losses: int,
) -> dict[str, Any] | None:
    """Classify probe episodes using only active published runtime evidence."""

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
        if item.get("generation_publication_occurred") is True
    )
    actual_frontier = (
        None
        if not activations
        else max(
            datetime.fromisoformat(str(item["active_frontier"]))
            for item in activations
        )
    )
    telemetry_by_identifier: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for transaction in ping_transactions:
        telemetry_by_identifier[int(transaction["identifier"])].append(transaction)
    migration_records = tuple(
        dict(item)
        for item in execution
        if item.get("record_type") == "zone_source_migration"
    )
    generator_events = sorted(
        (int(item.get("launch_monotonic_ns", item["host_monotonic_ns"])), 1)
        if item["record_type"] == "generator_launched" else
        (int(item.get("stop_monotonic_ns", item["host_monotonic_ns"])), -1)
        for item in execution
        if item.get("record_type") in {"generator_launched", "generator_stopped"}
    )
    active_generators = peak_generators = 0
    for _, change in generator_events:
        active_generators += change
        peak_generators = max(peak_generators, active_generators)
    policy = plan.get("qualification_policy", {})
    maximum_disruption = float(os.environ.get("SKYSEED_TRAFFIC_MAX_TRANSITION_DISRUPTION_SECONDS",
        policy.get("maximum_transition_disruption_seconds", 2.0)))
    if not isfinite(maximum_disruption) or maximum_disruption <= 0:
        raise ValueError("maximum transition disruption must be finite and positive")
    report_ranges = _ping_report_ranges(execution)
    episode_results = []
    activation_anomalies: dict[str, int] = defaultdict(int)
    for episode_id, episode in episodes.items():
        identity = identities[episode["probe_id"]]
        records = records_by_episode.get(episode_id, [])
        stops = [
            item
            for item in records
            if item.get("record_type") == "generator_stopped"
        ]
        observations = [
            item
            for item in records
            if item.get("record_type") == "ping_observation"
        ]
        armed = any(item.get("record_type") == "episode_armed" for item in records)
        not_run = any(
            item.get("record_type") == "episode_not_run" for item in records
        )
        sent = sum(int(item.get("sent", 0)) for item in stops)
        received = sum(int(item.get("received", 0)) for item in stops)
        rtts = sorted(
            float(item["rtt_ms"])
            for item in observations
            if item.get("observation") == "reply"
            and item.get("rtt_ms") is not None
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
        cases = _request_cases(episode, records, ping_transactions, activations, migration_records,
                               maximum_disruption_seconds=maximum_disruption, report_ranges=report_ranges)
        consecutive = longest_consecutive = 0
        recovered_isolated = recovered_transient = False
        for case in cases:
            if case["classification"] == "unexplained-reachable-loss":
                consecutive += 1
                longest_consecutive = max(longest_consecutive, consecutive)
            else:
                recovered_isolated |= consecutive == 1
                recovered_transient |= consecutive > 1
                consecutive = 0
            transaction = case.pop("transaction")
            if transaction is not None:
                transaction["executor_conformance"] = case["classification"]
                if case["classification"] == "expected-handover-disturbance":
                    transaction["classification"] = case["classification"]
                    transaction["classification_reason"] = "actual relevant transition, bounded disruption and observed recovery"
                    transaction["transition_recovery"] = case["recovery"]
        final_reachable_run = consecutive
        expected_unreachable_observations = sum(c["classification"] == "expected-unreachable" for c in cases)
        migration_observations = sum(c["actual_transition_association"] is not None and
                                    c["actual_transition_association"]["kind"] == "actual-generator-migration" for c in cases)
        launch_records = [
            item
            for item in records
            if item.get("record_type") == "generator_launched"
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
        elif final_reachable_run or recovered_transient:
            classifications.append("transient-disruption")
        if recovered_isolated:
            classifications.append("isolated-loss")
        for label in ("expected-handover-disturbance", "unexpected-reachable", "unclassified"):
            if any(c["classification"] == label for c in cases):
                classifications.append(label)
        if sent > len(cases):
            classifications.append("incomplete-request-evidence")
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
                "network_scope": identity.get("network_scope", ""),
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
                "expected_unreachable_requests": expected_unreachable_observations,
                "conformance_unit": "distinct generator segment and ICMP sequence; report duplicates and late replies reconciled",
                "migration_observations_excluded": 0,
                "migration_associated_requests": migration_observations,
                "request_conformance": cases,
                "unresolved_sent_request_count": max(0, sent - len(cases)),
                "conformance_counts": dict(sorted((label, sum(c["classification"] == label for c in cases)) for label in {c["classification"] for c in cases})),
                "generator_segments": len(launch_records),
                "cleanup_complete": cleanup_complete,
                "telemetry_transactions": telemetry_transactions,
                "packet_path_evidence_present": telemetry_transactions > 0,
                "classifications": classifications,
            }
        )
    sent = sum(item["sent"] for item in episode_results)
    received = sum(item["received"] for item in episode_results)
    all_rtts = sorted(
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
    formal_activations = tuple(item for item in activations if item.get("preparation") is not True)
    main_durations = [duration for item in formal_activations
                      if (duration := _optional_non_negative_seconds(item.get("main_seconds"))) is not None]
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
    benign = {"pass", "not-executed-outside-active-horizon", "expected-unreachable", "expected-handover-disturbance"}
    if status == "PASS" and any(
        any(label not in benign for label in item["classifications"])
        for item in episode_results
    ):
        status = "WARN"
    return {
        "summary": {
            "status": status,
            "qualification_policy": {"maximum_transition_disruption_seconds": maximum_disruption,
                "requires": "actual relevant transition + bounded duration + recovery of the logical probe",
                "historical_missing_request_time": "bracket -D/-O outstanding reports with recorded host clocks; require constant planned reachability throughout request window, otherwise uncertain"},
            "raw_executor_totals_authority": "generator_stopped sent/received; not packet-hop or conformance counts",
            "global_budget": plan.get("global_budget"),
            "planned_peak_concurrent_episodes": plan["planning"].get("maximum_concurrent_episode_bound"),
            "observed_peak_concurrent_generators": peak_generators,
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
            "activation_window_episodes": plan["planning"][
                "activation_window_episode_count"
            ],
            "direction_counts": plan["planning"]["direction_counts"],
            "path_length_class_counts": plan["planning"][
                "path_length_class_counts"
            ],
            "selection_reason_counts": plan["planning"][
                "selection_reason_counts"
            ],
            "estimated_echo_requests": plan["planning"][
                "estimated_echo_requests"
            ],
            "estimated_telemetry_events": plan["planning"][
                "estimated_telemetry_events"
            ],
            "sent": sent,
            "received": received,
            "loss": max(0, sent - received),
            "loss_percent": 0.0 if sent == 0 else (sent - received) * 100 / sent,
            "rtt_ms": _distribution(all_rtts),
            "longest_receive_gap_seconds": max(
                (
                    item["longest_receive_gap_seconds"]
                    for item in episode_results
                ),
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
            "active_activation_count": sum(item.get("preparation") is not True for item in activations),
            "completed_boundary_count": len(activations),
            "active_frontier": (
                None if actual_frontier is None else actual_frontier.isoformat()
            ),
        },
        "runner_observer_effect": {
            "main_seconds": _distribution(main_durations),
            "start_lag_seconds": _distribution([
                value for item in formal_activations
                if (value := _optional_non_negative_seconds(item.get("ideal_start_lag_seconds"))) is not None
            ]),
            "completion_lag_seconds": _distribution([
                value for item in formal_activations
                if (value := _optional_non_negative_seconds(item.get("ideal_completion_lag_seconds"))) is not None
            ]),
            "runner_rss_max_bytes": max(rss_values, default=None),
        },
        "worst_flow": worst_flow,
        "worst_activation": worst_activation,
        "zone_source_migrations": migration_records,
        "episodes": tuple(episode_results),
    }


def _episode_execution_disposition(records: list[dict[str, Any]]) -> str:
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
    generator = transaction.get("generator")
    return bool(generator and generator.get("episode_id") == launch.get("episode_id")
                and generator.get("segment_index") == launch.get("segment_index"))


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
    timestamp_ns: int, switches: tuple[tuple[int, int, str], ...]
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


def _optional_non_negative_seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    seconds = float(value)
    return seconds if isfinite(seconds) and seconds >= 0 else None


def _percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(len(values) - 1, lower + 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


__all__ = ["project_traffic_replay"]
