"""Example-only UT attachment: a stable IP address behind changing label egresses.

Original IP packets use the real GZ/MPLS plan. This adapter follows adopted pop rules,
not future geometric assignments. It is not a general UT mobility protocol.
"""
import errno
from datetime import datetime, timedelta
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid

from skyseed import load_plans
from skyseed.runner.instruction_executor import _setns
from skyseed.runner.netlink import RouteSocket, _attr, _text, _u32


class EdgeSocket(RouteSocket):
    """Add the example access interface to the existing BPF injection table."""
    def ingress(self, destination, *, delete=False):
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, 32, 0, 0, 0, 0, 0, 1, 0)
        payload += _attr(1, IPv4Address(destination).packed) + _text(3, "ssut")
        payload += _u32(6, 1) + _u32(15, 0x5353) + _attr(21, bytes([252]))
        self.request(33 if delete else 32, payload, flags=0 if delete else 0x600)


class TerminalExperiment:
    """Parent owns the UT container and one observer/attachment process."""
    def __init__(self, scene, deployment, plans, output, *, seconds, capture=False,
                 ground_stations=None, repetition=None):
        self.scene, self.deployment, self.plans = scene, deployment, plans
        self.output = Path(output).resolve()
        self.seconds = seconds
        self.capture = capture
        self.grounds = ground_stations
        self.repetition = repetition
        self.name = "skyseed-ut-" + uuid.uuid4().hex[:10]
        self.process = None
        self.container = None
        self.diagnostics = None

    def __enter__(self):
        experiment = self.scene.data["experiment"]
        zone = experiment["zone"]
        grounds = tuple(self.grounds) if self.grounds is not None else (experiment["ground_station"],)
        ground = grounds[0]
        targets, changes, prefix = {}, {}, None
        serving_nodes = set()
        first_route = True
        plans = load_plans(self.plans) if self.repetition is None else self.repetition.iterate(cycles=2)
        for plan in plans:
            targets.update((node["node_id"], node) for node in plan.targets["nodes"])
            if plan.stream != "route":
                continue
            operations = plan.operations
            if first_route:
                operations = (*plan.initial_operations, *plan.context_operations, *operations)
                first_route = False
            for operation in operations:
                for instruction in operation.instructions:
                    value = instruction.parameters
                    key = operation.timestamp.isoformat() + "|" + operation.target
                    changed = None
                    if value["destination"] == ("endpoint", zone):
                        prefix = value["prefix"]
                        if value["match"][0] == "label":
                            changed = value["kind"] != "withdraw" and value["action"] == "pop_deliver"
                            if value["action"] == "pop_deliver":
                                serving_nodes.add(operation.target)
                    # Keep a position for every instruction: a later same-time
                    # batch is adopted only when its own receipt arrives.
                    changes.setdefault(key, []).append(changed)
        # A retained local label may change from pop to swap at handover.
        # That replacement also removes this node's local delivery permission.
        changes = {key: value for key, value in changes.items() if key.split("|", 1)[1] in serving_nodes}
        if prefix is None:
            raise ValueError("the fixed UT zone has no compiled destination in this experiment")
        containers = self.deployment.containers()
        image = containers[targets[ground]["service"]]["Config"]["Image"]
        try:
            self.container = subprocess.run([
                "docker", "run", "-d", "--init", "--name", self.name,
                "--label", "skyseed.example=s4-handover", "--network", "none", "--cap-add", "NET_ADMIN",
                "--sysctl", "net.ipv4.conf.all.rp_filter=0", "--entrypoint", "sleep", image, "infinity"],
                check=True, capture_output=True, text=True).stdout.strip()
            ut = json.loads(subprocess.run(["docker", "inspect", self.container], check=True,
                                          capture_output=True, text=True).stdout)[0]
            relevant = {key.split("|", 1)[1] for key in changes} | set(grounds)
            config = {"ground": ground, "zone": zone, "zone_ip": prefix.split("/")[0],
                      "ground_ip": targets[ground]["identity_address"].split("/")[0],
                      "ut_address": experiment["ut_address"],
                      "ut_pid": ut["State"]["Pid"], "changes": changes, "seconds": self.seconds,
                      "capture": self.capture, "ut_container": self.name,
                      "ground_ips": {node: targets[node]["identity_address"].split("/")[0] for node in grounds},
                      "containers": {node: containers[targets[node]["service"]]["Name"].lstrip("/") for node in relevant},
                      "period_seconds": None if self.repetition is None else self.repetition.seconds,
                      "start_time": None if self.repetition is None else self.repetition.start.isoformat(),
                      "nodes": {node: containers[targets[node]["service"]]["State"]["Pid"] for node in relevant},
                      "output": str(self.output)}
            path = self.output / "terminal.json"
            path.write_text(json.dumps(config, indent=2) + "\n")
            self.diagnostics = (self.output / "terminal-helper.log").open("w")
            command = ["docker", "run", "--rm", "-i", "--privileged", "--pid", "host", "--network", "host",
                       "--name", self.name + "-helper", "--label", "skyseed.example=s4-handover",
                       "--mount", "type=bind,source=/,target=/host,readonly",
                       "--mount", f"type=bind,source={self.output},target=/host{self.output}",
                       "--entrypoint", "/usr/sbin/chroot", image, "/host", sys.executable,
                       str(Path(__file__).resolve()), str(path)]
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.diagnostics, text=True, start_new_session=True)
            if self.process.stdout.readline().strip() != "ready":
                raise RuntimeError(f"terminal preparation failed: {self.output / 'terminal-helper.log'}")
        except BaseException as error:
            try:
                self.close(cancel=True)
            except BaseException as cleanup:
                error.add_note(f"terminal cleanup also failed: {cleanup}")
            raise
        return self

    def close(self, *, cancel=False):
        if self.process is not None:
            if cancel and self.process.poll() is None:
                subprocess.run(["docker", "kill", "--signal", "TERM", self.name + "-helper"], check=True)
            status = self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None
        else:
            status = 0
        if self.container is not None:
            subprocess.run(["docker", "stop", self.container], check=True, capture_output=True)
            subprocess.run(["docker", "rm", self.container], check=True, capture_output=True)
            self.container = None
        if self.diagnostics:
            self.diagnostics.close()
        if status and not cancel:
            raise RuntimeError(f"terminal experiment failed ({status}); inspect {self.output / 'terminal-helper.log'}")

    def __exit__(self, kind, error, traceback):
        try:
            self.close(cancel=error is not None)
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"terminal cleanup also failed: {cleanup}")


