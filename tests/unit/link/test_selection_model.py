from collections import Counter
from itertools import combinations
from datetime import timedelta
import numpy as np

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, LinkCandidate
from skyseed.pc.link import ConnectionCandidate, LinkLayer, RetainFirstLinkSelectionModel
from skyseed.pc.link.ground_allocation import allocate_ground_links


def candidate(first, second, *, distance=1000, elevation=30, mutual=True):
    reports = {first: LinkCandidate(first, second, distance_m=distance, elevation_deg=elevation)}
    if mutual:
        reports[second] = LinkCandidate(second, first, distance_m=distance, elevation_deg=elevation)
    return ConnectionCandidate((first, second), reports)


def test_default_requires_mutual_reports_and_reserves_pending_capacity(epoch):
    scene = Scene()
    for satellite in ('a', 'b'):
        scene.create_satellite(satellite, position=ScenePositionSource('test/input', {}))
    scene.create_ground_station('g', latitude_deg=0, longitude_deg=0)
    layer = LinkLayer(PhysicalLayer(scene))
    layer.set_selection_hook(RetainFirstLinkSelectionModel, model_options={'ground_station_limit': 1})
    first = candidate('g', 'a', elevation=10)
    one_way = candidate('g', 'b', elevation=80, mutual=False)
    selected = layer.selection_model.select_links(scene, epoch, (first, one_way))
    assert selected == (first,)
    # Adopt a selection without making it operational: preparation still reserves it.
    from skyseed.pc.link import LinkConfiguration, LinkProperties
    layer.adopt_selection([LinkConfiguration(first, LinkProperties(first.id, 1e6, 0.01))])
    better = candidate('g', 'b', elevation=80)
    assert layer.selection_model.select_links(scene, epoch + timedelta(seconds=5), (first, better)) == (first,)
    assert layer.selection_model.select_links(scene, epoch + timedelta(seconds=10), (better,)) == (better,)


def test_same_and_adjacent_plane_budgets_use_scene_organization(epoch):
    scene = Scene()
    scene.create_constellation('c', planes=4, satellites_per_plane=4,
                               altitude_km=550, inclination_deg=53, epoch=epoch)
    layer = LinkLayer(PhysicalLayer(scene))
    nodes = scene.nodes
    candidates = tuple(candidate(first.node_id, second.node_id,
                                 distance=100 + index)
                       for index, (first, second) in enumerate(combinations(nodes, 2)))
    selected = layer.selection_model.select_links(scene, epoch, candidates)
    used = Counter()
    orbits = sorted((group for group in scene.groups if group.kind == 'orbit'), key=lambda orbit: orbit.raan_deg)
    adjacent = {frozenset((orbit.group_id, orbits[(index + 1) % 4].group_id))
                for index, orbit in enumerate(orbits)}
    for link in selected:
        first, second = [scene.get_node(node) for node in link.id]
        kind = 'same' if first.orbit is second.orbit else 'adjacent'
        if kind == 'adjacent':
            assert frozenset((first.orbit.group_id, second.orbit.group_id)) in adjacent
        for node in link.id:
            used[node, kind] += 1
    assert selected and max(used.values()) <= 2


def test_ground_allocator_matches_exhaustive_lexicographic_optimum():
    rng = np.random.default_rng(810)
    all_pairs = [(ground, satellite) for ground in ('g0', 'g1', 'g2') for satellite in ('s0', 's1', 's2')]
    for _ in range(25):
        edges = [(ground, satellite, int(rng.integers(0, 91)), (ground, satellite))
                 for ground, satellite in all_pairs if rng.random() < 0.65]
        ground_capacity = {node: int(rng.integers(1, 3)) for node in ('g0', 'g1', 'g2')}
        satellite_capacity = {node: int(rng.integers(1, 3)) for node in ('s0', 's1', 's2')}
        covered_ground = {'g0'} if rng.random() < 0.5 else set()
        covered_satellites = {'s0'} if rng.random() < 0.5 else set()
        quality = {edge[3]: edge[2] for edge in edges}

        def score(pairs):
            return (len({g for g, s in pairs} - covered_ground),
                    len({s for g, s in pairs} - covered_satellites),
                    len(pairs), sum(quality[pair] for pair in pairs))

        expected = (0, 0, 0, 0)
        for bits in range(1 << len(edges)):
            chosen = [edge[3] for index, edge in enumerate(edges) if bits & (1 << index)]
            ground_used = Counter(g for g, s in chosen)
            satellite_used = Counter(s for g, s in chosen)
            if (all(count <= ground_capacity[node] for node, count in ground_used.items())
                    and all(count <= satellite_capacity[node] for node, count in satellite_used.items())):
                expected = max(expected, score(chosen))
        selected = allocate_ground_links(edges, ground_capacity, satellite_capacity,
                                         covered_ground, covered_satellites)
        assert score(selected) == expected


def test_ground_coverage_requires_reassignment_not_greedy_elevation():
    edges = [('g1', 's1', 89, ('g1', 's1')), ('g1', 's2', 10, ('g1', 's2')),
             ('g2', 's1', 80, ('g2', 's1'))]
    chosen = allocate_ground_links(edges, {'g1': 1, 'g2': 1}, {'s1': 1, 's2': 1}, set(), set())
    assert set(chosen) == {('g1', 's2'), ('g2', 's1')}
