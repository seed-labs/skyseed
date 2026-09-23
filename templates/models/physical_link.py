"""An authored static relation graph, independent of orbital geometry.

Supply candidate tuples and inject physical_link_model=StaticPhysicalLinkModel(...).
Absent measures and future windows are intentional. Connections still acquire.

Read physical_frame.get_position(node_id), positions and timestamp; request
supplies pairs and inclusion flags. Public geometry helpers are optional.
Reference: docs/api/physical.md and docs/api/planning.md (physical candidates).
"""
from skyseed.simulator import physical


class StaticPhysicalLinkModel:
    def __init__(self, *, inter_satellite=(), ground_station=(), zone_service=()):
        self.inter_satellite = tuple(inter_satellite)
        self.ground_station = tuple(ground_station)
        self.zone_service = tuple(zone_service)

    def compute_observation(self, physical_world, physical_frame, request):
        pairs = set(request.inter_satellite_pairs)
        return physical.PhysicalLinkQueryResult(
            timestamp=physical_frame.timestamp,
            inter_satellite_candidates=tuple(c for c in self.inter_satellite
                if (c.endpoint_a, c.endpoint_b) in pairs),
            ground_station_access_candidates=self.ground_station if request.include_ground_station_access else (),
            zone_service_candidates=self.zone_service if request.include_zone_service else (),
        )
