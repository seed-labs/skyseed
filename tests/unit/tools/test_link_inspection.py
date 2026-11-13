from dataclasses import replace
from datetime import datetime, timedelta, timezone

from skyseed import Scene
from skyseed.pc.physical import PhysicalLayer, LinkCandidate
from skyseed.pc.link import (
    ConnectionCandidate, LinkConfiguration, LinkEvaluation, LinkLayer, LinkOperation,
    LinkPlan, LinkProperties, PeriodicLinkPlanModel,
)
from tools.observation.link import inspection
from tools.observation.link.inspection import write_inspection


def test_inspector_consumes_plans_and_optional_trace_without_regeneration(tmp_path, inspect_plans):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scene = Scene()
    scene.create_constellation('test', planes=1, satellites_per_plane=12,
                               altitude_km=550, inclination_deg=53, epoch=start)
    layer = LinkLayer(PhysicalLayer(scene))
    evaluations = []
    layer.set_plan_model(PeriodicLinkPlanModel, model_options={
        'isl_setup_duration': timedelta(seconds=7.5), 'observer': evaluations.append})

    class Suppress:
        def perturb(self, plan):
            return LinkPlan(plan.start_time, plan.end_time)
    layer.set_perturbation_hook(Suppress)
    layer.generate_plan(start, start + timedelta(seconds=11))
    model = layer.plan_model
    def unexpected(*args):
        raise AssertionError('inspection must not generate another plan')
    model.generate_plan = unexpected
    data = inspect_plans(inspection, layer.physical, layer.base_plans, layer.execution_plans,
                           evaluations=evaluations)
    frame = next(frame for frame in data['frames'] if frame['timestamp'] == (start + timedelta(seconds=7.5)).isoformat())
    assert frame['base'] and frame['execution'] == []
    assert frame['evaluation_timestamp'] == (start + timedelta(seconds=5)).isoformat()
    assert frame['pending'] == []
    assert data['frames'][0]['pending']
    assert len(data['events']['base']) > 0 and data['events']['execution'] == []
    path = write_inspection(data, tmp_path)
    assert path.exists() and (tmp_path / 'link.json').exists()
    assert '/*INSPECTION_DATA*/null' not in path.read_text()


def test_missing_trace_is_unknown_not_empty_selection(inspect_plans):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    plan = LinkPlan(start, start + timedelta(seconds=5))
    data = inspect_plans(inspection, PhysicalLayer(Scene()), [plan], [plan])
    assert data['frames'][0]['selected'] is None
    assert data['frames'][0]['candidates'] is None
    assert data['frames'][0]['positions'] == {}


def test_custom_display_times_keep_short_connections_and_recorded_preparation(inspect_plans):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def time(seconds):
        return start + timedelta(seconds=seconds)

    scene = Scene()
    scene.create_ground_station('a', latitude_deg=40, longitude_deg=115)
    scene.create_ground_station('b', latitude_deg=40, longitude_deg=116)
    candidate = ConnectionCandidate(('a', 'b'), {
        'a': LinkCandidate('a', 'b'), 'b': LinkCandidate('b', 'a')})
    link = LinkConfiguration(candidate, LinkProperties(candidate.id, 1e6, 0.001))
    base = LinkPlan(start, time(11), operations=(
        LinkOperation(time(5), 'establish', link.id, link),
        LinkOperation(time(7), 'withdraw', link.id)))
    execution = LinkPlan(start, time(11), operations=(
        LinkOperation(time(6), 'establish', link.id, link),
        LinkOperation(time(8), 'withdraw', link.id)))
    evaluations = [LinkEvaluation(time(seconds), (candidate,), {link.id: link}, {link.id: time(5)})
                   for seconds in (1, 4)]

    data = inspect_plans(inspection, PhysicalLayer(scene), [base], [execution],
                           evaluations=evaluations, timestamps=[time(2), time(10)])
    frames = data['frames']
    assert [frame['timestamp'] for frame in frames] == [
        time(seconds).isoformat() for seconds in (0, 1, 2, 4, 5, 6, 7, 8, 10)]
    assert [len(frame['base']) for frame in frames] == [0, 0, 0, 0, 1, 1, 0, 0, 0]
    assert [len(frame['execution']) for frame in frames] == [0, 0, 0, 0, 0, 1, 1, 0, 0]
    assert frames[0]['pending'] is None
    assert all(len(frame['pending']) == 1 for frame in frames[1:4])
    assert all(frame['pending'] == [] for frame in frames[4:])