class Attachment:
    def __init__(self, config):
        self.config = config
        self.changes = config["changes"]
        self.change_positions = {}
        self.output = Path(config["output"])
        self.original = os.open("/proc/self/ns/net", os.O_RDONLY)
        self.nodes = {}
        self.children = []
        self.captures = []
        self.files = []
        self.current = None
        self.clock = None
        self.client_started = False
        self.stopped = False
        self.log = (self.output / "attachment.jsonl").open("x")
        signal.signal(signal.SIGTERM, self.stop)
        for node, pid in {**config["nodes"], "ut": config["ut_pid"]}.items():
            namespace = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
            _setns(namespace)
            channel = EdgeSocket()
            _setns(self.original)
            self.nodes[node] = {"namespace": namespace, "socket": channel}

    def stop(self, *_):
        self.stopped = True

    def record(self, kind, **values):
        self.log.write(json.dumps({"kind": kind, "monotonic": time.monotonic(), **values}) + "\n")
        self.log.flush()

    def prepare(self):
        terminal = self.config["ut_address"]
        ground = self.config["ground_ip"]
        for identity, node in self.nodes.items():
            channel = node["socket"]
            if identity == "ut":
                channel.create_dummy("ssutidentity")
                index = channel.link_index("ssutidentity")
                node["identity"] = index
                channel.address(index, terminal + "/32")
                channel.set_link(index, up=True)
                for address in self.config["ground_ips"].values():
                    channel.route(address + "/32", protocol=252)
            elif identity not in self.config["ground_ips"]:
                # Before attachment (or after detach), do not fall through to
                # an unrelated default route for the terminal's address.
                channel.route(terminal + "/32", protocol=252)
        self.record("prepared", zone=self.config["zone_ip"], ground=ground,
                    ut_address=terminal, encapsulation="none")
        if self.config["capture"]:
            for node, name in (("ut", "ut"), (self.config["ground"], "gs")):
                self.launch(node, ["tcpdump", "-i", "any", "-n", "-U", "-w",
                                   str(self.output / f"{name}.pcap")], f"{name}-capture.log")
                self.captures.append(self.children.pop())

    def attach(self, node, *, activated):
        if node == self.current:
            return
        previous = self.current
        ut = self.nodes["ut"]
        if previous is not None:
            old = self.nodes[previous]["socket"]
            for address in self.config["ground_ips"].values():
                old.ingress(address, delete=True)
            old.delete_link(old.link_index("ssut"))
            old.route(self.config["ut_address"] + "/32", protocol=252)
            for address in self.config["ground_ips"].values():
                ut["socket"].route(address + "/32", protocol=252)
            self.current = None
        if node is not None:
            channel = self.nodes[node]["socket"]
            channel.create_veth("ssut", "ssut", ut["namespace"])
            star_index, ut_index = channel.link_index("ssut"), ut["socket"].link_index("ssut")
            for owner, index, address, mac, peer, peer_mac in (
                (channel, star_index, "169.254.254.1/30", "02:53:00:00:01:01", "169.254.254.2", "02:53:00:00:01:02"),
                (ut["socket"], ut_index, "169.254.254.2/30", "02:53:00:00:01:02", "169.254.254.1", "02:53:00:00:01:01"),
            ):
                owner.set_link(index, mac=mac, up=True)
                owner.local_ingress_ipv4(index)
                owner.address(index, address)
                owner.neighbour(index, peer, peer_mac)
            channel.route(self.config["ut_address"] + "/32", index=star_index, gateway="169.254.254.2", protocol=252)
            for address in self.config["ground_ips"].values():
                channel.ingress(address)
                ut["socket"].route(address + "/32", index=ut_index,
                                   gateway="169.254.254.1", source=self.config["ut_address"], protocol=252)
            self.current = node
        self.record("attachment", previous=previous, node=node, route_activated=activated)
        state_file = self.output / "attachment-state.tmp"
        state_file.write_text(json.dumps({"node": node}))
        state_file.replace(self.output / "attachment-state.json")
        if node is not None and self.clock is not None:
            self.start_client()

    def launch(self, node, command, name):
        output = (self.output / name).open("x")
        self.files.append(output)
        _setns(self.nodes[node]["namespace"])
        try:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        finally:
            _setns(self.original)
        self.children.append(process)

    def traffic(self):
        seconds = self.config["seconds"]
        script = str(Path(__file__).with_name("traffic.py"))
        self.launch(self.config["ground"], [sys.executable, script, "server", self.config["ground_ip"], str(None if seconds is None else seconds + 5),
                                           str(self.output / "tcp-server.jsonl")], "tcp-server.log")
        # Wait for the listening fact, not an assumed startup delay.
        server = self.output / "tcp-server.jsonl"
        while not server.exists() or not server.stat().st_size:
            if self.children[0].poll() is not None:
                raise RuntimeError("TCP server failed before listening")
            time.sleep(.01)
        self.record("traffic", wall_offset=time.time() - time.monotonic())

    def start_client(self):
        if self.client_started:
            return
        seconds = self.config["seconds"]
        remaining = None if seconds is None else self.clock + seconds - time.monotonic()
        script = str(Path(__file__).with_name("traffic.py"))
        self.launch("ut", [sys.executable, script, "client", self.config["ground_ip"], str(remaining),
                           str(self.output / "tcp-client.jsonl")], "tcp-client.log")
        self.client_started = True

    def adopted_service(self, event, active):
        newest = None
        for adoption in event["adoptions"]:
            for node in adoption["nodes"]:
                scheduled = adoption["scheduled"]
                timestamp = datetime.fromisoformat(scheduled)
                period = self.config.get("period_seconds")
                if period is not None:
                    start = datetime.fromisoformat(self.config["start_time"])
                    cycle = int((timestamp - start).total_seconds() // period)
                    if cycle > 1:
                        timestamp -= timedelta(seconds=(cycle - 1) * period)
                key = timestamp.isoformat() + "|" + node
                if key not in self.changes:
                    continue
                previous, position = self.change_positions.get(node, (None, 0))
                if previous != scheduled:
                    position = 0
                count = adoption["instruction_counts"][node]
                changes = self.changes[key][position:position + count]
                if len(changes) != count:
                    raise ValueError(f"UT receipt exceeds planned instruction count: {key}")
                self.change_positions[node] = (scheduled, position + count)
                for change in changes:
                    if change is True:
                        active.add(node)
                        newest = node
                    elif change is False:
                        active.discard(node)
                        if newest == node:
                            newest = None
        if newest is not None:
            return newest
        if self.current in active:
            return self.current
        return next(iter(sorted(active)), None)

    def follow(self):
        active = set()
        self.record("watching")
        with (self.output / "execution.jsonl").open() as stream:
            while not self.stopped:
                line = stream.readline()
                if not line:
                    time.sleep(.01)
                    continue
                event = json.loads(line)
                if event["kind"] == "clock":
                    self.clock = event["origin"]
                    self.traffic()
                    if self.current is not None:
                        self.start_client()
                elif event["kind"] == "route":
                    chosen = self.adopted_service(event, active)
                    self.attach(chosen, activated=event["activated"])
                elif event["kind"] in ("drained", "stopped"):
                    self.stopped = event["kind"] == "stopped"
                    break

    def close(self):
        # Stop the client before closing its peer, so a deliberate interruption
        # cannot be observed as an unexpected EOF/network error by that client.
        for child in reversed(self.children):
            if self.stopped and child.poll() is None:
                child.terminate()
            child.wait()
        self.record("processes", exit_codes=[child.returncode for child in self.children])
        for capture in self.captures:
            if capture.poll() is None:
                capture.send_signal(signal.SIGINT)
            capture.wait()
        errors = []

        def remove(operation, *args, **kwargs):
            try:
                operation(*args, **kwargs)
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ESRCH, errno.ENODEV):
                    errors.append(error)

        # Preparation or attachment can stop between any two operations. Each
        # owned resource is removed independently, including a partial veth.
        for identity, node in self.nodes.items():
            channel = node["socket"]
            if identity != "ut" and identity not in self.config["ground_ips"]:
                for address in self.config["ground_ips"].values():
                    remove(channel.ingress, address, delete=True)
                try:
                    edge = channel.link_index("ssut")
                except OSError as error:
                    if error.errno != errno.ENODEV:
                        errors.append(error)
                else:
                    remove(channel.delete_link, edge)
            if identity not in self.config["ground_ips"]:
                addresses = self.config["ground_ips"].values() if identity == "ut" else (self.config["ut_address"],)
                for address in addresses:
                    remove(channel.route, address + "/32", protocol=252, delete=True)
            if "identity" in node:
                remove(channel.delete_link, node["identity"])
            channel.close()
            os.close(node["namespace"])
        self.record("cleanup", previous=self.current, errors=[str(error) for error in errors])
        os.close(self.original)
        for output in self.files:
            output.close()
        self.log.close()
        if errors:
            raise ExceptionGroup("UT attachment cleanup failed", errors)


if __name__ == "__main__":
    configuration = Path(sys.argv[1])
    attachment = Attachment(json.loads(configuration.read_text()))
    try:
        attachment.prepare()
        print("ready", flush=True)
        attachment.follow()
    except BaseException as error:
        attachment.stopped = True
        try:
            attachment.close()
        except BaseException as cleanup:
            error.add_note(f"UT attachment cleanup also failed: {cleanup}")
        raise
    else:
        attachment.close()
