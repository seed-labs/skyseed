"""The editable native criterion uses actual B0 facts and cancellable queries."""
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

import pytest
from examples.Sky06_s4_native_ospf import readiness
from skyseed.experiment.preparation import _read_context
from tests.fixtures.native_zone_business import generate
from tests.integration.test_retained_workflow import await_state


def prepared(output):
    manifest = json.loads((output / 'results/compiler/runner-artifact/manifest.json').read_text())
    invocation = output / 'runs/offline'
    (invocation / 'runner').mkdir(parents=True)
    runner = {'activation_count': 1, 'frontiers': {'last_active': manifest['timeline']['start']},
        'runtime_foundation': {'bindings': [{'node_id': row['key'], 'container_id': row['key']}
                                          for row in manifest['dataplane']['nodes']]}}
    path = invocation / 'runner/prepared.json'
    path.write_text(json.dumps({'artifact_type': 'runner-inspection-handoff', 'format_version': 1,
                               'runner_snapshot': {'runner': runner}}))
    return invocation


@pytest.fixture
def criterion(tmp_path):
    output = generate(tmp_path / 'scene', 'bird', tools_enabled=False, traffic_enabled=False, duration=10, readiness=True)
    invocation = prepared(output)
    # No observation or Traffic artifacts are needed, and only the first
    # Planner record is valid; later bytes intentionally are not decodable.
    path = output / 'results/planner/planning.jsonl.gz'
    with gzip.open(path, 'rt') as stream:
        prefix = next(stream) + next(stream)
    with gzip.open(path, 'wt') as stream:
        stream.write(prefix + 'not a subsequent JSON record\n')
    return readiness.initial_criterion(_read_context(output, invocation)), output, invocation


def test_initial_facts_capture_real_access_return_and_all_isl(criterion):
    facts, output, invocation = criterion
    assert sum(map(len, facts['neighbors'].values())) == 4
    assert facts['candidate_count'] == 2
    assert {sample['gs'] for sample in facts['samples']} == {'65040/ground-station/g', '65040/ground-station/h'}
    assert all(sample['service'] == '65040/satellite/c' for sample in facts['samples'])
    assert all(sample['access_interface'].startswith('sv2') and sample['return_interface'].startswith('sv2')
               for sample in facts['samples'])
    definition = json.loads((output / 'results/compiler/execution.json').read_text())
    assert definition['initial_readiness_command'][4] == 'inputs/sources/examples/Sky06_s4_native_ospf/readiness.py'
    assert not (output / 'results/traffic/probe-plan.json').exists()
    path = invocation / 'runner/prepared.json'
    data = json.loads(path.read_text()); data['runner_snapshot']['runner']['activation_count'] = 2
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='initial Linux activation'):
        readiness.initial_criterion(_read_context(output, invocation))


def test_no_candidates_is_visible_failure(criterion):
    facts, output, invocation = criterion
    path = output / 'results/planner/planning.jsonl.gz'
    with gzip.open(path, 'rt') as stream:
        header = next(stream); row = json.loads(next(stream))
    transition = row['data']['plan']['transitions'][0]
    # Exercise the public transition decoder, preserving its actual schema.
    from skyseed.simulator import plan_transition_from_data, plan_transition_to_data
    from dataclasses import replace
    value = plan_transition_from_data(transition)
    value = replace(value, route_outcome_transitions=(), semantic_event_precedences=None)
    row['data']['plan']['transitions'][0] = plan_transition_to_data(value)
    with gzip.open(path, 'wt') as stream:
        stream.write(header + json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='no GS–Zone candidate'):
        readiness.initial_criterion(_read_context(output, invocation))


