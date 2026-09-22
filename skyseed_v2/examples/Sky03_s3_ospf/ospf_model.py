"""Destination-consistent OSPF computation; no protocol or backend machinery."""
from __future__ import annotations
from datetime import datetime

from collections.abc import Mapping
from collections import defaultdict
from itertools import product
from dataclasses import dataclass
from heapq import heappop, heappush

from skyseed_v2.shared import NodeId

from skyseed_v2.simulator.physical import PhysicalWorld

from skyseed_v2.simulator.planner.routing._demands import _GatewayDemandConstruction
from skyseed_v2.simulator.planner.routing import RoutePlanningResult
from skyseed_v2.simulator.planner._scopes import _validated_satellite_scopes, _validate_scope_world, _connections_by_scope
from skyseed_v2.simulator.planner.values import (
    GroundStationRouteDemandTarget, SatelliteRouteDemandTarget, OperationalConnection, OperationalConnectionId, OperationalConnectionKind,
    RouteDemand, RouteOutcome, ZoneServiceAssignment,
    ReachableRouteOutcome, RouteUnreachableReason,
    UnreachableRouteOutcome, _connection_key, _node_key, _route_demand_key,
)


def _area(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 0xffffffff):
        raise ValueError("area_id must be a 32-bit non-negative integer or None (excluded)")


@dataclass(frozen=True, slots=True)
class OspfInterfaceConfiguration:
    """Caller-supplied area and directional costs for one potential Connection.

    Layer: example-local route configuration. The example author creates it
    to describe actual interfaces and costs. It is not a configuration
    framework or operational Connection.

    Both ends of the point-to-point link use area_id; None explicitly excludes
    the link. Costs name the canonical ConnectionId endpoint order. An omitted
    cost uses the Model's cost_metric. This value owns no link or lifecycle.
    """

    connection_id: OperationalConnectionId
    area_id: int | None = 0
    cost_a_to_b: int | None = None
    cost_b_to_a: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        _area(self.area_id)
        for value in (self.cost_a_to_b, self.cost_b_to_a):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("OSPF interface costs must be positive integers or None")


