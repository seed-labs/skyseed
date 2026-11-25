from dataclasses import replace
from datetime import timedelta
import pytest
from skyseed.pc.link import (
    LinkOperation, LinkPerturbationModel, LinkPlan, LinkPlanModel,
    LinkPropertyModel, LinkSelectionModel, PeriodicLinkPlanModel,
)


def mutual(delay=0.01):
    return {('a', 'b'): delay, ('b', 'a'): delay}


def test_interface_subclasses_only_need_their_business_method(epoch, scheduled_layer):
    calls = []

    class Select(LinkSelectionModel):
        def select_links(self, scene, timestamp, candidates):
            calls.append('select')
            return candidates

    class Properties(LinkPropertyModel):
        def adjust_properties(self, scene, timestamp, links):
            calls.append('properties')
            return [replace(link, properties=replace(link.properties, bandwidth_bps=777))
                    for link in links]

    class Generate(LinkPlanModel):
        def generate_plan(self, scene, start_time, end_time):
            calls.append('generate')
            links = self.layer.evaluate_connections(start_time)
            self.layer.adopt_selection(links)
            previous = self.layer.previous_base_plan
            if previous is not None:
                return LinkPlan(start_time, end_time, previous.end_links)
            operations = tuple(LinkOperation(start_time, 'establish', link.id, link) for link in links)
            return LinkPlan(start_time, end_time, operations=operations)

    class Perturb(LinkPerturbationModel):
        def perturb(self, plan):
            calls.append('perturb')
            assert self.layer.scene is layer.scene
            return plan

    layer = scheduled_layer(lambda seconds: mutual())
    layer.set_selection_hook(Select)
    layer.set_property_hook(Properties)
    layer.set_plan_model(Generate)
    layer.set_perturbation_hook(Perturb)
    first = layer.generate_plan(epoch, epoch + timedelta(seconds=6))
    second = layer.generate_plan(first.end_time, epoch + timedelta(seconds=10))
    assert calls == ['generate', 'select', 'properties', 'perturb'] * 2
    assert len(first.operations) == 1 and second.operations == ()
    assert second.state_at(epoch + timedelta(seconds=8))[('a', 'b')].properties.bandwidth_bps == 777


def test_fractional_setup_crosses_requests_without_resampling(epoch, scheduled_layer):
    layer = scheduled_layer(lambda seconds: mutual())
    observations = []
    layer.set_plan_model(PeriodicLinkPlanModel, model_options={
        'isl_setup_duration': timedelta(seconds=7.5), 'observer': observations.append})
    first = layer.generate_plan(epoch, epoch + timedelta(seconds=3))
    second = layer.generate_plan(first.end_time, epoch + timedelta(seconds=7.5))
    third = layer.generate_plan(second.end_time, epoch + timedelta(seconds=11))
    assert first.operations == second.operations == ()
    assert [(op.timestamp, op.kind) for op in third.operations] == [
        (epoch + timedelta(seconds=7.5), 'establish')]
    assert [observation.timestamp for observation in observations] == [
        epoch + timedelta(seconds=seconds) for seconds in (0, 5, 10)]
    before = len(layer.physical.candidate_model.calls)
    history = layer.generate_plan(epoch + timedelta(seconds=1), epoch + timedelta(seconds=9))
    assert len(layer.physical.candidate_model.calls) == before
    assert history.state_at(epoch + timedelta(seconds=8)).keys() == {('a', 'b')}


def test_segmented_and_whole_generation_adopt_the_same_events(epoch, scheduled_layer):
    def schedule(seconds):
        return mutual(0.01 + seconds * 0.001) if seconds < 15 or seconds >= 20 else {}
    whole = scheduled_layer(schedule)
    split = scheduled_layer(schedule)
    expected = whole.generate_plan(epoch, epoch + timedelta(seconds=31))
    start = epoch
    for end in (2, 5, 6, 13, 20, 23, 31):
        split.generate_plan(start, epoch + timedelta(seconds=end))
        start = epoch + timedelta(seconds=end)
    actual = split.base_plan(epoch, start)
    assert actual.operations == expected.operations
    assert actual.end_links == expected.end_links


