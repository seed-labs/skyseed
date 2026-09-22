"""Build bounded experiment Traffic probes from compiled intended state."""

from __future__ import annotations

from heapq import nsmallest

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from ipaddress import IPv4Interface, ip_interface
import json
from math import asin, ceil, cos, isfinite, radians, sin, sqrt
import os
from pathlib import Path
import tempfile
from typing import Any

from skyseed_v2.backend.compiler.result import CompilationResult
from skyseed_v2.backend.execution_materials.actions import _ForwardingBinding, _ForwardingIntent, _ForwardingKey, _MutationOperation
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    OperationalConnectionKind,
    Plan,
    PhysicalWorld,
    ReachableRouteOutcome, LocalRouteOutcome,
    RouteDemand,
    RouteOutcomeTransition,
    UnreachableRouteOutcome,
    ZoneServiceTransition,
    ZoneRouteDemandTarget,
    plan_transition_fingerprint,
)


_ARTIFACT_TYPE = "traffic-probe-plan"
_FORMAT_VERSION = 4
_DIRECTION_GROUND_TO_ZONE = "ground-station-to-zone"
_DIRECTION_ZONE_TO_GROUND = "zone-to-ground-station"
_SAMPLING_SEED = 20260909
_PERSISTENT = "persistent"
_ROTATING_BACKGROUND = "rotating-background"
_ACTIVATION_WINDOW = "activation-window"
_MAX_PERSISTENT_CONTROLS = 2
_CANDIDATE_BUCKET_CAPACITY = 2
_NATIVE_CANDIDATE_BUCKET_CAPACITY = 8
_BOUNDARY_CATEGORY_QUOTA = 4
_BOUNDARY_FACT_INSPECTION_LIMIT = 128


@dataclass(frozen=True, slots=True)
class _ProbeStateInterval:
    start_time: datetime
    end_time: datetime
    expected_reachable: bool | None
    unreachable_reason: str | None
    service_satellite_id: str | None
    path_node_ids: tuple[str, ...]
    path_length_hops: int | None
    boundary_reasons: tuple[str, ...]

    def artifact(self) -> dict[str, object]:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "expected_reachable": self.expected_reachable,
            "path_status": "unknown-native-transit" if self.expected_reachable is None else "planned" if self.path_node_ids else "unavailable",
            "unreachable_reason": self.unreachable_reason,
            "service_satellite_id": self.service_satellite_id,
            "path_node_ids": list(self.path_node_ids),
            "path_length_hops": self.path_length_hops,
            "boundary_reasons": list(self.boundary_reasons),
        }


@dataclass(frozen=True, slots=True)
class _TrafficProbeIdentity:
    probe_id: str
    direction: str
    ground_station_id: str
    zone_id: str
    zone_ipv4: str
    selection_reasons: tuple[str, ...]
    path_stability: str
    path_length_class: str
    network_scope: str = ""
    geographic_separation_m: float | None = None
    geographic_separation_class: str = "unavailable"

    def artifact(self) -> dict[str, object]:
        return {
            "probe_id": self.probe_id,
            "direction": self.direction,
            "logical_source": (
                self.ground_station_id
                if self.direction == _DIRECTION_GROUND_TO_ZONE
                else self.zone_id
            ),
            "logical_destination": (
                self.zone_id
                if self.direction == _DIRECTION_GROUND_TO_ZONE
                else self.ground_station_id
            ),
            "ground_station_id": self.ground_station_id,
            "zone_id": self.zone_id,
            "network_scope": self.network_scope,
            "zone_ipv4": self.zone_ipv4,
            "selection_reasons": list(self.selection_reasons),
            "path_stability": self.path_stability,
            "path_length_class": self.path_length_class,
            "geographic_separation_m": self.geographic_separation_m,
            "geographic_separation_class": self.geographic_separation_class,
        }


@dataclass(frozen=True, slots=True)
class _TrafficProbeEpisode:
    episode_id: str
    probe_id: str
    mode: str
    start_time: datetime
    end_time: datetime
    interval_seconds: float
    affected_activation_times: tuple[datetime, ...]
    state_intervals: tuple[_ProbeStateInterval, ...]
    estimated_echo_requests: int
    estimated_telemetry_events: int
    coverage_cases: tuple[tuple[str, str], ...] = ()

    def artifact(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "probe_id": self.probe_id,
            "mode": self.mode,
            "episode_intent": "armed",
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "interval_seconds": self.interval_seconds,
            "affected_activation_times": [
                value.isoformat() for value in self.affected_activation_times
            ],
            "state_intervals": [value.artifact() for value in self.state_intervals],
            "estimated_echo_requests": self.estimated_echo_requests,
            "estimated_telemetry_events": self.estimated_telemetry_events,
            "coverage_cases": [{"shell_id": shell, "case": case} for shell, case in self.coverage_cases],
        }


@dataclass(frozen=True, slots=True)
class TrafficProbePlan:
    """Hold one private derived experiment observation plan.

    The application-side builder creates this value from trusted immutable
    Compiler outputs.  It is not a Simulator Plan, a runtime authority, or a
    persisted-realization transport contract.
    """

    coverage_start: datetime
    coverage_end: datetime
    probe_identities: tuple[_TrafficProbeIdentity, ...]
    episodes: tuple[_TrafficProbeEpisode, ...]
    nominal_boundaries: tuple[datetime, ...]
    supplied_realization_count: int
    revision_id: str
    candidate_pair_count: int
    planning_duration_seconds: float
    shell_metadata: tuple[tuple[str, str], ...] = ()
    global_budget: tuple[int, int, int] = (20, 8, 3600)
    shell_eligibility: tuple[tuple[str, str, str, int], ...] = ()
    forwarding_backend: str = "bpf"

    def artifact(self) -> dict[str, object]:
        persistent = sum(value.mode == _PERSISTENT for value in self.episodes)
        rotating = sum(
            value.mode == _ROTATING_BACKGROUND for value in self.episodes
        )
        activation = sum(
            value.mode == _ACTIVATION_WINDOW for value in self.episodes
        )
        direction_counts = _counts(
            value.direction for value in self.probe_identities
        )
        path_counts = _counts(
            value.path_length_class for value in self.probe_identities
        )
        stability_counts = _counts(
            value.path_stability for value in self.probe_identities
        )
        reason_counts = _counts(
            reason
            for value in self.probe_identities
            for reason in value.selection_reasons
        )
        estimated_echo = sum(
            value.estimated_echo_requests for value in self.episodes
        )
        estimated_telemetry = sum(
            value.estimated_telemetry_events for value in self.episodes
        )
        return {
            "artifact_type": _ARTIFACT_TYPE,
            "format_version": _FORMAT_VERSION,
            "authority": "derived-experiment-traffic-plan",
            "selection_source": "trusted in-memory CompilationResult sequence",
            "runtime_authority": False,
            "forwarding_backend": self.forwarding_backend,
            "transit_path_evidence": "unavailable" if not self.episodes else "unknown" if any(i.expected_reachable is None for e in self.episodes for i in e.state_intervals) else "planned",
            "packet_path_telemetry": "unavailable" if self.forwarding_backend == "linux" else "BPF when enabled",
            "classification_scope": "authoritative-active-published-runtime-only",
            "planning_horizon": {
                "coverage_start_time": self.coverage_start.isoformat(),
                "coverage_end_time": self.coverage_end.isoformat(),
                "supplied_realization_count": self.supplied_realization_count,
                "terminality": "unknown",
                "revision_semantics": "revisable-lookahead",
                "revision_id": self.revision_id,
                "nominal_boundaries": [
                    value.isoformat() for value in self.nominal_boundaries
                ],
            },
            "planning": {
                "workload_status": "ready" if self.episodes else "empty",
                "empty_reason": None if self.episodes else "no eligible probes within the configured coverage and budgets",
                "selection_strategy": "bounded-native-geographic-extremes" if self.forwarding_backend == "linux" else "bounded-stratified-reservoir",
                "global_flow_ranking": False,
                "candidate_bucket_capacity": _NATIVE_CANDIDATE_BUCKET_CAPACITY if self.forwarding_backend == "linux" else _CANDIDATE_BUCKET_CAPACITY,
                "duration_seconds": self.planning_duration_seconds,
                "candidate_pair_count": self.candidate_pair_count,
                "selected_probe_identity_count": len(self.probe_identities),
                "selected_episode_count": len(self.episodes),
                "persistent_episode_count": persistent,
                "rotating_background_episode_count": rotating,
                "activation_window_episode_count": activation,
                "direction_counts": direction_counts,
                "path_length_class_counts": path_counts,
                "path_stability_counts": stability_counts,
                "geographic_separation_class_counts": _counts(i.geographic_separation_class for i in self.probe_identities),
                "selection_reason_counts": reason_counts,
                "estimated_echo_requests": estimated_echo,
                "estimated_telemetry_events": estimated_telemetry,
                "maximum_concurrent_episode_bound": _peak_concurrency(self.episodes),
                "maximum_request_rate_per_second": sum(
                    1.0 / value.interval_seconds for value in self.episodes
                ),
            },
            "shell_metadata": [{"node_id": node, "shell_id": shell} for node, shell in self.shell_metadata],
            "shell_coverage": _shell_coverage(self),
            "shell_eligibility": self.shell_eligibility,
            "sampling": {"seed": _SAMPLING_SEED, "method": "bounded relative near/far arranged GS/Zone pairs; unavailable geography stays unlabeled; no inferred transit" if self.forwarding_backend == "linux" else "seeded hash sampling by Shell, direction, stable/transition case and time; two long controls reserved first"},
            "global_budget": {"maximum_episodes": self.global_budget[0], "maximum_concurrent_probes": self.global_budget[1], "maximum_echo_requests": self.global_budget[2]},
            "qualification_policy": {"maximum_transition_disruption_seconds": 2.0},
            "probe_identities": [
                value.artifact() for value in self.probe_identities
            ],
            "episodes": [value.artifact() for value in self.episodes],
        }

    @classmethod
    def from_artifact(cls, value: Mapping[str, object]) -> "TrafficProbePlan":
        if (
            value.get("artifact_type") != _ARTIFACT_TYPE
            or value.get("format_version") not in (2, 3, _FORMAT_VERSION)
            or value.get("runtime_authority") is not False
            or value.get("classification_scope")
            != "authoritative-active-published-runtime-only"
        ):
            raise ValueError("Traffic probe plan artifact is incompatible")
        simulation = _mapping(
            value.get("planning_horizon"), "probe plan planning horizon"
        )
        planning = _mapping(value.get("planning"), "probe plan planning")
        start = _timestamp(
            simulation.get("coverage_start_time"), "probe plan coverage start"
        )
        end = _timestamp(
            simulation.get("coverage_end_time"), "probe plan coverage end"
        )
        if (
            simulation.get("terminality") != "unknown"
            or simulation.get("revision_semantics") != "revisable-lookahead"
        ):
            raise ValueError("Traffic probe planning horizon claims terminality")
        boundaries = tuple(
            _timestamp(item, "probe plan boundary")
            for item in _list(simulation.get("nominal_boundaries"), "probe boundaries")
        )
        identities = tuple(
            _identity_from_artifact(item)
            for item in _list(
                value.get("probe_identities"), "probe plan identities"
            )
        )
        if len({item.probe_id for item in identities}) != len(identities):
            raise ValueError("Traffic probe identity is duplicated")
        identity_ids = {item.probe_id for item in identities}
        episodes = tuple(
            _episode_from_artifact(item, start, end, identity_ids)
            for item in _list(value.get("episodes"), "probe plan episodes")
        )
        if value.get("forwarding_backend", "bpf") != "linux" and any(
                interval.expected_reachable is None for episode in episodes for interval in episode.state_intervals):
            raise ValueError("BPF Traffic requires planned reachability")
        if len({item.episode_id for item in episodes}) != len(episodes):
            raise ValueError("Traffic probe episode identity is duplicated")
        if planning.get("selected_probe_identity_count") != len(identities):
            raise ValueError("Traffic probe plan identity count differs")
        if planning.get("selected_episode_count") != len(episodes):
            raise ValueError("Traffic probe plan episode count differs")
        if (
            planning.get("selection_strategy")
            not in {"bounded-stratified-reservoir", "bounded-native-edge-coverage", "bounded-native-geographic-extremes"}
            or planning.get("global_flow_ranking") is not False
        ):
            raise ValueError("Traffic probe plan selection strategy is incompatible")
        return cls(
            start,
            end,
            identities,
            episodes,
            boundaries,
            _positive_int(
                simulation.get("supplied_realization_count"),
                "supplied_realization_count",
            ),
            _non_empty_string(simulation.get("revision_id"), "revision_id"),
            _non_negative_int(
                planning.get("candidate_pair_count"), "candidate_pair_count"
            ),
            _non_negative_number(
                planning.get("duration_seconds"), "planning duration"
            ),
            tuple((str(r["node_id"]), str(r["shell_id"])) for r in value.get("shell_metadata", ())),
            tuple(int(value.get("global_budget", {}).get(k, d)) for k, d in zip(
                ("maximum_episodes", "maximum_concurrent_probes", "maximum_echo_requests"), (20, 8, 3600))),
            tuple(tuple(r) for r in value.get("shell_eligibility", ())),
            _one_of(value.get("forwarding_backend", "bpf"), {"bpf", "linux"}, "forwarding backend"),
        )


