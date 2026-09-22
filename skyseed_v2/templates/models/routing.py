"""Unscoped hop-count paths with destination-consistent next hops and GS terminals.

Inject route_model=SimpleRouteModel() and supply explicit route_demands.
No demand factory, cache, worker, inheritance or resource shutdown is required.

Read supplied operational connections, assignments, demands and previous outcomes.
GS/Satellite targets name nodes; Zone targets resolve through service assignments.
Use assignment.service_key to preserve Zone scope; world.get_node/get_ground_station
provide static entity data. Reference: docs/api/planning.md (service and routing).
For local candidates/data, see templates/dataplane/threshold/scene.py and
docs/development/dataplane.md. Use tools.development.check_model/check_scene
from a separate driver; importing this template runs nothing.
"""
from collections import deque
from skyseed_v2.simulator import planner


class SimpleRouteModel:
    def plan_route_outcomes(self, physical_world, operational_connections,
                            zone_service_assignments, demands, previous_outcomes, *, timestamp):
        adjacency = {}
        for connection in operational_connections:
            a, b = connection.connection_id.endpoint_a, connection.connection_id.endpoint_b
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
        assignments = {a.service_key: a.satellite_id for a in zone_service_assignments}
        tables, outcomes = {}, []
        for demand in demands:
            if demand.target.network_scope:
                raise ValueError("SimpleRouteModel implements unscoped routing only")
            ground_target = isinstance(demand.target, planner.GroundStationRouteDemandTarget)
            terminal = (demand.target.ground_station_id if ground_target
                        else demand.target.satellite_id if isinstance(demand.target, planner.SatelliteRouteDemandTarget)
                        else assignments[demand.target.service_key])
            if terminal is None:
                outcomes.append(planner.UnreachableRouteOutcome(demand, planner.RouteUnreachableReason.ZONE_UNSERVED))
                continue
            if terminal not in tables:
                towards, queue = {terminal: None}, deque((terminal,))
                while queue:
                    current = queue.popleft()
                    # A GS can be the destination, but never an intermediate router.
                    if current != terminal and current.entity_kind == "ground-station":
                        continue
                    for neighbor in sorted(adjacency.get(current, ()), key=lambda n: n.value):
                        if neighbor not in towards:
                            towards[neighbor] = current
                            queue.append(neighbor)
                tables[terminal] = towards
            towards = tables[terminal]
            if demand.source == terminal or demand.source not in towards or (ground_target and demand.source.entity_kind != "satellite"):
                outcomes.append(planner.UnreachableRouteOutcome(demand, planner.RouteUnreachableReason.NO_ELIGIBLE_PATH))
                continue
            path = [demand.source]
            while path[-1] != terminal:
                path.append(towards[path[-1]])
            outcomes.append(planner.ReachableRouteOutcome(demand,
                path[1], tuple(path)))
        return planner.RoutePlanningResult(tuple(outcomes))
