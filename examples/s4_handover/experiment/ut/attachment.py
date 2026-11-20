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


class TerminalAttachment:
    """Parent owns the UT container and one observer/attachment process."""
    def __init__(self, scene, deployment, plans, output, *, ground_stations=None, repetition=None):
        self.scene, self.deployment, self.plans = scene, deployment, plans
        self.output = Path(output).resolve()
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
        targets, changes = {}, {}
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
        if not serving_nodes:
            print(f"UT zone {zone}: no serving satellite in saved plans; UT will remain unattached.", flush=True)
        # Deployed GS can be absent from plans when they have no satellite links.
        containers = {}
        for container in self.deployment.containers().values():
            identity = container["Config"]["Labels"].get("org.seedsecuritylabs.seedemu.meta.skyseed.scene_id")
            if identity is not None:
                containers[identity] = container
        image = containers[ground]["Config"]["Image"]
        try:
            self.container = subprocess.run([
                "docker", "run", "-d", "--init", "--name", self.name,
                "--label", "skyseed.example=s4-handover", "--network", "none", "--cap-add", "NET_ADMIN",
                "--sysctl", "net.ipv4.conf.all.rp_filter=0", "--entrypoint", "sleep", image, "infinity"],
                check=True, capture_output=True, text=True).stdout.strip()
            ut = json.loads(subprocess.run(["docker", "inspect", self.container], check=True,
                                          capture_output=True, text=True).stdout)[0]
            relevant = {key.split("|", 1)[1] for key in changes} | set(grounds)
            config = {"zone": zone,
                      "ut_address": experiment["ut_address"],
                      "ut_pid": ut["State"]["Pid"], "changes": changes, "ut_container": self.name,
                      "ground_ips": {node: targets[node]["identity_address"].split("/")[0]
                                     for node in grounds if node in targets},
                      "containers": {node: containers[node]["Name"].lstrip("/") for node in relevant},
                      "period_seconds": None if self.repetition is None else self.repetition.seconds,
                      "start_time": None if self.repetition is None else self.repetition.start.isoformat(),
                      "nodes": {node: containers[node]["State"]["Pid"] for node in serving_nodes},
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
            raise RuntimeError(f"terminal attachment failed ({status}); inspect {self.output / 'terminal-helper.log'}")

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
        self.current = None
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
            else:
                # Before attachment (or after detach), do not fall through to
                # an unrelated default route for the terminal's address.
                channel.route(terminal + "/32", protocol=252)
        (self.output / "attachment-state.json").write_text(json.dumps({"node": None}) + "\n")
        self.record("prepared", zone=self.config["zone"], ut_address=terminal)

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
                position = stream.tell()
                line = stream.readline()
                if not line.endswith("\n"):
                    # The writer may still be appending this receipt.
                    stream.seek(position)
                    time.sleep(.01)
                    continue
                event = json.loads(line)
                if event["kind"] == "route":
                    chosen = self.adopted_service(event, active)
                    self.attach(chosen, activated=event["activated"])
                elif event["kind"] in ("drained", "stopped"):
                    self.stopped = event["kind"] == "stopped"
                    break

    def close(self):
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
            if identity != "ut":
                for address in self.config["ground_ips"].values():
                    remove(channel.ingress, address, delete=True)
                try:
                    edge = channel.link_index("ssut")
                except OSError as error:
                    if error.errno != errno.ENODEV:
                        errors.append(error)
                else:
                    remove(channel.delete_link, edge)
            addresses = self.config["ground_ips"].values() if identity == "ut" else (self.config["ut_address"],)
            for address in addresses:
                remove(channel.route, address + "/32", protocol=252, delete=True)
            if "identity" in node:
                remove(channel.delete_link, node["identity"])
            channel.close()
            os.close(node["namespace"])
        self.record("cleanup", previous=self.current, errors=[str(error) for error in errors])
        os.close(self.original)
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
