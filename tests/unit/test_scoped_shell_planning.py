"""Scoped service identities survive shared-GS planning, compilation and replay."""
from tests._support import MODEL_TIMESTAMP, selection_state
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from test_planning_reference_models import (
    START, _satellite, _ground_station, _operational_connection,
)
from skyseed.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition, ZoneDefinition, ZoneLayoutDefinition,
)
from skyseed.shared import NodeId
from skyseed.simulator import (
    RouteOutcomeTransition, ZoneServiceTransition,
    Simulator, SimulationDefinition, SimulationTimeline, CircularOrbitPositionModel,
    CoverageFirstGroundStationAccessPlanningModel, HighestElevationZoneServicePlanningModel,
    GatewayHopCountRoutePlanningModel, GroundStationAccessCandidate, ZoneServiceCandidate,
    ZoneServiceAssignment, ZoneServiceTransition, ZoneRouteDemandTarget, GroundStationRouteDemandTarget,
    RouteDemand, ReachableRouteOutcome, UnreachableRouteOutcome, RouteUnreachableReason,
    OperationalConnectionKind, PlanTransition, RouteOutcomeTransition,
    create_plan, apply_plan, planned_operational_state_fingerprint,
)
from skyseed.simulator.input import SimulatorStaticInput
from skyseed.simulator.planner.evidence import plan_to_data, plan_from_data
from skyseed.backend.bpf import compile_bpf
from skyseed.backend.execution_materials.sealing import _write_runner_artifact
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.backend.execution_materials.mechanics import _empty_state
from skyseed.backend.execution_materials.mechanics import _BoundaryStateIndexes


@pytest.fixture
def scoped_world():
    satellites = tuple(replace(_satellite(f'{scope}-{i}', i), shell_id=scope,
        orbit_id=scope+'-orbit', mean_anomaly_deg=i*90,
        max_simultaneous_ground_station_accesses=2) for scope in ('S1','S2') for i in range(3))
    ground = replace(_ground_station('g', 0), max_simultaneous_satellite_accesses=4)
    sim = Simulator(SimulationDefinition(SimulatorStaticInput(satellites, (ground,)),
        SimulationTimeline(START, START+timedelta(seconds=5), timedelta(seconds=5))))
    world = sim.create_physical_world(position_model=CircularOrbitPositionModel(),
        zone_layout_definition=ZoneLayoutDefinition(zones=(ZoneDefinition(zone_id='z', priority=1,
            geometry=LatitudeLongitudeRectangleDefinition(south_latitude_deg=-1,north_latitude_deg=1,
                west_longitude_deg=-1,east_longitude_deg=1)),), allow_overlap=False))
    scopes = {s.node_id:s.shell_id for s in satellites}
    return world, ground.node_id, tuple(s.node_id for s in satellites), scopes


def test_reserved_quotas_include_retained_preparation_without_borrowing(scoped_world):
    world, ground, sats, scopes = scoped_world
    model = CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=4,
        satellite_scopes=scopes, ground_station_scope_limits={'S1':2,'S2':2})
    candidates = tuple(GroundStationAccessCandidate(ground,s,1000,70-i,4) for i,s in enumerate(sats))
    retained = frozenset((ground,s) for s in (sats[1],sats[2],sats[3]))
    selected = model.select_ground_station_accesses(world,candidates,selection_state(retained))
    assert {scope:sum(scopes[c.satellite_id]==scope for c in selected) for scope in ('S1','S2')} == {'S1':2,'S2':2}
    sparse = tuple(c for c in candidates if c.satellite_id != sats[4] and c.satellite_id != sats[5])
    selected = model.select_ground_station_accesses(world,sparse,selection_state(retained))
    assert len(selected)==3
    assert sum(scopes[c.satellite_id]=='S1' for c in selected)==2
    assert {s for g,s in retained} <= {c.satellite_id for c in selected}
    with pytest.raises(ValueError,match='physical GS capacity'):
        CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=4,
            satellite_scopes=scopes,ground_station_scope_limits={'S1':3,'S2':2}).select_ground_station_accesses(world,candidates, state=selection_state())


