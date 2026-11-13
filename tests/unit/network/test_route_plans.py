from dataclasses import replace
from datetime import timedelta
import pytest
from skyseed.pc.network import (
    RouteDestination, ForwardingRule, RoutePlan, RouteOperation,
    RoutingModel, RoutePlanModel, DeliverySchedulingModel, FixedDelayDeliverySchedulingModel,
)


def install_test_routing(network, epoch):
    class Routes(RoutingModel):
        def compute_routes(self, scene, timestamp, link_plans):
            seconds = (timestamp - epoch).total_seconds()
            if seconds >= 15:
                return {}
            return {"a": (ForwardingRule(RouteDestination("prefix", "test"), "deliver",
                                         data={"generation": int(seconds // 5)}),)}
    network.set_routing_hook(Routes)


def test_state_boundaries_slices_and_ascending_queries(epoch):
    rule = ForwardingRule(RouteDestination("prefix", "test"), "deliver")
    plan = RoutePlan(epoch, epoch + timedelta(seconds=10), {}, (
        RouteOperation(epoch, "a", "install", rule),
        RouteOperation(epoch + timedelta(seconds=5), "a", "withdraw", rule)))
    assert plan.initial_rules == {} and plan.state_at(epoch) == {"a": (rule,)}
    assert list(plan.states_at([epoch, epoch + timedelta(seconds=5)])) == [{"a": (rule,)}, {}]
    later = plan.slice(epoch + timedelta(seconds=5), plan.end_time)
    assert later.initial_rules == {"a": (rule,)} and later.state_at(later.start_time) == {}
    with pytest.raises(ValueError, match="coverage"):
        plan.state_at(plan.end_time)


def test_periodic_continuity_history_and_explicit_withdrawal(epoch, network_factory):
    split, whole = network_factory(), network_factory()
    for network in (split, whole):
        install_test_routing(network, epoch)
    split.generate_plans(epoch, epoch + timedelta(seconds=3))
    split.generate_plans(epoch + timedelta(seconds=3), epoch + timedelta(seconds=18))
    whole.generate_plans(epoch, epoch + timedelta(seconds=18))
    assert tuple(op for plan in split.base_plans for op in plan.operations) == whole.base_plans[0].operations
    assert [(op.timestamp - epoch).total_seconds() for op in whole.base_plans[0].operations] == [0, 5, 10, 15]
    assert whole.base_plans[0].end_rules == {}
    calls = split.physical.candidate_model.calls
    split.generate_plans(epoch + timedelta(seconds=1), epoch + timedelta(seconds=8))
    assert split.physical.candidate_model.calls == calls


def test_delivery_tail_is_returned_automatically_and_context_is_not_redelivered(epoch, network_factory):
    network = network_factory()
    install_test_routing(network, epoch)
    network.set_delivery_scheduling_hook(FixedDelayDeliverySchedulingModel,
                                        model_options={"node_delays": {"a": timedelta(seconds=7)}})
    first, = network.generate_plans(epoch, epoch + timedelta(seconds=3))
    assert first.operations[0].timestamp == epoch + timedelta(seconds=7)
    assert first.state_at(epoch + timedelta(seconds=7))["a"][0].data["generation"] == 0
    second, = network.generate_plans(epoch + timedelta(seconds=3), epoch + timedelta(seconds=6))
    assert second.context_operations == first.operations
    assert [op.timestamp for op in second.operations] == [epoch + timedelta(seconds=12)]
    third, = network.generate_plans(epoch + timedelta(seconds=6), epoch + timedelta(seconds=10))
    assert third.operations == ()
    assert third.state_at(epoch + timedelta(seconds=7))["a"][0].data["generation"] == 0
    assert third.state_at(epoch + timedelta(seconds=12))["a"][0].data["generation"] == 1
    assert network.delivery_state_at(epoch + timedelta(seconds=12)) == third.end_rules
    history = network.delivery_plans_between(epoch, third.end_time)
    assert [op.timestamp for plan in history for op in plan.operations] == [
        epoch + timedelta(seconds=7), epoch + timedelta(seconds=12)]
    assert network.base_plans[-1].end_time == epoch + timedelta(seconds=10)


def test_later_batch_can_precede_old_tail_and_same_time_context_applies_first(epoch, network_factory):
    network = network_factory()
    class Routes:
        def compute_routes(self, scene, timestamp, link_plans):
            generation = int((timestamp - epoch).total_seconds() // 5)
            rule = ForwardingRule(RouteDestination("prefix", "x"), "deliver", data={"generation": generation})
            return {"a": (rule,)} if generation == 0 else {"a": (rule,), "b": (rule,)}
    class Scheduling:
        def schedule_plans(self, scene, route_plans):
            base = route_plans[0]
            operations = tuple(sorted((replace(op, timestamp=epoch + timedelta(seconds=7))
                                       if op.node_id == "a" else op for op in base.operations),
                                      key=lambda op: op.timestamp))
            return (RoutePlan(base.start_time, epoch + timedelta(seconds=8), base.initial_rules, operations),)
    network.set_routing_hook(Routes)
    network.set_delivery_scheduling_hook(Scheduling)
    first, = network.generate_plans(epoch, epoch + timedelta(seconds=5))
    second, = network.generate_plans(epoch + timedelta(seconds=5), epoch + timedelta(seconds=6))
    assert second.context_operations == first.operations
    assert [(op.node_id, op.kind) for op in second.operations] == [("b", "install"), ("a", "replace")]
    at_six = epoch + timedelta(seconds=6)
    assert first.state_at(at_six) == {}  # This immutable batch predates B's update.
    assert set(second.state_at(at_six)) == {"b"}
    at_seven = epoch + timedelta(seconds=7)
    assert network.delivery_state_at(at_seven)["a"][0].data["generation"] == 1
    selected = second.slice(at_seven, second.end_time)
    assert selected.state_at(at_seven) == second.state_at(at_seven)
    joined = network.delivery_plans_between(epoch, second.end_time)
    assert [op.kind for plan in joined for op in plan.operations] == ["install", "install", "replace"]


def test_whole_sequence_hook_minimal_custom_generator_and_lookahead(epoch, network_factory):
    network = network_factory()
    class Generator(RoutePlanModel):
        def generate_plans(self, scene, start_time, end_time, link_plans):
            self.inputs = (start_time, end_time, link_plans)
            self.layer.link_state_at(end_time + timedelta(seconds=2), link_plans)
            middle = start_time + (end_time - start_time) / 2
            return (RoutePlan(start_time, middle), RoutePlan(middle, end_time))
    class Scheduler(DeliverySchedulingModel):
        def schedule_plans(self, scene, route_plans):
            self.calls = getattr(self, "calls", 0) + 1
            self.count = len(route_plans)
            return route_plans
    network.set_plan_model(Generator)
    network.set_delivery_scheduling_hook(Scheduler)
    end = epoch + timedelta(seconds=10)
    network.generate_plans(epoch, end, link_lookahead=timedelta(seconds=4))
    assert network.plan_model.inputs[:2] == (epoch, end)
    assert network.plan_model.inputs[2][-1].end_time == end + timedelta(seconds=4)
    assert network.delivery_scheduling_model.calls == 1 and network.delivery_scheduling_model.count == 2
    assert network.link.execution_plans == ()


def test_shared_link_query_reads_retained_history_after_input_window_moves(epoch, network_factory):
    network = network_factory()
    install_test_routing(network, epoch)
    network.generate_plans(epoch, epoch + timedelta(seconds=3))
    original = network.link_state_at(epoch)
    network.generate_plans(epoch + timedelta(seconds=3), epoch + timedelta(seconds=8))
    calls = network.physical.candidate_model.calls
    assert network.link_state_at(epoch) == original
    assert network.physical.candidate_model.calls == calls


@pytest.mark.parametrize("segments", [20, 40])
def test_continuous_generation_does_not_rescan_all_link_history(epoch, segments):
    from skyseed import Scene
    from skyseed.pc.physical import PhysicalLayer
    from skyseed.pc.link import LinkLayer
    from skyseed.pc.network import NetworkLayer

    class CountedHistory(list):
        visited = 0

        def __iter__(self):
            for plan in super().__iter__():
                self.visited += 1
                yield plan

    network = NetworkLayer(LinkLayer(PhysicalLayer(Scene())))
    history = CountedHistory()
    network.link._base_plans = history
    for index in range(segments):
        start = epoch + timedelta(seconds=index)
        end = start + timedelta(seconds=1)
        plan, = network.generate_plans(start, end)
        assert (plan.start_time, plan.end_time, plan.end_rules) == (start, end, {})
    # Allow work proportional to newly requested segments, not their full past.
    assert history.visited <= 2 * segments


@pytest.mark.parametrize("disable", ["hook", "switch"])
def test_disabled_routes_skip_all_input_and_model_work(epoch, network_factory, disable):
    network = network_factory()
    original = network.routing_model
    if disable == "hook":
        network.set_routing_hook(None)
    else:
        network.set_precomputed_routing(False)
        assert network.routing_model is original
    assert network.generate_plans(epoch, epoch + timedelta(seconds=10)) == ()
    assert network.link.base_plans == () and network.physical.candidate_model.calls == 0
    network.link.generate_plan(epoch, epoch + timedelta(seconds=1))
    assert network.link.execution_plans


def test_scheduler_cannot_change_content_or_reorder_a_nodes_updates(epoch, network_factory):
    network = network_factory()
    install_test_routing(network, epoch)
    class Wrong:
        def schedule_plans(self, scene, route_plans):
            plan = route_plans[0]
            operation = plan.operations[0]
            changed = replace(operation, rule=replace(operation.rule, data={"wrong": True}))
            return (RoutePlan(plan.start_time, plan.end_time, plan.initial_rules, (changed,)),)
    network.set_delivery_scheduling_hook(Wrong)
    with pytest.raises(ValueError, match="content and order"):
        network.generate_plans(epoch, epoch + timedelta(seconds=1))
