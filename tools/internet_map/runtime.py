"""Launch the live viewer for one running deployment and its Runner journal."""
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

from skyseed.pc.network import GroundStationZoneRoutingModel

from .layout import display_nodes

IMAGE = "skyseed-internet-map"


def build_image():
    app = Path(__file__).with_name("app")
    subprocess.run(["docker", "build", "-t", IMAGE, str(app)], check=True)


def ensure_image():
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        build_image()


def container_node(container):
    labels = container["Config"]["Labels"]
    return {"container": container["Id"], "container_name": container["Name"].lstrip("/"),
            "container_state": container["State"].get("Status", "running"),
            "asn": labels.get("org.seedsecuritylabs.seedemu.meta.asn"), "labels": labels}


def zone_associations(scene, routing_model):
    """Save optional GS associations while the planning model is available."""
    if isinstance(routing_model, GroundStationZoneRoutingModel):
        return dict(routing_model.ground_station_associations(scene))
    return {}


def _schedule(plans, nodes, zones):
    routes, updates, links, prefixes = {}, {}, {}, {}
    seen = set()
    start = None
    for plan in plans:
        if start is None:
            start = plan.start_time
        for item in plan.targets["nodes"]:
            nodes[item["node_id"]]["identity_address"] = item["identity_address"]
        for item in plan.targets["destinations"]:
            if item["kind"] == "endpoint":
                prefixes[item["identity"]] = item["prefix"]
        for item in plan.targets["links"]:
            identity = json.dumps(item["link_id"], separators=(",", ":"))
            links[identity] = dict(item)
        operations = plan.operations
        if plan.stream not in seen:
            operations = (*plan.initial_operations, *plan.context_operations, *operations)
            seen.add(plan.stream)
        for op in operations:
            stamp = (op.timestamp - start) // timedelta(microseconds=1)
            if plan.stream == "link":
                identity = json.dumps(op.target, separators=(",", ":"))
                updates.setdefault(f"{stamp}|{identity}", []).append(
                    [dict(i.parameters) for i in op.instructions if i.opcode == "netem.replace"])
            elif zones:
                group = routes.setdefault(f"{stamp}|{op.target}", {"size": 0, "changes": []})
                if plan.forwarding_backend == "linux":
                    group.setdefault("operations", []).append(len(op.instructions))
                for instruction in op.instructions:
                    value = instruction.parameters
                    kind, target = value["destination"]
                    if kind == "endpoint" and target in zones:
                        # The local delivery rule binds the Zone address. Replacing
                        # it with a transit rule, or withdrawing it, releases it.
                        is_label = value.get("match", (None,))[0] == "label"
                        action = value["action"]
                        if is_label or action in {"forward", "deliver"}:
                            serving = action in {"deliver", "pop_deliver"} and value["kind"] != "withdraw"
                            group["changes"].append((group["size"], target, serving))
                    group["size"] += 1
    if start is None:
        raise ValueError("the map needs the initial Runner plans")
    return start, links, updates, {k: v for k, v in routes.items() if v["changes"]}, prefixes


def configuration(scene, deployment, plans, zones, extra, period, positions):
    """Index the finite schedule (include the wraparound once for a repeating run)."""
    nodes, layout = display_nodes(scene)
    inventory = deployment.containers()
    for item in inventory.values():
        identity = item["Config"]["Labels"].get("org.seedsecuritylabs.seedemu.meta.skyseed.scene_id")
        if identity in nodes:
            if "container" in nodes[identity]:
                raise ValueError(f"Duplicate deployed Scene identity: {identity}")
            nodes[identity].update(container_node(item))
    missing = [identity for identity, node in nodes.items() if "container" not in node]
    if missing:
        raise ValueError(f"Scene nodes missing from deployment: {missing}")
    attachment = None
    if extra:
        external, attachment = extra(layout)
        if nodes.keys() & external.keys():
            raise ValueError("extra map node duplicates a Scene node")
        nodes.update(external)
    else:
        layout["headings"] = [h for h in layout["headings"] if h["name"] != "UT"]
    start, links, updates, routes, prefixes = _schedule(plans, nodes, zones)
    for node in nodes.values():
        node.setdefault("identity_address", "")
    endpoints = {e.endpoint_id: {"location": f"{e.data['latitude_deg']:.4f}°, {e.data['longitude_deg']:.4f}°",
                                "prefix": prefixes.get(e.endpoint_id, ""), "ground_stations": zones[e.endpoint_id]}
                 for e in scene.get_endpoints("geographic-zone/v1") if zones and e.endpoint_id in zones}
    return dict(nodes=nodes, layout=layout, links=links, updates=updates, routes=routes, zones=endpoints,
                attachment=attachment, start=start.isoformat(), period=period, positions=bool(positions))


class InternetMap:
    def __init__(self, scene, deployment, plans, output, *, store=None, period=None, zones=None,
                 extra=None, positions=None, port=8080):
        self.args = scene, deployment, plans, zones, extra, period, positions
        self.output, self.store, self.port = Path(output).resolve(), store, port
        self.positions = positions
        self.container = None

    def __enter__(self):
        config = configuration(*self.args)
        config["run"] = uuid4().hex
        if self.store:
            config["store"] = {"endpoint": self.store.endpoint, "prefix": self.store.prefix + "runner/events/"}
        (self.output / "map-config.json").write_text(json.dumps(config, separators=(",", ":")))
        command = ["docker", "create", "--init", "--network", "host", "--label", "skyseed.internet-map",
                   "-e", f"SKYSEED_PORT={self.port}", "--mount",
                   "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock", "--mount",
                   f"type=bind,source={self.output},target=/run/skyseed,readonly"]
        if self.positions:
            command += ["--mount", f"type=bind,source={Path(self.positions).resolve()},target=/positions.json,readonly"]
        self.container = subprocess.check_output([*command, IMAGE], text=True).strip()
        try:
            subprocess.run(["docker", "start", self.container], check=True, stdout=subprocess.DEVNULL)
            self._wait_ready(config["run"])
        except BaseException:
            self.close()
            raise
        print(f"InternetMap2: http://localhost:{self.port}/pro/map", flush=True)
        return self

    def _wait_ready(self, run):
        # The map shares the host network. An existing service on the port
        # must not be mistaken for the new experiment's web server.
        http = build_opener(ProxyHandler({}))
        url = f"http://127.0.0.1:{self.port}/api/v1/skyseed/ready"
        while True:
            try:
                with http.open(url, timeout=1) as response:
                    if response.read(128).decode() != run:
                        raise RuntimeError(f"Another service is using map port {self.port}")
                    return
            except HTTPError as error:
                raise RuntimeError(f"Map port {self.port} returned HTTP {error.code}") from error
            except (URLError, ConnectionError, TimeoutError):
                running = subprocess.check_output(
                    ["docker", "inspect", "-f", "{{.State.Running}}", self.container], text=True)
                if running.strip() != "true":
                    logs = subprocess.check_output(["docker", "logs", self.container],
                                                   stderr=subprocess.STDOUT, text=True)
                    raise RuntimeError(f"InternetMap exited before opening port {self.port}:\n{logs}")
                time.sleep(.1)

    def close(self):
        if self.container:
            subprocess.run(["docker", "rm", "-f", self.container], check=True, stdout=subprocess.DEVNULL)
            self.container = None

    def __exit__(self, *exc):
        self.close()
