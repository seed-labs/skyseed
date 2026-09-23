"""Greedy capacity-aware selection over an author's static potential pair scope.

Inject inter_satellite_model=SimpleInterSatelliteModel(pairs). This small policy
keeps incumbents first but makes no future-service or optimality claim.

Use world.get_satellite(node_id) for static capabilities; state.selected_since
includes preparing reservations, while state.operational_connections holds IDs
already operational. Reference: docs/api/planning.md (selection state) and
docs/api/physical.md (entities).
"""
from collections import Counter
from skyseed.simulator import planner


class SimpleInterSatelliteModel:
    def __init__(self, pairs):
        self.pairs = tuple(sorted({tuple(sorted(pair, key=lambda n: (n.asn, n.entity_kind, n.entity_id)))
                                   for pair in pairs}, key=lambda p: tuple(n.value for n in p)))

    def create_inter_satellite_candidate_pairs(self, physical_world):
        return self.pairs  # Potential scope, not a per-timestamp selection.

    def select_inter_satellite_links(self, physical_world, candidates, state):
        def identity(candidate):
            return planner.OperationalConnectionId(planner.OperationalConnectionKind.INTER_SATELLITE,
                candidate.endpoint_a, candidate.endpoint_b)
        degree, selected = Counter(), []
        for candidate in sorted(candidates, key=lambda c: identity(c) not in state.selected_since):
            endpoints = candidate.endpoint_a, candidate.endpoint_b
            capacities = []
            for node in endpoints:
                capability = physical_world.get_satellite(node).inter_satellite_link_capability
                if capability is None:
                    raise ValueError("SimpleInterSatelliteModel requires declared ISL capacity")
                capacities.append(capability.maximum_simultaneous_links)
            if all(degree[node] < limit for node, limit in zip(endpoints, capacities)):
                selected.append(candidate)
                degree.update(endpoints)  # Preparing selections reserve capacity too.
        return planner.InterSatelliteSelection(tuple(selected))
