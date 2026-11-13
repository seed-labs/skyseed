"""Export adopted routing and delivery plans; never recompute routing for display."""
from datetime import datetime
from pathlib import Path

from skyseed.pc.link import LinkPlan
from skyseed.pc.network import RoutePlan
from skyseed.pc.network.forwarding_rules import indexed_rules
from tools.observation._presentation.inspection_page import write_page
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


def inspection_data(scene, base_plans, delivery_plans, link_plans, *, positions, evaluations=(), timestamps=()):
    base = _joined(base_plans)
    delivery = _joined(delivery_plans)
    links = _joined(link_plans, links=True)
    if not (base.start_time == delivery.start_time == links.start_time):
        raise ValueError("inspection histories must share their start")
    end = max(base.end_time, delivery.end_time)
    evaluations = sorted(evaluations, key=lambda value: value["timestamp"])
    times = {base.start_time}
    for plan in (base, delivery, links):
        times.update(operation.timestamp for operation in plan.operations if operation.timestamp < end)
    times.update(datetime.fromisoformat(value["timestamp"]) for value in evaluations
                 if base.start_time <= datetime.fromisoformat(value["timestamp"]) < end)
    times.update(timestamps)
    if base.end_time < end:
        times.add(base.end_time)
    times = sorted(times)
    if times[0] < base.start_time or times[-1] >= end:
        raise ValueError("display samples must lie inside plan coverage")
    frames = []
    destinations = {("endpoint", endpoint.endpoint_id) for endpoint in scene.endpoints}
    cursor = 0
    latest = None
    for timestamp, base_state, delivery_state, link_state in zip(
        times, _states(base, times), _states(delivery, times), _states(links, times, links=True), strict=True
    ):
        while cursor < len(evaluations) and datetime.fromisoformat(evaluations[cursor]["timestamp"]) <= timestamp:
            latest = evaluations[cursor]
            cursor += 1
        branches = {}
        for name, rules in (("base", base_state), ("delivery", delivery_state)):
            branches[name] = {node: [_rule(rule) for rule in values] for node, values in rules.items()}
            destinations.update((rule.destination.kind, rule.destination.identity)
                                for values in rules.values() for rule in values)
        frames.append({"timestamp": timestamp.isoformat(), **branches,
                       "positions": positions[timestamp.isoformat()],
                       "links": [[source, report.peer_id] for link in link_state.values()
                                 for source, report in link.candidate.reports.items()],
                       "base_held": timestamp >= base.end_time, "links_held": timestamp >= links.end_time,
                       "evaluation_timestamp": None if latest is None else latest["timestamp"],
                       "assignments": None if latest is None else latest["assignments"],
                       "associations": None if latest is None else latest["associations"]})
    events = {}
    for name, plan in (("base", base), ("delivery", delivery)):
        events[name] = [{"timestamp": operation.timestamp.isoformat(), "node": operation.node_id,
                         "kind": operation.kind, "rule": _rule(operation.rule)} for operation in plan.operations]
    return {**scene_inputs(scene), "format": "skyseed.network-inspection/v1",
            "start": base.start_time.isoformat(), "end": end.isoformat(),
            "base_end": base.end_time.isoformat(), "frames": frames, "events": events,
            "destinations": sorted(destinations),
            "endpoints": [{"id": endpoint.endpoint_id, "kind": endpoint.kind, "data": endpoint.data}
                          for endpoint in scene.endpoints]}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name("viewer.html"), data_name="network")
