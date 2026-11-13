"""Save the fixed S4 experiment settings beside its plans."""
import json
from .perturbation import outage_description


def save_configuration(output, pc, duration, route_evaluations):
    scene = pc.scene
    network = pc.get_layer("network-layer")
    link = pc.get_layer("link-layer")
    associations = network.routing_model.ground_station_associations(scene)
    grounds = associations[scene.data["experiment"]["zone"]]
    experiment = {**scene.data["experiment"], "ground_stations": grounds,
                  "ground_station": grounds[0]}
    schedule = []
    previous = object()
    start = route_evaluations[0].timestamp
    for evaluation in route_evaluations:
        node = evaluation.assignments[experiment["zone"]]
        if node != previous:
            schedule.append({"seconds": (evaluation.timestamp - start).total_seconds(), "node": node})
            previous = node
    summary = {"simulated_seconds": duration,
               "satellites": len(scene.get_satellites()), "ground_stations": len(scene.get_ground_stations()),
               "zones": len(scene.endpoints), "experiment": experiment,
               "handover_schedule": schedule,
               "routing": {"model": "TerminalRoutingModel",
                           "ground_stations_per_zone": network.routing_model.ground_stations_per_zone,
                           "associations": dict(route_evaluations[0].associations),
                           "service_min_elevation_deg": network.service_assignment_model.min_elevation_deg,
                           "ground_min_elevation_deg": pc.get_layer("physical-layer").candidate_model.min_ground_elevation_deg},
               "perturbation": outage_description(link.perturbation_model)}
    (output / "build.json").write_text(json.dumps(summary, indent=2) + "\n")

    lines = ["# Planned UT service changes / UT 计划服务星变化", "",
             "Times repeat each cycle. Actual attachment follows adoption receipts, not this table.",
             "每轮重复这些计划时刻；实际接入跟随已采用回执，可能稍晚。初次接入可能等待建链。", "",
             "| Cycle time / 轮内时刻 | Serving node / 服务卫星 |",
             "|---|---|"]
    for event in schedule:
        seconds = event["seconds"]
        lines.append(f"| {int(seconds // 60):02d}:{seconds % 60:04.1f} ({seconds:g}s) | `{event['node']}` |")
    lines += ["", "180–240s: configured GS outage / 配置的 GS 干扰窗口。",
              "600s: jump to the start topology / 回到起点拓扑，容器与 UT IP 保留。", "",
              "GS preference / GS 顺序: " + " → ".join(scene.get_node(node).name for node in grounds), ""]
    (output / "observations/handover-times.md").write_text("\n".join(lines))
