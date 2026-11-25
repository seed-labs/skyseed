from datetime import datetime, timezone
from math import sqrt
import numpy as np
import pytest

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, GeometricCandidateModel

TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class SpecifiedPositions:
    def compute_positions(self, scene, satellite_ids, timestamp):
        return {identity: NodeState(scene.get_node(identity).position_source.data['position'])
                for identity in satellite_ids}


def physical_at(points, *, ground=True, **options):
    scene = Scene()
    for identity, position in points.items():
        scene.create_satellite(identity, position=ScenePositionSource('test/ecef', {'position':list(position)}))
    if ground:
        scene.create_ground_station('ground', latitude_deg=0, longitude_deg=0)
    physical = PhysicalLayer(scene)
    physical.set_position_model(SpecifiedPositions)
    physical.set_candidate_model(GeometricCandidateModel, model_options=options)
    return physical


def test_overhead_opposite_and_distance_limits():
    physical = physical_at({'above':(6928137, 0, 0), 'far':(-6928137, 0, 0),
                            'near':(6928137, 100000, 0)}, max_isl_distance_m=200000)
    frame = physical.snapshot_at(TIME)
    reports = physical.candidates(frame)
    assert [c.peer_id for c in reports['above']] == ['near', 'ground']
    assert reports['far'] == ()
    overhead = next(c for c in reports['ground'] if c.peer_id == 'above')
    assert overhead.distance_m == 550000
    assert overhead.propagation_delay_s == pytest.approx(550000 / 299792458)
    assert overhead.elevation_deg == 90
    assert reports['above'][0].elevation_deg is None
    physical.set_candidate_model(GeometricCandidateModel, model_options={'max_ground_distance_m':500000})
    assert physical.candidates(frame, ['ground'])['ground'] == ()


def test_elevation_and_below_surface_visibility():
    physical = physical_at({'overhead':(6928137, 0, 0), 'low':(6478137, 1000000, 0),
                            'buried':(6378136, 0, 0)}, min_ground_elevation_deg=10)
    assert [c.peer_id for c in physical.candidates_at(TIME, ['ground'])['ground']] == ['overhead']
    physical.set_candidate_model(GeometricCandidateModel, model_options={'min_ground_elevation_deg':0})
    assert [c.peer_id for c in physical.candidates_at(TIME, ['ground'])['ground']] == ['overhead', 'low']


def test_earth_obstruction_even_inside_isl_range():
    radius = 6_928_137
    # Opposite satellites fit this deliberately large range, but Earth blocks.
    physical = physical_at({'a':(radius,0,0), 'b':(-radius,0,0)},
                           ground=False, max_isl_distance_m=20_000_000)
    assert physical.candidates_at(TIME)['a'] == ()


def test_subset_is_actual_peer_universe():
    physical = physical_at({'a':(6928137, 0, 0), 'b':(6928137, 1000, 0)})
    result = physical.candidates_at(TIME, ['a'], snapshot_ids=['a','ground'])
    assert tuple(result) == ('a',)
    assert [c.peer_id for c in result['a']] == ['ground']
    with pytest.raises(ValueError, match='missing from snapshot'):
        physical.candidates_at(TIME, ['b'], snapshot_ids=['a'])
    assert physical.candidates_at(TIME, snapshot_ids=[]) == {}


def test_sparse_search_matches_independent_segment_intersection():
    rng = np.random.default_rng(82)
    directions = rng.normal(size=(120,3))
    directions /= np.linalg.norm(directions, axis=1)[:,None]
    positions = directions * rng.uniform(6.8e6, 8e6, size=(120,1))
    points = {f's{i}':p for i,p in enumerate(positions)}
    physical = physical_at(points, ground=False, max_isl_distance_m=3e6)
    actual = physical.candidates_at(TIME)
    axes = np.array([6378137,6378137,6356752.314245])
    for i, first in enumerate(positions):
        expected = set()
        for j, second in enumerate(positions):
            if i == j or np.linalg.norm(second-first) > 3e6:
                continue
            # Solve the segment/ellipsoid quadratic rather than repeating the
            # implementation's closest-point algorithm or spatial search.
            start, delta = first/axes, (second-first)/axes
            a, b, c = np.dot(delta,delta), 2*np.dot(start,delta), np.dot(start,start)-1
            discriminant = b*b-4*a*c
            blocked = discriminant > 0 and any(0 < t < 1 for t in
                ((-b-sqrt(discriminant))/(2*a),(-b+sqrt(discriminant))/(2*a)))
            if not blocked:
                expected.add(f's{j}')
        assert {c.peer_id for c in actual[f's{i}']} == expected


@pytest.mark.parametrize('ground', [False, True])
def test_high_orbit_does_not_expand_short_range_isl_search(monkeypatch, ground):
    from skyseed.pc.physical.link_candidate_model import _SpatialGrid

    points = {}
    for index in range(128):
        z = 1 - 2 * (index + 0.5) / 128
        angle = index * np.pi * (3 - sqrt(5))
        horizontal = sqrt(1 - z * z)
        direction = np.array([
            horizontal * np.cos(angle),
            horizontal * np.sin(angle),
            z,
        ])
        points[f'low-{index}'] = 6_928_137 * direction
    points['high'] = (42_164_137, 0, 0)

    # Count points actually handed to distance filtering, not wall-clock time.
    # A high satellite must not turn a short-range ISL query into a full scan.
    examined_points = 0
    original_near = _SpatialGrid.near

    def counted_near(grid, point):
        nonlocal examined_points
        nearby = original_near(grid, point)
        examined_points += len(nearby)
        return nearby

    monkeypatch.setattr(_SpatialGrid, 'near', counted_near)
    physical = physical_at(points, ground=ground, max_isl_distance_m=1_000_000)
    reports = physical.candidates_at(TIME)

    for node_id in points:
        assert all(candidate.peer_id == 'ground' for candidate in reports[node_id])
    if ground:
        assert [candidate.peer_id for candidate in reports['high']] == ['ground']
        assert 'high' in {candidate.peer_id for candidate in reports['ground']}
    assert examined_points < len(points) ** 2 // 4


@pytest.mark.parametrize('options', [{'max_isl_distance_m':0}, {'max_ground_distance_m':-1},
                                    {'min_ground_elevation_deg':91}])
def test_invalid_geometry_options(options):
    with pytest.raises(ValueError):
        GeometricCandidateModel(**options)
