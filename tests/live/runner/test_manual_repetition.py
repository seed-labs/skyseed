"""Short real BPF run: three GS, retained sockets, loop reset and SIGINT cleanup."""
from datetime import datetime, timedelta, timezone
import json
import os
import signal
import subprocess
from threading import Timer
import time

import pytest

from skyseed import Scene, Runner, PlanCompiler, save_plans
from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RouteOperation, RoutePlan
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkPlan, LinkOperation
from skyseed.pc.physical import LinkCandidate
from examples.s4_handover.experiment.ut import TerminalExperiment
from examples.s4_handover.experiment.repetition import RepeatedPlans
from examples.s4_handover.experiment.console import RunConsole
from tools.observation.execution.inspection import applied_events
from tests.live.runner.test_kernel_forwarding import deployment


@pytest.mark.parametrize('deployment', [('a', 'b', 'g', 'h', 'i')], indirect=True)
def test_three_ground_stations_repeat_and_interrupt(deployment, tmp_path):
    deployed, image = deployment
    before = deployed.containers()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=3)
    links = {}
    for left, right in (('a', 'b'), ('b', 'g'), ('b', 'h'), ('b', 'i')):
        identity = left, right
        candidate = ConnectionCandidate(identity, {left: LinkCandidate(left, right), right: LinkCandidate(right, left)})
        properties = LinkProperties(identity, 10_000_000, .001, 0)
        links[identity] = LinkConfiguration(candidate, properties, properties)
    zone = RouteDestination('endpoint', 'z')
    ut = RouteDestination('prefix', '192.0.2.2/32')
    rules = {'a': [ForwardingRule(zone, 'pop_deliver', incoming_label=41)],
             'b': [ForwardingRule(zone, 'swap', LogicalExit(('a', 'b'), 'b'), 40, 41)]}
    for index, gs in enumerate(('g', 'h', 'i')):
        destination = RouteDestination('node', gs)
        rules['a'].append(ForwardingRule(destination, 'push', LogicalExit(('a', 'b'), 'a'), outgoing_label=16 + index))
        rules['b'].extend([
            ForwardingRule(destination, 'swap', LogicalExit(('b', gs), 'b'), 16 + index, 20 + index),
            ForwardingRule(destination, 'push', LogicalExit(('b', gs), 'b'), outgoing_label=20 + index)])
        rules[gs] = [ForwardingRule(destination, 'pop_deliver', incoming_label=20 + index),
                     ForwardingRule(zone, 'push', LogicalExit(('b', gs), gs), outgoing_label=40),
                     ForwardingRule(ut, 'push', LogicalExit(('b', gs), gs), outgoing_label=40)]
    operations = (
        RouteOperation(start + timedelta(seconds=1), 'a', 'replace',
                       ForwardingRule(zone, 'swap', LogicalExit(('a', 'b'), 'a'), 41, 40)),
        RouteOperation(start + timedelta(seconds=1), 'b', 'replace', ForwardingRule(zone, 'pop_deliver', incoming_label=40)),
    )
    link_plan = LinkPlan(start, end, links, (
        LinkOperation(start + timedelta(seconds=2), 'withdraw', ('b', 'h')),))
    plans = tuple(PlanCompiler(deployed.directory).compile_plans(
        link_plans=[link_plan], route_plans=[RoutePlan(start, end, rules, operations)]))
    repetition = RepeatedPlans(plans)
    path = tmp_path / 'input.plan'
    save_plans(path, plans)
    run = tmp_path / 'run'
    scene = Scene(data={'experiment': {'zone': 'z', 'ut_address': '192.0.2.2'}})
    console = RunConsole(tmp_path, run, {'simulated_seconds': 3, 'perturbation': {'start_seconds': 2, 'end_seconds': 3}})
    with Runner(repetition.iterate(), deployed, output=run) as runner:
        with TerminalExperiment(scene, deployed, path, run, seconds=None,
                                ground_stations=('g', 'h', 'i'), repetition=repetition) as terminal:
            # Exercise each GS -> UT and UT -> GS reply on real TCP. This socket
            # setup belongs to this test; the manual example only runs one echo.
            server = subprocess.Popen(['docker', 'exec', terminal.name, 'python3', '-u', '-c',
                "import socket; s=socket.socket(); s.bind(('192.0.2.2',2424)); s.listen(); print('ready',flush=True); "
                "exec('while True:\\n c,a=s.accept()\\n with c: c.sendall(c.recv(100))')"], stdout=subprocess.PIPE, text=True)
            assert server.stdout.readline().strip() == 'ready'
            errors = []
            replies = []
            def probe():
                try:
                    config = json.loads((run / 'terminal.json').read_text())
                    for gs in ('g', 'h', 'i'):
                        command = ['docker', 'exec', config['containers'][gs], 'python3', '-c',
                                   "import socket; s=socket.create_connection(('192.0.2.2',2424),2); "
                                   "s.sendall(b'hello'); print(s.recv(5).decode())"]
                        replies.append(subprocess.check_output(command, text=True).strip())
                except BaseException as error:
                    errors.append(error)
            probe_timer = Timer(.2, probe)
            stop_timer = Timer(7.2, lambda: os.kill(os.getpid(), signal.SIGINT))
            console.watch(runner)
            probe_timer.start()
            stop_timer.start()
            try:
                runner.run()
                probe_timer.join()
                stop_timer.join()
                after = deployed.containers()
                assert {name: c['State']['Pid'] for name, c in before.items()} == {name: c['State']['Pid'] for name, c in after.items()}
            finally:
                console.close()
                probe_timer.cancel()
                stop_timer.cancel()
    # Removing the owned UT stops its test listener too.
    server.wait()
    server.stdout.close()
    assert not errors, errors
    assert replies == ['hello'] * 3
    records = [json.loads(line) for line in (run / 'execution.jsonl').read_text().splitlines()]
    assert records[-1]['kind'] == 'stopped'
    replay = applied_events(repetition.iterate(cycles=3), records)
    assert any(event['kind'] == 'link' and event['time'] >= 6 for event in replay['events'])
    attachment = [json.loads(line) for line in (run / 'attachment.jsonl').read_text().splitlines()]
    assert [event['node'] for event in attachment if event['kind'] == 'attachment'] == ['a', 'b', 'a', 'b', 'a', 'b']
    assert next(event['errors'] for event in attachment if event['kind'] == 'cleanup') == []
    client = [json.loads(line) for line in (run / 'tcp-client.jsonl').read_text().splitlines()]
    assert sum(event['kind'] == 'connected' for event in client) == 1
    assert any(event['kind'] == 'echo' for event in client if event['monotonic'] > runner.origin + 6)
    assert not any(event['kind'] == 'error' for event in client)
    assert not subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=^/' + terminal.name + '(-helper)?$'], text=True).strip()
