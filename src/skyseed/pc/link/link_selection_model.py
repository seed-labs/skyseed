"""Connection selection contract and the retain-first reference policy."""
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, Sequence

from skyseed.scene import Scene
from .link_values import ConnectionCandidate
from .ground_allocation import allocate_ground_links

if TYPE_CHECKING:
    from .link_layer import LinkLayer


class LinkSelectionModel(Protocol):
    layer: "LinkLayer"  # Bound by the layer after construction.

    def select_links(
        self, scene: Scene, timestamp: datetime, candidates: Sequence[ConnectionCandidate]
    ) -> Sequence[ConnectionCandidate]: ...


class RetainFirstLinkSelectionModel:
    """Retain feasible incumbents, then fill ISL and GS capacity.

    Default ISLs require Scene orbit organization: two same-plane slots and
    two adjacent-plane slots. TLE-only nodes need a custom ISL selection policy.
    GS defaults retain the old four-links-per-GS engineering baseline; satellite
    GS capacity is unlimited unless configured. Both limits belong to this model.
    """
    def __init__(self, *, same_plane_limit=2, adjacent_plane_limit=2,
                 ground_station_limit=4, satellite_ground_limit=None):
        for name, value in (("same_plane_limit", same_plane_limit),
                            ("adjacent_plane_limit", adjacent_plane_limit),
                            ("ground_station_limit", ground_station_limit),
                            ("satellite_ground_limit", satellite_ground_limit)):
            if value is None and name == "satellite_ground_limit":
                continue
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        self.same_plane_limit = same_plane_limit
        self.adjacent_plane_limit = adjacent_plane_limit
        self.ground_station_limit = ground_station_limit
        self.satellite_ground_limit = satellite_ground_limit
        self._organization = None

    def _prepare(self, scene):
        # Entity membership is fixed; adding nodes changes this small signature.
        signature = (scene, len(scene.nodes), len(scene.groups))
        if self._organization == signature:
            return
        self._nodes = {node.node_id: node for node in scene.nodes}
        shells = {}
        for group in scene.groups:
            if group.kind == "orbit":
                shells.setdefault(group.shell.group_id, []).append(group)
        self._adjacent = set()
        for orbits in shells.values():
            orbits.sort(key=lambda orbit: (orbit.raan_deg, orbit.group_id))
            if len(orbits) < 2:
                continue
            for index, orbit in enumerate(orbits):
                other = orbits[(index + 1) % len(orbits)]
                self._adjacent.add(frozenset((orbit.group_id, other.group_id)))
        self._organization = signature

    def _kind(self, candidate):
        first, second = (self._nodes[node] for node in candidate.endpoints)
        if first.kind != second.kind:
            return "ground"
        if first.kind != "satellite" or first.orbit is None or second.orbit is None:
            return None
        if first.orbit is second.orbit:
            return "same"
        if frozenset((first.orbit.group_id, second.orbit.group_id)) in self._adjacent:
            return "adjacent"
        return None

    def select_links(self, scene, timestamp, candidates):
        self._prepare(scene)
        feasible = {}
        kinds = {}
        for candidate in candidates:
            if candidate.bidirectional:
                kind = self._kind(candidate)
                if kind is not None:
                    feasible[candidate.id] = candidate
                    kinds[candidate.id] = kind
        selected = []
        selected_ids = set()
        used = Counter()

        def limit(node_id, kind):
            if kind == "same":
                return self.same_plane_limit
            if kind == "adjacent":
                return self.adjacent_plane_limit
            if self._nodes[node_id].kind == "ground_station":
                return self.ground_station_limit
            return self.satellite_ground_limit

        def adopt(candidate):
            kind = kinds[candidate.id]
            for node_id in candidate.endpoints:
                capacity = limit(node_id, kind)
                if capacity is not None and used[node_id, kind] >= capacity:
                    return False
            selected.append(candidate)
            selected_ids.add(candidate.id)
            for node_id in candidate.endpoints:
                used[node_id, kind] += 1
            return True

        # This uses the generator's adopted selection, not the last trial query.
        for identity in self.layer.selected_links:
            if identity in feasible:
                adopt(feasible[identity])

        distances = {}
        missing = []
        for identity, candidate in feasible.items():
            if identity in selected_ids or kinds[identity] == "ground":
                continue
            values = [report.distance_m for report in candidate.reports.values()
                      if report.distance_m is not None]
            if values:
                distances[identity] = min(values)
            else:
                missing.append(candidate)
        if missing:
            from math import dist
            node_ids = {node for candidate in missing for node in candidate.endpoints}
            snapshot = self.layer.physical.snapshot_at(timestamp, sorted(node_ids))
            for candidate in missing:
                first, second = candidate.endpoints
                distances[candidate.id] = dist(snapshot[first].position_m, snapshot[second].position_m)
        for identity in sorted(distances, key=lambda identity: (distances[identity], identity)):
            adopt(feasible[identity])

        ground_capacity = {}
        satellite_capacity = {}
        edges = []
        for identity, candidate in feasible.items():
            if kinds[identity] != "ground" or identity in selected_ids:
                continue
            ground, satellite = candidate.endpoints
            if self._nodes[ground].kind == "satellite":
                ground, satellite = satellite, ground
            ground_left = self.ground_station_limit - used[ground, "ground"]
            satellite_left = len(feasible)
            if self.satellite_ground_limit is not None:
                satellite_left = self.satellite_ground_limit - used[satellite, "ground"]
            if ground_left <= 0 or satellite_left <= 0:
                continue
            ground_capacity[ground] = ground_left
            satellite_capacity[satellite] = satellite_left
            elevation = candidate.reports[ground].elevation_deg
            if elevation is not None and not -90 <= elevation <= 90:
                raise ValueError(f"connection {identity}: elevation must be between -90 and 90 degrees")
            # Unknown elevation supplies no preference, not a fabricated value
            # in the physical report. Coverage and capacity still apply.
            quality = -90 if elevation is None else elevation
            edges.append((ground, satellite, quality, candidate))
        covered = {node for (node, kind), count in used.items() if kind == "ground" and count}
        selected.extend(allocate_ground_links(
            edges, ground_capacity, satellite_capacity, covered, covered
        ))
        return tuple(selected)
