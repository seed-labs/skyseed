"""Per-node candidate contract and the built-in geometric candidate model."""
from itertools import product
from math import isfinite
from typing import Protocol, Sequence

import numpy as np

from skyseed.scene import Scene
from .physical_results import LinkCandidate, PhysicalPositionSnapshot
from .earth_geometry import WGS84_B_M, LIGHT_SPEED_M_S, earth_visible, ground_up


class NodeLinkCandidateModel(Protocol):
    """Return one node's communication opportunities within a position snapshot."""

    def compute_candidates(
        self,
        scene: Scene,
        snapshot: PhysicalPositionSnapshot,
        node_id: str,
    ) -> Sequence[LinkCandidate]: ...


class _SpatialGrid:
    def __init__(self, positions, indices, cell_size):
        self.cell_size = cell_size
        self.cells = {}
        for index in indices:
            cell = tuple(np.floor(positions[index] / cell_size).astype(np.int64))
            self.cells.setdefault(cell, []).append(index)

    def near(self, point):
        # Cell width is at least every query radius. Searching 27 neighbours
        # therefore cannot lose a pair; exact distance is checked afterwards.
        cell = np.floor(point / self.cell_size).astype(np.int64)
        nearby = []
        for offset in product((-1, 0, 1), repeat=3):
            neighbour = tuple(cell + offset)
            nearby.extend(self.cells.get(neighbour, ()))
        return np.array(nearby, dtype=int)


class GeometricCandidateModel:
    """WGS84 visibility, range, ground elevation and one-way light time.

    Defaults are configurable engineering baselines, not hardware guarantees.
    No terminal allocation, bandwidth or random loss is assigned here.
    """
    def __init__(
        self,
        *,
        max_isl_distance_m=6_000_000.0,
        min_ground_elevation_deg=5.0,
        max_ground_distance_m=None,
    ):
        for name, value in (
            ("max_isl_distance_m", max_isl_distance_m),
            ("max_ground_distance_m", max_ground_distance_m),
        ):
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite")
        if max_isl_distance_m is None:
            raise ValueError("max_isl_distance_m is required")
        if not isfinite(min_ground_elevation_deg) or not 0 <= min_ground_elevation_deg <= 90:
            raise ValueError("min_ground_elevation_deg must be between 0 and 90")
        self.max_isl_distance_m = max_isl_distance_m
        self.min_ground_elevation_deg = min_ground_elevation_deg
        self.max_ground_distance_m = max_ground_distance_m
        self._snapshot = None

    def _prepare(self, scene, snapshot):
        if self._snapshot is snapshot:
            return
        identities = tuple(snapshot.states)
        positions = np.array([
            snapshot[node_id].position_m for node_id in identities
        ]).reshape((-1, 3))
        nodes = [scene.get_node(node_id) for node_id in identities]
        satellite = np.array([node.kind == "satellite" for node in nodes], dtype=bool)
        satellite_rows = np.flatnonzero(satellite)
        ground_rows = np.flatnonzero(~satellite)
        self._ids = identities
        self._positions = positions
        self._satellite = satellite
        self._rows = {node_id: row for row, node_id in enumerate(identities)}
        # A high satellite's ground horizon must not widen short-range ISL
        # searches. The two link types index the same positions separately.
        self._isl_sat_grid = _SpatialGrid(
            positions, satellite_rows, self.max_isl_distance_m + 1e-5
        )
        self._ground_range = 0.0
        self._ground_sat_grid = None
        self._ground_grid = None
        if len(satellite_rows) and len(ground_rows):
            radii = np.linalg.norm(positions, axis=1)
            # A sphere inside WGS84 gives a conservative search bound;
            # the exact ellipsoid still decides visibility afterwards.
            horizons = np.sqrt(np.maximum(0, radii * radii - WGS84_B_M**2))
            ground_range = float(
                horizons[satellite_rows].max() + horizons[ground_rows].max()
            )
            if self.max_ground_distance_m is not None:
                ground_range = min(ground_range, self.max_ground_distance_m)
            self._ground_range = ground_range + 1e-5
            self._ground_sat_grid = _SpatialGrid(
                positions, satellite_rows, self._ground_range
            )
            self._ground_grid = _SpatialGrid(
                positions, ground_rows, self._ground_range
            )
        up_vectors = []
        for node, is_satellite in zip(nodes, satellite, strict=True):
            if is_satellite:
                up_vectors.append(np.zeros(3))
            else:
                up_vectors.append(ground_up(node.latitude_deg, node.longitude_deg))
        self._ups = np.array(up_vectors).reshape((-1, 3))
        self._snapshot = snapshot

    def compute_candidates(self, scene, snapshot, node_id):
        self._prepare(scene, snapshot)
        row = self._rows[node_id]
        point = self._positions[row]
        if self._satellite[row]:
            peers = self._isl_sat_grid.near(point)
            if self._ground_grid is not None:
                peers = np.concatenate((peers, self._ground_grid.near(point)))
        else:
            if self._ground_sat_grid is None:
                return ()
            peers = self._ground_sat_grid.near(point)
        peers = peers[peers != row]
        if not len(peers):
            return ()
        delta = self._positions[peers] - point
        distance = np.linalg.norm(delta, axis=1)
        isl = self._satellite[row] & self._satellite[peers]
        limit = np.where(isl, self.max_isl_distance_m, self._ground_range)
        accepted = (distance > 0) & (distance <= limit)
        peers = peers[accepted]
        delta = delta[accepted]
        distance = distance[accepted]
        isl = isl[accepted]
        visible = earth_visible(point, self._positions[peers])
        elevation = np.full(len(peers), np.nan)
        ground_pairs = ~isl
        if np.any(ground_pairs):
            if self._satellite[row]:
                ground_rows = peers[ground_pairs]
                from_ground = -delta[ground_pairs]
            else:
                ground_rows = np.full(np.sum(ground_pairs), row)
                from_ground = delta[ground_pairs]
            sine = (
                np.sum(from_ground * self._ups[ground_rows], axis=1)
                / distance[ground_pairs]
            )
            elevation[ground_pairs] = np.degrees(np.arcsin(np.clip(sine, -1, 1)))
            visible[ground_pairs] &= elevation[ground_pairs] >= self.min_ground_elevation_deg
        # Preserve snapshot ordering for reproducible reports, not grid order.
        selected = sorted(np.flatnonzero(visible), key=lambda index: peers[index])
        candidates = []
        for index in selected:
            elevation_deg = None
            if not isl[index]:
                elevation_deg = float(elevation[index])
            candidates.append(LinkCandidate(
                node_id=node_id,
                peer_id=self._ids[peers[index]],
                distance_m=float(distance[index]),
                propagation_delay_s=float(distance[index] / LIGHT_SPEED_M_S),
                elevation_deg=elevation_deg,
            ))
        return tuple(candidates)
