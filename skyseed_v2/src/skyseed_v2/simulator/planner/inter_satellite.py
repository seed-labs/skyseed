"""Inter-satellite selection contract and retain-first algorithm."""
from __future__ import annotations
from types import MappingProxyType
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable
from skyseed_v2.shared import NodeId
from ..physical import PhysicalWorld, InterSatelliteLinkCandidate
from ._durations import _validate_service_duration, _required_contact_duration, _has_useful_contact
from .selection import ConnectionSelectionState
from .diagnostics import CandidateDecision, CandidateDecisionKind
from .values import _node_key, OperationalConnectionId, OperationalConnectionKind
_IslPair = tuple[NodeId, NodeId]


@dataclass(frozen=True, slots=True)
class InterSatelliteSelection:
    """Return one validated ISL policy selection and its compact evidence.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable ISL Models must return one stable contract to ``Planner``. It
    is not an operational Connection set, acquisition state, or physical fact
    owner.
    """

    selected_candidates: tuple[InterSatelliteLinkCandidate, ...]

    def __post_init__(self) -> None:
        candidates = tuple(self.selected_candidates)
        if any(
            not isinstance(value, InterSatelliteLinkCandidate)
            for value in candidates
        ):
            raise TypeError(
                "selected_candidates must contain ISL candidate values"
            )
        ordered = tuple(sorted(candidates, key=_isl_candidate_identity_key))
        identities = tuple(_candidate_pair(value) for value in ordered)
        if len(identities) != len(set(identities)):
            raise ValueError("selected_candidates contains a duplicate pair")
        object.__setattr__(self, "selected_candidates", ordered)


@dataclass(frozen=True, slots=True)
class InterSatelliteSelectionDiagnostics:
    """Optional Model explanations consumed by inspection, never selection admission.

    Layer: ISL Model diagnostic value. The algorithm creates it; optional
    recording/inspection consumes it. It is public because independent Models
    return these explanations. It is not Plan truth or a correctness proof.
    """

    decisions: tuple[CandidateDecision, ...] = ()
    underfilled_slots: int | None = None

    def __post_init__(self):
        decisions = tuple(self.decisions)
        if any(not isinstance(value, CandidateDecision) for value in decisions):
            raise TypeError("decisions must contain CandidateDecision values")
        value = self.underfilled_slots
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("underfilled_slots must be non-negative or None")
        object.__setattr__(self, "decisions", decisions)


@runtime_checkable
class InterSatelliteConnectionPlanningModel(Protocol):
    """Define the replaceable ISL candidate-scope and selection policy.

    Layer: public Simulator planning Model contract. It is public because
    callers inject ISL policy into ``Planner`` without replacing the physical
    or downstream layers. It is not a PhysicalLinkModel, lifecycle state
    owner, or hardware-capability aggregate.

    Optional read-only ``selection_statistics()`` returns named numeric work
    counters from the last selection. Planner queries it only when recording;
    custom Models need not provide it or expose any private counter fields.
    """

    def create_inter_satellite_candidate_pairs(
        self, physical_world: PhysicalWorld
    ) -> tuple[_IslPair, ...]:
        """Enumerate only bounded ISL relationships for physical evaluation."""

    def select_inter_satellite_links(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[InterSatelliteLinkCandidate, ...],
        state: ConnectionSelectionState,
    ) -> InterSatelliteSelection:
        """Choose current candidates within declared endpoint capacity."""


@runtime_checkable
class InterSatelliteCandidateSearchModel(Protocol):
    """Optional ISL search optimization, independent of the selection contract.

    Layer: Planner Model capability. It is public because custom Models may
    implement advice consumed by Planner. It is not a required selection role
    or a physical query executor. Model authors create implementations.
    """

    def candidate_neighbor_count(self, physical_world: PhysicalWorld) -> int | None:
        """Return a positive cross-plane neighbor count, or None for unrestricted."""