def test_pending_disappearance_cancels_and_reselection_restarts_setup(epoch, scheduled_layer):
    layer = scheduled_layer(lambda seconds: mutual() if seconds < 5 or seconds >= 10 else {})
    plan = layer.generate_plan(epoch, epoch + timedelta(seconds=16))
    assert [(op.timestamp, op.kind) for op in plan.operations] == [
        (epoch + timedelta(seconds=15), 'establish')]


def test_delay_uses_last_adopted_value_and_independent_direction_clocks(epoch, scheduled_layer):
    def schedule(seconds):
        return {('a', 'b'): 1 + seconds * 0.006, ('b', 'a'): 1 + seconds * 0.0001}
    layer = scheduled_layer(schedule)
    layer.set_plan_model(PeriodicLinkPlanModel, model_options={
        'isl_setup_duration': timedelta(0), 'max_delay_hold': timedelta(seconds=25)})
    plan = layer.generate_plan(epoch, epoch + timedelta(seconds=26))
    assert [(int((op.timestamp - epoch).total_seconds()), op.kind) for op in plan.operations] == [
        (0, 'establish'), (20, 'update'), (25, 'update')]
    at20 = plan.state_at(epoch + timedelta(seconds=20))[('a', 'b')]
    assert at20.properties_from('a').delay_s == pytest.approx(1.12)
    assert at20.properties_from('b').delay_s == 1
    at25 = plan.end_links[('a', 'b')]
    assert at25.properties_from('a').delay_s == pytest.approx(1.12)
    assert at25.properties_from('b').delay_s == pytest.approx(1.0025)


def test_property_trials_start_fresh_and_do_not_adopt(epoch, scheduled_layer):
    class SharedCapacity:
        def adjust_properties(self, scene, timestamp, links):
            bandwidth = 1000 / max(1, len(links))
            return tuple(replace(link, properties=replace(link.properties, bandwidth_bps=bandwidth))
                         for link in links)
    layer = scheduled_layer(lambda seconds: mutual())
    layer.set_property_hook(SharedCapacity)
    layer.set_plan_model(PeriodicLinkPlanModel, model_options={'isl_setup_duration': timedelta(0)})
    estimate = layer.evaluate_connections(epoch)
    assert estimate[0].properties.bandwidth_bps == 1000
    assert layer.selected_links == {} and layer.previous_base_plan is None
    plan = layer.generate_plan(epoch, epoch + timedelta(seconds=6))
    assert len(plan.operations) == 1
    assert plan.end_links[('a', 'b')].properties.bandwidth_bps == 1000


def test_non_delay_changes_are_adopted_at_the_next_evaluation(epoch, scheduled_layer):
    class ChangingProperties:
        def adjust_properties(self, scene, timestamp, links):
            changed = timestamp >= epoch + timedelta(seconds=5)
            result = []
            for link in links:
                properties = replace(link.properties, bandwidth_bps=500 if changed else 1000,
                                     packet_loss_ppm=10 if changed else 0)
                result.append(replace(link, properties=properties))
            return result

    layer = scheduled_layer(lambda seconds: mutual(1 + seconds * 0.001))
    layer.set_property_hook(ChangingProperties)
    layer.set_plan_model(PeriodicLinkPlanModel, model_options={'isl_setup_duration': timedelta(0)})
    plan = layer.generate_plan(epoch, epoch + timedelta(seconds=11))
    assert [(op.timestamp, op.kind) for op in plan.operations] == [
        (epoch, 'establish'), (epoch + timedelta(seconds=5), 'update')]
    value = plan.end_links[('a', 'b')]
    assert value.properties.bandwidth_bps == 500
    assert value.properties.packet_loss_ppm == 10
    assert value.properties.delay_s == 1  # The small delay change is still suppressed.


