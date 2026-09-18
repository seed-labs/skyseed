"""First available candidate per Zone; inject zone_service_model=FirstCandidateServiceModel()."""
from skyseed_v2.simulator import planner


class FirstCandidateServiceModel:
    def assign_zone_service(self, physical_world, candidates, previous_assignments, *, timestamp):
        first = {}
        for candidate in candidates:
            first.setdefault(candidate.zone_id, candidate.satellite_id)
        try:
            zones = physical_world.get_zone_layout().zones
        except RuntimeError:
            zones = ()
        return planner.ZoneServicePlanningResult(tuple(
            planner.ZoneServiceAssignment(zone.zone_id, first.get(zone.zone_id)) for zone in zones))