class OspfRoutePlanningModel(_GatewayDemandConstruction):
    """Replaceable ordinary-area, single-next-hop OSPF computational subset.

    Layer: example-local Simulator route algorithm. The example author supplies
    it to replace route computation independently. It is not a protocol stack
    or forwarding backend.

    Planner owns invocation/shutdown. Configuration belongs to the caller;
    current area graphs and requested destination tables are bounded derived
    work. See this example’s README for RFC mapping and terminal conventions.
    """

    def __init__(
        self,
        *,
        interfaces: tuple[OspfInterfaceConfiguration, ...] = (),
        default_area: int | None = 0,
        cost_metric: str = "unit",
        satellite_scopes: Mapping[NodeId, str] | None = None,
        gateway_candidate_count: int = 1,
        gateway_candidate_max_distance_m: float | None = None,
    ) -> None:
        self._initialize_demands(gateway_candidate_count, gateway_candidate_max_distance_m)
        _area(default_area)
        if cost_metric not in {"unit", "delay_us"}:
            raise ValueError("cost_metric must be 'unit' or 'delay_us'")
        interfaces = tuple(interfaces)
        if any(not isinstance(v, OspfInterfaceConfiguration) for v in interfaces):
            raise TypeError("interfaces must contain OspfInterfaceConfiguration values")
        self._interfaces = tuple(sorted(interfaces, key=lambda v: _connection_key(v.connection_id)))
        self._configured = {v.connection_id: v for v in interfaces}
        if len(self._configured) != len(interfaces):
            raise ValueError("duplicate OSPF interface configuration")
        self._default_area = default_area
        self._cost_metric = cost_metric
        self._satellite_scopes = _validated_satellite_scopes(satellite_scopes)
        self._world = None
        self._satellites = {}
        self._memberships = {}
        self._graphs = {}
        self._destinations = {}
        self._closed = False

    def can_reuse_unchanged_inputs(self) -> bool:
        return True

    def depends_on_connection_parameters(self) -> bool:
        """Declare whether parameter-only changes can affect route decisions."""
        return self._cost_metric == "delay_us"

    def describe_configuration(self):
        return {"routing_semantics": "ospf", "interfaces": self._interfaces, "default_area": self._default_area,
                "cost_metric": self._cost_metric, "satellite_scopes": self._satellite_scopes,
                "gateway_candidate_count": self._gateway_candidate_count,
                "gateway_candidate_max_distance_m": self._gateway_candidate_max_distance_m}

    def create_route_demands(
        self,
        physical_world: PhysicalWorld,
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        *, timestamp: datetime,
    ) -> tuple[RouteDemand, ...]:
        """Construct the shared geographical gateway demands."""
        if self._closed:
            raise RuntimeError("Route Model is shut down")
        return self._create_route_demands(physical_world, zone_service_assignments)

    def shutdown(self, *, wait: bool = True) -> None:
        """Release current derived work; idempotent, with no worker processes."""
        self._graphs.clear()
        self._destinations.clear()
        self._closed = True

    def route_resource_statistics(self) -> Mapping[str, int]:
        """Read current route worker/resource counts without mutation."""
        return {"route_pool_workers": 0}

    def route_work_statistics(self) -> Mapping[str, int]:
        """Read bounded counters from the last route evaluation without mutation."""
        return dict(self._construction_work)

    def _prepare(self, world):
        if self._closed:
            raise RuntimeError("OspfRoutePlanningModel is shut down")
        self._bind_world(world)
        if self._world is world:
            return
        _validate_scope_world(world, self._satellite_scopes)
        shells = {}
        for system in world.autonomous_systems:
            for shell in system.shells:
                for orbit in shell.orbits:
                    for sat in orbit.satellites:
                        shells[sat.node_id] = (system.asn, shell.shell_id)
        self._satellites = {node: self._satellite_scopes.get(node, "") for node in shells}
        self._memberships = {node: set() for node in shells}
        for interface in self._interfaces:
            link = interface.connection_id
            for node in (link.endpoint_a, link.endpoint_b):
                world.get_node(node)
            if link.kind is OperationalConnectionKind.INTER_SATELLITE:
                a, b = link.endpoint_a, link.endpoint_b
                if shells[a] != shells[b] or self._satellites[a] != self._satellites[b]:
                    raise ValueError("OSPF interface configuration cannot connect Shells or network scopes")
                if interface.area_id is not None:
                    self._memberships[a].add(interface.area_id)
                    self._memberships[b].add(interface.area_id)
            elif interface.area_id is not None:
                # An explicit terminal stub is an interface of its satellite.
                # It reserves that area even while its operational access is down;
                # the GS itself never participates in the router graph.
                satellite = (link.endpoint_a if link.endpoint_a.entity_kind == "satellite"
                             else link.endpoint_b)
                self._memberships[satellite].add(interface.area_id)
        # Configured interface membership survives outages. A lost backbone cannot
        # turn an ABR into an internal router that accepts spoke summaries.
        self._shells = shells
        self._world = world

    def _graph(self, scope, connections):
        rows = []
        for connection in connections:
            link = connection.connection_id
            a, b = link.endpoint_a, link.endpoint_b
            satellites = [n for n in (a, b) if n.entity_kind == "satellite"]
            if len(satellites) == 2 and self._shells[a] != self._shells[b]:
                continue
            interface = self._configured.get(link)
            area = interface.area_id if interface is not None else self._default_area
            if interface is not None and area is None:
                continue
            costs = [1, 1] if self._cost_metric == "unit" else [
                max(1, connection.endpoint_a_to_b.delay_us),
                max(1, connection.endpoint_b_to_a.delay_us),
            ]
            if interface is not None:
                costs = [interface.cost_a_to_b or costs[0], interface.cost_b_to_a or costs[1]]
            # Unconfigured access inherits satellite areas; never default_area
            # exclusion (which controls unlisted ISLs).
            if len(satellites) == 2 and area is None:
                continue
            rows.append((a, b, area, *costs, interface is not None))
        signature = tuple(sorted(rows, key=lambda r: (_node_key(r[0]), _node_key(r[1]))))
        old = self._graphs.get(scope)
        if old is not None and old[0] == signature:
            return old[1]
        members = {n: set(self._memberships[n]) for n, s in self._satellites.items() if s == scope}
        reverse, accesses = {}, {}
        for a, b, area, ab, ba, explicit in signature:
            if a.entity_kind == b.entity_kind == "satellite":
                members[a].add(area)
                members[b].add(area)
                reverse.setdefault(area, {}).setdefault(b, []).append((a, ab))
                reverse[area].setdefault(a, []).append((b, ba))
        for n, areas in members.items():
            if not areas and self._default_area is not None:
                areas.add(self._default_area)
        for a, b, area, ab, ba, explicit in signature:
            if b.entity_kind == "ground-station":
                a, b, ab, ba = b, a, ba, ab
            if a.entity_kind == "ground-station":
                areas = (area,) if explicit else tuple(sorted(members[b]))
                accesses.setdefault(a, []).append((b, areas, ab, ba))
        costs = {}
        for area_graph in reverse.values():
            for target, incoming in area_graph.items():
                for source, cost in incoming:
                    costs[source, target] = cost
        for gs, links in accesses.items():
            for sat, _, inbound, outbound in links:
                costs[gs, sat], costs[sat, gs] = inbound, outbound
        backbone = frozenset(n for n, areas in members.items() if 0 in areas)
        abrs = frozenset(n for n, areas in members.items() if len(areas) > 1 or 0 in areas)
        graph = (members, reverse, accesses, costs, backbone, abrs)
        self._graphs[scope] = signature, graph
        # Each scope retains only the latest graph and requested destinations.
        self._destinations[scope] = {}
        return graph

    def plan_route_outcomes(
        self,
        physical_world: PhysicalWorld,
        operational_connections: tuple[OperationalConnection, ...],
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        demands: tuple[RouteDemand, ...],
        previous_outcomes: tuple[RouteOutcome, ...],
        *, timestamp: datetime,
    ) -> RoutePlanningResult:
        self._prepare(physical_world)
        scopes = set(self._satellites.values())
        assignments = {v.service_key: v.satellite_id for v in zone_service_assignments}
        previous = {v.demand: v for v in previous_outcomes}
        if len(previous) != len(previous_outcomes):
            raise ValueError("previous_outcomes contains a duplicate")
        ordered = tuple(sorted(demands, key=_route_demand_key))
        if len(set(ordered)) != len(ordered):
            raise ValueError("demands contains a duplicate")
        grouped = {}
        for demand in ordered:
            scope = demand.target.network_scope
            if self._satellite_scopes and scope not in scopes:
                raise ValueError("Route demand has unknown network scope")
            key = scope if self._satellite_scopes else ""
            physical_world.get_node(demand.source)
            if demand.source.entity_kind == "satellite" and self._satellites[demand.source] != key:
                raise ValueError("Route source is outside its network scope")
            ground = isinstance(demand.target, GroundStationRouteDemandTarget)
            terminal = (demand.target.ground_station_id if ground
                        else demand.target.satellite_id if isinstance(demand.target, SatelliteRouteDemandTarget)
                        else assignments.get(demand.target.service_key))
            if terminal is not None:
                physical_world.get_node(terminal)
                if not ground and self._satellites[terminal] != key:
                    raise ValueError("Satellite terminal is outside its network scope")
            grouped.setdefault(key, []).append((demand, ground, terminal))
        outcomes = []
        connections_by_scope = _connections_by_scope(operational_connections, self._satellites)
        computed = reused = 0
        for scope, requests in grouped.items():
            graph = self._graph(scope, connections_by_scope.get(scope, ()))
            old_tables = self._destinations.get(scope, {})
            tables = {}
            for demand, ground, terminal in requests:
                path = None
                reason = RouteUnreachableReason.ZONE_UNSERVED if terminal is None else None
                if terminal is not None:
                    key = ground, terminal
                    if key not in tables:
                        if key in old_tables:
                            tables[key] = old_tables[key]
                            reused += 1
                        else:
                            tables[key] = _destination_table(graph, ground, terminal)
                            computed += 1
                    path = _source_path(graph, tables[key], demand.source)
                    if path is None or len(path) < 2:
                        reason = RouteUnreachableReason.NO_ELIGIBLE_PATH
                prior = previous.pop(demand, None)
                if (reason is None and isinstance(prior, ReachableRouteOutcome) and prior.path == path
                    or reason is not None and isinstance(prior, UnreachableRouteOutcome) and prior.reason is reason):
                    outcome = prior
                elif reason is None:
                    outcome = ReachableRouteOutcome(demand, path[1], path)
                else:
                    outcome = UnreachableRouteOutcome(demand, reason)
                outcomes.append(outcome)
            self._destinations[scope] = tables
        for scope in set(self._graphs) - set(grouped):
            del self._graphs[scope]
            self._destinations.pop(scope, None)
        self._construction_work.update(route_destination_jobs=computed, route_destination_jobs_reused=reused)
        return RoutePlanningResult(tuple(outcomes))


