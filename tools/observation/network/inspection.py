"""Export adopted routing and delivery plans; never recompute routing for display."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

from skyseed.pc.physical import PhysicalLayer
from skyseed.pc.link import LinkLayer, LinkPlan
from skyseed.pc.network import NetworkLayer, RoutePlan, GroundStationZoneRoutingModel, FixedDelayDeliverySchedulingModel
from skyseed.pc.network.forwarding_rules import indexed_rules
from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.plan_details import write_plan_details
from tools.observation._presentation.scene_display import scene_inputs


def _rule(rule):
    return {"destination": {"kind": rule.destination.kind, "identity": rule.destination.identity},
            "key": rule.key, "action": rule.action,
            "incoming_label": rule.incoming_label, "outgoing_label": rule.outgoing_label,
            "next_hop": None if rule.exit is None else rule.exit.peer,
            "link_id": None if rule.exit is None else rule.exit.link_id, "data": dict(rule.data)}


def _joined(plans, *, links=False):
    plans = tuple(plans)
    if not plans:
        raise ValueError("inspection needs nonempty plan histories")
    if not links:
        projected = []
        for index, plan in enumerate(plans):
            end = plan.end_time
            if index + 1 < len(plans):
                end = min(end, plans[index + 1].start_time)
            selected = plan.slice(plan.start_time, end)
            projected.append(RoutePlan(selected.start_time, selected.end_time, selected.initial_rules,
                                       tuple(selected.iter_state_operations())))
        plans = tuple(projected)
    initial_name, end_name = ("initial_links", "end_links") if links else ("initial_rules", "end_rules")
    for previous, current in zip(plans, plans[1:]):
        previous_state = getattr(previous, end_name)
        current_state = getattr(current, initial_name)
        if not links:
            previous_state = indexed_rules(previous_state)
            current_state = indexed_rules(current_state)
        if previous.end_time != current.start_time or previous_state != current_state:
            raise ValueError("inspection histories must continue without gaps or state resets")
    plan_type = LinkPlan if links else RoutePlan
    return plan_type(plans[0].start_time, plans[-1].end_time, getattr(plans[0], initial_name),
                     tuple(operation for plan in plans for operation in plan.operations))


def _states(plan, timestamps, *, links=False):
    inside = [timestamp for timestamp in timestamps if timestamp < plan.end_time]
    yield from plan.states_at(inside)
    final = plan.end_links if links else plan.end_rules
    for _ in range(len(timestamps) - len(inside)):
        yield final


def inspection_data(physical, base_plans, delivery_plans, link_plans, *, evaluations=(), timestamps=()):
    base = _joined(base_plans)
    delivery = _joined(delivery_plans)
    links = _joined(link_plans, links=True)
    if not (base.start_time == delivery.start_time == links.start_time):
        raise ValueError("inspection histories must share their start")
    end = max(base.end_time, delivery.end_time)
    evaluations = sorted(evaluations, key=lambda value: value.timestamp)
    times = {base.start_time}
    for plan in (base, delivery, links):
        times.update(operation.timestamp for operation in plan.operations if operation.timestamp < end)
    times.update(value.timestamp for value in evaluations if value.timestamp < end)
    times.update(timestamps)
    if base.end_time < end:
        times.add(base.end_time)
    times = sorted(times)
    if times[0] < base.start_time or times[-1] >= end:
        raise ValueError("display samples must lie inside plan coverage")
    frames = []
    destinations = {("endpoint", endpoint.endpoint_id) for endpoint in physical.scene.endpoints}
    cursor = 0
    latest = None
    for timestamp, base_state, delivery_state, link_state in zip(
        times, _states(base, times), _states(delivery, times), _states(links, times, links=True), strict=True
    ):
        while cursor < len(evaluations) and evaluations[cursor].timestamp <= timestamp:
            latest = evaluations[cursor]
            cursor += 1
        branches = {}
        for name, rules in (("base", base_state), ("delivery", delivery_state)):
            branches[name] = {node: [_rule(rule) for rule in values] for node, values in rules.items()}
            destinations.update((rule.destination.kind, rule.destination.identity)
                                for values in rules.values() for rule in values)
        frames.append({"timestamp": timestamp.isoformat(), **branches,
                       "positions": {node: state.position_m for node, state in physical.snapshot_at(timestamp).states.items()},
                       "links": [[source, report.peer_id] for link in link_state.values()
                                 for source, report in link.candidate.reports.items()],
                       "base_held": timestamp >= base.end_time, "links_held": timestamp >= links.end_time,
                       "evaluation_timestamp": None if latest is None else latest.timestamp.isoformat(),
                       "assignments": None if latest is None else dict(latest.assignments),
                       "associations": None if latest is None else dict(latest.associations)})
    events = {}
    for name, plan in (("base", base), ("delivery", delivery)):
        events[name] = [{"timestamp": operation.timestamp.isoformat(), "node": operation.node_id,
                         "kind": operation.kind, "rule": _rule(operation.rule)} for operation in plan.operations]
    return {**scene_inputs(physical.scene), "format": "skyseed.network-inspection/v1",
            "start": base.start_time.isoformat(), "end": end.isoformat(),
            "base_end": base.end_time.isoformat(), "frames": frames, "events": events,
            "destinations": sorted(destinations),
            "endpoints": [{"id": endpoint.endpoint_id, "kind": endpoint.kind, "data": endpoint.data}
                          for endpoint in physical.scene.endpoints]}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name("viewer.html"), data_name="network")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    from tools.observation.scene.inspection import demo_scene
    start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    scene = demo_scene(start)
    for node in scene.get_ground_stations():
        latitude, longitude = node.latitude_deg, node.longitude_deg + 8
        scene.create_zone(f"{node.name}-zone", latitude_deg=latitude, longitude_deg=longitude,
                          bounds={"south_deg": latitude - 5, "north_deg": latitude + 5,
                                  "west_deg": longitude - 6, "east_deg": longitude + 6})
    for identity, latitude, longitude in (("Pacific", 20, 160), ("Atlantic", 0, -30), ("CentralAsia", 40, 70)):
        scene.create_zone(identity, latitude_deg=latitude, longitude_deg=longitude,
                          bounds={"south_deg": latitude - 5, "north_deg": latitude + 5,
                                  "west_deg": longitude - 6, "east_deg": longitude + 6})
    physical = PhysicalLayer(scene)
    network = NetworkLayer(LinkLayer(physical))
    observations = []
    network.set_routing_hook(GroundStationZoneRoutingModel, model_options={"observer": observations.append})
    delayed = {node.node_id: timedelta(milliseconds=200) for index, node in enumerate(scene.get_satellites())
               if index % 2 == 0}
    network.set_delivery_scheduling_hook(FixedDelayDeliverySchedulingModel, model_options={"node_delays": delayed})
    begun = perf_counter()
    network.generate_plans(start, start + timedelta(seconds=args.seconds))
    planned = perf_counter()
    data = inspection_data(physical, network.base_plans, network.delivery_plans,
                           network.link.base_plans, evaluations=observations)
    sampled = perf_counter()
    write_plan_details(args.output, kind="route", streams={
        "base": network.base_plans, "delivery": network.delivery_plans})
    archived = perf_counter()
    data["plan_details"] = "plans/index.html"
    path = write_inspection(data, args.output)
    print(f"{path.resolve()}\n{len(scene.nodes)} nodes; {len(data['frames'])} frames; "
          f"planning {planned-begun:.3f}s; display {sampled-planned:.3f}s; "
          f"plan files and details {archived-sampled:.3f}s; page write {perf_counter()-archived:.3f}s")


if __name__ == "__main__":
    main()
