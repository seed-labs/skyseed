"""Retain-first greedy access, respecting both endpoint capacities.

Inject ground_station_model=SimpleGroundStationModel(). This policy uses the
explicit author fallback only for unspecified GroundStation hardware capacity.

Use world.get_ground_station/get_satellite for endpoint capacities, and the
supplied state for retained/preparing selections. Unspecified limits are None.
Reference: docs/api/physical.md (entities) and docs/api/planning.md (selection state).
"""
from collections import Counter
from skyseed_v2.simulator import planner


class SimpleGroundStationModel:
    def __init__(self, unspecified_station_limit=1):
        self.unspecified_station_limit = unspecified_station_limit

    def select_ground_station_accesses(self, physical_world, candidates, state):
        def identity(candidate):
            return planner.OperationalConnectionId(planner.OperationalConnectionKind.GROUND_STATION_ACCESS,
                candidate.ground_station_id, candidate.satellite_id)
        degree, selected = Counter(), []
        for candidate in sorted(candidates, key=lambda c: identity(c) not in state.selected_since):
            station, satellite = candidate.ground_station_id, candidate.satellite_id
            station_limit = physical_world.get_ground_station(station).max_simultaneous_satellite_accesses
            if station_limit is None:
                station_limit = self.unspecified_station_limit
            satellite_limit = physical_world.get_satellite(satellite).max_simultaneous_ground_station_accesses
            if degree[station] < station_limit and (satellite_limit is None or degree[satellite] < satellite_limit):
                selected.append(candidate)
                degree.update((station, satellite))
        return tuple(selected)