def _spf(reverse, seeds):
    """Reverse SPF once per area/destination; costs are outgoing at predecessor."""
    distance, next_hop, pending = {}, {}, []
    for node, (cost, hop) in seeds.items():
        distance[node], next_hop[node] = cost, hop
        heappush(pending, (cost, _node_key(node), node))
    while pending:
        cost, _, node = heappop(pending)
        if distance[node] != cost:
            continue
        for predecessor, edge_cost in reverse.get(node, ()):
            total = cost + edge_cost
            prior = distance.get(predecessor)
            if prior is None or total < prior or (total == prior and _node_key(node) < _node_key(next_hop[predecessor])):
                distance[predecessor], next_hop[predecessor] = total, node
                heappush(pending, (total, _node_key(predecessor), predecessor))
    return {node: (cost, next_hop[node]) for node, cost in distance.items()}


def _destination_table(graph, ground, terminal):
    members, reverse, accesses, costs, backbone, abrs = graph
    seeds = {}
    if ground:
        for sat, areas, _, outgoing in accesses.get(terminal, ()):
            for area in areas:
                seeds.setdefault(area, {})[sat] = outgoing, terminal
    else:
        for area in members[terminal]:
            seeds.setdefault(area, {})[terminal] = 0, None
    intra = {}
    for area, roots in seeds.items():
        for node, (cost, hop) in _spf(reverse.get(area, {}), roots).items():
            candidate = cost, area, hop
            if node not in intra or candidate[:2] < intra[node][:2]:
                intra[node] = candidate
    choices = {n: hop for n, (_, _, hop) in intra.items()}
    # Exact cost summaries from reachable area destinations at backbone routers.
    summaries = {n: (intra[n][0], intra[n][2]) for n in backbone if n in intra}
    backbone_routes = ({} if set(seeds) <= {0} or backbone <= intra.keys()
                       else _spf(reverse.get(0, {}), summaries))
    for n, (_, hop) in backbone_routes.items():
        if n not in intra:
            choices[n] = hop
    for area in sorted(set(reverse) | set(seeds)):
        if area == 0:
            continue
        # Internal routers compare intra-area distance to each advertising ABR
        # plus its summary cost. That SPF may traverse other ABRs. Its choices
        # are installed only at internal routers: an ABR keeps its own
        # intra-area/backbone decision, not this ordinary-area SPF continuation.
        summaries = {}
        for n in backbone & abrs:
            if area not in members[n]:
                continue
            if n in intra:
                if intra[n][1] != area:
                    summaries[n] = intra[n][0], intra[n][2]
            elif n in backbone_routes:
                summaries[n] = backbone_routes[n]
        for n, (_, hop) in _spf(reverse.get(area, {}), summaries).items():
            if n not in intra and n not in abrs:
                choices[n] = hop
    # Resolve the one selected forwarding function, never independent SPF paths.
    # Intra-area cost decreases; inter-area forwarding reaches backbone then
    # intra-area forwarding. This also handles multi-attached destinations.
    paths = {terminal: (terminal,)}
    for source in sorted(members, key=_node_key):
        chain, seen, current = [], set(), source
        while current not in paths and current in choices:
            if current in seen:
                raise ValueError("OSPF computation produced a forwarding loop")
            seen.add(current)
            chain.append(current)
            current = choices[current]
        tail = paths.get(current)
        for node in reversed(chain):
            tail = None if tail is None else (node, *tail)
            paths[node] = tail
    return paths, {terminal: 0}


