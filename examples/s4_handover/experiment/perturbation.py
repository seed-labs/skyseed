"""Example hook: interrupt a region's GS links during one fixed interval."""
from collections import defaultdict
from datetime import timedelta
from math import asin, cos, radians, sin, sqrt

from skyseed.pc.link import LinkOperation, LinkPlan


def inside_region(station, region):
    """A spherical surface-distance threshold, not a radio propagation model."""
    latitude = radians(station.latitude_deg)
    centre_latitude = radians(region["latitude_deg"])
    longitude_delta = radians(station.longitude_deg - region["longitude_deg"])
    haversine = sin((latitude - centre_latitude) / 2) ** 2
    haversine += cos(latitude) * cos(centre_latitude) * sin(longitude_delta / 2) ** 2
    distance_km = 2 * 6371.0088 * asin(min(1.0, sqrt(haversine)))
    return distance_km <= region["radius_km"]


def outage_description(model):
    """Export this example's circular regions for its own result viewer."""
    if model is None:
        return None
    regions = []
    stations = set()
    for region in model.regions:
        members = [station.node_id for station in model.layer.scene.get_ground_stations()
                   if inside_region(station, region)]
        regions.append({**region, "affected_stations": members})
        stations.update(members)
    return {"start_seconds": model.start_seconds, "end_seconds": model.end_seconds,
            "regions": regions, "affected_stations": sorted(stations)}


class RegionalGroundOutage:
    """Keep ISLs unchanged; restore the latest base state when the outage ends.

    Model settings belong here. The layer is bound by LinkLayer after creation.
    Locations are fixed before routing; neither region interrupts the UT adapter.
    """
    def __init__(self):
        self.start_seconds = 180
        self.end_seconds = 240
        self.regions = (
            {"name": "Yamaguchi", "latitude_deg": 34.2171, "longitude_deg": 131.55566, "radius_km": 180},
            {"name": "Western Europe", "latitude_deg": 50.2, "longitude_deg": 1.5, "radius_km": 600},
        )
        self.affected_stations = None
        self.carried_links = None

    def perturb(self, plan):
        if self.affected_stations is None:
            self.affected_stations = set()
            for station in self.layer.scene.get_ground_stations():
                if any(inside_region(station, region) for region in self.regions):
                    self.affected_stations.add(station.node_id)
            epoch = self.layer.scene.get_shells()[0].epoch
            self.outage_start = epoch + timedelta(seconds=self.start_seconds)
            self.outage_end = epoch + timedelta(seconds=self.end_seconds)

        base = dict(plan.initial_links)
        initial = dict(base if self.carried_links is None else self.carried_links)
        execution = dict(initial)
        events = defaultdict(list)
        events[plan.start_time] = []
        for operation in plan.operations:
            events[operation.timestamp].append(operation)
        for timestamp in (self.outage_start, self.outage_end):
            if plan.start_time <= timestamp < plan.end_time:
                events[timestamp]  # Include the boundary even without a base update.

        operations = []
        for timestamp, changes in sorted(events.items()):
            changed_links = {operation.link_id for operation in changes}
            for operation in changes:
                operation.apply(base)
            # Only the two boundaries and initial restoration need a scan.
            # Ordinary base updates touch only their own links.
            if timestamp in (plan.start_time, self.outage_start, self.outage_end):
                changed_links.update(base)
                changed_links.update(execution)
            interrupted = self.outage_start <= timestamp < self.outage_end
            for identity in sorted(changed_links):
                desired = base.get(identity)
                if interrupted and self.affected_stations.intersection(identity):
                    desired = None
                previous = execution.get(identity)
                if previous == desired:
                    continue
                if desired is None:
                    operation = LinkOperation(timestamp, "withdraw", identity)
                elif previous is None:
                    operation = LinkOperation(timestamp, "establish", identity, desired)
                else:
                    operation = LinkOperation(timestamp, "update", identity, desired)
                operation.apply(execution)
                operations.append(operation)
        self.carried_links = execution
        return LinkPlan(plan.start_time, plan.end_time, initial, tuple(operations))
