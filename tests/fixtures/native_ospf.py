"""Deterministic validation scene: GS withdrawal/move, then ISL recreation.

Seconds 0–60: g→a; 60–90: g detached; 90 onward: g→c.
The b–c ISL is absent during 150–180 seconds. h→b and a–b remain.
The 240-second horizon includes FRR's ordinary broadcast adjacency wait.
The service-enabled live variant shifts link events 30 seconds later and runs
390 seconds: the service is on c during 30–150 and 300 onward. Probe windows
allow default broadcast election/SPF recovery without changing protocol timers.
"""
from datetime import datetime, timedelta, timezone
import seedemu as seed
from skyseed import emulator, simulator
from skyseed.shared import NodeId
from skyseed.simulator import physical
from skyseed.simulator.geometry import EarthFixedPosition
from templates.models.position import StaticPositionModel
from templates.models.inter_satellite import SimpleInterSatelliteModel
from templates.models.ground_station import SimpleGroundStationModel
from templates.models.zone_service import FirstCandidateServiceModel
from templates.models.parameters import FixedParameterModel
from skyseed.configuration.simulator import (
    ZoneLayoutDefinition, ZoneDefinition, LatitudeLongitudeRectangleDefinition,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
A, B, C = (NodeId(65040, 'satellite', n) for n in ('a', 'b', 'c'))
G, H = (NodeId(65040, 'ground-station', n) for n in ('g', 'h'))


def build_small():
    network, base = seed.Emulator(), seed.Base()
    system = emulator.seedemu.EmulatorAutonomousSystem(65040)
    system.setIbgpMode('disabled')
    base.setAutonomousSystem(system)
    orbit = system.create_shell('small-s3').create_orbit('orbit', parameters=emulator.entities.OrbitParameters(
        altitude_km=550, inclination_deg=53, right_ascension_deg=0, epoch_utc=START))
    for i, n in enumerate((A, B, C)):
        orbit.create_satellite(n.entity_id, orbit_index=i, mean_anomaly_deg=i,
                               maximum_simultaneous_inter_satellite_links=2)
    system.createNetwork('ground-lan', '203.0.0.0/16')
    for i, n in enumerate((G, H), 2):
        gs = system.create_ground_station(n.entity_id, station_type='gateway', operator=None,
            location=emulator.entities.GroundStationLocation(latitude_deg=0, longitude_deg=i, elevation_m=0),
            max_simultaneous_satellite_accesses=1)
        gs.joinNetwork('ground-lan', f'203.0.0.{i}')
    network.addLayer(base)
    return network


class ScheduledPhysicalLinks:
    def __init__(self, service=False):
        self.service = service

    def compute_observation(self, world, frame, request):
        t = (frame.timestamp - START).total_seconds()
        if self.service:
            t -= 30  # Leave time for ordinary FRR broadcast election and SPF.
        pairs = [(A, B)] + ([(B, C)] if not 150 <= t < 180 else [])
        accesses = [(H, B)] + ([(G, A)] if t < 60 else [(G, C)] if t >= 90 else [])
        return physical.PhysicalLinkQueryResult(timestamp=frame.timestamp,
            inter_satellite_candidates=tuple(physical.InterSatelliteLinkCandidate(*p) for p in pairs),
            ground_station_access_candidates=tuple(physical.GroundStationAccessCandidate(*p) for p in accesses),
            zone_service_candidates=tuple(physical.ZoneServiceCandidate(z.zone_id, C)
                for z in world.get_zone_layout().zones) if self.service else ())


class ScheduledService:
    """One test-only service /32, independent of GS destination routing policy."""
    def assign_zone_service(self, world, candidates, previous, *, timestamp):
        t = (timestamp - START).total_seconds()
        selected = C if 30 <= t < 150 or t >= 300 else None
        return simulator.ZoneServicePlanningResult(tuple(
            simulator.ZoneServiceAssignment(z.zone_id, selected) for z in world.get_zone_layout().zones))


def create_small_simulator(static, routes, *, duration=240, service=False,
                           physical_links=None, zone_service=None, route_demands=()):
    layout = ZoneLayoutDefinition(zones=(ZoneDefinition(zone_id='service-probe', priority=1,
        geometry=LatitudeLongitudeRectangleDefinition(south_latitude_deg=-10, north_latitude_deg=10,
            west_longitude_deg=-10, east_longitude_deg=10)),), allow_overlap=False) if service else None
    return simulator.create_default_simulator(simulator.SimulationDefinition(static,
        simulator.SimulationTimeline(START, START + timedelta(seconds=duration), timedelta(seconds=5))),
        configuration=simulator.DefaultSimulatorConfiguration(zone_layout_definition=layout),
        position_model=StaticPositionModel({n: EarthFixedPosition(7e6, i * 1000, 0)
            for i, n in enumerate((A, B, C, G, H))}),
        physical_link_model=physical_links or ScheduledPhysicalLinks(service),
        inter_satellite_model=SimpleInterSatelliteModel(((A, B), (B, C))),
        ground_station_model=SimpleGroundStationModel(),
        zone_service_model=zone_service or (ScheduledService() if service else FirstCandidateServiceModel()),
        parameter_model=FixedParameterModel(), route_model=routes,
        planner_configuration=simulator.PlannerConfiguration(inter_satellite_acquisition_duration=timedelta(0),
            ground_station_acquisition_duration=timedelta(0), frames_per_batch=1), route_demands=route_demands)
