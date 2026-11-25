"""Opt-in real Linux tests: SKYSEED_LIVE_IMAGE must name a cached node image."""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import re
import time

import pytest
import yaml

from skyseed import PlanCompiler
from skyseed.pc.compiler.bpf.compile_program import compile_label_program
from skyseed.pc.link import ConnectionCandidate, LinkConfiguration, LinkProperties, LinkPlan, LinkOperation
from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RoutePlan
from skyseed.pc.physical import LinkCandidate
from skyseed.runner.deployment import Deployment
from skyseed.runner.execution_process import CommandChannel
from skyseed.runner.plan_queue import instruction_record


@pytest.fixture
def deployment(tmp_path, request):
    image = os.environ.get("SKYSEED_LIVE_IMAGE")
    if not image:
        pytest.skip("set SKYSEED_LIVE_IMAGE for explicitly requested live tests")
    services = {}
    for name in getattr(request, "param", ("a", "b", "c")):
        services[name] = {
            "image": image, "entrypoint": ["sleep", "infinity"], "network_mode": "none",
            "cap_add": ["NET_ADMIN"], "init": True,
            "sysctls": {"net.ipv4.conf.all.rp_filter": 0, "net.ipv4.ip_forward": 1},
            "labels": {"org.seedsecuritylabs.seedemu.meta.skyseed.scene_id": name,
                       "org.seedsecuritylabs.seedemu.meta.skyseed.scene_kind": "satellite"}}
    (tmp_path / "docker-compose.yml").write_text(yaml.safe_dump({"services": services}))
    deployment = Deployment(tmp_path, project="skyseed-runner-live")
    try:
        deployment.up()
        yield deployment, image
    finally:
        deployment.down()


def material(directory, backend):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    links = {}
    for left, right in (("a", "b"), ("b", "c")):
        identity = (left, right)
        candidate = ConnectionCandidate(identity, {
            left: LinkCandidate(left, right), right: LinkCandidate(right, left)})
        links[identity] = LinkConfiguration(candidate, LinkProperties(identity, 10_000_000, .002, 0),
                                            LinkProperties(identity, 10_000_000, .003, 0))
    link_plan = LinkPlan(start, start + timedelta(seconds=5), links)
    rules = {name: [] for name in ("a", "b", "c")}
    for source, middle, target, label_in, label_out in (("a", "b", "c", 21, 31), ("c", "b", "a", 41, 51)):
        destination = RouteDestination("node", target)
        first = LogicalExit(tuple(sorted((source, middle))), source)
        second = LogicalExit(tuple(sorted((middle, target))), middle)
        if backend == "bpf":
            rules[source].append(ForwardingRule(destination, "push", first, outgoing_label=label_in))
            rules[middle].append(ForwardingRule(destination, "swap", second, label_in, label_out))
            rules[target].append(ForwardingRule(destination, "pop_deliver", incoming_label=label_out))
        else:
            rules[source].append(ForwardingRule(destination, "forward", first))
            rules[middle].append(ForwardingRule(destination, "forward", second))
            rules[target].append(ForwardingRule(destination, "deliver"))
    route_plan = RoutePlan(start, start + timedelta(seconds=5), rules)
    compiler = PlanCompiler(directory, forwarding_backend=backend)
    plans = tuple(compiler.compile_plans(link_plans=[link_plan], route_plans=[route_plan]))
    down = LinkPlan(start + timedelta(seconds=5), start + timedelta(seconds=6), links,
                    (LinkOperation(start + timedelta(seconds=5), "withdraw", ("b", "c")),))
    up = LinkPlan(start + timedelta(seconds=6), start + timedelta(seconds=7), {},
                  (LinkOperation(start + timedelta(seconds=6), "establish", ("b", "c"), links["b", "c"]),))
    down_plan, up_plan = compiler.compile_plans(link_plans=[down, up])
    return plans, down_plan, up_plan


def ping(container, *, succeeds):
    result = subprocess.run(["docker", "exec", container, "ping", "-n", "-c", "2", "-W", "1",
                             "198.18.0.3"], text=True, capture_output=True)
    assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("backend", ["bpf", "linux"])
