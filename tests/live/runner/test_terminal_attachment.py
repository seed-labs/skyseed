"""Real example adapter: one TCP connection survives a change of serving node."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import time

from skyseed import Scene, Runner, PlanCompiler, save_plans
from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RouteOperation, RoutePlan
from examples.s4_handover.experiment.ut.attachment import TerminalAttachment
from tools.observation.execution.inspection import applied_events
from tests.live.runner.test_kernel_forwarding import deployment, material


def test_original_ip_over_labels_keeps_same_tcp_connection_across_service_change(deployment, tmp_path):
    deployed, _ = deployment
    initial_interfaces = {}
    for name, container in deployed.containers().items():
        output = subprocess.check_output(["docker", "exec", container["Id"], "ip", "-j", "link"], text=True)
        initial_interfaces[name] = {item["ifname"] for item in json.loads(output)}
    old, _, _ = material(deployed.directory, "bpf")
    # Reuse the fixture's actual logical links, not its unrelated node route plan.
    from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkPlan
    from skyseed.pc.physical import LinkCandidate
    links = {}
    for left, right in (("a", "b"), ("b", "c")):
        identity = (left, right)
        candidate = ConnectionCandidate(identity, {left: LinkCandidate(left, right), right: LinkCandidate(right, left)})
        links[identity] = LinkConfiguration(candidate, LinkProperties(identity, 10_000_000, .002, 0),
                                            LinkProperties(identity, 10_000_000, .002, 0))
    start = old[0].start_time
    end = start + timedelta(seconds=8)
    ground, zone = RouteDestination("node", "c"), RouteDestination("endpoint", "zone")
    rules = {
        "a": [ForwardingRule(ground, "push", LogicalExit(("a", "b"), "a"), outgoing_label=21),
              ForwardingRule(zone, "pop_deliver", incoming_label=51)],
        "b": [ForwardingRule(ground, "swap", LogicalExit(("b", "c"), "b"), 21, 31),
              ForwardingRule(zone, "swap", LogicalExit(("a", "b"), "b"), 41, 51)],
        "c": [ForwardingRule(ground, "pop_deliver", incoming_label=31),
              ForwardingRule(zone, "push", LogicalExit(("b", "c"), "c"), outgoing_label=41),
              ForwardingRule(RouteDestination("prefix", "192.0.2.2/32"), "push",
                             LogicalExit(("b", "c"), "c"), outgoing_label=41)],
    }
    change = start + timedelta(seconds=3)
    first_service = start + timedelta(seconds=.4)
    operations = [RouteOperation(first_service, node, "install", rule)
                  for node, entries in rules.items() for rule in entries]
    operations.extend([RouteOperation(change, "a", "replace", ForwardingRule(zone, "swap",
                                 LogicalExit(("a", "b"), "a"), 51, 41)),
                  RouteOperation(change, "b", "replace", ForwardingRule(zone, "pop_deliver", incoming_label=41)),
                  RouteOperation(change, "b", "install", ForwardingRule(ground, "push",
                                 LogicalExit(("b", "c"), "b"), outgoing_label=31))])
    compiler = PlanCompiler(deployed.directory)
    plans = list(compiler.compile_plans(link_plans=[LinkPlan(start, end, links)],
                                       route_plans=[RoutePlan(start, end, {}, operations)]))
    path = tmp_path / "plans.plan"
    save_plans(path, plans)
    scene = Scene(data={"experiment": {"zone": "zone", "ground_station": "c",
                                      "ut_address": "192.0.2.2"}})
    output = tmp_path / "experiment"
    with Runner(plans, deployed, output=output) as runner:
        with TerminalAttachment(scene, deployed, path, output) as terminal:
            configuration = json.loads((output / 'terminal.json').read_text())
            ground_ip = configuration['ground_ips']['c']
            with ThreadPoolExecutor(max_workers=1) as worker:
                running = worker.submit(runner.run)
                capture = None

                def wait_for_service(node):
                    state = output / 'attachment-state.json'
                    while not state.exists() or json.loads(state.read_text())['node'] != node:
                        if running.done():
                            running.result()
                            raise AssertionError(f'run finished without attachment to {node}')
                        time.sleep(.01)

                try:
                    wait_for_service('a')
                    # Capture belongs to the test too, not to the UT adapter.
                    capture_code = '''
import json, socket, struct, time
frames = []
with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)) as s:
    s.settimeout(.2)
    print('ready', flush=True)
    end = time.monotonic() + 8
    while time.monotonic() < end:
        try:
            packet = s.recv(65535)
        except TimeoutError:
            continue
        if len(packet) < 42 or packet[12:14] != b'\\x88\\x47':
            continue
        ip = packet[18:]
        if ip[0] >> 4 != 4:
            continue
        offset = (ip[0] & 15) * 4
        frames.append([socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20]),
                       ip[9], list(struct.unpack('!HH', ip[offset:offset+4]))])
print(json.dumps(frames), flush=True)
'''
                    capture = subprocess.Popen(['docker', 'exec', configuration['containers']['c'],
                                                'python3', '-u', '-c', capture_code],
                                               stdout=subprocess.PIPE, text=True)
                    assert capture.stdout.readline().strip() == 'ready'
                    # Traffic belongs to this test, just as nc belongs to the user.
                    server_code = (
                        "import socket,sys; s=socket.socket(); s.settimeout(10); "
                        "s.bind((sys.argv[1],9090)); s.listen(); print('ready',flush=True); "
                        "c,_=s.accept(); c.settimeout(10); f=c.makefile('rwb',buffering=0); "
                        "exec('for line in f:\\n f.write(line)'); c.close(); s.close()")
                    client_code = (
                        "import socket,sys; s=socket.create_connection((sys.argv[1],9090),5); "
                        "print(s.getsockname()[0],flush=True); f=s.makefile('rwb',buffering=0); "
                        "exec('for line in sys.stdin:\\n f.write(line.encode())\\n print(f.readline().decode().strip(),flush=True)'); "
                        "s.close()")
                    with subprocess.Popen(['docker', 'exec', configuration['containers']['c'],
                                           'python3', '-u', '-c', server_code, ground_ip],
                                          stdout=subprocess.PIPE, text=True) as server:
                        assert server.stdout.readline().strip() == 'ready'
                        with subprocess.Popen(['docker', 'exec', '-i', terminal.name, 'python3', '-u',
                                               '-c', client_code, ground_ip], stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE, text=True) as client:
                            assert client.stdout.readline().strip() == '192.0.2.2'
                            for node in ('a', 'b'):
                                wait_for_service(node)
                                client.stdin.write(node + '\n')
                                client.stdin.flush()
                                assert client.stdout.readline().strip() == node
                        assert client.returncode == 0
                    assert server.returncode == 0
                    running.result()
                finally:
                    runner.stop()
                    if capture is not None:
                        captured, _ = capture.communicate()

    assert capture.returncode == 0
    packets = json.loads(captured)
    assert any('192.0.2.2' in packet[:2] and 9090 in packet[3] for packet in packets)
    assert all(packet[2] != 4 for packet in packets)  # No IP-in-IP inside MPLS.

    execution = [json.loads(line) for line in (output / 'execution.jsonl').read_text().splitlines()]
    replay = applied_events(plans, execution)
    assert any(event['time'] > 3 and event['kind'] == 'route' for event in replay['events'])
    attachments = [json.loads(line) for line in (output / "attachment.jsonl").read_text().splitlines()]
    assert [event['node'] for event in attachments if event['kind'] == 'attachment'] == ['a', 'b']
    assert False in configuration["changes"][change.isoformat() + "|a"]
    assert next(event['errors'] for event in attachments if event['kind'] == 'cleanup') == []
    assert not (output / 'tcp-client.jsonl').exists()
    assert not (output / 'ut.pcap').exists()
    for name, container in deployed.containers().items():
        links = json.loads(subprocess.check_output(
            ["docker", "exec", container["Id"], "ip", "-j", "link", "show"], text=True))
        assert {link["ifname"] for link in links} == initial_interfaces[name]
    assert not subprocess.check_output(
        ["docker", "ps", "-aq", "--filter", "name=^/" + terminal.name + "(-helper)?$"], text=True).strip()
