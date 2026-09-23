"""Three-node teaching scene, with explicit static links and fresh Model owners."""
from datetime import datetime, timedelta, timezone
from skyseed.shared import NodeId
from skyseed.simulator import (
    SimulationDefinition, SimulationTimeline, create_default_simulator,
    DefaultSimulatorConfiguration, PlannerConfiguration, RoutePlanningResult,
    LocalRouteOutcome, RouteCandidate, RouteDisposition, LogicalExit, PolicyRecord,
    RouteDemand, GroundStationRouteDemandTarget,
)
from skyseed.simulator.input import SimulatorStaticInput, SatelliteSimulationInput, GroundStationSimulationInput
from skyseed.simulator.physical import InterSatelliteLinkCandidate, GroundStationAccessCandidate
from skyseed.simulator.geometry import EarthFixedPosition
from templates.models.position import StaticPositionModel
from templates.models.physical_link import StaticPhysicalLinkModel
from templates.models.inter_satellite import SimpleInterSatelliteModel
from templates.models.ground_station import SimpleGroundStationModel
from templates.models.zone_service import FirstCandidateServiceModel
from templates.models.parameters import FixedParameterModel

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
A, B = (NodeId(65001, 'satellite', name) for name in ('a', 'b'))
G = NodeId(65001, 'ground-station', 'g')


class ThresholdRoutes:
    def __init__(self, threshold=128):
        self.threshold = threshold

    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        outcomes = []
        for demand in demands:
            source = demand.source
            candidates = []
            for connection in connections:
                c = connection.connection_id
                if source in (c.endpoint_a, c.endpoint_b):
                    exit = LogicalExit(c, source)
                    # A chooses direct G or via B; B always continues to G.
                    if source == A or (source == B and exit.peer == G):
                        candidates.append(RouteCandidate(exit, PolicyRecord(weight=100)))
            candidates.sort(key=lambda c: (c.exit.peer != G, c.exit.peer.value))
            outcomes.append(LocalRouteOutcome(demand,
                RouteDisposition.PASS if source == G else RouteDisposition.FORWARD,
                () if source == G else tuple(candidates), PolicyRecord(threshold=self.threshold)))
        return RoutePlanningResult(tuple(outcomes))


def create_scene(route_model=None, *, duration=10):
    """Called separately for each stateful trial or ordinary preparation."""
    satellites = tuple(SatelliteSimulationInput(n, n.entity_id, 'shell', 'orbit', i,
        550, 0, 0, i, START, maximum_simultaneous_inter_satellite_links=2)
        for i, n in enumerate((A, B)))
    station = GroundStationSimulationInput(G, 'g', 0, 0, 0, 'gateway', None,
                                           max_simultaneous_satellite_accesses=2)
    definition = SimulationDefinition(SimulatorStaticInput(satellites, (station,)),
        SimulationTimeline(START, START + timedelta(seconds=duration), timedelta(seconds=5)))
    return create_default_simulator(definition,
        configuration=DefaultSimulatorConfiguration(zone_layout_definition=None),
        position_model=StaticPositionModel({A: EarthFixedPosition(7e6, 0, 0),
            B: EarthFixedPosition(7e6, 1000, 0), G: EarthFixedPosition(6.4e6, 0, 0)}),
        physical_link_model=StaticPhysicalLinkModel(inter_satellite=(InterSatelliteLinkCandidate(A, B),),
            ground_station=(GroundStationAccessCandidate(G, A), GroundStationAccessCandidate(G, B))),
        inter_satellite_model=SimpleInterSatelliteModel(((A, B),)),
        ground_station_model=SimpleGroundStationModel(), zone_service_model=FirstCandidateServiceModel(),
        parameter_model=FixedParameterModel(), route_model=route_model or ThresholdRoutes(),
        planner_configuration=PlannerConfiguration(inter_satellite_acquisition_duration=timedelta(0),
            ground_station_acquisition_duration=timedelta(0), frames_per_batch=1),
        route_demands=tuple(RouteDemand(n, GroundStationRouteDemandTarget(G)) for n in (A, B, G)))
