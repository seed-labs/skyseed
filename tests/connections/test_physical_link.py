"""Real Scene → Physical → Link handoffs and replaceable model calls."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, LinkCandidate
from skyseed.pc.link import LinkLayer, PeriodicLinkPlanModel, LinkPlan, LinkOperation

TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_physical_geometry_reaches_default_link_plan_and_continues():
    scene = Scene()
    scene.create_constellation('demo', planes=3, satellites_per_plane=12,
                               altitude_km=550, inclination_deg=53, epoch=TIME)
    scene.create_ground_station('g', latitude_deg=40, longitude_deg=116)
    physical = PhysicalLayer(scene)
    layer = LinkLayer(physical)
    first = layer.generate_plan(TIME, TIME + timedelta(seconds=8))
    second = layer.generate_plan(first.end_time, TIME + timedelta(seconds=13))
    assert first.end_links and second.initial_links == first.end_links
    for configuration in first.end_links.values():
        assert configuration.candidate.bidirectional
        for source, report in configuration.candidate.reports.items():
            properties = configuration.properties_from(source)
            assert properties.delay_s == report.propagation_delay_s
            assert properties.packet_loss_ppm == 0
    assert layer.physical is physical and layer.scene is scene


def test_asymmetric_unknown_and_custom_fields_survive_property_adjustment():
    scene = Scene()
    scene.create_satellite('sat', position=ScenePositionSource('test/overhead', {}))
    scene.create_ground_station('ground', latitude_deg=0, longitude_deg=0)

    class Overhead:
        def __init__(self):
            self.calls = 0
        def compute_positions(self, scene, satellite_ids, timestamp):
            self.calls += 1
            return {node: NodeState((6_928_137, 0, 0)) for node in satellite_ids}

    class Asymmetric:
        def compute_candidates(self, scene, snapshot, node_id):
            if node_id == 'ground':
                return (LinkCandidate('ground', 'sat', data={'doppler_hz': 12}),)
            return (LinkCandidate('sat', 'ground', propagation_delay_s=0.02, data={'doppler_hz': -11}),)

    class ProcessingDelay:
        def adjust_properties(self, scene, timestamp, links):
            adjusted = []
            for link in links:
                forward = replace(link.properties, delay_s=link.properties.delay_s + 0.001)
                reverse = replace(link.reverse_properties, delay_s=link.reverse_properties.delay_s + 0.002)
                adjusted.append(replace(link, properties=forward, reverse_properties=reverse))
            return tuple(adjusted)

    physical = PhysicalLayer(scene)
    physical.set_position_model(Overhead)
    physical.set_candidate_model(Asymmetric)
    layer = LinkLayer(physical)
    layer.set_property_hook(ProcessingDelay)
    result = layer.evaluate_connections(TIME)[0]
    assert physical.position_model.calls == 1  # Fallback reuses this evaluation's snapshot.
    assert result.properties_from('ground').delay_s == pytest.approx(550000 / 299792458 + 0.001)
    assert result.properties_from('sat').delay_s == pytest.approx(0.022)
    assert result.candidate.reports['ground'].propagation_delay_s is None
    assert result.candidate.reports['sat'].data['doppler_hz'] == -11
    assert layer.evaluate_connections(TIME)[0].properties == result.properties  # No cumulative adjustment.


def test_custom_generator_may_query_future_without_adopting_it(tiny_scene):
    class StaticPlan:
        def generate_plan(self, scene, start_time, end_time):
            predictions = self.layer.candidates_at(end_time + timedelta(seconds=100))
            assert predictions is not None
            self.calls = getattr(self, 'calls', 0) + 1
            previous = self.layer.previous_base_plan
            initial = {} if previous is None else previous.end_links
            return LinkPlan(start_time, end_time, initial)
    layer = LinkLayer(PhysicalLayer(tiny_scene))
    layer.set_plan_model(StaticPlan)
    first = layer.generate_plan(TIME, TIME + timedelta(seconds=10))
    second = layer.generate_plan(first.end_time, TIME + timedelta(seconds=20))
    assert first.operations == second.operations == ()
    assert layer.plan_model.calls == 2 and layer.selected_links == {}