def test_scoped_service_and_routes_compile_and_replay_without_shared_gs_overwrite(scoped_world,tmp_path):
    world, ground, sats, scopes = scoped_world
    zone = HighestElevationZoneServicePlanningModel(satellite_scopes=scopes)
    candidates = tuple(ZoneServiceCandidate('z',s,1000,70-i,4) for i,s in enumerate(sats))
    assigned = zone.assign_zone_service(world,candidates,(), timestamp=MODEL_TIMESTAMP)
    assert [(v.network_scope,v.satellite_id) for v in assigned.assignments] == [('S1',sats[0]),('S2',sats[3])]
    missing = zone.assign_zone_service(world,candidates[:3],assigned.assignments, timestamp=MODEL_TIMESTAMP)
    assert missing.assignments[1] == ZoneServiceAssignment('z',None,'S2')
    connections = tuple(_operational_connection(OperationalConnectionKind.GROUND_STATION_ACCESS,ground,s)
                        for s in (sats[0],sats[3]))
    # Deliberately include a cross-scope edge: configurable scoped route Models must exclude it.
    cross = _operational_connection(OperationalConnectionKind.INTER_SATELLITE,sats[0],sats[3])
    route = GatewayHopCountRoutePlanningModel(gateway_candidate_count=1,satellite_scopes=scopes)
    try:
        demands = route.create_route_demands(world,assigned.assignments, timestamp=MODEL_TIMESTAMP)
        assert len(demands)==4
        result = route.plan_route_outcomes(world,connections+(cross,),assigned.assignments,demands,(), timestamp=MODEL_TIMESTAMP)
        for outcome in result.outcomes:
            assert isinstance(outcome,ReachableRouteOutcome)
            assert all(n==ground or scopes[n]==outcome.demand.target.network_scope for n in outcome.path)
        no_s2_access = route.plan_route_outcomes(world,connections[:1]+(cross,),assigned.assignments,demands,result.outcomes, timestamp=MODEL_TIMESTAMP)
        assert all(isinstance(o,UnreachableRouteOutcome) for o in no_s2_access.outcomes if o.demand.target.network_scope=='S2')
        transition = PlanTransition(START, established_connections=connections,
            zone_service_transitions=tuple(ZoneServiceTransition(a.zone_id, None, a.satellite_id, a.network_scope) for a in assigned.assignments), route_outcome_transitions=tuple(RouteOutcomeTransition(o.demand, None, o) for o in result.outcomes))
        plan = create_plan(None,previous_plan_id=None,physical_world_fingerprint=world.semantic_fingerprint(),
            observation_interval=timedelta(seconds=5),start_timestamp=START,transitions=(transition,))
        assert plan_from_data(json.loads(json.dumps(plan_to_data(plan)))) == plan
        compiled = compile_bpf(plan,world,None)
        prefixes = dict(compiled.zone_prefixes)
        assert set(prefixes)=={('S1','z'),('S2','z')}
        assert len(set(prefixes.values()))==2
        bindings = [b for b in compiled.intended_forwarding if b.route_key.source==ground]
        assert len(bindings)==2 and len({b.packet_prefix for b in bindings})==2
        manifest = _write_runner_artifact((compiled,),tmp_path/'artifact')
        artifact = _open_runner_artifact(manifest)
        try:
            assert {tuple(k): v for k, v in artifact._snapshot["zone_prefixes"]} == prefixes
            assert len(artifact.boundaries()) == len(compiled.compiled_activation_boundaries)
            assert {v.service_key for v in compiled.intended_zone_edges} == set(prefixes)
        finally:
            artifact.close()
        data=json.loads(manifest.read_text());data['format_version']=3;manifest.write_text(json.dumps(data))
        with pytest.raises(ValueError,match='version'):
            _open_runner_artifact(manifest)
    finally:
        route.shutdown()


def test_traffic_keeps_explicit_scoped_identity_without_routes(scoped_world):
    from tools.generation.traffic.workload import select_workload
    world, ground, sats, scopes = scoped_world
    result = select_workload(world, pairs=[(ground.value, 'S1', 'z'), (ground.value, 'S2', 'z')])
    assert len(result.communications) == 4
    assert {c.network_scope for c in result.communications} == {'S1', 'S2'}
    assert {c.direction for c in result.communications} == {'ground-to-zone', 'zone-to-ground'}
