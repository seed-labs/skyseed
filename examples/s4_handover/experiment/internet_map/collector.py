"""Read namespace interfaces and adopted execution records for InternetMap."""
from bisect import bisect_right
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

from skyseed.runner.instruction_executor import _setns
from skyseed.runner.netlink import RouteSocket, _ifinfo, _inventory_attributes


def interfaces(channel):
    result = {}
    for _, body in channel.request(18, _ifinfo(), flags=0x300):
        attrs = _inventory_attributes(body, 16)
        index, flags = struct.unpack_from("=iI", body, 4)
        name = attrs[3].rstrip(b"\0").decode()
        result[index] = {
            "name": name, "index": index,
            "peer_index": struct.unpack("=I", attrs.get(5, bytes(4)))[0],
            "mac": attrs.get(1, b"").hex(":"), "up": bool(flags & 1), "addresses": [],
        }
    for _, body in channel.request(22, struct.pack("=BBBBI", socket.AF_UNSPEC, 0, 0, 0, 0), flags=0x300):
        family, prefix, _, _, index = struct.unpack_from("=BBBBI", body)
        attrs = _inventory_attributes(body, 8)
        address = attrs.get(2, attrs.get(1))
        if index in result and address is not None and family in (socket.AF_INET, socket.AF_INET6):
            result[index]["addresses"].append(f"{socket.inet_ntop(family, address)}/{prefix}")
    return list(result.values())


