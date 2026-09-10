"""Focused reference checks for the authorized finite computation round."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import combinations
from random import Random

import pytest

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._durable_evidence import _OrderedEvidenceWriter, _read_jsonl, _reset_jsonl
from skyseed_v2.simulator._ground_station_allocation import (
    _GroundAccessAllocationPreference, _GroundAccessChoice,
    _allocate_continuous_ground_station_accesses,
)


def test_retained_isl_reuse_matches_fresh_policy_under_pool_and_distance_changes():
    from test_network_planner_semantic_core import _world, _satellite, _isl_candidate
    from skyseed_v2.simulator import RetainFirstInterSatelliteConnectionPlanningModel
    world, satellites, _ = _world(satellites=tuple(_satellite(i, capability=1) for i in range(4)))
    pool = tuple(_isl_candidate(a, b) for a, b in combinations(satellites, 2))
    model = RetainFirstInterSatelliteConnectionPlanningModel()
    retained = frozenset()
    cases = (pool, tuple(replace(c, distance_m=10000-i*50) for i, c in enumerate(pool)),
             pool[:-1], pool[:2], pool)
    reused = False
    for candidates in cases:
        actual = model.select_inter_satellite_links(world, candidates, retained)
        reference = RetainFirstInterSatelliteConnectionPlanningModel().select_inter_satellite_links(world, candidates, retained)
        assert actual.selected_candidates == reference.selected_candidates
        assert set(actual.decisions) == set(reference.decisions)
        assert actual.underfilled_slots == reference.underfilled_slots
        reused |= bool(model._selection_work['isl_retained_selection_reused'])
        retained = frozenset((c.endpoint_a, c.endpoint_b) for c in actual.selected_candidates)
    assert reused


def test_coverage_continuity_quality_matches_exhaustive_small_assignments():
    random = Random(41)
    stations = tuple(NodeId(1, 'ground-station', str(i)) for i in range(3))
    satellites = tuple(NodeId(1, 'satellite', str(i)) for i in range(3))
    all_pairs = tuple((g, s) for g in stations for s in satellites)
    for _ in range(180):
        feasible = {pair for pair in all_pairs if random.random() < .7}
        retained = frozenset(pair for pair in all_pairs if random.random() < .3)
        limits = {g: random.choice((0, 1, 2, 4, 8)) for g in stations}
        capacities = {s: random.choice((None, 0, 1, 2)) for s in satellites}
        rows = tuple(_GroundAccessAllocationPreference(g, limits[g], tuple(
            _GroundAccessChoice(float(90-i), s) for i, s in enumerate(satellites) if (g, s) in feasible
        )) for g in stations)
        ranks = {(row.ground_station_id, c.satellite_id): rank * 4 + satellites.index(c.satellite_id)
                 for row in rows for rank, c in enumerate(row.candidates)}
        def score(pairs):
            return (-len({g for g, _ in pairs}), -len({s for _, s in pairs}), -len(pairs), len(pairs ^ retained), sum(ranks[p] for p in pairs))
        legal = []
        pairs = sorted(feasible, key=all_pairs.index)
        for mask in range(1 << len(pairs)):
            chosen = frozenset(pair for i, pair in enumerate(pairs) if mask & (1 << i))
            if all(sum(g == station for g, _ in chosen) <= limits[station] for station in stations) and all(
                capacities[satellite] is None or sum(s == satellite for _, s in chosen) <= capacities[satellite] for satellite in satellites
            ):
                legal.append(score(chosen))
        actual = _allocate_continuous_ground_station_accesses(rows, capacities, retained)
        chosen = frozenset((g, s) for g, values in actual.items() for s in values)
        assert score(chosen) == min(legal)
        reordered = tuple(replace(row, candidates=tuple(reversed(row.candidates))) for row in reversed(rows))
        assert _allocate_continuous_ground_station_accesses(reordered, capacities, retained) == actual


def test_retained_flow_prefix_still_replaces_incumbent_to_restore_coverage():
    stations = tuple(NodeId(1, 'ground-station', str(i)) for i in range(3))
    satellites = tuple(NodeId(1, 'satellite', str(i)) for i in range(3))
    candidates = ((0, 2), (1,), (0,))
    rows = tuple(_GroundAccessAllocationPreference(g, 1, tuple(
        _GroundAccessChoice(float(90-rank), satellites[index])
        for rank, index in enumerate(choices)
    )) for g, choices in zip(stations, candidates))
    retained = frozenset(((stations[0], satellites[0]), (stations[1], satellites[1])))
    work = {}
    result = _allocate_continuous_ground_station_accesses(
        rows, dict.fromkeys(satellites, 1), retained, work=work,
    )
    assert result == {stations[0]: frozenset((satellites[2],)),
                      stations[1]: frozenset((satellites[1],)),
                      stations[2]: frozenset((satellites[0],))}
    assert work['gs_seeded_retained_assignments'] == 2
    assert work['gs_residual_cycle_corrections'] > 0
    # Without the spare endpoint, coverage cannot grow; a better ranked new
    # station cannot justify dropping an incumbent at the same coverage.
    blocked = (replace(rows[0], candidates=rows[0].candidates[:1]), *rows[1:])
    result = _allocate_continuous_ground_station_accesses(
        blocked, dict.fromkeys(satellites, 1), retained, work=work,
    )
    assert result == {stations[0]: frozenset((satellites[0],)),
                      stations[1]: frozenset((satellites[1],)), stations[2]: frozenset()}
    assert work['gs_retained_optimum'] == 1  # exact residual certificate handles shortage


def test_route_reuse_invalidates_only_relevant_ground_access_dependencies():
    from types import MappingProxyType
    from skyseed_v2.simulator._routing_core import (
        _RoutingCoreContext, _required_ground_next_hops, _required_zone_next_hops,
    )
    from skyseed_v2.simulator._route_computation import _GroupedRouteEngine, _RoutePathRequest

    satellites = tuple(NodeId(1, 'satellite', str(i)) for i in range(3))
    grounds = tuple(NodeId(1, 'ground-station', str(i)) for i in range(2))
    a, b, c = satellites
    g, h = grounds
    def context(access):
        graph = {node: [] for node in (*satellites, *grounds)}
        for left, right in ((a, b), (b, c), (g, access), (h, c)):
            graph[left].append(right)
            graph[right].append(left)
        return _RoutingCoreContext(frozenset(graph), frozenset(satellites), frozenset(grounds),
            MappingProxyType({node: tuple(sorted(neighbors, key=lambda n: (n.asn, n.entity_kind, n.entity_id)))
                              for node, neighbors in graph.items()}))
    requests = (_RoutePathRequest(0, True, c, g), _RoutePathRequest(1, True, a, h),
                _RoutePathRequest(2, False, g, b), _RoutePathRequest(3, False, h, b))
    engine = _GroupedRouteEngine()
    try:
        first = engine.compute(context(a), requests)
        changed_context = context(b)
        actual = engine.compute(changed_context, tuple(reversed(requests)))
        assert engine.last_compute_observation.reused_request_count == 2
        reference = engine.compute(changed_context, requests,
            choice_resolvers=(_required_ground_next_hops, _required_zone_next_hops))
        assert actual == reference
        assert actual[0] != first[0] and actual[2] != first[2]
        assert actual[1] == first[1] and actual[3] == first[3]
    finally:
        engine.shutdown()


def test_evidence_queue_order_bound_and_sticky_failure(tmp_path):
    path = tmp_path / 'records.jsonl.gz'
    _reset_jsonl(path, {'header': 1})
    writer = _OrderedEvidenceWriter(maximum_pending=2)
    for i in range(11):
        writer.submit(writer.append, path, {'index': i})
    writer.finish()
    assert writer.peak_pending <= 2
    assert _read_jsonl(path) == ({'header': 1}, *({'index': i} for i in range(11)))
    failed = _OrderedEvidenceWriter(1)
    def fail():
        raise OSError('required serialization failed')
    failed.submit(fail)
    with pytest.raises(OSError, match='required serialization'):
        failed.finish()
    with pytest.raises(OSError, match='required serialization'):
        failed.finish()


def test_batched_geometry_and_contact_proofs_against_dense_scalar_reference():
    from skyseed_v2.simulator import CircularOrbitPositionModel, SimulationDefinition, SimulationTimeline, Simulator
    from skyseed_v2.simulator.input import SatelliteSimulationInput, GroundStationSimulationInput, SimulatorStaticInput
    from skyseed_v2.simulator.models import GeometricPhysicalLinkModel
    from skyseed_v2.simulator.physical import PhysicalLinkRequest
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    satellites = tuple(SatelliteSimulationInput(
        node_id=NodeId(1, 'satellite', str(i)), satellite_id=str(i), shell_id='shell',
        orbit_id=str(i//2), orbit_index=i%2, altitude_km=570, inclination_deg=70,
        right_ascension_deg=20*(i//2), mean_anomaly_deg=2*(i%2), epoch_utc=epoch,
        maximum_simultaneous_inter_satellite_links=4,
    ) for i in range(4))
    ground = GroundStationSimulationInput(node_id=NodeId(1, 'ground-station', 'g'),
        ground_station_id='g', latitude_deg=0, longitude_deg=0, elevation_m=0,
        station_type='gateway', operator='test')
    model = CircularOrbitPositionModel()
    world = Simulator(SimulationDefinition(SimulatorStaticInput(satellites, (ground,)),
        SimulationTimeline(epoch, epoch+timedelta(seconds=6000), timedelta(seconds=30)))).create_physical_world(position_model=model)
    links = GeometricPhysicalLinkModel(maximum_isl_distance_m=1_000_000,
        ground_station_minimum_elevation_deg=10, ground_station_maximum_distance_m=None,
        zone_coverage_minimum_elevation_deg=10, propagation_speed_m_per_s=299_792_458)
    request = PhysicalLinkRequest(tuple(combinations((s.node_id for s in satellites), 2)),
        include_ground_station_access=True, include_zone_service=False)
    fingerprint = world.semantic_fingerprint()
    counts = links.predict_contacts(world, request, start_timestamp=epoch,
        end_timestamp=epoch+timedelta(seconds=6000), observation_interval=timedelta(seconds=30),
        inter_satellite_tail=timedelta(seconds=185), ground_station_tail=timedelta(seconds=65))
    core = links._core
    prediction = core._prediction
    assert 0 < counts['stable_isl_proofs'] < len(request.inter_satellite_pairs)
    assert counts['prediction_frames'] == 207
    cross_feasibility, proven = set(), 0
    for seconds in range(0, 6186, 10):
        timestamp = epoch + timedelta(seconds=seconds)
        frame = world.frame_at(timestamp)
        scalar = core._compute_scalar(timestamp, frame.positions, request)
        batched = links.compute_observation(world, frame, request)
        for name in ('inter_satellite_candidates', 'ground_station_access_candidates'):
            left, right = getattr(scalar, name), getattr(batched, name)
            assert len(left) == len(right)
            for a, b in zip(left, right):
                assert a.propagation_delay_us == b.propagation_delay_us
                assert a.distance_m == pytest.approx(b.distance_m, abs=1e-7)
        feasible = {(c.endpoint_a, c.endpoint_b) for c in scalar.inter_satellite_candidates}
        cross_feasibility.add(request.inter_satellite_pairs[1] in feasible)
        for key, pair in enumerate(request.inter_satellite_pairs):
            window = prediction.window('isl', key, timestamp)
            if window is not None:
                proven += 1
                assert pair in feasible
        feasible_ground = {(c.satellite_id, c.ground_station_id) for c in scalar.ground_station_access_candidates}
        for key, satellite in enumerate(core._batch.satellites):
            window = prediction.window('ground', key, timestamp)
            if window is not None:
                assert (satellite, ground.node_id) in feasible_ground
    assert cross_feasibility == {False, True}
    assert proven > 0
    assert world.semantic_fingerprint() == fingerprint
    assert model.semantic_fingerprint_material() == {}


def test_empty_contact_prediction_and_invalid_time_bounds():
    from skyseed_v2.simulator import CircularOrbitPositionModel, SimulationDefinition, SimulationTimeline, Simulator
    from skyseed_v2.simulator.input import SimulatorStaticInput
    from skyseed_v2.simulator.models import GeometricPhysicalLinkModel
    from skyseed_v2.simulator.physical import PhysicalLinkRequest

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start+timedelta(seconds=5)
    world = Simulator(SimulationDefinition(SimulatorStaticInput(), SimulationTimeline(
        start, end, timedelta(seconds=5)))).create_physical_world(position_model=CircularOrbitPositionModel())
    model = GeometricPhysicalLinkModel(maximum_isl_distance_m=1e6,
        ground_station_minimum_elevation_deg=10, ground_station_maximum_distance_m=None,
        zone_coverage_minimum_elevation_deg=10, propagation_speed_m_per_s=3e8)
    arguments = dict(start_timestamp=start, end_timestamp=end, observation_interval=timedelta(seconds=5),
                     inter_satellite_tail=timedelta(0), ground_station_tail=timedelta(0))
    assert set(model.predict_contacts(world, PhysicalLinkRequest(), **arguments).values()) == {0}
    for invalid in ({'observation_interval': timedelta(0)}, {'end_timestamp': start-timedelta(seconds=1)},
                    {'inter_satellite_tail': timedelta(seconds=-1)}, {'ground_station_tail': timedelta(seconds=-1)}):
        with pytest.raises(ValueError):
            model.predict_contacts(world, PhysicalLinkRequest(), **(arguments | invalid))


def test_route_component_reuse_shortcuts_merges_splits_and_multiple_accesses():
    from types import MappingProxyType
    from skyseed_v2.simulator._routing_core import _RoutingCoreContext
    from skyseed_v2.simulator._route_computation import _GroupedRouteEngine, _RoutePathRequest
    sats = tuple(NodeId(1, 'satellite', str(i)) for i in range(7))
    gs = tuple(NodeId(1, 'ground-station', str(i)) for i in range(2))
    a,b,c,d,e,f,g = sats
    x,y = gs
    base = {(a,b),(b,c),(c,d),(e,f),(f,g),(x,a),(y,e)}
    requests = (_RoutePathRequest(0, False, x, d), _RoutePathRequest(1, False, y, g),
                _RoutePathRequest(2, True, d, x), _RoutePathRequest(3, True, g, y))
    engine = _GroupedRouteEngine()
    try:
        for index, edges in enumerate((base, base|{(a,d)}, base|{(d,e)}, base,
                                       base|{(x,f)}, base-{(b,c)}, base)):
            graph = {n: set() for n in (*sats,*gs)}
            for left,right in edges:
                graph[left].add(right); graph[right].add(left)
            context = _RoutingCoreContext(frozenset(graph), frozenset(sats), frozenset(gs),
                MappingProxyType({n:tuple(sorted(v,key=lambda n:n.value)) for n,v in graph.items()}))
            actual = engine.compute(context, requests)
            if index == 1:
                assert engine.last_compute_observation.reused_request_count == 2
                assert actual[0] == (x,a,d)
            cold = _GroupedRouteEngine()
            try:
                assert actual == cold.compute(context, requests)
            finally:
                cold.shutdown()
    finally:
        engine.shutdown()