@dataclass(slots=True)
class _BoundaryProjection:
    nominal_time: datetime
    route_outcomes: dict[
        RouteDemand, ReachableRouteOutcome | UnreachableRouteOutcome | LocalRouteOutcome
    ]
    forwarding: dict[_ForwardingKey, _ForwardingBinding]
    zone_edges: dict[str, tuple[str, NodeId]]
    resources: set[OperationalConnectionId]
    route_outcomes_changed: list[RouteDemand]
    changed_access_ground_stations: list[NodeId]
    changed_zones: list[str]
    prior_zone_edges: dict[str, tuple[str, NodeId]]


@dataclass(frozen=True, slots=True)
class _TrafficBoundaryDelta:
    """Retain only Traffic-relevant facts while rich values are still live."""

    nominal_time: datetime
    route_transitions: tuple[RouteOutcomeTransition, ...]
    forwarding_intents: tuple[_ForwardingIntent, ...]
    established_connection_ids: tuple[OperationalConnectionId, ...]
    withdrawn_connection_ids: tuple[OperationalConnectionId, ...]
    zone_transitions: tuple[ZoneServiceTransition, ...]
    changed_access_ground_stations: tuple[NodeId, ...]


@dataclass(slots=True)
class _PairSeed:
    direction: str
    ground_station: NodeId
    service_key: tuple[str, str]
    reason_times: dict[str, set[datetime]]
    geographic_separation_m: float | None = None
    geographic_separation_class: str = "unavailable"

    @property
    def key(self) -> tuple[str, str, tuple[str, str]]:
        return self.direction, self.ground_station.value, self.service_key


_EpisodeRequest = tuple[_PairSeed, str, datetime, datetime, datetime]


class _MissingPlannedRouteOutcome(ValueError):
    """Signal that one candidate cannot support its requested probe horizon."""