class Receipts:
    def __init__(self, config):
        self.config = config
        self.positions = {}
        self.zones = {node: set() for node in config["nodes"]}
        self.parameters = {}
        self.origin = None
        self.phase = "preparing"

    def key(self, scheduled):
        timestamp = datetime.fromisoformat(scheduled)
        start = datetime.fromisoformat(self.config["start"])
        period = self.config["period"]
        cycle = int((timestamp - start).total_seconds() // period)
        if cycle > 1:
            timestamp -= timedelta(seconds=(cycle - 1) * period)
        return timestamp.isoformat()

    def position(self, identity, scheduled, count):
        previous, position = self.positions.get(identity, (None, 0))
        if previous != scheduled:
            position = 0
        self.positions[identity] = scheduled, position + count
        return position

    def apply(self, event):
        if event["kind"] == "clock":
            self.origin = event["origin"]
            self.phase = "running"
        elif event["kind"] in ("stopped", "drained"):
            self.phase = event["kind"]
        elif event["kind"] == "route":
            for adoption in event["adoptions"]:
                scheduled = adoption["scheduled"]
                for node in adoption["nodes"]:
                    count = adoption["instruction_counts"][node]
                    position = self.position(node, scheduled, count)
                    entry = self.config["routes"][self.key(scheduled) + "|" + node]
                    if position + count > entry["count"]:
                        raise ValueError(f"map receipt exceeds instruction count: {node} at {scheduled}")
                    for offset, zone, enabled in entry["changes"]:
                        if position <= offset < position + count:
                            if enabled:
                                self.zones[node].add(zone)
                            else:
                                self.zones[node].discard(zone)
        elif event["kind"] == "link":
            identity = json.dumps(event["target"], separators=(",", ":"))
            scheduled = event["scheduled"]
            position = self.position(identity, scheduled, 1)
            key = self.key(scheduled) + "|" + identity
            for value in self.config["updates"][key][position]:
                self.parameters[value["interface"]] = value


def connected_links(config, nodes, service, parameters):
    """Match known endpoints to reciprocal live veth indices."""
    lookup = {(identity, interface["name"]): interface
              for identity, node in nodes.items() for interface in node["interfaces"]}
    candidates = list(config["links"].values())
    if service is not None:
        access = config["attachment"]
        candidates.append({"link_id": [access["node"], service], "endpoints": [
            {"node_id": access["node"], "interface": access["interface"]},
            {"node_id": service, "interface": access["peer_interface"]},
        ]})
    links = []
    for candidate in candidates:
        left, right = candidate["endpoints"]
        first = lookup.get((left["node_id"], left["interface"]))
        second = lookup.get((right["node_id"], right["interface"]))
        if first is None or second is None:
            continue
        if first["peer_index"] != second["index"] or second["peer_index"] != first["index"]:
            continue
        ends = []
        for endpoint, facts, peer in ((left, first, right), (right, second, left)):
            facts["peer"] = peer["node_id"]
            facts["peer_interface"] = peer["interface"]
            ends.append({"node": endpoint["node_id"], **facts,
                         "parameters": parameters.get(facts["name"], {})})
        links.append({"id": json.dumps(candidate["link_id"], separators=(",", ":")), "ends": ends})
    return links


def current_zones(config, nodes):
    """A service receipt must still have its endpoint address on a live satellite."""
    zones = {identity: {**zone, "serving": []} for identity, zone in config.items()}
    for identity, node in nodes.items():
        if not node["running"]:
            continue
        addresses = {address for interface in node["interfaces"] if interface["name"] == "ssidentity"
                     for address in interface["addresses"]}
        for zone in node["zones"]:
            if zone in zones and zones[zone]["prefix"] in addresses:
                zones[zone]["serving"].append(identity)
    return zones


class Collector:
    def __init__(self, config):
        self.config = config
        self.output = Path(config["run_output"])
        self.receipts = Receipts(config)
        self.stream = (self.output / "execution.jsonl").open()
        positions = json.loads((Path(config["output"]) / "observations/map-positions.json").read_text())
        self.positions = sorted((datetime.fromisoformat(stamp), nodes)
                                for stamp, nodes in positions.items())
        self.times = [stamp for stamp, _ in self.positions]
        self.channels = {}
        original = os.open("/proc/self/ns/net", os.O_RDONLY)
        try:
            for identity, node in config["nodes"].items():
                with open(f"/proc/{node['pid']}/ns/net") as namespace:
                    _setns(namespace.fileno())
                    self.channels[identity] = os.fstat(namespace.fileno()).st_ino, RouteSocket()
        finally:
            _setns(original)
            os.close(original)

    def snapshot(self):
        while True:
            position = self.stream.tell()
            line = self.stream.readline()
            if not line.endswith("\n"):
                self.stream.seek(position)
                break
            self.receipts.apply(json.loads(line))
        elapsed = 0 if self.receipts.origin is None else max(0, time.monotonic() - self.receipts.origin)
        seconds = elapsed % self.config["period"]
        timestamp = datetime.fromisoformat(self.config["start"]) + timedelta(seconds=seconds)
        sample = bisect_right(self.times, timestamp) - 1
        stamp, positions = self.positions[sample] if sample >= 0 else (None, {})
        nodes = {}
        for identity, config in self.config["nodes"].items():
            inode, channel = self.channels[identity]
            node = {**config, "interfaces": [], "zones": sorted(self.receipts.zones[identity]),
                    "position_m": positions.get(identity), "position_time": None if stamp is None else stamp.isoformat()}
            try:
                node["running"] = os.stat(f"/proc/{config['pid']}/ns/net").st_ino == inode
                if node["running"]:
                    node["interfaces"] = interfaces(channel)
            except FileNotFoundError:
                node["running"] = False
            nodes[identity] = node
        attachment = Path(self.config["attachment"]["state_file"])
        service = json.loads(attachment.read_text())["node"] if attachment.exists() else None
        links = connected_links(self.config, nodes, service, self.receipts.parameters)
        if not any(any(end["node"] == self.config["attachment"]["node"] for end in link["ends"]) for link in links):
            service = None
        return {"layout": self.config["layout"], "nodes": nodes, "zones": current_zones(self.config["zones"], nodes),
                "links": links, "service": service, "elapsed": elapsed,
                "seconds": seconds, "period": self.config["period"],
                "phase": self.receipts.phase, "updated": time.time()}

    def close(self):
        self.stream.close()
        for _, channel in self.channels.values():
            channel.close()


if __name__ == "__main__":
    collector = Collector(json.loads(Path(sys.argv[1]).read_text()))
    try:
        while True:
            temporary = collector.output / "map-state.tmp"
            temporary.write_text(json.dumps(collector.snapshot()))
            temporary.replace(collector.output / "map-state.json")
            time.sleep(2)
    finally:
        collector.close()
