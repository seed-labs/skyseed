"""A hook edits execution plans, including outage recovery across batch boundaries."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from skyseed import Scene
from skyseed.pc.physical import LinkCandidate
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkOperation, LinkPlan
from examples.s4_handover.experiment.perturbation import RegionalGroundOutage, inside_region, outage_description

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds):
    return START + timedelta(seconds=seconds)


def connection(first, second, delay=.01):
    identity = tuple(sorted((first, second)))
    reports = {first: LinkCandidate(first, second), second: LinkCandidate(second, first)}
    return LinkConfiguration(ConnectionCandidate(identity, reports), LinkProperties(identity, 100, delay))


def test_regional_outage_preserves_other_links_and_restores_newest_base_state():
    scene = Scene()
    constellation = scene.create_constellation('test')
    scene.create_shell('shell', constellation=constellation.group_id, altitude_km=550, inclination_deg=53, epoch=START)
    scene.create_ground_station('east', node_id='east', latitude_deg=30, longitude_deg=120)
    scene.create_ground_station('west', node_id='west', latitude_deg=30, longitude_deg=-100)
    model = RegionalGroundOutage()
    model.regions = ({"name": "test", "latitude_deg": 30, "longitude_deg": 120, "radius_km": 100},)
    model.layer = SimpleNamespace(scene=scene)
    model.start_seconds, model.end_seconds = 5, 15
    east = connection('east', 'sat')
    west = connection('west', 'sat')
    isl = connection('sat', 'sat2')
    newer = connection('east', 'sat', delay=.02)
    base = {value.id: value for value in (east, west, isl)}
    first = LinkPlan(at(0), at(10), base, (LinkOperation(at(8), 'update', east.id, newer),))
    execution = model.perturb(first)
    assert execution.state_at(at(4)) == base
    assert set(execution.state_at(at(5))) == {west.id, isl.id}
    assert first.state_at(at(8))[east.id] == newer
    assert [(op.kind, op.timestamp) for op in execution.operations] == [('withdraw', at(5))]
    second = model.perturb(LinkPlan(at(10), at(15), first.end_links))
    assert second.initial_links == execution.end_links
    third = model.perturb(LinkPlan(at(15), at(20), first.end_links))
    assert third.initial_links == second.end_links
    assert third.state_at(at(15))[east.id] == newer
    assert [(op.kind, op.timestamp) for op in third.operations] == [('establish', at(15))]


def test_circular_regions_use_surface_distance_and_union_their_stations():
    scene = Scene()
    near = scene.create_ground_station('near', latitude_deg=0, longitude_deg=-179.8)
    far = scene.create_ground_station('far', latitude_deg=1, longitude_deg=179.8)
    region = {"name": "dateline", "latitude_deg": 0, "longitude_deg": 179.8, "radius_km": 50}
    assert inside_region(near, region)  # About 44 km across the date line.
    assert not inside_region(far, region)  # About 111 km north.
    model = RegionalGroundOutage()
    model.layer = SimpleNamespace(scene=scene)
    model.regions = (region, {**region, "name": "overlap"})
    description = outage_description(model)
    assert description['affected_stations'] == [near.node_id]
    assert all(value['affected_stations'] == [near.node_id] for value in description['regions'])
