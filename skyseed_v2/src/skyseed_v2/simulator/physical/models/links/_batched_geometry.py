"""Private array geometry shared by instantaneous queries and prediction.

Only feasible rows become public candidate values. Dense surface queries use
bounded satellite tiles; exact ellipsoid/elevation tests follow radial pruning.
"""

from __future__ import annotations

import numpy as np

from skyseed_v2.simulator.geometry._elevation import _wgs84_local_up_unit_vector, _elevation_degrees_array
from skyseed_v2.simulator.geometry.coordinates import (
    _LOS_SURFACE_TOLERANCE,
    _WGS84_SEMI_MAJOR_AXIS_M,
    _WGS84_SEMI_MINOR_AXIS_M,
)


# Matched canonical trials: fewer GIL handoffs, bounded surface scratch space.
_SURFACE_TILE_SIZE = 720

_AXES = np.array([_WGS84_SEMI_MAJOR_AXIS_M] * 2 + [_WGS84_SEMI_MINOR_AXIS_M])


def _xyz(position):
    return position.x_m, position.y_m, position.z_m


def _norm(values):
    return np.sqrt(np.sum(values * values, axis=-1))


def _line_of_sight(first, second):
    origin = first / _AXES
    direction = (second - first) / _AXES
    length = np.sum(direction * direction, axis=-1)
    closest = np.divide(-np.sum(origin * direction, axis=-1), length,
                        out=np.zeros_like(length), where=length != 0)
    level = np.sum((origin + closest[..., None] * direction) ** 2, axis=-1) - 1
    return ((np.sum(origin * origin, axis=-1) - 1 >= -_LOS_SURFACE_TOLERANCE)
            & (np.sum((origin + direction) ** 2, axis=-1) - 1 >= -_LOS_SURFACE_TOLERANCE)
            & ((closest <= 0) | (closest >= 1) | (level >= 0)))


class _BatchedPhysicalGeometry:
    def __init__(self, core, request, *, relation, radius_bounds, maximum_slant):
        self._radius_bounds = radius_bounds
        self._maximum_slant = maximum_slant

        self.core = core
        self.request = request
        static = core._static_geometry
        self.satellites = tuple(s.node_id for s in static.satellites)
        self.stations = tuple(s.node_id for s in static.ground_stations)
        self.zones = static.zones
        indexes = {node: i for i, node in enumerate(self.satellites)}
        self.pairs = request.inter_satellite_pairs
        self.pair_indexes = np.array([(indexes[a], indexes[b]) for a, b in self.pairs],
                                     dtype=np.int32).reshape((-1, 2))
        self.relations = tuple(relation(static, a, b) for a, b in self.pairs)
        self.zone_positions = np.array([_xyz(z.representative_position) for z in self.zones]).reshape((-1, 3))
        self.zone_ups = np.array([z.local_up for z in self.zones]).reshape((-1, 3))
        self._ground_values = None

    def arrays(self, positions):
        satellites = np.array([_xyz(positions[n]) for n in self.satellites]).reshape((-1, 3))
        ground_values = tuple(positions[n] for n in self.stations)
        if self._ground_values != ground_values:
            self._ground_values = ground_values
            self.ground_positions = np.array([_xyz(p) for p in ground_values]).reshape((-1, 3))
            self.ground_ups = np.array([_wgs84_local_up_unit_vector(p) for p in ground_values]).reshape((-1, 3))
        return satellites

    def isl(self, satellites):
        first = satellites[self.pair_indexes[:, 0]]
        second = satellites[self.pair_indexes[:, 1]]
        distances = _norm(second - first)
        near = np.flatnonzero(distances <= self.core._maximum_isl_distance_m)
        indexes = near[_line_of_sight(first[near], second[near])]
        return indexes, distances[indexes], len(near)

    def surface(self, satellites, *, zones=False):
        if zones:
            positions, ups = self.zone_positions, self.zone_ups
            minimum = self.core._zone_coverage_minimum_elevation_deg
            maximum = None
        else:
            positions, ups = self.ground_positions, self.ground_ups
            minimum = self.core._ground_station_minimum_elevation_deg
            maximum = self.core._ground_station_maximum_distance_m
        rows, distances, elevations = [], [], []
        evaluated = 0
        if not len(positions):
            return np.empty((0, 2), dtype=np.int32), np.array([]), np.array([]), 0
        from skyseed_v2.simulator.geometry import EarthFixedPosition

        # Reuse static bounds; arbitrary injected PositionModels may move GSs.
        if not hasattr(self, '_bounds'):
            self._bounds = {}
        cached = self._bounds.get(zones)
        if cached is None or cached[0] is not positions:
            radius_bounds = self._radius_bounds(tuple(
                EarthFixedPosition(*p) for p in positions))
            self._bounds[zones] = (positions, radius_bounds)
        else:
            radius_bounds = cached[1]
        for start in range(0, len(satellites), _SURFACE_TILE_SIZE):
            tile = satellites[start:start + _SURFACE_TILE_SIZE]
            delta = tile[:, None, :] - positions[None, :, :]
            distance = _norm(delta)
            if maximum is None:
                radii = np.array([self._maximum_slant(
                    EarthFixedPosition(*p), minimum, *radius_bounds) for p in tile])
            else:
                radii = np.full(len(tile), maximum)
            a, b = np.nonzero(distance <= radii[:, None] + 1.0)
            evaluated += len(a)
            d = distance[a, b]
            elevation = _elevation_degrees_array(delta[a, b], ups[b], d)
            accepted = (elevation >= minimum) & _line_of_sight(positions[b], tile[a])
            if maximum is not None:
                accepted &= d <= maximum
            rows.extend(zip((a[accepted] + start).tolist(), b[accepted].tolist()))
            distances.extend(d[accepted].tolist())
            elevations.extend(elevation[accepted].tolist())
        return np.array(rows, dtype=np.int32).reshape((-1, 2)), np.array(distances), np.array(elevations), evaluated