def test_custom_one_way_selection_never_invents_the_reverse_direction(epoch, scheduled_layer):
    layer = scheduled_layer(lambda seconds: {('b', 'a'): 0.02})
    plan = layer.generate_plan(epoch, epoch + timedelta(seconds=6))
    configuration = plan.end_links[('a', 'b')]
    assert configuration.properties_from('a') is None
    assert configuration.properties_from('b').delay_s == 0.02
    assert configuration.candidate.reports.keys() == {'b'}


def test_directional_perturbation_continues_across_segments_without_compounding(epoch, scheduled_layer):
    class ReduceReverseBandwidth:
        @staticmethod
        def adjust(link):
            reverse = replace(link.properties_from('b'), bandwidth_bps=250)
            return replace(link, reverse_properties=reverse)

        def perturb(self, plan):
            initial = {identity: self.adjust(link) for identity, link in plan.initial_links.items()}
            operations = []
            for operation in plan.operations:
                if operation.configuration is not None:
                    operation = replace(operation, configuration=self.adjust(operation.configuration))
                operations.append(operation)
            return LinkPlan(plan.start_time, plan.end_time, initial, tuple(operations))

    layer = scheduled_layer(lambda seconds: mutual(0.01 if seconds < 10 else 0.02))
    layer.set_perturbation_hook(ReduceReverseBandwidth)
    layer.generate_plan(epoch, epoch + timedelta(seconds=8))
    layer.generate_plan(epoch + timedelta(seconds=8), epoch + timedelta(seconds=16))
    execution = layer.generate_plan(epoch, epoch + timedelta(seconds=16))
    base = layer.base_plan(epoch, execution.end_time)
    assert layer.execution_plans[1].initial_links == layer.execution_plans[0].end_links
    assert [op.kind for op in execution.operations] == ['establish', 'update']
    base_link = base.end_links[('a', 'b')]
    execution_link = execution.end_links[('a', 'b')]
    assert base_link.properties_from('b').bandwidth_bps == 1_000_000_000
    assert execution_link.properties_from('a') == base_link.properties_from('a')
    assert execution_link.properties_from('b').bandwidth_bps == 250
    assert execution_link.properties_from('b').delay_s == 0.02


def test_execution_perturbation_is_retained_once_without_changing_base(epoch, scheduled_layer):
    class Suppress:
        def __init__(self):
            self.calls = 0
        def perturb(self, plan):
            self.calls += 1
            assert self.layer.base_plan(plan.start_time, plan.end_time).operations == plan.operations
            return LinkPlan(plan.start_time, plan.end_time)
    layer = scheduled_layer(lambda seconds: mutual())
    layer.set_perturbation_hook(Suppress)
    execution = layer.generate_plan(epoch, epoch + timedelta(seconds=10))
    assert execution.end_links == {}
    assert ('a', 'b') in layer.base_plan(epoch, execution.end_time).end_links
    layer.generate_plan(epoch + timedelta(seconds=2), epoch + timedelta(seconds=8))
    layer.generate_plan(execution.end_time, epoch + timedelta(seconds=15))
    assert layer.perturbation_model.calls == 2
    assert layer.selected_links.keys() == {('a', 'b')}


def test_model_configuration_and_generation_boundaries(epoch, scheduled_layer):
    layer = scheduled_layer(lambda seconds: mutual())
    original = layer.plan_model
    with pytest.raises(TypeError, match='model class'):
        layer.set_plan_model(None)
    assert layer.plan_model is original
    layer.generate_plan(epoch, epoch + timedelta(seconds=2))
    with pytest.raises(ValueError, match='new layer'):
        layer.set_selection_hook(None)
    with pytest.raises(ValueError, match='continue'):
        layer.generate_plan(epoch + timedelta(seconds=3), epoch + timedelta(seconds=7))
