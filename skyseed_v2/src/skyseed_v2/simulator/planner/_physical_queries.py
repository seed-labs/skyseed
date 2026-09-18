"""Scoped physical query cooperation, independent of Planner advancement.

The same injected Models declare potential pairs and optional search/prediction
advice at every cutoff. This helper has no operational state or lifecycle authority;
Planner supplies retained selection facts only when planning actually runs.
"""
from dataclasses import replace
from datetime import timedelta
from ..physical import PhysicalLinkRequest
from .selection import ConnectionSelectionState
from .ground_station import GroundStationCandidateSearch


class _PhysicalQueries:
    def __init__(self, world, inter_satellite_model, ground_station_model, configuration, interval):
        self._physical_world = world
        self._inter_satellite_model = inter_satellite_model
        self._ground_station_model = ground_station_model
        self._configuration = configuration
        self._observation_interval = interval

    def _physical_prediction_requirements(self):
        """Request only horizons explicitly declared by selection Models."""
        def required(model, duration):
            capability = getattr(model, "required_contact_duration", None)
            value = (capability(acquisition_duration=duration,
                     observation_interval=self._observation_interval)
                     if callable(capability) else timedelta(0))
            if not isinstance(value, timedelta) or value < timedelta(0):
                raise ValueError("required_contact_duration must return a non-negative timedelta")
            return value
        return (required(self._inter_satellite_model, self._configuration.inter_satellite_acquisition_duration),
                required(self._ground_station_model, self._configuration.ground_station_acquisition_duration))

    def create_physical_link_request(self) -> PhysicalLinkRequest:
        pairs = self._inter_satellite_model.create_inter_satellite_candidate_pairs(
            self._physical_world
        )
        if not isinstance(pairs, tuple):
            raise TypeError("ISL model must return a tuple of candidate pairs")
        canonical_pairs = []
        seen = set()
        for pair in pairs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("ISL model candidate pairs must be two-item tuples")
            first, second = pair
            self._physical_world.get_satellite(first)
            self._physical_world.get_satellite(second)
            if first == second:
                raise ValueError("ISL model returned a self-link candidate pair")
            canonical = _canonical_pair(first, second)
            if pair != canonical:
                raise ValueError("ISL model candidate pairs must be canonical")
            if canonical in seen:
                raise ValueError("ISL model returned a duplicate candidate pair")
            seen.add(canonical)
            canonical_pairs.append(canonical)
        return PhysicalLinkRequest(
            inter_satellite_pairs=tuple(canonical_pairs),
            include_ground_station_access=True,
            include_zone_service=True,
        )

    def _candidate_search_request(self, base, *, supports_ground_station_limits=False, retained_ground_station_pairs=(), retained_inter_satellite_pairs=()):
        # Models advise search breadth; Planner adds lifecycle retention and
        # admission coverage. Absence of advice means unrestricted candidates.
        isl_tail, ground_tail = self._physical_prediction_requirements()
        advise_ground = getattr(self._ground_station_model, "initial_candidate_search", None)
        search = (advise_ground(self._physical_world, minimum_contact_duration=ground_tail)
                  if supports_ground_station_limits and callable(advise_ground) else GroundStationCandidateSearch(None))
        if not isinstance(search, GroundStationCandidateSearch):
            raise TypeError("initial_candidate_search must return GroundStationCandidateSearch")
        advise_isl = getattr(self._inter_satellite_model, "candidate_neighbor_count", None)
        return replace(base,
            ground_station_minimum_contact_duration=ground_tail,
            inter_satellite_minimum_contact_duration=isl_tail,
            ground_station_candidate_limit=search.candidate_limit,
            retained_ground_station_pairs=tuple(retained_ground_station_pairs),
            cross_plane_neighbor_count=advise_isl(self._physical_world) if callable(advise_isl) else None,
            retained_inter_satellite_pairs=tuple(retained_inter_satellite_pairs))

    def _expanded_candidate_request(self, request, observation, state=None):
        limit = request.ground_station_candidate_limit
        model = self._ground_station_model
        expand = getattr(model, "expand_candidate_search", None)
        if limit is None or observation.ground_station_search_complete is True:
            return None
        if observation.ground_station_search_complete is None:
            # One unbounded query settles an unknown bounded-search result.
            return replace(request, ground_station_candidate_limit=None)
        if not callable(expand):
            return None
        if state is None:
            state = ConnectionSelectionState(observation.timestamp, self._observation_interval,
                self._configuration.ground_station_acquisition_duration, {}, frozenset())
        search = expand(self._physical_world,
            search=GroundStationCandidateSearch(limit),
            minimum_contact_duration=request.ground_station_minimum_contact_duration,
            state=state,
            returned_candidates=observation.ground_station_access_candidates)
        if search is None:
            return None
        if not isinstance(search, GroundStationCandidateSearch):
            raise TypeError("expand_candidate_search must return GroundStationCandidateSearch or None")
        if search.candidate_limit is not None and search.candidate_limit <= limit:
            raise ValueError("expanded candidate search must increase the candidate limit")
        return replace(request, ground_station_candidate_limit=search.candidate_limit)


def _canonical_pair(first, second):
    return tuple(sorted((first, second), key=lambda node: (node.asn, node.entity_kind, node.entity_id)))
