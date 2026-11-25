"""Replay follows actual adoption, including late per-node and partial publications."""
from datetime import datetime, timedelta, timezone

import pytest
from skyseed.pc.compiler import BackendInstruction, PhysicalOperation, PhysicalPlan
from tools.observation.execution.inspection import applied_events

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds):
    return START + timedelta(seconds=seconds)


def route(node, seconds, label):
    instruction = BackendInstruction('bpf.rule', node, {'match': ('label', label), 'action': 'pop_deliver'})
    return PhysicalOperation(at(seconds), 'install', node, (instruction,))


def test_actual_time_and_each_adopted_node_control_replay():
    targets = {'nodes': (), 'links': (), 'destinations': ()}
    plans = [PhysicalPlan(at(0), at(20), 'route', 'bpf', 'demo', targets,
                          operations=(route('a', 5, 11), route('b', 5, 12), route('a', 10, 13)))]
    records = [{'kind': 'clock', 'origin': 100, 'start_time': START.isoformat()},
               {'kind': 'route', 'activated': 106, 'generation': 1,
                'adoptions': [{'scheduled': at(5).isoformat(), 'nodes': ['b']}]},
               {'kind': 'route', 'activated': 108, 'generation': 2,
                'adoptions': [{'scheduled': at(5).isoformat(), 'nodes': ['a']}]}]
    data = applied_events(plans, records)
    assert [event['time'] for event in data['events']] == [6, 8]
    assert [event['instructions'][0]['node'] for event in data['events']] == ['b', 'a']
    assert [event['instructions'][0]['parameters']['match'][1] for event in data['events']] == [12, 11]
    # The unacknowledged future update must not appear as applied.
    assert len(data['events']) == 2
    with pytest.raises(KeyError):
        applied_events([], records)


def test_same_time_late_batches_follow_receipt_counts_not_all_future_instructions():
    targets = {'nodes': (), 'links': (), 'destinations': ()}
    plans = [PhysicalPlan(at(0), at(10), 'route', 'bpf', 'demo', targets,
                          operations=(route('a', 5, 11),)),
             PhysicalPlan(at(5), at(15), 'route', 'bpf', 'demo', targets,
                          operations=(route('a', 5, 12),))]
    records = [{'kind': 'clock', 'origin': 100, 'start_time': START.isoformat()},
               {'kind': 'route', 'activated': 106, 'generation': 1,
                'adoptions': [{'scheduled': at(5).isoformat(), 'nodes': ['a'], 'instruction_counts': {'a': 1}}]},
               {'kind': 'route', 'activated': 108, 'generation': 2,
                'adoptions': [{'scheduled': at(5).isoformat(), 'nodes': ['a'], 'instruction_counts': {'a': 1}}]}]
    data = applied_events(plans, records)
    assert [event['instructions'][0]['parameters']['match'][1] for event in data['events']] == [11, 12]
    records[1]['adoptions'][0].pop('instruction_counts')
    with pytest.raises(ValueError, match='ambiguous same-time'):
        applied_events(plans, records)
