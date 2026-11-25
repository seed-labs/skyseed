"""A cycle boundary restores its initial network without rebuilding stable links."""
from datetime import datetime, timedelta, timezone

from skyseed import PlanCompiler
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkPlan, LinkOperation
from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RoutePlan, RouteOperation
from skyseed.pc.physical import LinkCandidate
from skyseed.runner.plan_queue import groups
from examples.s4_handover.experiment.repetition import RepeatedPlans
from tests.unit.compiler.conftest import deployment


def test_repeat_restores_links_rules_and_properties_on_forward_clock(deployment):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=4)
    change = start + timedelta(seconds=2)
    def link(left, right, delay=.002):
        identity = (left, right)
        candidate = ConnectionCandidate(identity, {left: LinkCandidate(left, right), right: LinkCandidate(right, left)})
        properties = LinkProperties(identity, 1_000_000, delay, 0)
        return LinkConfiguration(candidate, properties, properties)
    links = LinkPlan(start, end, {}, (
        LinkOperation(start, 'establish', ('a', 'b'), link('a', 'b')),
        LinkOperation(start, 'establish', ('b', 'c'), link('b', 'c')),
        LinkOperation(change, 'update', ('a', 'b'), link('a', 'b', .01)),
        LinkOperation(change, 'withdraw', ('b', 'c')),
        LinkOperation(change, 'establish', ('a', 'c'), link('a', 'c')),
    ))
    zone = RouteDestination('endpoint', 'z')
    first = ForwardingRule(zone, 'pop_deliver', incoming_label=20)
    second = ForwardingRule(zone, 'swap', LogicalExit(('a', 'b'), 'a'), 20, 30)
    routes = RoutePlan(start, end, {}, (
        RouteOperation(start, 'a', 'install', first),
        RouteOperation(change, 'a', 'replace', second),
        RouteOperation(change, 'b', 'install', ForwardingRule(zone, 'pop_deliver', incoming_label=30)),
    ))
    repeat = RepeatedPlans(PlanCompiler(deployment).compile_plans(link_plans=[links], route_plans=[routes]))
    timeline = list(groups(repeat.iterate(cycles=3)))
    for seconds in (4, 8):
        boundary = next(group for group in timeline if group.timestamp == start + timedelta(seconds=seconds))
        assert {(op.target, op.kind) for op in boundary.links} == {
            (('a', 'b'), 'update'), (('b', 'c'), 'establish'), (('a', 'c'), 'withdraw')}
        stable = next(op for op in boundary.links if op.target == ('a', 'b'))
        assert [item.opcode for item in stable.instructions] == ['netem.replace', 'netem.replace']
        assert all(item.parameters['delay_us'] == 2000 for item in stable.instructions)
        assert {(op.target, op.kind, op.instructions[0].parameters['action']) for op in boundary.routes} == {
            ('a', 'replace', 'pop_deliver'), ('b', 'withdraw', 'pop_deliver')}
    assert timeline[-1].timestamp == start + timedelta(seconds=12)
    assert repeat.plans[0].start_time == start


def test_ut_receipts_repeat_without_growing_history():
    from examples.s4_handover.experiment.ut.attachment import Attachment
    attachment = Attachment.__new__(Attachment)
    attachment.config = {'period_seconds': 4, 'start_time': '2026-01-01T00:00:00+00:00'}
    attachment.current = None
    attachment.change_positions = {}
    attachment.changes = {'2026-01-01T00:00:04+00:00|a': [True, None, False]}
    active = set()
    for seconds in (4, 8, 12, 400):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        event = {'adoptions': [{'scheduled': when.isoformat(), 'nodes': ['a'], 'instruction_counts': {'a': 1}}]}
        assert attachment.adopted_service(event, active) == 'a'
        attachment.current = 'a'
        assert attachment.adopted_service(event, active) == 'a'
        assert attachment.adopted_service(event, active) is None
    assert len(attachment.change_positions) == 1
