"""Capacity policy and search scope do not redefine physical capabilities."""
from tests._support import selection_state
from dataclasses import replace
from datetime import timedelta
import pytest

from skyseed.simulator.planner import CoverageFirstGroundStationAccessPlanningModel, RetainFirstInterSatelliteConfiguration, RetainFirstInterSatelliteConnectionPlanningModel
from skyseed.simulator.physical import PhysicalLinkRequest
from test_network_planner_semantic_core import _world, _satellite, _station, _ground_candidate, _planner, _observation, EPOCH


@pytest.mark.parametrize('capacity', [None, 2, 4, 8])
def test_explicit_station_capability_overrides_fallback_and_policy_is_separate(capacity):
    world, satellites, stations = _world(satellites=tuple(_satellite(i) for i in range(8)),
        stations=(replace(_station(), max_simultaneous_satellite_accesses=capacity),))
    candidates = tuple(_ground_candidate(stations[0], satellite) for satellite in satellites)
    model = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=4)
    assert len(model.select_ground_station_accesses(world, candidates, state=selection_state())) == (4 if capacity is None else capacity)
    ceiling = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=4, ground_station_policy_limit=2)
    assert len(ceiling.select_ground_station_accesses(world, candidates, state=selection_state())) == 2


@pytest.mark.parametrize('size', [1, 2, 3, 5])
def test_cyclic_same_plane_scope_uses_anomaly_not_names(size):
    inputs = tuple(replace(_satellite(i), mean_anomaly_deg=((i*2)%size)*360/size) for i in range(size)) if size % 2 else tuple(_satellite(i) for i in range(size))
    world, satellites, _ = _world(satellites=inputs)
    model = RetainFirstInterSatelliteConnectionPlanningModel(RetainFirstInterSatelliteConfiguration(
        same_plane_neighbor_span=1, maximum_cross_plane_separation=1))
    pairs = model.create_inter_satellite_candidate_pairs(world)
    ordered = sorted(satellites, key=lambda value: value.mean_anomaly_deg)
    expected = {frozenset((s.node_id, ordered[(i+1)%size].node_id)) for i,s in enumerate(ordered) if size > 1}
    assert {frozenset(p) for p in pairs} == expected
    wide = RetainFirstInterSatelliteConnectionPlanningModel(RetainFirstInterSatelliteConfiguration(same_plane_neighbor_span=None))
    assert len(wide.create_inter_satellite_candidate_pairs(world)) == size*(size-1)//2


def test_ground_search_expands_for_competition_and_keeps_reservations():
    world, satellites, stations = _world(satellites=tuple(_satellite(i) for i in range(4)))
    model = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=4, initial_candidate_count=1)
    planner, _ = _planner(world, ground_station_model=model)
    try:
        request = planner._candidate_search_request(PhysicalLinkRequest(), supports_ground_station_limits=True)
        observation = _observation(EPOCH, ground=(_ground_candidate(stations[0], satellites[0]),))
        observation = replace(observation, ground_station_search_complete=False, work_counts=None)
        expanded = planner._expanded_candidate_request(request, observation)
        assert expanded.ground_station_candidate_limit == 2
        planner.observe(world.frame_at(EPOCH), observation)
        next_request = planner._candidate_search_request(PhysicalLinkRequest(), supports_ground_station_limits=True)
        assert (stations[0].node_id, satellites[0].node_id) in next_request.retained_ground_station_pairs
    finally:
        planner.shutdown()
