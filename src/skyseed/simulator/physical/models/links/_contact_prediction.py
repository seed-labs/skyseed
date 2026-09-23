"""Finite compact physical contact proofs owned by the geometric link Model.

Prediction uses no Planner, route, or compiled state. Circular-orbit speed
bounds certify entire sample cells, not just their endpoints. Cells lacking a
proof remain unknown; the finite coverage endpoint is not a disappearance.
"""

from __future__ import annotations

from math import cos, sin, radians, sqrt

import numpy as np

from skyseed.simulator.physical.queries.physical_link import PhysicalContactWindow
from skyseed.simulator.physical.models.links._batched_geometry import _AXES, _norm
from skyseed.simulator.physical.models.circular_orbit import CircularOrbitPositionModel


class _ContactPrediction:
    def __init__(self, core, request, *, start_timestamp, end_timestamp,
                 observation_interval, inter_satellite_tail, ground_station_tail,
                 continuation=None):
        self.start = start_timestamp
        self.end = end_timestamp
        self.windows = {'isl': {}, 'ground': {}}
        self.coverage = {'isl': end_timestamp + inter_satellite_tail,
                         'ground': end_timestamp + ground_station_tail}
        self.interval = observation_interval
        self.request = request
        if continuation is not None:
            # Keep useful future proofs, discard expired history. Earlier values
            # already delivered to Planner remain immutable.
            self.windows = {
                kind: {key: [w for w in windows if w.end_timestamp >= start_timestamp]
                       for key, windows in rows.items()
                       if windows and windows[-1].end_timestamp >= start_timestamp}
                for kind, rows in continuation.windows.items()}
        self.pair_indexes = {pair: i for i, pair in enumerate(request.inter_satellite_pairs)}
        self.stable = frozenset()
        self._stable_window = PhysicalContactWindow(start_timestamp, None)
        self.counts = {'prediction_frames': 0, 'isl_interval_evaluations': 0,
                       'ground_interval_evaluations': 0, 'stable_isl_proofs': 0}
        world = core.physical_world
        if type(world.get_position_model()) is not CircularOrbitPositionModel:
            # A sampled position alone supplies no continuous-motion bound.
            return
        batch = core._prepare_batch(request)
        if not batch.satellites:
            return
        elements = _circular_elements(world, batch.satellites, start_timestamp)
        radii, motion, cosine, sine = elements
        speeds = radii * (motion + 0.00008)  # Earth rotation, polar motion/precession bound.
        # Equal circular radii and angular speeds allow an all-phase distance
        # bound. The circumscribed WGS84 sphere gives a conservative LOS proof.
        a, b = batch.pair_indexes.T
        c, d = cosine[a] - cosine[b], sine[a] - sine[b]
        cc, dd, cd = np.sum(c*c, axis=1), np.sum(d*d, axis=1), np.sum(c*d, axis=1)
        maximum2 = (cc + dd + np.sqrt((cc-dd)**2 + 4*cd*cd)) / 2
        stable = ((core._maximum_isl_distance_m >= 1.0) & (radii[a] == radii[b]) & (motion[a] == motion[b])
                  & (maximum2 <= (core._maximum_isl_distance_m - 1.0)**2)
                  & (radii[a]**2 - maximum2/4 > (float(_AXES[0]) + 1.0)**2))
        self.stable = (frozenset(np.flatnonzero(stable).tolist()) if continuation is None
                       else continuation.stable)
        stable = np.array([i in self.stable for i in range(len(stable))], dtype=bool)
        self.counts['stable_isl_proofs'] = len(self.stable)
        uncertain = np.flatnonzero(~stable)
        isl_end = end_timestamp + inter_satellite_tail
        ground_end = end_timestamp + ground_station_tail
        limit = max(isl_end if len(uncertain) else end_timestamp,
                    ground_end if request.include_ground_station_access else end_timestamp)
        active = {'isl': {}, 'ground': {}}
        begins = {kind: start_timestamp if continuation is None else continuation.coverage[kind]
                  for kind in active}
        for kind, rows in self.windows.items():
            for key, windows in rows.items():
                if windows and windows[-1].end_timestamp == begins[kind]:
                    window = windows.pop()
                    active[kind][key] = window.start_timestamp, window.end_timestamp

        def extend(kind, indexes, left, right):
            live = active[kind]
            current = set(map(int, indexes))
            for key in live.keys() - current:
                begin, end = live.pop(key)
                self.windows[kind].setdefault(key, []).append(PhysicalContactWindow(begin, end))
            for key in current:
                begin = live[key][0] if key in live else left
                live[key] = begin, right

        left = min(begins['isl'] if len(uncertain) else limit,
                   begins['ground'] if request.include_ground_station_access else limit)
        while left < limit:
            right = min(left + observation_interval, limit)
            midpoint = left + (right-left)/2
            half = (right-left).total_seconds()/2
            # Only physical positions at prediction cells; no rich candidate graph.
            frame = world.frame_at(midpoint)
            positions = batch.arrays(frame.positions)
            self.counts['prediction_frames'] += 1
            if begins['isl'] <= left < isl_end and len(uncertain):
                first, second = batch.pair_indexes[uncertain].T
                p, q = positions[first], positions[second]
                displacement = speeds * half + 1.0
                delta = q-p
                distance = _norm(delta)
                denominator = np.sum(delta*delta, axis=1)
                t = np.clip(np.divide(-np.sum(p*delta, axis=1), denominator,
                                     out=np.zeros_like(denominator), where=denominator != 0), 0, 1)
                clearance = _norm(p+t[:, None]*delta) - float(_AXES[0])
                proven = ((distance + displacement[first] + displacement[second]
                           <= core._maximum_isl_distance_m)
                          & (clearance > np.maximum(displacement[first], displacement[second])))
                extend('isl', uncertain[proven], left, min(right, isl_end))
                self.counts['isl_interval_evaluations'] += len(uncertain)
            if request.include_ground_station_access and begins['ground'] <= left < ground_end:
                rows, distances, _, evaluated = batch.surface(positions)
                self.counts['ground_interval_evaluations'] += evaluated
                si, gi = rows.T
                p, q = batch.ground_positions[gi], positions[si]
                delta = q-p
                bound = speeds[si] * half + 1.0
                minimum_sine = sin(radians(core._ground_station_minimum_elevation_deg))
                elevation_margin = np.sum(delta*batch.ground_ups[gi], axis=1) - minimum_sine*distances
                normal = p / (_AXES*_AXES)
                # Convex ellipsoid supporting half-space: every segment point
                # lies outside if the fixed endpoint is outside and points out.
                proven = ((elevation_margin >= (1+abs(minimum_sine))*bound)
                          & (np.sum((p/_AXES)**2, axis=1) >= 1-1e-12)
                          & (np.sum(delta*normal, axis=1) >= _norm(normal)*bound))
                if core._ground_station_maximum_distance_m is not None:
                    proven &= distances+bound <= core._ground_station_maximum_distance_m
                extend('ground', (si*len(batch.stations)+gi)[proven], left, min(right, ground_end))
            left = right
        for kind, live in active.items():
            for key, (begin, end) in live.items():
                self.windows[kind].setdefault(key, []).append(PhysicalContactWindow(begin, end))
        self.windows = {kind: {key: tuple(value) for key, value in rows.items()}
                        for kind, rows in self.windows.items()}

    def window(self, kind, key, timestamp):
        if kind == 'isl' and isinstance(key, tuple):
            key = self.pair_indexes.get(key)
            if key is None:
                return None
        if kind == 'isl' and key in self.stable and timestamp >= self.start:
            return self._stable_window
        for window in self.windows[kind].get(key, ()):
            if window.covers(timestamp, timestamp):
                return window
        return None


def _circular_elements(world, nodes, epoch):
    elements = {}
    for autonomous_system in world.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                radius = 6378137.0 + orbit.altitude_km * 1000
                motion = sqrt(3.986004418e14 / radius**3)
                raan, inclination = radians(orbit.right_ascension_deg), radians(orbit.inclination_deg)
                u = np.array([cos(raan), sin(raan), 0.0]) * radius
                v = np.array([-sin(raan)*cos(inclination), cos(raan)*cos(inclination), sin(inclination)]) * radius
                for satellite in orbit.satellites:
                    phase = radians(satellite.mean_anomaly_deg) + motion*(epoch-orbit.epoch_utc).total_seconds()
                    elements[satellite.node_id] = (radius, motion,
                        u*cos(phase)+v*sin(phase), -u*sin(phase)+v*cos(phase))
    return tuple(np.array([elements[n][i] for n in nodes]) for i in range(4))
