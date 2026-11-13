"""Real example adapter: one TCP connection survives a change of serving node."""
from datetime import timedelta
import json
import subprocess

from skyseed import Scene, Runner, PlanCompiler, save_plans
from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RouteOperation, RoutePlan
from examples.s4_handover.experiment.ut.attachment import TerminalExperiment
from examples.s4_handover.experiment.record_store import RecordStore, archive_measurements
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
    with RecordStore(output) as store:
        with Runner(plans, deployed, output=output, store=store) as runner:
            with TerminalExperiment(scene, deployed, path, output, seconds=8, capture=True) as terminal:
                runner.run()
        stored = archive_measurements(store, output)
        execution = [value for key, value in sorted(stored.items()) if key.startswith("runner/events/")]
        assert execution == [json.loads(line) for line in (output / "execution.jsonl").read_text().splitlines()]
        replay = applied_events(plans, execution)
        assert any(event["time"] > 3 and event["kind"] == "route" for event in replay["events"])
        assert store.get_user("tcp-client/000000000")["kind"] == "connecting"

    client = [json.loads(line) for line in (output / "tcp-client.jsonl").read_text().splitlines()]
    server = [json.loads(line) for line in (output / "tcp-server.jsonl").read_text().splitlines()]
    attachments = [json.loads(line) for line in (output / "attachment.jsonl").read_text().splitlines()]
    processes = next(event for event in attachments if event["kind"] == "processes")
    assert processes["exit_codes"] == [0, 0]
    first_attachment = next(event for event in attachments if event["kind"] == "attachment")
    first_connect = next(event for event in client if event["kind"] == "connecting")
    assert first_connect["monotonic"] >= first_attachment["monotonic"]
    handover = next(event["monotonic"] for event in attachments if event.get("node") == "b")
    configuration = json.loads((output / "terminal.json").read_text())
    assert False in configuration["changes"][change.isoformat() + "|a"]
    assert sum(event["kind"] == "accepted" for event in server) == 1
    assert sum(event["kind"] == "connected" for event in client) == 1
    assert not [event for event in client if event["kind"] == "error"]
    assert any(event["kind"] == "echo" and event["monotonic"] < handover for event in client)
    assert any(event["kind"] == "echo" and event["monotonic"] > handover + 1 for event in client)
    connected = next(event for event in client if event["kind"] == "connected")
    assert connected["local"][0] == "192.0.2.2"
    assert connected["peer"][0] == configuration["ground_ip"]
    # Check the actual wire format, not just the absence of a tunnel in code.
    for side in ("ut", "gs"):
        encapsulated = subprocess.check_output(
            ["tcpdump", "-n", "-r", str(output / f"{side}.pcap"), "ip proto 4"],
            text=True, stderr=subprocess.DEVNULL)
        assert not encapsulated.strip()
    labeled = subprocess.check_output(
        ["tcpdump", "-nn", "-r", str(output / "gs.pcap")],
        text=True, stderr=subprocess.DEVNULL)
    packets = [line for line in labeled.splitlines() if "MPLS" in line]
    assert any("192.0.2.2" in line and ".2323" in line for line in packets)
    assert all(line.count(" IP ") == 1 for line in packets)
    for name, container in deployed.containers().items():
        links = json.loads(subprocess.check_output(
            ["docker", "exec", container["Id"], "ip", "-j", "link", "show"], text=True))
        assert {link["ifname"] for link in links} == initial_interfaces[name]
    assert not subprocess.check_output(
        ["docker", "ps", "-aq", "--filter", "name=^/" + terminal.name + "(-helper)?$"], text=True).strip()