def test_wait_for_full_neighbors_and_exact_meaningful_routes(criterion):
    facts, _, invocation = criterion
    directory = invocation / 'readiness'; directory.mkdir()
    polls = {}
    def query(node, commands, stop):
        count = polls.get(node, 0); polls[node] = count + 1
        result = []
        if node in facts['neighbors']:
            rows = facts['neighbors'][node]
            text = 'BIRD 2.17.2 ready.\nsatellite_ospf:\nRouter ID Pri State DTime Interface Router IP\n'
            text += '\n'.join(f"{row['router_id']} 1 {'Full/PtP' if count else 'Init/PtP'} 39.2 {row['interface']} 172.16.0.1" for row in rows)
            result.append({'stdout': text})
        routes = [row for row in facts['routes'] if row['node'] == node]
        if routes:
            result.append({'stdout': json.dumps([{'dst': row['destination'], 'dev': row['interfaces'][0]} for row in routes] if count else [{'dst': 'default', 'dev': 'eth0'}])})
        return result
    assert readiness.wait_ready(facts, directory, Event(), query_fn=query, interval=0)
    rows = [json.loads(line) for line in (directory / 'native.jsonl').read_text().splitlines()]
    assert [row['status'] for row in rows] == ['waiting', 'ready']
    assert any('neighbor' in row for row in rows[0]['pending'])
    assert any('route' in row for row in rows[0]['pending'])


def test_local_service_is_accepted_but_management_default_and_dead_hops_are_not():
    value = {'destination': '198.18.0.1/32', 'interfaces': ['sv2link'], 'local': False}
    for route in ({'dst': 'default', 'dev': 'sv2link'}, {'dst': '198.18.0.1', 'dev': 'eth0'},
                  {'dst': '198.18.0.1', 'dev': 'sv2link', 'flags': ['linkdown']},
                  {'dst': '198.18.0.1', 'dev': 'sv2link', 'type': 'blackhole'}):
        assert not readiness.route_present(value, [route], 'sv2identity')
    assert readiness.route_present(value, [{'dst': '198.18.0.1', 'nexthops': [{'dev': 'sv2link'}]}], 'sv2identity')
    local = {'dst': '198.18.0.1', 'type': 'local', 'dev': 'sv2identity'}
    assert not readiness.route_present(value, [local], 'sv2identity')
    assert readiness.route_present({**value, 'local': True}, [local], 'sv2identity')


def test_query_error_is_not_retried_as_learning(criterion):
    facts, _, invocation = criterion
    directory = invocation / 'readiness'; directory.mkdir()
    def fail(*args):
        raise RuntimeError('daemon socket/configuration failure')
    with pytest.raises(RuntimeError, match='command/configuration errors'):
        readiness.wait_ready(facts, directory, Event(), query_fn=fail, interval=0)
    rows = (directory / 'native.jsonl').read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])['errors']


def test_query_eof_stops_and_reaps_remote_command(tmp_path, monkeypatch):
    # Substitute only the Docker transport, retaining the real wrapper process,
    # stdin cancellation and in-container command process ownership.
    original = subprocess.Popen
    def popen(argv, **kwargs):
        assert argv[:3] == ['docker', 'exec', '-i']
        return original(['sh', '-c', argv[6], *argv[7:]], **kwargs)
    monkeypatch.setattr(readiness.subprocess, 'Popen', popen)
    code = '''import os,sys,signal,time,pathlib
p=pathlib.Path(sys.argv[1])
def stop(*a):
    (p/'drained').touch(); sys.exit(0)
signal.signal(signal.SIGTERM,stop)
(p/'ready').write_text(str(os.getpid()))
while True: time.sleep(.05)
'''
    stop = Event(); result = []; errors = []
    def run():
        try:
            result.append(readiness.query('container', [[sys.executable, '-c', code, str(tmp_path)]], stop))
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=run); thread.start()
    try:
        await_state(lambda: errors or (tmp_path / 'ready').exists())
        assert not errors
    finally:
        stop.set(); thread.join()
    assert not errors and result == [None] and (tmp_path / 'drained').exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / 'ready').read_text()), 0)
