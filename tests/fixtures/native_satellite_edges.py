"""Only GS access edges. Satellite transit is computed by the actual OSPF daemon."""
from skyseed.simulator import (
    OperationalConnectionKind, RoutePlanningResult, RouteDemand,
    GroundStationRouteDemandTarget, SatelliteRouteDemandTarget,
    LocalRouteOutcome, LogicalExit, RouteCandidate,
)


class EdgeRoutes:
    def __init__(self, satellite_destinations):
        self.satellite_destinations = tuple(satellite_destinations)

    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        access = {}
        outcomes = []
        for connection in connections:
            c = connection.connection_id
            if c.kind is not OperationalConnectionKind.GROUND_STATION_ACCESS:
                continue
            gs, sat = sorted((c.endpoint_a, c.endpoint_b), key=lambda n: n.entity_kind)
            access.setdefault(gs, []).append((sat, c))
            # Every attached satellite advertises only its installed GS /32 edge.
            outcomes.append(LocalRouteOutcome(RouteDemand(sat, GroundStationRouteDemandTarget(gs)),
                candidates=(RouteCandidate(LogicalExit(c, sat)),)))
        for gs, choices in sorted(access.items(), key=lambda pair: pair[0].value):
            # Explicit single uplink if the connection Model permits multiple accesses.
            sat, c = min(choices, key=lambda pair: pair[0].value)
            for target in self.satellite_destinations:
                outcomes.append(LocalRouteOutcome(RouteDemand(gs, SatelliteRouteDemandTarget(target)),
                    candidates=(RouteCandidate(LogicalExit(c, gs)),)))
        return RoutePlanningResult(tuple(outcomes))
