"""Prepare display inputs and own the original InternetMap container."""
import json
from pathlib import Path
import subprocess
import sys

from .layout import display_nodes
from .temporary_ut import display_terminal


IMAGE = "skyseed-s4-internet-map"


def receipt_index(plans):
    """Index only display changes, retaining each instruction's receipt position."""
    routes, links, targets, prefixes, seen = {}, {}, {}, {}, set()
    serving = set()
    for plan in plans:
        targets.update((item["node_id"], item) for item in plan.targets["nodes"])
        prefixes.update((item["identity"], item["prefix"]) for item in plan.targets["destinations"]
                        if item["kind"] == "endpoint")
        operations = plan.operations
        if plan.stream not in seen:
            operations = (*plan.initial_operations, *plan.context_operations, *operations)
            seen.add(plan.stream)
        for operation in operations:
            stamp = operation.timestamp.isoformat()
            if plan.stream == "route":
                key = stamp + "|" + operation.target
                entry = routes.setdefault(key, {"count": 0, "changes": []})
                for instruction in operation.instructions:
                    value = instruction.parameters
                    if value["destination"][0] == "endpoint" and value["match"][0] == "label":
                        zone = value["destination"][1]
                        identity = operation.target, zone
                        enabled = value["kind"] != "withdraw" and value["action"] == "pop_deliver"
                        if enabled != (identity in serving):
                            entry["changes"].append([entry["count"], zone, enabled])
                        if enabled:
                            serving.add(identity)
                        else:
                            serving.discard(identity)
                    entry["count"] += 1
            else:
                key = stamp + "|" + json.dumps(operation.target, separators=(",", ":"))
                links.setdefault(key, []).append([
                    dict(item.parameters) for item in operation.instructions if item.opcode == "netem.replace"
                ])
    return routes, links, targets, prefixes


class InternetMap:
    @staticmethod
    def check_ready(output):
        """Catch a missed map build before deploying the experiment's containers."""
        if not (Path(output) / "observations/map-positions.json").is_file():
            raise FileNotFoundError("Map positions are missing. Run python map.py in the example directory first.")
        result = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"Cannot use map image {IMAGE}: {result.stderr.strip()}\n"
                               "Check Docker access and run python map.py in the example directory.")

    def __init__(self, scene, deployment, repetition, output, run_output):
        self.scene, self.deployment, self.repetition = scene, deployment, repetition
        self.output, self.run_output = Path(output).resolve(), Path(run_output).resolve()
        self.container = None

    def __enter__(self):
        nodes, layout = display_nodes(self.scene)
        routes, updates, targets, prefixes = receipt_index(self.repetition.iterate(cycles=2))
        associations = json.loads((self.output / "build.json").read_text())["routing"]["associations"]
        zones = {zone.endpoint_id: {
            "prefix": prefixes.get(zone.endpoint_id, ""),
            "location": f"{zone.data['latitude_deg']:.4f}°, {zone.data['longitude_deg']:.4f}°",
            "ground_stations": associations[zone.endpoint_id],
        } for zone in self.scene.get_endpoints("geographic-zone/v1")}
        # Plans contain only referenced nodes; the map includes every deployed Scene node.
        containers = {}
        for container in self.deployment.containers().values():
            identity = container["Config"]["Labels"].get("org.seedsecuritylabs.seedemu.meta.skyseed.scene_id")
            if identity is not None:
                containers[identity] = container
        for identity, node in nodes.items():
            container = containers[identity]
            node.update(container=container["Id"], container_name=container["Name"].lstrip("/"),
                        pid=container["State"]["Pid"])
            node["identity_address"] = targets[identity]["identity_address"] if identity in targets else ""
        nodes["ut"], attachment = display_terminal(self.scene, self.run_output, layout["ut"])
        links = {}
        for plan in self.repetition.plans:
            for link in plan.targets["links"]:
                links[json.dumps(link["link_id"], separators=(",", ":"))] = dict(link)
        config = {"layout": layout, "attachment": attachment, "nodes": nodes, "zones": zones, "links": links, "routes": routes, "updates": updates,
                  "start": self.repetition.start.isoformat(), "period": self.repetition.seconds,
                  "output": str(self.output), "run_output": str(self.run_output)}
        (self.run_output / "map-config.json").write_text(json.dumps(config))
        try:
            self.container = subprocess.check_output([
                "docker", "create", "--init", "--privileged", "--pid", "host",
                "--label", "skyseed.example=s4-handover", "--name", nodes["ut"]["container_name"] + "-map",
                "-p", "127.0.0.1:8080:8080",
                "--mount", "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
                "--mount", "type=bind,source=/,target=/host,readonly",
                "--mount", f"type=bind,source={self.run_output},target=/host{self.run_output}",
                "--mount", f"type=bind,source={self.run_output},target=/s4,readonly",
                "-e", f"SKYSEED_PYTHON={sys.executable}",
                "-e", f"SKYSEED_COLLECTOR={Path(__file__).with_name('collector.py')}",
                "-e", f"SKYSEED_CONFIG={self.run_output / 'map-config.json'}", IMAGE,
            ], text=True).strip()
            subprocess.run(["docker", "start", self.container], check=True, capture_output=True)
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(f"InternetMap cleanup also failed: {cleanup}")
            raise
        print("InternetMap2: http://localhost:8080/pro/map", flush=True)
        return self

    def close(self):
        if self.container:
            subprocess.run(["docker", "stop", self.container], check=True, capture_output=True)
            subprocess.run(["docker", "rm", self.container], check=True, capture_output=True)
            self.container = None

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"InternetMap cleanup also failed: {cleanup}")