class TrafficProbePlanBuilder:
    """Select bounded probes from recorded Route and compilation deltas.

    Finite and retained callers share append/flush; flush retains only end state.
    """

    def __init__(
        self,
        *,
        maximum_persistent_probes: int = 12,
        maximum_activation_window_probes: int = 8,
        persistent_interval_seconds: float = 1.0,
        activation_interval_seconds: float = 0.5,
        physical_world: PhysicalWorld | None = None,
        maximum_concurrent_probes: int = 8,
        maximum_echo_requests: int = 3600,
    ) -> None:
        for label, value in (
            ("maximum_persistent_probes", maximum_persistent_probes),
            ("maximum_activation_window_probes", maximum_activation_window_probes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative int")
        for label, value in (
            ("persistent_interval_seconds", persistent_interval_seconds),
            ("activation_interval_seconds", activation_interval_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{label} must be positive")
        if maximum_persistent_probes + maximum_activation_window_probes <= 0:
            raise ValueError("Traffic probe selection requires a positive bound")
        if physical_world is not None and not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        for label, limit in (("maximum_concurrent_probes", maximum_concurrent_probes), ("maximum_echo_requests", maximum_echo_requests)):
            _positive_int(limit, label)
        self._node_shells = {} if physical_world is None else {
            satellite.node_id: shell.shell_id for autonomous in physical_world.autonomous_systems
            for shell in autonomous.shells for orbit in shell.orbits for satellite in orbit.satellites}
        # Backend arrives with the first compiled result. Avoid native-only
        # geography queries/copies for BPF, then release this temporary reference.
        self._geography_world = physical_world
        self._ground_coordinates = {}
        self._zone_coordinates = {}
        self._global_budget = (maximum_persistent_probes + maximum_activation_window_probes, maximum_concurrent_probes, maximum_echo_requests)
        self._maximum_persistent = maximum_persistent_probes
        self._maximum_windowed = maximum_activation_window_probes
        self._persistent_interval = float(persistent_interval_seconds)
        self._activation_interval = float(activation_interval_seconds)
        self._deltas: list[_TrafficBoundaryDelta] = []
        self._zone_prefixes: tuple[tuple[str, str], ...] | None = None
        self._physical_projection: object | None = None
        self._previous_lineage: tuple[str, str, datetime, str] | None = None
        self._coverage_start: datetime | None = None
        self._coverage_end: datetime | None = None
        self._realization_count = 0
        self._finalized = False
        self._work_seconds = 0.0
        self._initial_projection = None
        self._observation_interval = None
        self._forwarding_backend = None

    def build(self, plans: Sequence[Plan], realizations: Sequence[CompilationResult]) -> TrafficProbePlan:
        """Feed a finite aligned sequence through append and finalize."""
        if self._previous_lineage is not None or self._finalized:
            raise RuntimeError("Traffic build requires an unused builder")
        if len(plans) != len(realizations):
            raise ValueError("Traffic probe planning requires aligned authoritative Plans")
        for plan, realization in zip(plans, realizations, strict=True):
            self.append(plan, realization)
        return self.finalize()

    def seal_batch(self, output, *, index, end_time, final):
        """Publish one delivery without finalizing retained computation."""
        path = Path(output) / ("results/traffic/probe-plan.json" if index == 0 else f"results/traffic/probe-plan-{index:03d}.json")
        write_traffic_probe_plan(self.flush(end_time=end_time), path)
        return path

    def append(self, plan: Plan, realization: CompilationResult) -> None:
        """Retain one exact Traffic-only delta sequence from a live pair."""

        from time import monotonic

        started = monotonic()

        if self._finalized:
            raise RuntimeError("incremental Traffic probe builder is finalized")
        if not isinstance(plan, Plan):
            raise TypeError("incremental Traffic probe builder requires a Plan")
        if not isinstance(realization, CompilationResult):
            raise TypeError(
                "incremental Traffic probe builder requires a "
                "CompilationResult"
            )
        if (
            plan.plan_id != realization.source_plan_id
            or plan.previous_plan_id != realization.source_previous_plan_id
            or plan.physical_world_fingerprint
            != realization.physical_world_fingerprint
            or plan.input_state_fingerprint
            != realization.input_state_fingerprint
            or plan.output_state_fingerprint
            != realization.output_state_fingerprint
            or plan.observation_interval != realization.observation_interval
            or plan.start_timestamp != realization.start_timestamp
            or plan.end_timestamp != realization.end_timestamp
            or tuple(value.timestamp for value in plan.transitions)
            != realization.activation_timestamps
        ):
            raise ValueError(
                "incremental Traffic Plan/realization lineage is misaligned"
            )
        if self._forwarding_backend is None:
            self._forwarding_backend = realization.forwarding_backend
            world = self._geography_world
            if self._forwarding_backend == 'linux' and world is not None:
                self._ground_coordinates = {
                    gs.node_id: (gs.latitude_deg, gs.longitude_deg)
                    for autonomous in world.autonomous_systems for gs in autonomous.ground_stations}
                try:
                    self._zone_coordinates = {z.zone_id: z.representative_point for z in world.get_zone_layout().zones}
                except RuntimeError:  # Zones explicitly disabled.
                    pass
            self._geography_world = None
        elif realization.forwarding_backend != self._forwarding_backend:
            raise ValueError("Traffic backend changed within one computation")
        previous = self._previous_lineage
        if previous is None:
            if (
                realization.source_previous_plan_id is not None
                or realization.input_state_fingerprint is not None
            ):
                raise ValueError(
                    "incremental Traffic sequence requires a bootstrap pair"
                )
            projection = realization._physical_world_projection
            self._observation_interval = realization.observation_interval
            self._physical_projection = projection
            self._coverage_start = realization.start_timestamp
        else:
            previous_plan_id, previous_output, previous_end, world = previous
            if (
                realization.source_previous_plan_id != previous_plan_id
                or realization.input_state_fingerprint != previous_output
                or realization.physical_world_fingerprint != world
                or realization._physical_world_projection
                != self._physical_projection
                or realization.observation_interval != self._observation_interval
                or realization.start_timestamp != previous_end + self._observation_interval
            ):
                raise ValueError(
                    "incremental Traffic realization lineage is not continuous"
                )
        if self._zone_prefixes is None:
            self._zone_prefixes = realization.zone_prefixes
        elif realization.zone_prefixes != self._zone_prefixes:
            raise ValueError("incremental Traffic Zone prefixes changed")

        forwarding_by_time: dict[datetime, list[_ForwardingIntent]] = defaultdict(
            list
        )
        for intent in realization.forwarding_realizations:
            forwarding_by_time[intent.nominal_time].append(intent)
        boundary_times = set(realization.activation_timestamps)
        if set(forwarding_by_time) - boundary_times:
            raise ValueError(
                "incremental Traffic forwarding lies outside activation coverage"
            )
        for transition, boundary in zip(
            plan.transitions,
            realization.compiled_activation_boundaries,
            strict=True,
        ):
            if plan_transition_fingerprint(transition) != boundary.transition_fingerprint:
                raise ValueError(
                    "incremental Traffic transition differs from Compiler provenance"
                )
            changed_access: list[NodeId] = []
            changed_access_seen: set[NodeId] = set()
            connection_ids = tuple(
                value.connection_id for value in transition.established_connections
            ) + transition.withdrawn_connection_ids
            for connection_id in connection_ids:
                if (
                    connection_id.kind
                    is OperationalConnectionKind.GROUND_STATION_ACCESS
                ):
                    for node_id in (
                        connection_id.endpoint_a,
                        connection_id.endpoint_b,
                    ):
                        if (
                            node_id.entity_kind == "ground-station"
                            and node_id not in changed_access_seen
                        ):
                            changed_access.append(node_id)
                            changed_access_seen.add(node_id)
            self._deltas.append(
                _TrafficBoundaryDelta(
                    boundary.timestamp,
                    transition.route_outcome_transitions,
                    tuple(forwarding_by_time.get(boundary.timestamp, ())),
                    tuple(
                        value.connection_id
                        for value in transition.established_connections
                    ),
                    transition.withdrawn_connection_ids,
                    transition.zone_service_transitions,
                    tuple(changed_access),
                )
            )
        self._previous_lineage = (
            realization.source_plan_id,
            realization.output_state_fingerprint,
            realization.end_timestamp,
            realization.physical_world_fingerprint,
        )
        self._coverage_end = realization.end_timestamp
        self._realization_count += 1
        self._work_seconds += monotonic() - started

    def finalize(self) -> TrafficProbePlan:
        """Select and reconstruct the exact bounded probe workload."""

        result = self.flush()
        self._finalized = True
        return result

    def flush(self, *, end_time: datetime | None = None) -> TrafficProbePlan:
        """Seal this delivery's Traffic inputs, retaining only its final replay state."""

        from time import monotonic

        started = monotonic()

        if self._finalized:
            raise RuntimeError("incremental Traffic probe builder is finalized")
        deltas = tuple(self._deltas)
        if (
            not deltas
            or self._coverage_start is None
            or self._coverage_end is None
            or self._zone_prefixes is None
            or self._physical_projection is None
        ):
            raise ValueError("incremental Traffic probe planning requires input")
        nominal_boundaries = tuple(value.nominal_time for value in deltas)
        coverage_start = deltas[0].nominal_time
        coverage_end = self._coverage_end if end_time is None else end_time
        if coverage_end < self._coverage_end:
            raise ValueError("Traffic coverage cannot precede its last formal boundary")
        zone_ids = self._physical_projection.service_keys
        prefixes = _zone_ipv4_from(self._zone_prefixes, zone_ids)
        seeds = _candidate_pairs_from_deltas(
            deltas, self._zone_prefixes, nominal_boundaries, node_shells=self._node_shells,
            persistent_interval=self._persistent_interval,
            initial_projection=self._initial_projection, native=self._forwarding_backend == "linux",
            ground_coordinates=self._ground_coordinates, zone_coordinates=self._zone_coordinates,
        )
        select_episodes = _select_native_episode_requests if self._forwarding_backend == "linux" else _select_episode_requests
        episode_requests = select_episodes(
            seeds,
            maximum_background=self._maximum_persistent,
            maximum_events=self._maximum_windowed,
            global_budget=self._global_budget,
            intervals=(self._persistent_interval, self._activation_interval),
            nominal_boundaries=nominal_boundaries,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        selected_seeds = {value[0].key: value[0] for value in episode_requests}
        final_projections = []
        intervals_by_key, affected_by_key = (
            _reconstruct_selected_paths_from_deltas(
                deltas,
                self._zone_prefixes,
                tuple(selected_seeds[key] for key in sorted(selected_seeds)),
                episode_requests=episode_requests,
                nominal_boundaries=nominal_boundaries,
                coverage_end=coverage_end,
                initial_projection=self._initial_projection,
                final_projection=final_projections.append,
                native=self._forwarding_backend == "linux",
            )
        )
        viable_episode_requests = tuple(
            request
            for request in episode_requests
            if any(
                interval.expected_reachable is not False
                for interval in intervals_by_key[request[0].key]
                if interval.end_time > request[3]
                and interval.start_time < request[4]
            )
        )
        viable_keys = {request[0].key for request in viable_episode_requests}
        identities = {
            key: _make_identity(
                selected_seeds[key],
                intervals_by_key[key],
                zone_ipv4=prefixes[selected_seeds[key].service_key],
            )
            for key in sorted(viable_keys)
        }
        episodes = []
        for seed, mode, anchor, start, end in viable_episode_requests:
            episodes.append(
                _make_episode(
                    seed,
                    intervals_by_key[seed.key],
                    identity=identities[seed.key],
                    mode=mode,
                    start=start,
                    end=end,
                    interval_seconds=(
                        self._activation_interval
                        if mode == _ACTIVATION_WINDOW
                        else self._persistent_interval
                    ),
                    episode_anchor=anchor,
                    affected_times=tuple(sorted(t for t in affected_by_key[seed.key] if start <= t < end)),
                )
            )
        ordered_identities = tuple(
            identities[key] for key in sorted(identities)
        )
        ordered_episodes = tuple(replace(e, estimated_telemetry_events=0)
                                 if self._forwarding_backend == "linux" else e for e in episodes)
        result = TrafficProbePlan(
            coverage_start,
            coverage_end,
            ordered_identities,
            ordered_episodes,
            nominal_boundaries,
            self._realization_count,
            _traffic_plan_revision_id(
                coverage_start,
                coverage_end,
                nominal_boundaries,
                ordered_identities,
                ordered_episodes,
            ),
            len(seeds),
            self._work_seconds + monotonic() - started,
            tuple(sorted((n.value, shell) for n, shell in self._node_shells.items())), self._global_budget, _shell_eligibility(seeds), self._forwarding_backend,
        )

        self._initial_projection = final_projections[0]
        self._deltas.clear()
        self._work_seconds = 0.0
        return result

    def snapshot(self) -> dict[str, int | bool]:
        """Report bounded retained work for timing/review evidence."""

        return {
            "realization_count": self._realization_count,
            "boundary_delta_count": len(self._deltas),
            "forwarding_delta_count": sum(
                len(value.forwarding_intents) for value in self._deltas
            ),
            "finalized": self._finalized,
        }


def write_traffic_probe_plan(plan: TrafficProbePlan, path: Path) -> Path:
    if not isinstance(plan, TrafficProbePlan):
        raise TypeError("plan must be a TrafficProbePlan")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan.artifact(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_traffic_probe_plan(path: Path) -> TrafficProbePlan:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Traffic probe plan artifact is invalid") from error
    return TrafficProbePlan.from_artifact(_mapping(value, "Traffic probe plan"))


def _scan_boundary_deltas(
    deltas: tuple[_TrafficBoundaryDelta, ...],
    zone_prefixes: tuple[tuple[str, str], ...],
    visitor: Callable[[_BoundaryProjection], None],
    *, initial_projection=None, final_projection=None,
) -> tuple[datetime, ...]:
    """Replay the compact Traffic-only event log into exact boundary views."""

    prefixes = dict(zone_prefixes)
    route_outcomes: dict[
        RouteDemand, ReachableRouteOutcome | UnreachableRouteOutcome | LocalRouteOutcome
    ] = {}
    forwarding: dict[_ForwardingKey, _ForwardingBinding] = {}
    assignments: dict[str, NodeId | None] = {}
    resources: set[OperationalConnectionId] = set()
    if initial_projection is not None:
        route_outcomes.update(initial_projection.route_outcomes)
        forwarding.update(initial_projection.forwarding)
        assignments.update({key: edge[1] for key, edge in initial_projection.zone_edges.items()})
        resources.update(initial_projection.resources)
    visited: list[datetime] = []
    for delta in deltas:
        prior_zone_edges = {
            zone_id: (prefixes[zone_id], satellite_id)
            for zone_id, satellite_id in assignments.items()
            if satellite_id is not None
        }
        for transition in delta.route_transitions:
            prior = route_outcomes.get(transition.demand)
            if prior != transition.previous:
                raise ValueError(
                    "Traffic compact Route predecessor is stale"
                )
            if transition.current is None:
                route_outcomes.pop(transition.demand, None)
            else:
                route_outcomes[transition.demand] = transition.current
        for transition in delta.zone_transitions:
            prior = assignments.get(transition.service_key)
            if prior != transition.previous_satellite_id:
                raise ValueError(
                    "Traffic compact Zone predecessor is stale"
                )
            assignments[transition.service_key] = transition.current_satellite_id
        for connection_id in delta.established_connection_ids:
            if connection_id in resources:
                raise ValueError("Traffic compact resource is duplicated")
            resources.add(connection_id)
        for connection_id in delta.withdrawn_connection_ids:
            if connection_id not in resources:
                raise ValueError("Traffic compact resource predecessor is absent")
            resources.remove(connection_id)
        for intent in delta.forwarding_intents:
            prior = forwarding.get(intent.route_key)
            if prior != intent.previous:
                raise ValueError(
                    "Traffic compact forwarding predecessor is stale"
                )
            if intent.operation is _MutationOperation.REMOVE:
                if prior is None or intent.current is not None:
                    raise ValueError(
                        "Traffic compact forwarding removal is invalid"
                    )
                del forwarding[intent.route_key]
            else:
                if intent.current is None:
                    raise ValueError(
                        "Traffic compact forwarding update is incomplete"
                    )
                forwarding[intent.route_key] = intent.current
        zone_edges = {
            zone_id: (prefixes[zone_id], satellite_id)
            for zone_id, satellite_id in assignments.items()
            if satellite_id is not None
        }
        projection = _BoundaryProjection(
                delta.nominal_time,
                route_outcomes,
                forwarding,
                zone_edges,
                resources,
                (list(route_outcomes) if not visited and initial_projection is not None else
                 [value.demand for value in delta.route_transitions]),
                list(delta.changed_access_ground_stations),
                (list(zone_edges) if not visited and initial_projection is not None else
                 [value.service_key for value in delta.zone_transitions]),
                prior_zone_edges,
        )
        visitor(projection)
        visited.append(delta.nominal_time)
    if final_projection is not None:
        final_projection(projection)
    if len(visited) != len(set(visited)) or visited != sorted(visited):
        raise ValueError("Traffic compact boundaries are not strictly ordered")
    return tuple(visited)


def _zone_ipv4_from(
    zone_prefixes: tuple[tuple[str, str], ...],
    zone_ids: tuple[str, ...],
) -> dict[str, str]:
    prefixes = dict(zone_prefixes)
    if set(prefixes) != set(zone_ids):
        raise ValueError(
            "Traffic probe Zone prefix coverage differs from PhysicalWorld"
        )
    result = {}
    for zone_id, prefix in prefixes.items():
        interface = ip_interface(prefix)
        if not isinstance(interface, IPv4Interface) or interface.network.prefixlen != 32:
            raise ValueError("Traffic probe Zone identity must be an IPv4 /32")
        result[zone_id] = str(interface.ip)
    return result


class _StratifiedCandidateReservoir:
    """Keep tiny deterministic samples per direction/reason/time stratum."""

    def __init__(
        self, *, bucket_capacity: int = _CANDIDATE_BUCKET_CAPACITY
    ) -> None:
        self._capacity = bucket_capacity
        self._buckets: dict[
            tuple[str, str, int],
            dict[tuple[str, str, str], tuple[_PairSeed, str]],
        ] = defaultdict(dict)

    def add(
        self,
        direction: str,
        ground: NodeId,
        zone_id: str,
        reason: str,
        nominal_time: datetime,
        time_stratum: int,
    ) -> None:
        if ground.entity_kind != "ground-station":
            raise ValueError("Traffic candidate GroundStation identity is invalid")
        seed = _PairSeed(direction, ground, zone_id, defaultdict(set))
        seed.reason_times[reason].add(nominal_time)
        bucket = self._buckets[(direction, reason, time_stratum)]
        existing = bucket.get(seed.key)
        if existing is not None:
            for label, times in seed.reason_times.items():
                existing[0].reason_times[label].update(times)
            return
        priority = sha256(
            json.dumps((*seed.key, reason, str(time_stratum)), separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if len(bucket) < self._capacity:
            bucket[seed.key] = seed, priority
            return
        worst_key, (_, worst_priority) = max(
            bucket.items(), key=lambda item: (item[1][1], item[0])
        )
        if (priority, seed.key) < (worst_priority, worst_key):
            del bucket[worst_key]
            bucket[seed.key] = seed, priority

    def values(self) -> tuple[_PairSeed, ...]:
        merged: dict[tuple[str, str, str], _PairSeed] = {}
        for bucket in self._buckets.values():
            for seed, _priority in bucket.values():
                current = merged.get(seed.key)
                if current is None:
                    current = _PairSeed(
                        seed.direction,
                        seed.ground_station,
                        seed.service_key,
                        defaultdict(set),
                    )
                    merged[seed.key] = current
                for label, times in seed.reason_times.items():
                    current.reason_times[label].update(times)
        return tuple(
            sorted(
                merged.values(),
                key=lambda value: (
                    min(
                        (
                            timestamp
                            for times in value.reason_times.values()
                            for timestamp in times
                        )
                    ),
                    value.key,
                ),
            )
        )


def _cyclic_values(
    values: Sequence[Any],
    offset: int,
    *,
    maximum: int | None = None,
) -> Iterable[Any]:
    """Visit a bounded rotating prefix without ranking the supplied facts."""

    if not values:
        return
    count = len(values) if maximum is None else min(len(values), maximum)
    start = offset % len(values)
    for index in range(count):
        yield values[(start + index) % len(values)]


def _candidate_pairs_from_deltas(
    deltas: tuple[_TrafficBoundaryDelta, ...],
    zone_prefixes: tuple[tuple[str, str], ...],
    nominal_boundaries: tuple[datetime, ...],
    *, node_shells=None, persistent_interval=1.0, initial_projection=None, native=False,
    ground_coordinates=None, zone_coordinates=None,
) -> tuple[_PairSeed, ...]:
    candidate_scan = _native_candidates_from_scan if native else _candidate_pairs_from_scan
    return candidate_scan(
        lambda visitor: _scan_boundary_deltas(
            deltas, zone_prefixes, visitor, initial_projection=initial_projection
        ),
        nominal_boundaries, node_shells=node_shells, persistent_interval=persistent_interval,
        **({'ground_coordinates': ground_coordinates, 'zone_coordinates': zone_coordinates} if native else {}),
    )


def _geographic_separation_m(first, second):
    """Great-circle endpoint separation; neither route length nor latency."""
    lat_a, lon_a, lat_b, lon_b = map(radians, (*first, *second))
    haversine = sin((lat_b - lat_a) / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin((lon_b - lon_a) / 2) ** 2
    return 2 * 6_371_008.8 * asin(sqrt(min(1.0, max(0.0, haversine))))


def _native_candidates_from_scan(scan, nominal_boundaries, *, ground_coordinates=None,
                                 zone_coordinates=None, **_unused):
    """Retain bounded distance extremes only from independently arranged coverage."""
    ground_coordinates, zone_coordinates = ground_coordinates or {}, zone_coordinates or {}
    near, far = {}, {}
    # Only actually encountered coverage, scoped to this delivery's scan.
    distances = {}
    fallback = _StratifiedCandidateReservoir(bucket_capacity=_NATIVE_CANDIDATE_BUCKET_CAPACITY)
    def visit(projection):
        ground_edges = _native_ground_edges(projection)
        # Service can recover without an uplink change, so consider current
        # arrangements at every boundary, not only route-outcome deltas.
        for demand, outcome in projection.route_outcomes.items():
            if (demand.source.entity_kind != "ground-station"
                    or not isinstance(demand.target, ZoneRouteDemandTarget)
                    or not isinstance(outcome, (LocalRouteOutcome, ReachableRouteOutcome))):
                continue
            seed = _PairSeed(_DIRECTION_GROUND_TO_ZONE, demand.source, demand.target.service_key, {})
            if _resolve_native_path(seed, projection, ground_edges=ground_edges)[0] is False:
                continue
            first, second = ground_coordinates.get(demand.source), zone_coordinates.get(demand.target.zone_id)
            if first is None or second is None:
                for direction in (_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND):
                    fallback.add(direction, demand.source, demand.target.service_key,
                                 "persistent-control-candidate", nominal_boundaries[0], 0)
                continue
            key = (demand.source, demand.target.service_key)
            if key not in distances:
                distances[key] = _geographic_separation_m(first, second)
            distance = distances[key]
            # No all-pairs table or full sort: retain at most eight per extreme.
            for bucket, sign in ((near, 1), (far, -1)):
                bucket[key] = distance
                if len(bucket) > _NATIVE_CANDIDATE_BUCKET_CAPACITY:
                    worst = max(bucket, key=lambda k: (sign * bucket[k], k[0].value, k[1]))
                    del bucket[worst]
    if scan(visit) != nominal_boundaries:
        raise ValueError("Traffic native candidate scan omitted a boundary")
    if not near:
        values = fallback.values()
        return _take_direction_coverage(values, maximum=len(values))
    low = sorted(near, key=lambda k: (near[k], k[0].value, k[1]))
    high = sorted(far, key=lambda k: (-far[k], k[0].value, k[1]))
    distances = {**near, **far}
    seeds, seen = [], set()
    for low_key, high_key in zip(low, high):
        for key, label in ((low_key, 'near'), (high_key, 'far')):
            if key in seen:
                continue
            seen.add(key)
            if len(distances) == 1:
                label = 'only-candidate'
            elif near[low[0]] == far[high[0]]:
                label = 'equal'
            for direction in (_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND):
                seeds.append(_PairSeed(direction, *key,
                    {'persistent-control-candidate': {nominal_boundaries[0]}}, distances[key], label))
    return tuple(seeds)


def _select_native_episode_requests(seeds, *, maximum_background, maximum_events,
        global_budget, intervals, nominal_boundaries, coverage_start, coverage_end):
    # Each bounded business identity stays armed across withdrawals and service
    # moves. The executor resolves actual source nodes only at completed ACTIVE.
    selected = seeds[:maximum_background]
    requests = [(seed, _PERSISTENT, coverage_start, coverage_start, coverage_end) for seed in selected]
    return _budgeted_requests(seeds, requests, coverage_start, coverage_end, global_budget, intervals)


def _native_ground_edges(projection):
    return {key.target for key, binding in projection.forwarding.items()
            if isinstance(key.target, GroundStationRouteDemandTarget)
            and binding.connection_id is not None
            and binding.connection_id.kind is OperationalConnectionKind.GROUND_STATION_ACCESS
            and binding.next_hop == key.target.ground_station_id
            and binding.connection_id in projection.resources}


def _resolve_native_path(seed, projection, *, ground_edges=None):
    edge = projection.zone_edges.get(seed.service_key)
    service = None if edge is None else edge[1]
    if service is None:
        return False, (), None, "zone-unserved"
    target = ZoneRouteDemandTarget(seed.service_key[1], seed.service_key[0])
    forward = projection.route_outcomes.get(RouteDemand(seed.ground_station, target))
    reverse = projection.route_outcomes.get(RouteDemand(service,
        GroundStationRouteDemandTarget(seed.ground_station, seed.service_key[0])))
    if isinstance(forward, ReachableRouteOutcome) and isinstance(reverse, ReachableRouteOutcome):
        return _resolve_path(seed, projection)
    binding = projection.forwarding.get(_ForwardingKey(seed.ground_station, target))
    if not isinstance(forward, (LocalRouteOutcome, ReachableRouteOutcome)) or binding is None or binding.connection_id not in projection.resources:
        return False, (), service, "gs-uplink-unarranged"
    ground_target = GroundStationRouteDemandTarget(seed.ground_station, seed.service_key[0])
    if ground_edges is None:
        ground_edges = _native_ground_edges(projection)
    if ground_target not in ground_edges:
        return False, (), service, "gs-edge-unarranged"
    # Both local edge arrangements exist. Nothing here proves OSPF transit.
    return None, (), service, None


def _candidate_pairs_from_scan(
    scan: Callable[
        [Callable[[_BoundaryProjection], None]], tuple[datetime, ...]
    ],
    nominal_boundaries: tuple[datetime, ...],
    *, node_shells=None, persistent_interval=1.0,
) -> tuple[_PairSeed, ...]:
    reservoir = _StratifiedCandidateReservoir()
    eligible_scopes = {}
    initial_pairs = set()
    changed_pairs = set()
    eligible_by_scope = defaultdict(set)
    eligible_grounds_by_zone = defaultdict(set)
    node_shells = {} if node_shells is None else node_shells
    ground_stations_by_zone: dict[str, set[NodeId]] = defaultdict(set)
    ground_targets_by_satellite: dict[NodeId, set[NodeId]] = defaultdict(set)
    zones_by_satellite: dict[NodeId, set[str]] = defaultdict(set)
    boundary_index = {
        value: index for index, value in enumerate(nominal_boundaries)
    }
    sample_indexes = {
        0,
        len(nominal_boundaries) // 4,
        len(nominal_boundaries) // 2,
        len(nominal_boundaries) * 3 // 4,
        len(nominal_boundaries) - 1,
    }

    def visit(projection: _BoundaryProjection) -> None:
        index = boundary_index[projection.nominal_time]
        stratum = min(3, index * 4 // len(nominal_boundaries))
        quota: dict[tuple[str, str], int] = defaultdict(int)

        for demand in projection.route_outcomes_changed:
            target = demand.target
            present = isinstance(
                projection.route_outcomes.get(demand), ReachableRouteOutcome
            )
            if demand.source.entity_kind == "ground-station" and isinstance(
                target, ZoneRouteDemandTarget
            ):
                update = (
                    ground_stations_by_zone[target.service_key].add
                    if present
                    else ground_stations_by_zone[target.service_key].discard
                )
                update(demand.source)
            elif isinstance(target, GroundStationRouteDemandTarget):
                update = (
                    ground_targets_by_satellite[demand.source].add
                    if present
                    else ground_targets_by_satellite[demand.source].discard
                )
                update(target.ground_station_id)
        for zone_id in projection.changed_zones:
            prior = projection.prior_zone_edges.get(zone_id)
            current = projection.zone_edges.get(zone_id)
            if prior is not None:
                zones_by_satellite[prior[1]].discard(zone_id)
            if current is not None:
                zones_by_satellite[current[1]].add(zone_id)

        if node_shells:
            # Route/Zone deltas identify the affected endpoint pairs. Eligibility
            # uses authoritative paths; compiled path verification is deferred
            # to reconstruction of the bounded selected workload.
            affected = set()
            for demand in projection.route_outcomes_changed:
                if demand.source.entity_kind == "ground-station" and isinstance(demand.target, ZoneRouteDemandTarget):
                    affected.add((demand.source, demand.target.service_key))
                elif isinstance(demand.target, GroundStationRouteDemandTarget):
                    affected.update((demand.target.ground_station_id, zone)
                                    for zone in zones_by_satellite.get(demand.source, ()))
            for zone in projection.changed_zones:
                grounds = set(ground_stations_by_zone.get(zone, ()))
                for edge in (projection.prior_zone_edges.get(zone), projection.zone_edges.get(zone)):
                    if edge is not None:
                        grounds.update(ground_targets_by_satellite.get(edge[1], ()))
                grounds.update(eligible_grounds_by_zone.get(zone, ()))
                affected.update((ground, zone) for ground in grounds)
            if index == 0:
                initial_pairs.update(affected)
            else:
                changed_pairs.update(affected)
            for ground, zone in sorted(affected, key=lambda p: (p[0].value, p[1])):
                key = ground, zone
                for scope in eligible_scopes.pop(key, ()):
                    eligible_by_scope[scope].discard(key)
                eligible_grounds_by_zone[zone].discard(ground)
                edge = projection.zone_edges.get(zone)
                if edge is None:
                    continue
                forward = projection.route_outcomes.get(RouteDemand(ground, ZoneRouteDemandTarget(zone[1], zone[0])))
                reverse = projection.route_outcomes.get(RouteDemand(edge[1], GroundStationRouteDemandTarget(ground, zone[0])))
                if not isinstance(forward, ReachableRouteOutcome) or not isinstance(reverse, ReachableRouteOutcome):
                    continue
                scopes = set()
                for direction, outcome in ((_DIRECTION_GROUND_TO_ZONE, forward), (_DIRECTION_ZONE_TO_GROUND, reverse)):
                    for shell in {node_shells[n] for n in outcome.path if n in node_shells}:
                        scope = shell, direction
                        scopes.add(scope)
                        eligible_by_scope[scope].add(key)
                        if index > 0:
                            reservoir.add(direction, ground, zone, f"shell-coverage:{shell}:transition", projection.nominal_time, stratum)
                eligible_scopes[key] = scopes
                eligible_grounds_by_zone[zone].add(ground)
            if index in sample_indexes:
                for (shell, direction), pairs in sorted(eligible_by_scope.items()):
                    # Only a bounded sample enters the time reservoir. No path
                    # resolutions occur at unchanged boundaries.
                    sampled = nsmallest(_CANDIDATE_BUCKET_CAPACITY, pairs,
                        key=lambda p: sha256(f"{_SAMPLING_SEED}|{p[0].value}|{p[1]}|{stratum}".encode()).hexdigest())
                    for ground, zone in sampled:
                        if (ground, zone) not in affected or index == 0:
                            reservoir.add(direction, ground, zone, f"shell-coverage:{shell}:stable", projection.nominal_time, stratum)

        def add(
            direction: str,
            ground: NodeId,
            zone_id: str,
            reason: str,
            category: str,
        ) -> bool:
            quota_key = direction, category
            if quota[quota_key] >= _BOUNDARY_CATEGORY_QUOTA:
                return False
            reservoir.add(
                direction,
                ground,
                zone_id,
                reason,
                projection.nominal_time,
                stratum,
            )
            quota[quota_key] += 1
            return True

        def full(direction: str, category: str) -> bool:
            return quota[(direction, category)] >= _BOUNDARY_CATEGORY_QUOTA

        if index in sample_indexes:
            background_reason = (
                "persistent-control-candidate"
                if index == 0
                else "rotating-background-candidate"
            )
            zone_ids = sorted(ground_stations_by_zone)
            for zone_id in _cyclic_values(zone_ids, index):
                grounds = sorted(
                    ground_stations_by_zone[zone_id],
                    key=lambda value: value.value,
                )
                for ground in _cyclic_values(grounds, index):
                    add(
                        _DIRECTION_GROUND_TO_ZONE,
                        ground,
                        zone_id,
                        background_reason,
                        "background",
                    )
                    if full(_DIRECTION_GROUND_TO_ZONE, "background"):
                        break
                if full(_DIRECTION_GROUND_TO_ZONE, "background"):
                    break
            satellites = sorted(
                zones_by_satellite,
                key=lambda value: value.value,
            )
            for satellite in _cyclic_values(satellites, index):
                zones = sorted(zones_by_satellite[satellite])
                grounds = sorted(
                    ground_targets_by_satellite.get(satellite, ()),
                    key=lambda value: value.value,
                )
                if not grounds:
                    continue
                for zone_offset, zone_id in enumerate(
                    _cyclic_values(zones, index)
                ):
                    add(
                        _DIRECTION_ZONE_TO_GROUND,
                        grounds[(index + zone_offset) % len(grounds)],
                        zone_id,
                        background_reason,
                        "background",
                    )
                    if full(_DIRECTION_ZONE_TO_GROUND, "background"):
                        break
                if full(_DIRECTION_ZONE_TO_GROUND, "background"):
                    break

        for demand in _cyclic_values(
            projection.route_outcomes_changed,
            index * 31,
            maximum=_BOUNDARY_FACT_INSPECTION_LIMIT,
        ):
            if full(
                _DIRECTION_GROUND_TO_ZONE, "forwarding"
            ) and full(_DIRECTION_ZONE_TO_GROUND, "forwarding"):
                break
            target = demand.target
            if demand.source.entity_kind == "ground-station" and isinstance(
                target, ZoneRouteDemandTarget
            ) and not full(_DIRECTION_GROUND_TO_ZONE, "forwarding"):
                add(
                    _DIRECTION_GROUND_TO_ZONE,
                    demand.source,
                    target.service_key,
                    "forwarding-next-hop-change",
                    "forwarding",
                )
            elif isinstance(target, GroundStationRouteDemandTarget) and not full(
                _DIRECTION_ZONE_TO_GROUND, "forwarding"
            ):
                zones = zones_by_satellite.get(demand.source, ())
                for zone_id in sorted(zones)[:2]:
                    add(
                        _DIRECTION_ZONE_TO_GROUND,
                        target.ground_station_id,
                        zone_id,
                        "forwarding-next-hop-change",
                        "forwarding",
                    )
                    if full(_DIRECTION_ZONE_TO_GROUND, "forwarding"):
                        break

        for zone_id in _cyclic_values(
            projection.changed_zones,
            index * 17,
            maximum=_BOUNDARY_FACT_INSPECTION_LIMIT,
        ):
            if full(_DIRECTION_GROUND_TO_ZONE, "zone") and full(
                _DIRECTION_ZONE_TO_GROUND, "zone"
            ):
                break
            old_edge = projection.prior_zone_edges.get(zone_id)
            new_edge = projection.zone_edges.get(zone_id)
            reason = (
                "zone-service-establishment"
                if old_edge is None and new_edge is not None
                else "zone-service-withdrawal"
                if old_edge is not None and new_edge is None
                else "zone-service-handover"
            )
            for ground in sorted(
                ground_stations_by_zone.get(zone_id, ()),
                key=lambda value: value.value,
            )[:2]:
                add(
                    _DIRECTION_GROUND_TO_ZONE,
                    ground,
                    zone_id,
                    reason,
                    "zone",
                )
                if full(_DIRECTION_GROUND_TO_ZONE, "zone"):
                    break
            services = {
                edge[1]
                for edge in (old_edge, new_edge)
                if edge is not None
            }
            reverse_grounds = sorted(
                {
                    ground
                    for service in services
                    for ground in ground_targets_by_satellite.get(
                        service, ()
                    )
                },
                key=lambda value: value.value,
            )
            for ground in reverse_grounds[:2]:
                add(
                    _DIRECTION_ZONE_TO_GROUND,
                    ground,
                    zone_id,
                    reason,
                    "zone",
                )
                if full(_DIRECTION_ZONE_TO_GROUND, "zone"):
                    break

    visited = scan(visit)
    if visited != nominal_boundaries:
        raise ValueError("Traffic candidate scan omitted a nominal boundary")
    seeds = list(reservoir.values())
    by_key = {seed.key: seed for seed in seeds}
    for case, pairs in (("stable", initial_pairs - changed_pairs), ("changing", initial_pairs & changed_pairs)):
        for ground, zone in nsmallest(1, pairs & set(eligible_scopes),
                key=lambda p: sha256(f"{_SAMPLING_SEED}|{p[0].value}|{p[1]}".encode()).hexdigest()):
            direction = _DIRECTION_GROUND_TO_ZONE if case == "stable" else _DIRECTION_ZONE_TO_GROUND
            seed = by_key.get((direction, ground.value, zone))
            if seed is None:
                seed = _PairSeed(direction, ground, zone, defaultdict(set))
                seeds.append(seed)
            seed.reason_times[f"long-{case}-candidate"].add(nominal_boundaries[0])
            seed.reason_times["persistent-control-candidate"].add(nominal_boundaries[0])
    if node_shells:
        _complete_control_coverage(scan, tuple(seeds), nominal_boundaries, node_shells,
                                   interval_seconds=persistent_interval)
    return tuple(seeds)


def _persistent_controls(seeds, maximum):
    representatives = tuple(value for case in ("stable", "changing") for value in seeds
                            if f"long-{case}-candidate" in value.reason_times)
    fallback = _take_direction_coverage(tuple(value for value in seeds
        if "persistent-control-candidate" in value.reason_times), maximum=maximum)
    keys = tuple(dict.fromkeys(value.key for value in (*representatives, *fallback)))[:maximum]
    by_key = {value.key: value for value in seeds}
    return tuple(by_key[key] for key in keys)


def _complete_control_coverage(scan, seeds, nominal_boundaries, node_shells, *, interval_seconds=1.0):
    """Recover eligible windows for at most two controls outside the reservoir.

    Representatives are known only after the horizon scan. A second delta scan
    checks their authoritative forward/return paths, without retaining histories
    for all eligible pairs or resolving unselected compiled paths.
    """
    controls = _persistent_controls(seeds, _MAX_PERSISTENT_CONTROLS)
    if not controls:
        return
    for seed in controls:
        for reason in tuple(seed.reason_times):
            if reason.startswith("shell-coverage:"):
                del seed.reason_times[reason]
    next_boundary = dict(zip(nominal_boundaries, nominal_boundaries[1:]))
    prior = {}
    origin = nominal_boundaries[0]
    transition_window = timedelta(seconds=max(3.0, 2 * interval_seconds))
    def first_request(at):
        return origin + timedelta(seconds=ceil((at-origin).total_seconds() / interval_seconds) * interval_seconds)

    def visit(projection):
        at = projection.nominal_time
        end = next_boundary.get(at, at)
        for seed in controls:
            edge = projection.zone_edges.get(seed.service_key)
            forward = projection.route_outcomes.get(RouteDemand(seed.ground_station, ZoneRouteDemandTarget(seed.service_key[1], seed.service_key[0])))
            reverse = None if edge is None else projection.route_outcomes.get(
                RouteDemand(edge[1], GroundStationRouteDemandTarget(seed.ground_station, seed.service_key[0])))
            reachable = isinstance(forward, ReachableRouteOutcome) and isinstance(reverse, ReachableRouteOutcome)
            state = (forward, reverse, edge)
            changed = (seed.key in prior and prior[seed.key] != state
                       or seed.ground_station in projection.changed_access_ground_stations)
            prior[seed.key] = state
            if not reachable or end <= at:
                continue
            outcome = forward if seed.direction == _DIRECTION_GROUND_TO_ZONE else reverse
            shells = {node_shells[n] for n in outcome.path if n in node_shells}
            for shell in shells:
                if changed and at > origin and first_request(at) < min(end, at + transition_window):
                    seed.reason_times.setdefault(f"shell-coverage:{shell}:transition", set()).add(at)
                # A reachable interval can support stable observations after its
                # short transition window; B0 itself is never transition credit.
                stable_at = first_request(at + transition_window if changed else at)
                if stable_at < end:
                    seed.reason_times.setdefault(f"shell-coverage:{shell}:stable", set()).add(stable_at)

    if scan(visit) != nominal_boundaries:
        raise ValueError("Traffic control eligibility scan omitted a nominal boundary")


def _select_episode_requests(
    seeds: tuple[_PairSeed, ...],
    *,
    maximum_background: int,
    maximum_events: int,
    global_budget: tuple[int, int, int] = (20, 8, 3600),
    intervals: tuple[float, float] = (1.0, 0.5),
    nominal_boundaries: tuple[datetime, ...],
    coverage_start: datetime,
    coverage_end: datetime,
) -> tuple[_EpisodeRequest, ...]:
    """Apply cheap direction/time quotas before selected-path reconstruction."""

    control_limit = min(maximum_background, _MAX_PERSISTENT_CONTROLS)
    controls = _persistent_controls(seeds, control_limit)
    result = [
        (
            value,
            _PERSISTENT,
            coverage_start,
            coverage_start,
            coverage_end,
        )
        for value in controls
    ]

    rotating_observations = tuple(
        (value.direction, timestamp, "background", value)
        for value in seeds
        for timestamp in value.reason_times.get(
            "rotating-background-candidate", ()
        )
    )
    rotating_limit = max(0, maximum_background - len(controls))
    for _direction, timestamp, _label, seed in _take_stratified_observations(
        rotating_observations,
        maximum=rotating_limit,
        nominal_boundaries=nominal_boundaries,
    ):
        start, end = _bounded_episode_window(
            timestamp,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            before_seconds=1,
            after_seconds=4,
        )
        if end > start:
            result.append(
                (
                    seed,
                    _ROTATING_BACKGROUND,
                    timestamp,
                    start,
                    end,
                )
            )

    background_labels = {
        "persistent-control-candidate",
        "rotating-background-candidate",
    }
    event_observations = tuple(
        (value.direction, timestamp, reason, value)
        for value in seeds
        for reason, timestamps in value.reason_times.items()
        if reason not in background_labels and not reason.startswith("shell-coverage:")
        for timestamp in timestamps
    )
    for _direction, timestamp, _label, seed in _take_stratified_observations(
        event_observations,
        maximum=maximum_events,
        nominal_boundaries=nominal_boundaries,
    ):
        start, end = _bounded_episode_window(
            timestamp,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            before_seconds=2,
            after_seconds=3,
        )
        if end > start:
            result.append(
                (
                    seed,
                    _ACTIVATION_WINDOW,
                    timestamp,
                    start,
                    end,
                )
            )

    return _budgeted_requests(seeds, result, coverage_start, coverage_end, global_budget, intervals)


def _budgeted_requests(seeds, diversity, start, end, budget, intervals):
    maximum, concurrent, echoes = budget
    selected = []
    used = set()
    def identity(request):
        return request[0].key, request[1], request[2]
    def fits(request):
        if identity(request) in used or len(selected) >= maximum:
            return False
        proposals = [*selected, request]
        events = sorted((at, delta) for r in proposals for at, delta in ((r[3], 1), (r[4], -1)))
        current = peak = 0
        for _, delta in events:
            current += delta
            peak = max(peak, current)
        count = sum(ceil((r[4]-r[3]).total_seconds() / intervals[r[1] == _ACTIVATION_WINDOW]) for r in proposals)
        return peak <= concurrent and count <= echoes
    # Reserve the two long controls before short-flow stratified coverage.
    for request in (r for r in diversity if r[1] == _PERSISTENT):
        if fits(request):
            selected.append(request)
            used.add(identity(request))
    candidates = {}
    for seed in seeds:
        for reason, times in seed.reason_times.items():
            if not reason.startswith("shell-coverage:"):
                continue
            shell, case = reason.removeprefix("shell-coverage:").rsplit(":", 1)
            for anchor in sorted(times):
                if anchor >= end:
                    continue
                begin, finish = _bounded_episode_window(anchor, coverage_start=start, coverage_end=end,
                                                        before_seconds=1 if case == "stable" else 2,
                                                        after_seconds=4 if case == "stable" else 3)
                request = (seed, _ROTATING_BACKGROUND if case == "stable" else _ACTIVATION_WINDOW, anchor, begin, finish)
                key = identity(request)
                candidates.setdefault(key, (request, set()))[1].add((shell, seed.direction, case))
    needed = {goal for _, goals in candidates.values() for goal in goals}
    # A long control earns only the same eligible-window credit as a short
    # episode. Its duration alone does not establish stable/transition coverage.
    for request in selected:
        seed, _, _, begin, finish = request
        for reason, times in seed.reason_times.items():
            if reason.startswith("shell-coverage:") and any(begin <= t < finish for t in times):
                shell, case = reason.removeprefix("shell-coverage:").rsplit(":", 1)
                needed.discard((shell, seed.direction, case))
    for case in ("stable", "transition"):
        while any(goal[2] == case for goal in needed):
            choices = [(request, goals & needed) for request, goals in candidates.values()
                       if any(goal[2] == case for goal in goals & needed) and fits(request)]
            if not choices:
                break
            request, covered = min(choices, key=lambda item: (
                -len(item[1]),
                sum(abs((item[0][2] - r[2]).total_seconds()) < 30 for r in selected),
                sha256(f"{_SAMPLING_SEED}|{identity(item[0])!r}".encode()).hexdigest()))
            selected.append(request); used.add(identity(request)); needed.difference_update(covered)
    for request in diversity:
        if fits(request):
            selected.append(request); used.add(identity(request))
    return tuple(sorted(selected, key=lambda r: (r[3], r[1], r[0].key)))


def _peak_concurrency(episodes):
    current = peak = 0
    for _, delta in sorted((at, delta) for e in episodes for at, delta in ((e.start_time, 1), (e.end_time, -1))):
        current += delta
        peak = max(peak, current)
    return peak


def _shell_eligibility(seeds):
    counts = defaultdict(int)
    for seed in seeds:
        for reason, times in seed.reason_times.items():
            if reason.startswith("shell-coverage:"):
                shell, case = reason.removeprefix("shell-coverage:").rsplit(":", 1)
                counts[(shell, seed.direction, case)] += len(times)
    return tuple((*key, count) for key, count in sorted(counts.items()))


def _shell_coverage(plan):
    if plan.forwarding_backend == "linux":
        return []  # Unknown transit cannot establish per-Shell path coverage.
    membership = dict(plan.shell_metadata)
    identities = {i.probe_id: i for i in plan.probe_identities}
    eligibility = {(s, d, c): count for s, d, c, count in plan.shell_eligibility}
    result = []
    for shell in sorted(set(membership.values())):
        episodes = []
        for e in plan.episodes:
            intervals = [i for i in e.state_intervals if i.expected_reachable and any(membership.get(n) == shell for n in i.path_node_ids)]
            if intervals:
                episodes.append({"episode_id": e.episode_id, "direction": identities[e.probe_id].direction,
                                 "mode": e.mode, "reachable_interval_count": len(intervals),
                                 "cases": sorted({case for s, case in e.coverage_cases if s == shell})})
        goals = [{"direction": d, "case": c, "eligible_candidate_observations": eligibility.get((shell, d, c), 0),
                  "selected": any(e['direction'] == d and c in e['cases'] for e in episodes)}
                 for d in (_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND) for c in ("stable", "transition")]
        eligible = any(g['eligible_candidate_observations'] for g in goals)
        result.append({"shell_id": shell, "eligible": eligible, "episodes": episodes, "goals": goals,
                       "status": "selected" if eligible and all(g['selected'] or not g['eligible_candidate_observations'] for g in goals) else "budget-or-window-gap" if eligible else "no-eligible-candidate",
                       "reason": None if eligible else "No explicit bidirectionally reachable GS/Zone planned path through this typed Shell in the scanned horizon."})
    return result


def _take_direction_coverage(
    values: tuple[_PairSeed, ...], *, maximum: int
) -> tuple[_PairSeed, ...]:
    if maximum <= 0:
        return ()
    by_direction = {
        direction: sorted(
            (value for value in values if value.direction == direction),
            key=lambda value: value.key,
        )
        for direction in (
            _DIRECTION_GROUND_TO_ZONE,
            _DIRECTION_ZONE_TO_GROUND,
        )
    }
    selected: list[_PairSeed] = []
    while len(selected) < maximum:
        added = False
        for direction in (
            _DIRECTION_GROUND_TO_ZONE,
            _DIRECTION_ZONE_TO_GROUND,
        ):
            bucket = by_direction[direction]
            if bucket and len(selected) < maximum:
                selected.append(bucket.pop(0))
                added = True
        if not added:
            break
    return tuple(selected)


def _take_stratified_observations(
    values: tuple[tuple[str, datetime, str, _PairSeed], ...],
    *,
    maximum: int,
    nominal_boundaries: tuple[datetime, ...],
) -> tuple[tuple[str, datetime, str, _PairSeed], ...]:
    if maximum <= 0:
        return ()
    boundary_index = {
        value: index for index, value in enumerate(nominal_boundaries)
    }
    buckets: dict[
        tuple[str, int],
        list[tuple[str, datetime, str, _PairSeed]],
    ] = defaultdict(list)
    seen = set()
    for value in values:
        direction, timestamp, label, seed = value
        identity = direction, timestamp, label, seed.key
        if identity in seen or timestamp not in boundary_index:
            continue
        seen.add(identity)
        index = boundary_index[timestamp]
        stratum = min(3, index * 4 // len(nominal_boundaries))
        buckets[(direction, stratum)].append(value)
    for bucket in buckets.values():
        bucket.sort(key=lambda value: (value[2], value[3].key, value[1]))

    selected = []
    selected_episode_keys = set()
    bucket_order = sorted(
        buckets,
        key=lambda value: (
            value[1],
            0 if value[0] == _DIRECTION_GROUND_TO_ZONE else 1,
        ),
    )
    while len(selected) < maximum:
        added = False
        for bucket_key in bucket_order:
            bucket = buckets[bucket_key]
            while bucket:
                candidate = bucket.pop(0)
                episode_key = candidate[3].key, candidate[1]
                if episode_key not in selected_episode_keys:
                    selected.append(candidate)
                    selected_episode_keys.add(episode_key)
                    added = True
                    break
            if len(selected) >= maximum:
                break
        if not added:
            break
    return tuple(selected)


def _bounded_episode_window(
    anchor: datetime,
    *,
    coverage_start: datetime,
    coverage_end: datetime,
    before_seconds: int,
    after_seconds: int,
) -> tuple[datetime, datetime]:
    start = max(coverage_start, anchor - timedelta(seconds=before_seconds))
    end = min(coverage_end, anchor + timedelta(seconds=after_seconds))
    if end <= start and coverage_end > coverage_start:
        start = max(coverage_start, coverage_end - timedelta(seconds=1))
        end = coverage_end
    return start, end


def _reconstruction_boundaries(
    requests: tuple[_EpisodeRequest, ...],
    nominal_boundaries: tuple[datetime, ...],
) -> dict[tuple[str, str, str], set[datetime]]:
    """Select full control horizons and local context for bounded episodes."""

    result: dict[tuple[str, str, str], set[datetime]] = defaultdict(set)
    for seed, mode, _anchor, start, end in requests:
        if mode == _PERSISTENT:
            result[seed.key].update(nominal_boundaries)
            continue
        index = max(0, bisect_right(nominal_boundaries, start) - 1)
        result[seed.key].add(nominal_boundaries[index])
        index += 1
        while index < len(nominal_boundaries) and nominal_boundaries[index] < end:
            result[seed.key].add(nominal_boundaries[index])
            index += 1
    return result


def _reconstruct_selected_paths_from_deltas(
    deltas: tuple[_TrafficBoundaryDelta, ...],
    zone_prefixes: tuple[tuple[str, str], ...],
    seeds: tuple[_PairSeed, ...],
    *,
    episode_requests: tuple[_EpisodeRequest, ...],
    nominal_boundaries: tuple[datetime, ...],
    coverage_end: datetime,
    initial_projection=None, final_projection=None, native=False,
) -> tuple[
    dict[tuple[str, str, str], tuple[_ProbeStateInterval, ...]],
    dict[tuple[str, str, str], set[datetime]],
]:
    return _reconstruct_selected_paths_from_scan(
        lambda visitor: _scan_boundary_deltas(
            deltas, zone_prefixes, visitor, initial_projection=initial_projection,
            final_projection=final_projection,
        ),
        seeds,
        episode_requests=episode_requests,
        nominal_boundaries=nominal_boundaries,
        coverage_end=coverage_end, native=native,
    )


def _reconstruct_selected_paths_from_scan(
    scan: Callable[
        [Callable[[_BoundaryProjection], None]], tuple[datetime, ...]
    ],
    seeds: tuple[_PairSeed, ...],
    *,
    episode_requests: tuple[_EpisodeRequest, ...],
    nominal_boundaries: tuple[datetime, ...],
    coverage_end: datetime,
    native=False,
) -> tuple[
    dict[tuple[str, str, str], tuple[_ProbeStateInterval, ...]],
    dict[tuple[str, str, str], set[datetime]],
]:
    """Resolve only selected probe paths at required execution boundaries."""

    required = _reconstruction_boundaries(episode_requests, nominal_boundaries)
    seeds_by_boundary: dict[datetime, list[_PairSeed]] = defaultdict(list)
    for seed in seeds:
        for boundary in required[seed.key]:
            seeds_by_boundary[boundary].append(seed)
    interval_by_key: dict[tuple[str, str, str], list[_ProbeStateInterval]] = {
        seed.key: [] for seed in seeds
    }
    affected_by_key: dict[tuple[str, str, str], set[datetime]] = {
        seed.key: set() for seed in seeds
    }
    prior_by_key: dict[
        tuple[str, str, str],
        tuple[tuple[bool | None, tuple[str, ...], str | None], int],
    ] = {}
    unsupported_keys: set[tuple[str, str, str]] = set()
    return_seed_by_key = {
        seed.key: _PairSeed(
            (
                _DIRECTION_ZONE_TO_GROUND
                if seed.direction == _DIRECTION_GROUND_TO_ZONE
                else _DIRECTION_GROUND_TO_ZONE
            ),
            seed.ground_station,
            seed.service_key,
            defaultdict(set),
        )
        for seed in seeds
    }
    boundary_index = {
        value: index for index, value in enumerate(nominal_boundaries)
    }
    next_boundary = {
        value: (
            nominal_boundaries[index + 1]
            if index + 1 < len(nominal_boundaries)
            else coverage_end
        )
        for index, value in enumerate(nominal_boundaries)
    }

    def visit(projection: _BoundaryProjection) -> None:
        start = projection.nominal_time
        end = next_boundary[start]
        current_index = boundary_index[start]
        if end <= start:
            return
        current_seeds = [seed for seed in seeds_by_boundary.get(start, ()) if seed.key not in unsupported_keys]
        ground_edges = _native_ground_edges(projection) if native and current_seeds else None
        resolve = _resolve_native_path if native else _resolve_path
        options = {'ground_edges': ground_edges} if native else {}
        for seed in current_seeds:
            key = seed.key
            try:
                (
                    primary_reachable,
                    path,
                    service,
                    primary_unreachable,
                ) = resolve(seed, projection, **options)
                (
                    return_reachable,
                    return_path,
                    _unused,
                    return_unreachable,
                ) = resolve(return_seed_by_key[key], projection, **options)
            except _MissingPlannedRouteOutcome:
                # A candidate may cease to be an active RouteDemand during a
                # requested persistent or windowed horizon. Do not infer an
                # unreachable state: exclude the unsupported probe identity.
                unsupported_keys.add(key)
                interval_by_key[key].clear()
                affected_by_key[key].clear()
                prior_by_key.pop(key, None)
                continue
            reachable = (False if primary_reachable is False or return_reachable is False else
                         None if primary_reachable is None or return_reachable is None else True)
            unreachable = (
                primary_unreachable
                if primary_reachable is False
                else f"return-{return_unreachable}" if return_reachable is False else None
            )
            path_ids = tuple(value.value for value in path)
            return_path_ids = tuple(value.value for value in return_path)
            service_id = None if service is None else service.value
            current = (
                reachable,
                (*path_ids, "<return-path>", *return_path_ids),
                service_id,
            )
            prior_record = prior_by_key.get(key)
            prior = (
                prior_record[0]
                if prior_record is not None and prior_record[1] + 1 == current_index
                else None
            )
            boundary_reasons = set()
            if seed.ground_station in projection.changed_access_ground_stations:
                boundary_reasons.add("ground-station-attachment-change")
            if prior is not None and current[1] != prior[1]:
                boundary_reasons.add("forwarding-next-hop-change")
            if prior is not None and current[0] != prior[0]:
                boundary_reasons.add(
                    ("edge-availability-restored" if current[0] is not False else "edge-availability-withdrawn")
                    if native else "expected-restoration" if current[0] else "expected-withdrawal"
                )
            if prior is not None and current[2] != prior[2]:
                if prior[2] is None:
                    boundary_reasons.add("zone-service-establishment")
                elif current[2] is None:
                    boundary_reasons.add("zone-service-withdrawal")
                else:
                    boundary_reasons.add("zone-service-handover")
                    if seed.direction == _DIRECTION_ZONE_TO_GROUND:
                        boundary_reasons.add("zone-source-migration")
            if boundary_reasons:
                affected_by_key[key].add(start)
            path_length = None if not primary_reachable else len(path) - 1
            _append_state_interval(
                interval_by_key[key],
                _ProbeStateInterval(
                    start,
                    end,
                    reachable,
                    unreachable,
                    service_id,
                    path_ids,
                    path_length,
                    tuple(sorted(boundary_reasons)),
                ),
            )
            prior_by_key[key] = current, current_index

    visited = scan(visit)
    if visited != nominal_boundaries:
        raise ValueError("Traffic path scan omitted a nominal boundary")
    return (
        {key: tuple(intervals) for key, intervals in interval_by_key.items()},
        affected_by_key,
    )


def _append_state_interval(
    intervals: list[_ProbeStateInterval], current: _ProbeStateInterval
) -> None:
    if not intervals:
        intervals.append(current)
        return
    prior = intervals[-1]
    if (
        prior.end_time == current.start_time
        and not current.boundary_reasons
        and prior.expected_reachable == current.expected_reachable
        and prior.unreachable_reason == current.unreachable_reason
        and prior.service_satellite_id == current.service_satellite_id
        and prior.path_node_ids == current.path_node_ids
        and prior.path_length_hops == current.path_length_hops
    ):
        intervals[-1] = _ProbeStateInterval(
            prior.start_time,
            current.end_time,
            prior.expected_reachable,
            prior.unreachable_reason,
            prior.service_satellite_id,
            prior.path_node_ids,
            prior.path_length_hops,
            tuple(sorted(set(prior.boundary_reasons) | set(current.boundary_reasons))),
        )
        return
    intervals.append(current)


def _resolve_path(
    seed: _PairSeed,
    projection: _BoundaryProjection,
) -> tuple[bool, tuple[NodeId, ...], NodeId | None, str | None]:
    edge = projection.zone_edges.get(seed.service_key)
    if seed.direction == _DIRECTION_GROUND_TO_ZONE:
        source = seed.ground_station
        target = ZoneRouteDemandTarget(seed.service_key[1], seed.service_key[0])
        service = None if edge is None else edge[1]
        terminal = service
    else:
        if edge is None:
            return False, (), None, "zone-unserved"
        service = edge[1]
        source = service
        target = GroundStationRouteDemandTarget(seed.ground_station, seed.service_key[0])
        terminal = seed.ground_station
    demand = RouteDemand(source, target)
    outcome = projection.route_outcomes.get(demand)
    if outcome is None:
        raise _MissingPlannedRouteOutcome(
            "Traffic demand has no explicit planned Route outcome"
        )
    if isinstance(outcome, UnreachableRouteOutcome):
        return False, (), service, outcome.reason.value
    if isinstance(outcome, LocalRouteOutcome):
        raise _MissingPlannedRouteOutcome("local arrangement does not establish an end-to-end probe path")
    if not isinstance(outcome, ReachableRouteOutcome):  # pragma: no cover
        raise TypeError("Traffic demand has an unsupported planned Route outcome")
    if terminal is None:
        raise ValueError(
            "planned reachable Zone Route has no service assignment"
        )
    path = outcome.path
    if path[-1] != terminal:
        raise ValueError("planned Traffic Route terminates at the wrong node")
    for current, next_hop in zip(path, path[1:]):
        binding = projection.forwarding.get(_ForwardingKey(current, target))
        if binding is None or binding.next_hop != next_hop:
            raise ValueError(
                "planned reachable Route lacks matching compiled forwarding"
            )
        if binding.connection_id not in projection.resources:
            raise ValueError("Traffic path uses an inactive compiled resource")
    return True, path, service, None


def _make_identity(
    seed: _PairSeed,
    intervals: tuple[_ProbeStateInterval, ...],
    *,
    zone_ipv4: str,
) -> _TrafficProbeIdentity:
    unknown = any(value.expected_reachable is None for value in intervals)
    reachable = tuple(value for value in intervals if value.expected_reachable is not False)
    paths = {value.path_node_ids for value in reachable}
    services = {value.service_satellite_id for value in reachable}
    stable = bool(reachable) and len(reachable) == len(intervals) and len(paths) == 1 and len(services) <= 1
    reasons = {
        reason for interval in intervals for reason in interval.boundary_reasons
    }
    if {
        "persistent-control-candidate",
        "rotating-background-candidate",
    } & seed.reason_times.keys():
        reasons.add("bounded-stratified-background-coverage")
    reasons.add("native-transit-path-unknown" if unknown else
                "persistent-stable-control" if stable else "persistent-changing-path")
    representative_length = next(
        (
            value.path_length_hops
            for value in reachable
            if value.path_length_hops is not None
        ),
        0,
    )
    path_length_class = (
        "short"
        if representative_length <= 3
        else "long" if representative_length > 8 else "median"
    )
    digest = sha256(
        "|".join(
            (
                seed.direction,
                seed.ground_station.value,
                *seed.service_key,
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    return _TrafficProbeIdentity(
        f"traffic-probe-{digest}",
        seed.direction,
        seed.ground_station.value,
        seed.service_key[1],
        zone_ipv4,
        tuple(sorted(reasons)),
        "unknown" if unknown else "stable" if stable else "changing",
        "unknown" if unknown else path_length_class,
        seed.service_key[0],
        seed.geographic_separation_m, seed.geographic_separation_class,
    )


def _make_episode(
    seed: _PairSeed,
    intervals: tuple[_ProbeStateInterval, ...],
    *,
    identity: _TrafficProbeIdentity,
    mode: str,
    start: datetime,
    end: datetime,
    interval_seconds: float,
    episode_anchor: datetime,
    affected_times: tuple[datetime, ...],
) -> _TrafficProbeEpisode:
    selected_intervals = tuple(
        value
        for value in intervals
        if value.end_time > start and value.start_time < end
    )
    echo_requests = ceil((end - start).total_seconds() / interval_seconds)
    estimated_events = 0
    for value in selected_intervals:
        overlap_start = max(start, value.start_time)
        overlap_end = min(end, value.end_time)
        if overlap_end <= overlap_start or not value.expected_reachable:
            continue
        requests = ceil((overlap_end - overlap_start).total_seconds() / interval_seconds)
        estimated_events += requests * 2 * max(1, value.path_length_hops or 0)
    digest = sha256(
        "|".join(
            (
                seed.direction,
                identity.probe_id,
                mode,
                episode_anchor.isoformat(),
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    return _TrafficProbeEpisode(
        f"traffic-episode-{digest}",
        identity.probe_id,
        mode,
        start,
        end,
        interval_seconds,
        affected_times,
        selected_intervals,
        echo_requests,
        estimated_events,
        tuple(sorted(tuple(reason.removeprefix("shell-coverage:").rsplit(":", 1))
                     for reason, times in seed.reason_times.items() if reason.startswith("shell-coverage:")
                     and (episode_anchor in times or (mode == _PERSISTENT and any(start <= t < end for t in times))))),
    )


def _identity_from_artifact(value: object) -> _TrafficProbeIdentity:
    item = _mapping(value, "Traffic probe identity")
    direction = _one_of(
        item.get("direction"),
        {_DIRECTION_GROUND_TO_ZONE, _DIRECTION_ZONE_TO_GROUND},
        "probe direction",
    )
    separation = item.get('geographic_separation_m')
    separation_class = _one_of(item.get('geographic_separation_class', 'unavailable'),
        {'near', 'far', 'equal', 'only-candidate', 'unavailable'}, 'geographic separation class')
    if separation is not None:
        separation = _non_negative_number(separation, 'geographic separation')
        if not isfinite(separation):
            raise ValueError('geographic separation must be finite')
    if (separation is None) != (separation_class == 'unavailable'):
        raise ValueError('geographic separation and its class must be available together')
    return _TrafficProbeIdentity(
        _non_empty_string(item.get("probe_id"), "probe_id"),
        direction,
        _non_empty_string(item.get("ground_station_id"), "ground_station_id"),
        _non_empty_string(item.get("zone_id"), "zone_id"),
        _non_empty_string(item.get("zone_ipv4"), "zone_ipv4"),
        tuple(
            _non_empty_string(raw, "selection reason")
            for raw in _list(item.get("selection_reasons"), "selection reasons")
        ),
        _one_of(item.get("path_stability"), {"stable", "changing", "unknown"}, "path stability"),
        _one_of(item.get("path_length_class"), {"short", "median", "long", "unknown"}, "path class"),
        item["network_scope"],
        separation, separation_class,
    )


def _episode_from_artifact(
    value: object,
    coverage_start: datetime,
    coverage_end: datetime,
    identity_ids: set[str],
) -> _TrafficProbeEpisode:
    item = _mapping(value, "Traffic probe episode")
    probe_id = _non_empty_string(item.get("probe_id"), "episode probe_id")
    if probe_id not in identity_ids:
        raise ValueError("Traffic probe episode references an unknown identity")
    if item.get("episode_intent") != "armed":
        raise ValueError("Traffic probe episode intent is invalid")
    mode = _one_of(
        item.get("mode"),
        {_PERSISTENT, _ROTATING_BACKGROUND, _ACTIVATION_WINDOW},
        "episode mode",
    )
    start = _timestamp(item.get("start_time"), "probe start")
    end = _timestamp(item.get("end_time"), "probe end")
    if not coverage_start <= start < end <= coverage_end:
        raise ValueError("Traffic probe falls outside its planning horizon")
    intervals = tuple(
        _interval_from_artifact(raw, start, end)
        for raw in _list(item.get("state_intervals"), "probe state intervals")
    )
    if not intervals:
        raise ValueError("Traffic probe has no state intervals")
    return _TrafficProbeEpisode(
        _non_empty_string(item.get("episode_id"), "episode_id"),
        probe_id,
        mode,
        start,
        end,
        _positive_number(item.get("interval_seconds"), "probe interval"),
        tuple(
            _timestamp(raw, "affected activation")
            for raw in _list(item.get("affected_activation_times"), "affected activations")
        ),
        intervals,
        _non_negative_int(item.get("estimated_echo_requests"), "estimated echo requests"),
        _non_negative_int(item.get("estimated_telemetry_events"), "estimated telemetry events"),
        tuple((str(r["shell_id"]), str(r["case"])) for r in item.get("coverage_cases", ())),
    )


def _interval_from_artifact(
    value: object, probe_start: datetime, probe_end: datetime
) -> _ProbeStateInterval:
    item = _mapping(value, "Traffic probe state interval")
    start = _timestamp(item.get("start_time"), "state interval start")
    end = _timestamp(item.get("end_time"), "state interval end")
    if end <= probe_start or start >= probe_end or end <= start:
        raise ValueError("Traffic probe state interval is outside the probe")
    reachable = item.get("expected_reachable")
    if reachable is not None and not isinstance(reachable, bool):
        raise ValueError("Traffic probe reachability must be boolean or unknown")
    if reachable is None and item.get("path_status") != "unknown-native-transit":
        raise ValueError("Unknown Traffic reachability needs explicit native transit status")
    raw_path = _list(item.get("path_node_ids"), "state interval path")
    path = tuple(_non_empty_string(raw, "path node") for raw in raw_path)
    raw_length = item.get("path_length_hops")
    length = None if raw_length is None else _non_negative_int(raw_length, "path length")
    if reachable is None and (path or length is not None):
        raise ValueError("Unknown native transit cannot carry an invented path")
    if reachable and (not path or length != len(path) - 1):
        raise ValueError("Traffic probe reachable path evidence is inconsistent")
    return _ProbeStateInterval(
        start,
        end,
        reachable,
        (
            None
            if item.get("unreachable_reason") is None
            else _non_empty_string(item.get("unreachable_reason"), "unreachable reason")
        ),
        (
            None
            if item.get("service_satellite_id") is None
            else _non_empty_string(item.get("service_satellite_id"), "service satellite")
        ),
        path,
        length,
        tuple(
            _non_empty_string(raw, "boundary reason")
            for raw in _list(item.get("boundary_reasons"), "boundary reasons")
        ),
    )


def _traffic_plan_revision_id(
    coverage_start: datetime,
    coverage_end: datetime,
    nominal_boundaries: tuple[datetime, ...],
    identities: tuple[_TrafficProbeIdentity, ...],
    episodes: tuple[_TrafficProbeEpisode, ...],
) -> str:
    payload = {
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "nominal_boundaries": [
            value.isoformat() for value in nominal_boundaries
        ],
        "probe_identities": [value.artifact() for value in identities],
        "episodes": [value.artifact() for value in episodes],
    }
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a timestamp") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return result


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative int")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive int")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


def _non_negative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be non-negative")
    return float(value)


def _one_of(value: object, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} is invalid")
    return value


__all__: list[str] = []


def read_generated_plan(path, *, published_batches=None):
    """Combine sealed workload plans for preview/inspection, without planning."""
    output = path.parent.parent.parent
    catalog = output / "results/compiler/materials.jsonl"
    if not catalog.is_file():
        return read_traffic_probe_plan(path).artifact()
    from dataclasses import replace
    from itertools import islice
    count = published_batches
    with catalog.open() as stream:
        plans = [read_traffic_probe_plan(output / json.loads(line)["traffic"]) for line in islice(stream, count)]
    identities = {value.probe_id: value for plan in plans for value in plan.probe_identities}
    combined = replace(plans[0], coverage_end=plans[-1].coverage_end,
        probe_identities=tuple(identities.values()), episodes=tuple(v for p in plans for v in p.episodes),
        nominal_boundaries=tuple(v for p in plans for v in p.nominal_boundaries),
        supplied_realization_count=plans[-1].supplied_realization_count,
        revision_id="delivered-observation-workloads", planning_duration_seconds=sum(p.planning_duration_seconds for p in plans),
        global_budget=(sum(p.global_budget[0] for p in plans), max(p.global_budget[1] for p in plans),
                       sum(p.global_budget[2] for p in plans))).artifact()
    combined["delivery_plans"] = [p.artifact() for p in plans]
    combined["authority"] = "detached aggregate of sealed per-delivery Traffic inputs"
    return combined