@dataclass(frozen=True, slots=True)
class RetainFirstInterSatelliteConfiguration:
    """Configure the reference retain-first ISL planning policy.

    Layer: public Simulator planning configuration value. It is public because
    callers configure the supplied ISL Model through constructor injection. It
    is not PhysicalWorld hardware capability, a selected-link set, or lifecycle
    state.
    """

    desired_same_plane_links: int = 2
    desired_cross_plane_links: int = 2
    maximum_cross_plane_separation: int = 3
    same_plane_neighbor_span: int | None = None
    cross_plane_neighbor_count: int | None = None
    minimum_service_duration: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        for field_name in (
            "desired_same_plane_links",
            "desired_cross_plane_links",
            "maximum_cross_plane_separation",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        for name in ("same_plane_neighbor_span", "cross_plane_neighbor_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be positive or None")
        if self.maximum_cross_plane_separation == 0:
            if self.desired_cross_plane_links:
                raise ValueError(
                    "cross-plane demand requires a positive separation"
                )
        _validate_service_duration(self.minimum_service_duration)


def _isl_blocked_reason(candidate, total, same, cross, capabilities, configuration):
    first, second = _candidate_pair(candidate)
    if not candidate.same_plane and candidate.cyclic_plane_separation > configuration.maximum_cross_plane_separation:
        return "outside configured cross-plane candidate scope"
    if total[first] >= capabilities[first] or total[second] >= capabilities[second]:
        return "PhysicalWorld capability exhausted at an endpoint"
    if candidate.same_plane and (same[first] >= configuration.desired_same_plane_links or same[second] >= configuration.desired_same_plane_links):
        return "same-plane policy target reached at an endpoint"
    if not candidate.same_plane and (cross[first] >= configuration.desired_cross_plane_links or cross[second] >= configuration.desired_cross_plane_links):
        return "cross-plane policy target reached at an endpoint"
    return None


class RetainFirstInterSatelliteConnectionPlanningModel:
    """Apply the reference bounded K-scope, retain-first ISL policy.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied, configurable implementation of the ISL
    Model contract. It is not PhysicalWorld capability, physical visibility,
    or Planner-owned acquisition state.
    """

    __slots__ = ("_configuration", "_prepared_world", "_capabilities", "_decisions", "_selection_cache", "_selection_work", "_diagnostics")

    def __init__(
        self,
        configuration: RetainFirstInterSatelliteConfiguration = (
            RetainFirstInterSatelliteConfiguration()
        ),
    ) -> None:
        if not isinstance(
            configuration, RetainFirstInterSatelliteConfiguration
        ):
            raise TypeError(
                "configuration must be RetainFirstInterSatelliteConfiguration"
            )
        self._configuration = configuration
        self._prepared_world = None
        self._capabilities = {}
        self._decisions = {}
        self._selection_cache = None
        self._selection_work = {}
        self._diagnostics = InterSatelliteSelectionDiagnostics()

    def required_contact_duration(self, *, acquisition_duration, observation_interval):
        return _required_contact_duration(acquisition_duration, observation_interval, self.minimum_service_duration)

    def selection_diagnostics(self) -> InterSatelliteSelectionDiagnostics:
        return self._diagnostics

    @property
    def minimum_service_duration(self) -> timedelta:
        return self._configuration.minimum_service_duration

    def selection_statistics(self) -> Mapping[str, int]:
        """Read the most recent selection's bounded work counters without mutation."""
        return MappingProxyType(self._selection_work)

    def describe_configuration(self):
        return self._configuration

    def candidate_neighbor_count(self, physical_world: PhysicalWorld) -> int | None:
        """Optional nearest-candidate advice; retained relationships are Planner-owned."""
        return self._configuration.cross_plane_neighbor_count

    def create_inter_satellite_candidate_pairs(
        self, physical_world: PhysicalWorld
    ) -> tuple[_IslPair, ...]:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        pairs: set[_IslPair] = set()
        maximum_separation = self._configuration.maximum_cross_plane_separation
        for autonomous_system in physical_world.autonomous_systems:
            for shell in autonomous_system.shells:
                orbits = tuple(
                    sorted(
                        shell.orbits,
                        key=lambda value: (
                            value.right_ascension_deg,
                            value.orbit_id,
                        ),
                    )
                )
                for orbit in orbits:
                    satellites = tuple(value.node_id for value in sorted(
                        orbit.satellites, key=lambda value: (value.mean_anomaly_deg, _node_key(value.node_id))))
                    span = self._configuration.same_plane_neighbor_span
                    for first_index, first in enumerate(satellites):
                        offsets = range(1, len(satellites)) if span is None else range(1, min(span, len(satellites) // 2) + 1)
                        for offset in offsets:
                            second = satellites[(first_index + offset) % len(satellites)]
                            if first != second:
                                pairs.add(_canonical_pair(first, second))
                orbit_count = len(orbits)
                for first_index, first_orbit in enumerate(orbits):
                    for second_index in range(first_index + 1, orbit_count):
                        separation = min(
                            second_index - first_index,
                            orbit_count - (second_index - first_index),
                        )
                        if separation == 0 or separation > maximum_separation:
                            continue
                        second_orbit = orbits[second_index]
                        for first in first_orbit.satellites:
                            for second in second_orbit.satellites:
                                pairs.add(
                                    _canonical_pair(
                                        first.node_id, second.node_id
                                    )
                                )
        return tuple(
            sorted(
                pairs,
                key=lambda value: (_node_key(value[0]), _node_key(value[1])),
            )
        )

    def select_inter_satellite_links(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[InterSatelliteLinkCandidate, ...],
        state: ConnectionSelectionState,
    ) -> InterSatelliteSelection:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        retained_pairs = frozenset((key.endpoint_a, key.endpoint_b) for key in state.selected_since)
        accepted, excluded = [], []
        for candidate in candidates:
            for name in ("same_plane", "cyclic_plane_separation", "distance_m"):
                if getattr(candidate, name) is None:
                    raise ValueError(f"RetainFirstInterSatelliteConnectionPlanningModel requires {name}")
            identifier = OperationalConnectionId(OperationalConnectionKind.INTER_SATELLITE,
                candidate.endpoint_a, candidate.endpoint_b)
            if _has_useful_contact(candidate, identifier, state, self.minimum_service_duration):
                accepted.append(candidate)
            else:
                excluded.append(CandidateDecision(CandidateDecisionKind.REJECTED,
                    "known future contact does not cover preparation and useful service",
                    candidate.endpoint_a, candidate.endpoint_b))
        candidates = tuple(accepted)
        def result(selected, decisions, underfilled):
            self._diagnostics = InterSatelliteSelectionDiagnostics(tuple(decisions) + tuple(excluded), underfilled)
            return InterSatelliteSelection(tuple(selected))
        if self._prepared_world is not physical_world:
            self._capabilities = _isl_capabilities(physical_world)
            self._prepared_world = physical_world
            self._decisions = {}
            self._selection_cache = None
        capabilities = self._capabilities
        by_pair = {}
        shapes = set()
        for candidate in candidates:
            if not isinstance(candidate, InterSatelliteLinkCandidate):
                raise TypeError("candidates must contain ISL candidate values")
            pair = _candidate_pair(candidate)
            by_pair[pair] = candidate
            shapes.add((pair, candidate.same_plane, candidate.cyclic_plane_separation))
        self._selection_work = {"isl_candidate_index_entries": len(candidates),
                                "isl_candidate_policy_sorts": 0, "isl_retained_selection_reused": 0}
        cached = self._selection_cache
        if cached is not None and retained_pairs == cached[0] and shapes == cached[1] and len(by_pair) == len(candidates):
            # Greedy completion left every unselected edge blocked by a fixed
            # capacity/scope/degree constraint. Retaining the whole selection
            # preserves those constraints regardless of changing distances.
            self._selection_work["isl_retained_selection_reused"] = 1
            return result(tuple(by_pair[pair] for pair in cached[2]), cached[3], cached[4])
        decisions_by_key = {}
        def decide(kind, reason, first=None, second=None):
            key = kind, reason, first, second
            value = self._decisions.get(key)
            if value is None:
                value = CandidateDecision(kind, reason, first, second)
            decisions_by_key[key] = value
            return value
        if (cached is not None and retained_pairs == cached[0] and len(by_pair) == len(candidates)
            and all(pair in by_pair and (pair, by_pair[pair].same_plane,
                    by_pair[pair].cyclic_plane_separation) in cached[1] for pair in cached[0])):
            rejected = []
            for pair, candidate in by_pair.items():
                if pair in retained_pairs:
                    continue
                reason = _isl_blocked_reason(candidate, *cached[5], capabilities, self._configuration)
                if reason is None:
                    break
                rejected.append(decide(CandidateDecisionKind.REJECTED, reason, *pair))
            else:
                # Candidate arrivals/departures do not invalidate an incumbent
                # when every current alternative is still provably blocked.
                decisions = tuple(value for value in cached[3]
                    if value.decision is CandidateDecisionKind.RETAINED) + tuple(rejected) + tuple(
                    value for value in cached[3] if value.decision is CandidateDecisionKind.UNDERFILLED)
                self._selection_cache = cached[0], shapes, cached[2], decisions, cached[4], cached[5]
                self._decisions = decisions_by_key
                self._selection_work["isl_retained_selection_reused"] = 1
                return result(tuple(by_pair[pair] for pair in cached[2]), decisions, cached[4])
        ordered_candidates = tuple(
            sorted(candidates, key=_isl_candidate_policy_key)
        )
        self._selection_work["isl_candidate_policy_sorts"] = 1
        selected: dict[_IslPair, InterSatelliteLinkCandidate] = {}
        total_degree = {node_id: 0 for node_id in capabilities}
        same_degree = {node_id: 0 for node_id in capabilities}
        cross_degree = {node_id: 0 for node_id in capabilities}
        decisions: list[CandidateDecision] = []

        for pair in sorted(retained_pairs, key=_pair_key):
            candidate = by_pair.get(_canonical_pair(*pair))
            if candidate is None:
                continue
            _select_candidate(
                selected,
                total_degree,
                same_degree,
                cross_degree,
                candidate,
            )
            decisions.append(
                decide(
                    CandidateDecisionKind.RETAINED,
                    "selected relationship remains physically feasible",
                    *pair,
                )
            )

        for candidate in ordered_candidates:
            pair = _candidate_pair(candidate)
            if pair in selected:
                continue
            first, second = pair
            reason = _isl_blocked_reason(candidate, total_degree, same_degree, cross_degree,
                                         capabilities, self._configuration)
            if reason is None:
                _select_candidate(
                    selected,
                    total_degree,
                    same_degree,
                    cross_degree,
                    candidate,
                )
                decisions.append(
                    decide(
                        CandidateDecisionKind.SELECTED,
                        "filled remaining policy and PhysicalWorld capacity",
                        first,
                        second,
                    )
                )
                continue
            decisions.append(
                decide(
                    CandidateDecisionKind.REJECTED,
                    reason,
                    first,
                    second,
                )
            )

        desired_total = (
            self._configuration.desired_same_plane_links
            + self._configuration.desired_cross_plane_links
        )
        underfilled = sum(
            max(0, min(capabilities[node_id], desired_total) - degree)
            for node_id, degree in total_degree.items()
        )
        if underfilled:
            decisions.append(
                decide(
                    CandidateDecisionKind.UNDERFILLED,
                    f"{underfilled} endpoint slots had no conflict-free "
                    "feasible candidate",
                )
            )
        self._decisions = decisions_by_key
        selected_order = tuple(sorted(selected, key=_pair_key))
        stable_decisions = tuple(CandidateDecision(
            CandidateDecisionKind.RETAINED, "selected relationship remains physically feasible", *pair)
            for pair in selected_order) + tuple(sorted(
                (value for value in decisions if value.decision is CandidateDecisionKind.REJECTED),
                key=lambda value: _pair_key((value.endpoint_a, value.endpoint_b)))) + tuple(
                value for value in decisions if value.decision is CandidateDecisionKind.UNDERFILLED)
        self._selection_cache = (frozenset(selected), shapes, selected_order, stable_decisions, underfilled,
                                 (total_degree, same_degree, cross_degree))
        return result(tuple(selected.values()), tuple(decisions), underfilled)


def _isl_capabilities(physical_world: PhysicalWorld) -> dict[NodeId, int]:
    capabilities: dict[NodeId, int] = {}
    for autonomous_system in physical_world.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    capability = satellite.inter_satellite_link_capability
                    if capability is None:
                        raise ValueError(
                            "Network Planner requires explicit ISL capability "
                            f"for {satellite.node_id.value}"
                        )
                    capabilities[satellite.node_id] = (
                        capability.maximum_simultaneous_links
                    )
    return capabilities


def _select_candidate(
    selected: dict[_IslPair, InterSatelliteLinkCandidate],
    total_degree: dict[NodeId, int],
    same_degree: dict[NodeId, int],
    cross_degree: dict[NodeId, int],
    candidate: InterSatelliteLinkCandidate,
) -> None:
    pair = _candidate_pair(candidate)
    selected[pair] = candidate
    for endpoint in pair:
        total_degree[endpoint] += 1
        (same_degree if candidate.same_plane else cross_degree)[endpoint] += 1


def _candidate_pair(
    candidate: InterSatelliteLinkCandidate,
) -> _IslPair:
    return candidate.endpoint_a, candidate.endpoint_b


def _canonical_pair(first: NodeId, second: NodeId) -> _IslPair:
    return (
        (first, second)
        if _node_key(first) < _node_key(second)
        else (second, first)
    )


def _isl_candidate_policy_key(
    candidate: InterSatelliteLinkCandidate,
) -> tuple:
    # Same-plane is the explicit first category for the canonical reference.
    return (
        0 if candidate.same_plane else 1,
        candidate.distance_m,
        _node_key(candidate.endpoint_a),
        _node_key(candidate.endpoint_b),
    )


def _isl_candidate_identity_key(
    candidate: InterSatelliteLinkCandidate,
) -> tuple:
    return _pair_key(_candidate_pair(candidate))


def _pair_key(pair: _IslPair) -> tuple:
    canonical = _canonical_pair(*pair)
    return _node_key(canonical[0]), _node_key(canonical[1])