def _source_path(graph, table, source):
    paths, tail_costs = table
    if source in paths or source.entity_kind != "ground-station":
        return paths.get(source)
    # Resolve only requested GS sources. Most GS destinations are requested by
    # satellites; computing every possible GS source for each was wasted work.
    _, _, accesses, costs, _, _ = graph
    candidates = []
    for sat, areas, inbound, _ in accesses.get(source, ()):
        path = paths.get(sat)
        if not areas or not path:
            continue
        for i in range(len(path)-2, -1, -1):
            if path[i] not in tail_costs:
                tail_costs[path[i]] = costs[path[i], path[i+1]] + tail_costs[path[i+1]]
        candidates.append((inbound + tail_costs[sat], _node_key(sat), sat))
    sat = min(candidates)[2] if candidates else None
    paths[source] = None if sat is None else (source, *paths[sat])
    return paths[source]


# Fixed identity selection at the prepared input epoch, never instantaneous positions.
ORBITS_PER_AREA = 6
BACKBONE_PHASE_START_DEG = 0
BACKBONE_PHASE_WIDTH_DEG = 72
COST_METRIC = "unit"  # Also supported by the Model: "delay_us".


def area_interfaces(static_input):
    """Assign the default planner's potential S3 ISLs to stable interface areas."""
    by_orbit = defaultdict(list)
    for satellite in static_input.list_satellites():
        by_orbit[satellite.orbit_id].append(satellite)
    orbits = sorted(by_orbit.values(), key=lambda values: (
        values[0].right_ascension_deg, values[0].orbit_id,
    ))
    regions = {}
    for index, satellites in enumerate(orbits):
        satellites.sort(key=lambda s: (s.mean_anomaly_deg, s.node_id.value))
        for satellite in satellites:
            in_band = ((satellite.mean_anomaly_deg - BACKBONE_PHASE_START_DEG) % 360
                       < BACKBONE_PHASE_WIDTH_DEG)
            regions[satellite.node_id] = (
                0 if index % ORBITS_PER_AREA == ORBITS_PER_AREA - 1 or in_band
                else index // ORBITS_PER_AREA + 1
            )
    interfaces = []
    for index, satellites in enumerate(orbits):
        # Default ISLs: cyclic same-plane neighbors; any candidate pairing in
        # adjacent RAAN-sorted orbits, including the final-to-first boundary.
        pairs = list(zip(satellites, satellites[1:] + satellites[:1]))
        pairs.extend(product(satellites, orbits[(index + 1) % len(orbits)]))
        for first, second in pairs:
            a, b = regions[first.node_id], regions[second.node_id]
            if a and b and a != b:
                raise ValueError("ordinary regions need a backbone orbit between them")
            interfaces.append(OspfInterfaceConfiguration(
                OperationalConnectionId(
                    OperationalConnectionKind.INTER_SATELLITE,
                    first.node_id, second.node_id,
                ),
                area_id=a or b,  # both backbone -> 0; otherwise the internal region
                # Optional positive directional overrides can be set here;
                # omitted costs use COST_METRIC in canonical endpoint order.
            ))
    return tuple(interfaces)



def create_s3_route_model(static_input):
    """Bind S3’s fixed Areas to input identities and phases, with unit costs."""
    return OspfRoutePlanningModel(
        interfaces=area_interfaces(static_input), default_area=None,
        cost_metric=COST_METRIC, gateway_candidate_count=3,
    )
