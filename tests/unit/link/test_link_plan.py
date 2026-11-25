from datetime import timedelta
import pytest
from skyseed.pc.link import LinkOperation, LinkPlan


def test_boundaries_slice_and_single_pass_history(epoch, configuration):
    link = configuration()
    plan = LinkPlan(epoch, epoch + timedelta(seconds=10), {}, (
        LinkOperation(epoch, 'establish', link.id, link),
        LinkOperation(epoch + timedelta(seconds=5), 'withdraw', link.id),
    ))
    assert plan.initial_links == {}
    states = list(plan.states_at([epoch, epoch + timedelta(seconds=4), epoch + timedelta(seconds=5)]))
    assert link.id in states[0] and link.id in states[1] and states[2] == {}
    assert plan.end_links == {}
    sliced = plan.slice(epoch + timedelta(seconds=5), plan.end_time)
    assert link.id in sliced.initial_links
    assert sliced.operations[0].kind == 'withdraw'
    assert sliced.state_at(sliced.start_time) == {}
    with pytest.raises(ValueError, match='within'):
        plan.state_at(plan.end_time)
    with pytest.raises(TypeError):
        plan.initial_links[link.id] = link


def test_invalid_operation_cannot_hide_broken_plan(epoch, configuration):
    link = configuration()
    with pytest.raises(ValueError, match='absent'):
        LinkPlan(epoch, epoch + timedelta(seconds=10), {}, (
            LinkOperation(epoch, 'update', link.id, link),))
    with pytest.raises(ValueError, match='ordered'):
        LinkPlan(epoch, epoch + timedelta(seconds=10), {}, (
            LinkOperation(epoch + timedelta(seconds=10), 'establish', link.id, link),))


def test_directional_properties_and_unknown_direction(configuration):
    from dataclasses import replace
    from skyseed.pc.link import ConnectionCandidate
    link = configuration(reverse_delay=0.03)
    assert link.properties_from('a').delay_s == 0.01
    assert link.properties_from('b').delay_s == 0.03
    candidate = ConnectionCandidate(link.id, {'b': link.candidate.reports['b']})
    one_way = replace(link, candidate=candidate)
    assert one_way.properties_from('a') is None
    assert one_way.properties_from('b').delay_s == 0.03
