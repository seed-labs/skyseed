"""Service assignment contract and highest-elevation geographic assignment."""
from itertools import product
from math import isfinite
from typing import TYPE_CHECKING, Mapping, Protocol

import numpy as np

from skyseed.scene import Scene
from ..physical import PhysicalPositionSnapshot
from ..physical.earth_geometry import WGS84_B_M, earth_visible, ground_ecef, ground_up

if TYPE_CHECKING:
    from .network_layer import NetworkLayer


class ServiceAssignmentModel(Protocol):
    layer: "NetworkLayer"

    def assign_service(self, scene: Scene, snapshot: PhysicalPositionSnapshot) -> Mapping[str, str | None]: ...


class HighestElevationServiceAssignmentModel:
    def __init__(self, *, min_elevation_deg=5.0):
        if not isfinite(min_elevation_deg) or not 0 <= min_elevation_deg <= 90:
            raise ValueError("service elevation must lie between 0 and 90 degrees")
        self.min_elevation_deg = min_elevation_deg
        self._scene = None

    def assign_service(self, scene, snapshot):
        if self._scene is not scene:
            zones = scene.get_endpoints("geographic-zone/v1")
            self._zones = []
            for zone in zones:
                zone.validate()
                latitude = zone.data["latitude_deg"]
                longitude = zone.data["longitude_deg"]
                point = np.asarray(ground_ecef(latitude, longitude, 0))
                horizon = float(np.sqrt(max(0, point @ point - WGS84_B_M**2)))
                self._zones.append((zone.endpoint_id, point, ground_up(latitude, longitude), horizon))
            self._max_ground_horizon = max((zone[3] for zone in self._zones), default=0)
            self._scene = scene
        identities = sorted(node for node in snapshot.states if scene.get_node(node).kind == "satellite")
        result = {identity: None for identity, _, _, _ in self._zones}
        if not identities or not self._zones:
            return result
        positions = np.array([snapshot[node].position_m for node in identities])
        horizons = np.sqrt(np.maximum(0, np.sum(positions * positions, axis=1) - WGS84_B_M**2))
        # Separate horizon scales prevent a GEO satellite from turning every
        # LEO coverage query into a scan of the entire constellation.
        grids = {}
        for row, horizon in enumerate(horizons):
            # The inscribed-sphere bound needs both endpoints: a surface point
            # on WGS84 is generally above that sphere, including at zero altitude.
            search_radius = horizon + self._max_ground_horizon
            exponent = int(np.ceil(np.log2(max(1.0, search_radius + 1e-5))))
            width = 2.0**exponent
            cells = grids.setdefault(width, {})
            cell = tuple(np.floor(positions[row] / width).astype(np.int64))
            cells.setdefault(cell, []).append(row)
        for identity, point, up, ground_horizon in self._zones:
            rows = []
            for width, cells in grids.items():
                centre = np.floor(point / width).astype(np.int64)
                for offset in product((-1, 0, 1), repeat=3):
                    rows.extend(cells.get(tuple(centre + offset), ()))
            if not rows:
                continue
            rows = np.asarray(rows, dtype=int)
            delta = positions[rows] - point
            distance = np.linalg.norm(delta, axis=1)
            near = (distance > 0) & (distance <= horizons[rows] + ground_horizon + 1e-5)
            rows, delta, distance = rows[near], delta[near], distance[near]
            sine = np.clip(delta @ up / distance, -1, 1)
            elevations = np.degrees(np.arcsin(sine))
            eligible = (elevations >= self.min_elevation_deg) & earth_visible(point, positions[rows])
            choices = np.flatnonzero(eligible)
            if len(choices):
                best = min(choices, key=lambda index: (-elevations[index], identities[rows[index]]))
                result[identity] = identities[rows[best]]
        return result
