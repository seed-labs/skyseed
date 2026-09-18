"""Three small current-information algorithms; no default Model inheritance.

Geometry uses WGS84 line of sight, a 6,000 km ISL envelope, GS elevation >= 5°
within 2,000 km, and Zone representative-point elevation >= 25°. These are
engineering assumptions, not claims about commercial Starlink equipment.
Selection retains feasible reservations, then greedily prefers shorter ISLs or
higher GS elevation. It offers no future-service proof or global optimum.
"""
from collections import Counter
from itertools import combinations

from skyseed_v2.simulator import physical, planner
from skyseed_v2.simulator.geometry import (
    ecef_distance_m, elevation_deg, geodetic_to_ecef, has_earth_line_of_sight,
)


class CurrentGeometryModel:
    """Evaluate the requested static pair scope at this frame, without prediction."""

    maximum_isl_distance_m = 6_000_000.0
    ground_station_minimum_elevation_deg = 5.0
    ground_station_maximum_distance_m = 2_000_000.0
    zone_minimum_elevation_deg = 25.0

    def __init__(self):
        self._world = None

    def compute_observation(self, world, frame, request):
        if self._world is not world:
            # Cache only static identities/Zone positions; frame positions stay current.
            self._satellites = tuple(s.node_id for system in world.autonomous_systems
                                    for shell in system.shells for orbit in shell.orbits
                                    for s in orbit.satellites)
            self._stations = tuple(s.node_id for system in world.autonomous_systems
                                   for s in system.ground_stations)
            try:
                zones = world.get_zone_layout().zones
            except RuntimeError:  # Zone service can be disabled by the caller.
                zones = ()
            self._zones = tuple((z.zone_id, geodetic_to_ecef(*z.representative_point, 0))
                                for z in zones)
            self._world = world
        positions = frame.positions
        isl, accesses, services = [], [], []
        for a, b in request.inter_satellite_pairs:
            first, second = positions[a], positions[b]
            distance = ecef_distance_m(first, second)
            if distance <= self.maximum_isl_distance_m and has_earth_line_of_sight(first, second):
                isl.append(physical.InterSatelliteLinkCandidate(
                    a, b, distance_m=distance, propagation_delay_us=round(distance / 299.792458)))
        if request.include_ground_station_access:
            for station in self._stations:
                origin = positions[station]
                for satellite in self._satellites:
                    target = positions[satellite]
                    distance = ecef_distance_m(origin, target)
                    if distance > self.ground_station_maximum_distance_m:
                        continue
                    elevation = elevation_deg(origin, target)
                    if elevation >= self.ground_station_minimum_elevation_deg and has_earth_line_of_sight(origin, target):
                        accesses.append(physical.GroundStationAccessCandidate(
                            station, satellite, distance_m=distance, elevation_deg=elevation,
                            propagation_delay_us=round(distance / 299.792458)))
        if request.include_zone_service:
            for zone, origin in self._zones:
                for satellite in self._satellites:
                    target = positions[satellite]
                    elevation = elevation_deg(origin, target)
                    if elevation >= self.zone_minimum_elevation_deg:
                        distance = ecef_distance_m(origin, target)
                        services.append(physical.ZoneServiceCandidate(
                            zone, satellite, distance_m=distance, elevation_deg=elevation,
                            propagation_delay_us=round(distance / 299.792458)))
        # All requested GS pairs were considered. No candidate budget support,
        # future windows, plane classifications, work counts or diagnostics needed.
        return physical.PhysicalLinkQueryResult(
            timestamp=frame.timestamp, inter_satellite_candidates=tuple(isl),
            ground_station_access_candidates=tuple(accesses), zone_service_candidates=tuple(services))

    def describe_configuration(self):
        return {"maximum_isl_distance_m": self.maximum_isl_distance_m,
                "ground_station_minimum_elevation_deg": self.ground_station_minimum_elevation_deg,
                "ground_station_maximum_distance_m": self.ground_station_maximum_distance_m,
                "zone_minimum_elevation_deg": self.zone_minimum_elevation_deg,
                "prediction": "none; current frame only"}


class GreedyInterSatelliteModel:
    """Within each Shell, retain reservations then fill free ports by distance."""

    def create_inter_satellite_candidate_pairs(self, world):
        # At 172 nodes a complete within-Shell envelope is small and understandable.
        # There is no cross-Shell link or per-plane 2+2 policy in this algorithm.
        return tuple(pair for system in world.autonomous_systems for shell in system.shells
                     for pair in combinations(sorted((s.node_id for orbit in shell.orbits
                                                      for s in orbit.satellites), key=lambda n: n.value), 2))

    def select_inter_satellite_links(self, world, candidates, state):
        def priority(candidate):
            identity = planner.OperationalConnectionId(planner.OperationalConnectionKind.INTER_SATELLITE,
                                                       candidate.endpoint_a, candidate.endpoint_b)
            return (identity not in state.selected_since, candidate.distance_m,
                    candidate.endpoint_a.value, candidate.endpoint_b.value)
        degree, selected = Counter(), []
        for candidate in sorted(candidates, key=priority):
            endpoints = candidate.endpoint_a, candidate.endpoint_b
            capacities = [world.get_satellite(n).inter_satellite_link_capability for n in endpoints]
            if any(c is None for c in capacities):
                raise ValueError("S5 selection requires declared ISL device capacity")
            if all(degree[n] < c.maximum_simultaneous_links for n, c in zip(endpoints, capacities)):
                selected.append(candidate)
                degree.update(endpoints)  # Includes preparation, not just operational links.
        return planner.InterSatelliteSelection(tuple(selected))


class GreedyGroundStationModel:
    """Retain access reservations, then allocate remaining ports by elevation.

    The frozen GS records do not declare capacity. Four is this local policy's
    fallback only; any declared GS limit and satellite access limit take priority.
    This greedy policy does not maximize covered stations or balance satellites.
    """

    unspecified_station_limit = 4

    def select_ground_station_accesses(self, world, candidates, state):
        def priority(candidate):
            identity = planner.OperationalConnectionId(planner.OperationalConnectionKind.GROUND_STATION_ACCESS,
                                                       candidate.ground_station_id, candidate.satellite_id)
            return (identity not in state.selected_since, -candidate.elevation_deg,
                    candidate.ground_station_id.value, candidate.satellite_id.value)
        degree, selected = Counter(), []
        for candidate in sorted(candidates, key=priority):
            station, satellite = candidate.ground_station_id, candidate.satellite_id
            station_limit = world.get_ground_station(station).max_simultaneous_satellite_accesses
            if station_limit is None:
                station_limit = self.unspecified_station_limit
            satellite_limit = world.get_satellite(satellite).max_simultaneous_ground_station_accesses
            if degree[station] < station_limit and (satellite_limit is None or degree[satellite] < satellite_limit):
                selected.append(candidate)
                degree.update((station, satellite))
        return tuple(selected)

    def describe_configuration(self):
        return {"unspecified_station_limit": self.unspecified_station_limit,
                "selection": "retain reservations, then highest elevation"}
