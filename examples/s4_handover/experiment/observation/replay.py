"""S4-specific geometry, UT and outage overlays on adopted instruction records."""
from datetime import datetime
from math import floor
from ..repetition import RepeatedPlans
import json
from pathlib import Path

from skyseed import load_plans
from tools.observation.execution.inspection import applied_events
from tools.observation._presentation.inspection_page import write_page

from .results import read_records


def write_replay(scene, plans, run_output, traffic, *, store_records=None):
    if store_records is None:
        records = read_records(run_output / "execution.jsonl")
        source = "local execution.jsonl · historical run without etcd"
        attachments = read_records(run_output / "attachment.jsonl")
    else:
        records = [value for key, value in sorted(store_records.items()) if key.startswith("runner/events/")]
        attachments = [value for key, value in sorted(store_records.items()) if key.startswith("user/attachment/")]
        source = "etcd export · Runner live journal + post-run measurement archive"
    source_plans = load_plans(plans)
    repeated = traffic["build"].get("repeat", False)
    if repeated:
        repetition = RepeatedPlans(source_plans)
        cycles = floor(traffic["summary"]["duration_s"] / repetition.seconds) + 1
        source_plans = repetition.iterate(cycles=cycles)
    data = applied_events(source_plans, records)
    # Stations without executable operations still belong to the scene. Show
    # their locations without inventing addresses, bindings or active rules.
    compiled_nodes = {node["node_id"] for node in data["nodes"]}
    for node in scene.nodes:
        if node.node_id not in compiled_nodes:
            data["nodes"].append({"node_id": node.node_id, "kind": node.kind})
    grounds = {node.node_id: node for node in scene.get_ground_stations()}
    for node in data["nodes"]:
        if node["node_id"] in grounds:
            station = grounds[node["node_id"]]
            node["ground_location"] = [station.latitude_deg, station.longitude_deg, station.elevation_m]
    epoch = datetime.fromisoformat(data["clock"]["start_time"])
    duration = traffic["summary"]["duration_s"]
    observations = json.loads((run_output / "inputs/observations.json").read_text())
    geometry = []
    for timestamp, positions in sorted(observations["positions"].items()):
        seconds = (datetime.fromisoformat(timestamp) - epoch).total_seconds()
        if 0 <= seconds <= duration + 5:
            geometry.append({"time": seconds, "positions": positions})
    experiment = traffic["build"]["experiment"]
    zone = scene.get_endpoint(experiment["zone"])
    data.update(format="skyseed.s4-runtime-replay/v1", source=source, duration=duration,
                geometry=geometry, repeat_period=traffic["build"]["simulated_seconds"] if repeated else None, experiment=experiment, zone=dict(zone.data), traffic=traffic,
                zones=[{"id": item.endpoint_id, **dict(item.data)}
                       for item in scene.get_endpoints("geographic-zone/v1")],
                routing=traffic["build"]["routing"],
                perturbation=traffic["build"].get("perturbation"),
                attachments=[{**event, "time": event["monotonic"] - data["clock"]["origin"]}
                             for event in attachments if event["kind"] == "attachment"],
                names={node.node_id: node.name for node in scene.nodes})
    return write_page(data, run_output / "views/replay", template=Path(__file__).with_name("replay.html"),
                      data_name="replay")