def test_real_compiler_to_kernel_roundtrip_break_and_restore(deployment, tmp_path, backend):
    deployed, image = deployment
    plans, down, up = material(deployed.directory, backend)
    containers = deployed.containers()
    # Kernel modules may create default devices (for example tunl0) in every
    # namespace. Cleanup must restore the existing interface inventory.
    initial_interfaces = {}
    for name, container in containers.items():
        output = subprocess.check_output(["docker", "exec", container["Id"], "ip", "-j", "link"], text=True)
        initial_interfaces[name] = {item["ifname"] for item in json.loads(output)}
    nodes = {target["node_id"]: target for plan in plans for target in plan.targets["nodes"]}
    targets = [{**target, "pid": containers[target["service"]]["State"]["Pid"]} for target in nodes.values()]
    channel = CommandChannel(image, backend, compile_label_program() if backend == "bpf" else None,
                             tmp_path / f"{backend}-helper.log")
    try:
        channel.request("nodes", targets=targets)
        if backend == "bpf":
            channel.request("prefixes", prefixes=plans[0].targets["address_pools"])
        channel.request("apply", instructions=[instruction_record(item) for operation in plans[0].initial_operations
                                               for item in operation.instructions])
        instructions = [instruction_record(item) for operation in plans[1].initial_operations
                        for item in operation.instructions]
        if backend == "bpf":
            channel.request("prepare", job="initial", instructions=instructions)
            while True:
                result = channel.request("step", job="initial")["result"]
                if result["done"]:
                    break
            channel.request("publish", generation=result["value"])
        else:
            channel.request("apply", instructions=instructions)
        output = ping(containers["a"]["Id"], succeeds=True)
        (tmp_path / f"{backend}-ping.txt").write_text(output)
        channel.request("apply", instructions=[instruction_record(item) for operation in down.operations
                                               for item in operation.instructions])
        ping(containers["a"]["Id"], succeeds=False)
        channel.request("apply", instructions=[instruction_record(item) for operation in up.operations
                                               for item in operation.instructions])
        ping(containers["a"]["Id"], succeeds=True)
    finally:
        channel.close()
    for name, container in containers.items():
        result = subprocess.run(["docker", "exec", container["Id"], "ip", "-j", "link"],
                                check=True, capture_output=True, text=True)
        assert {item["ifname"] for item in json.loads(result.stdout)} == initial_interfaces[name]

def test_runner_keeps_one_clock_and_drains_future_link_changes(deployment, tmp_path):
    from dataclasses import replace
    from skyseed import Runner
    from skyseed.pc.compiler import PhysicalOperation
    deployed, _image = deployment
    plans, down, up = material(deployed.directory, "bpf")
    route = plans[1]
    transit = next(operation for operation in route.initial_operations if operation.target == "b")
    instruction = transit.instructions[0]
    withdrawal = replace(instruction, parameters={**instruction.parameters, "kind": "withdraw"})
    route = replace(route, operations=(
        PhysicalOperation(route.start_time + timedelta(seconds=2), "withdraw", "b", (withdrawal,)),
        PhysicalOperation(route.start_time + timedelta(seconds=3), "install", "b", (instruction,))))
    containers = deployed.containers()
    with Runner([plans[0], route, down, up], deployed, output=tmp_path / "runtime") as runner:
        wall_offset = time.time() - time.monotonic()
        process = subprocess.Popen(["docker", "exec", containers["a"]["Id"], "ping", "-n", "-D",
                                    "-i", ".2", "-c", "40", "-W", "1", "198.18.0.3"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            runner.run()
        finally:
            output, errors = process.communicate()
        (tmp_path / "runtime/ping.txt").write_text(output + errors)
        assert "icmp_seq=40" in output, output + errors
    records = [json.loads(line) for line in (tmp_path / "runtime/execution.jsonl").read_text().splitlines()]
    clock, = [record for record in records if record["kind"] == "clock"]
    links = [record for record in records if record["kind"] == "link"]
    assert [record["operation"] for record in links] == ["establish", "establish", "withdraw", "establish"]
    assert links[2]["started"] >= clock["origin"] + 5
    assert links[3]["started"] >= clock["origin"] + 6
    assert records[-1]["kind"] == "drained"
    assert records[-1]["completed"] >= clock["origin"] + 7
    publications = [record for record in records if record["kind"] == "route"]
    assert len(publications) == 3
    assert publications[1]["adoptions"][0]["nodes"] == ["b"]
    assert publications[2]["activated"] >= clock["origin"] + 3
    replies = [float(match.group(1)) - wall_offset
               for match in re.finditer(r"\[(\d+\.\d+)\].*bytes from", output)]
    withdrawn, restored = publications[1]["activated"], publications[2]["activated"]
    assert any(clock["origin"] + .5 < timestamp < withdrawn for timestamp in replies)
    assert not any(withdrawn + .1 < timestamp < restored for timestamp in replies)
    assert any(restored + .1 < timestamp < links[2]["started"] for timestamp in replies)
