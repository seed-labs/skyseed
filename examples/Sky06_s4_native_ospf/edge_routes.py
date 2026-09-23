"""Explicit GS↔Zone edges; actual satellite transit belongs to OSPF."""
from skyseed.simulator import (
    OperationalConnectionKind, RoutePlanningResult, RouteDemand,
    GroundStationRouteDemandTarget, ZoneRouteDemandTarget,
    LocalRouteOutcome, LogicalExit, RouteCandidate,
    GatewayHopCountRoutePlanningModel,
)


class EdgeRoutes:
    def __init__(self, *, gateway_candidate_count=3, gateway_candidate_max_distance_m=None):
        # Match the ordinary Simulator assembly's association, independent of backend.
        # Only its public demand constructor is used; no route graph/workers run.
        self._association = GatewayHopCountRoutePlanningModel(
            gateway_candidate_count=gateway_candidate_count,
            gateway_candidate_max_distance_m=gateway_candidate_max_distance_m)
        self._closed = False

    def create_route_demands(self, world, assignments, *, timestamp):
        return self._association.create_route_demands(world, assignments, timestamp=timestamp)

    def describe_configuration(self):
        return self._association.describe_configuration()

    def shutdown(self, *, wait=True):
        self._closed = True
        self._association.shutdown(wait=wait)

    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        if self._closed:
            raise RuntimeError('Route Model is shut down')
        access, outcomes = {}, []
        for connection in connections:
            c = connection.connection_id
            if c.kind is not OperationalConnectionKind.GROUND_STATION_ACCESS:
                continue
            gs, sat = sorted((c.endpoint_a, c.endpoint_b), key=lambda n: n.entity_kind)
            access.setdefault(gs, []).append((sat, c))
            outcomes.append(LocalRouteOutcome(RouteDemand(sat, GroundStationRouteDemandTarget(gs)),
                candidates=(RouteCandidate(LogicalExit(c, sat)),)))
        exits = {gs: min(links, key=lambda pair: pair[0].value)[1] for gs, links in access.items()}
        for demand in demands:
            if demand.source.entity_kind != 'ground-station' or not isinstance(demand.target, ZoneRouteDemandTarget):
                continue
            gs = demand.source
            if gs not in exits:
                continue
            # Stable Zone target: service moves do not change this GS uplink.
            outcomes.append(LocalRouteOutcome(demand,
                candidates=(RouteCandidate(LogicalExit(exits[gs], gs)),)))
        return RoutePlanningResult(tuple(outcomes))
