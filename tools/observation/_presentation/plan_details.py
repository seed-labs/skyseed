"""Save real plan branches and render their restored values beside the inspectors."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from itertools import zip_longest
import json
import os
from pathlib import Path
from time import perf_counter

from skyseed import save_plans, load_plans
from skyseed.pc.link import LinkPlan
from tools.observation._presentation.inspection_page import write_page


def _field_values(value):
    """Make a JSON field view; the plan archive retains the original value types."""
    if is_dataclass(value):
        return {item.name: _field_values(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _field_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_field_values(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rule_text(rule):
    match = f"label {rule.incoming_label}" if rule.incoming_label is not None else rule.destination.identity
    text = f"{match} → {rule.action}"
    if rule.outgoing_label is not None:
        text += f" {rule.outgoing_label}"
    if rule.exit is not None:
        text += f" → {rule.exit.peer}"
    return text


def _connection_text(configuration):
    values = []
    for source in configuration.candidate.endpoints:
        properties = configuration.properties_from(source)
        if properties is not None:
            values.append(f"{source}: {properties.bandwidth_bps:g} bit/s, "
                          f"{properties.delay_s:g} s, loss {properties.packet_loss_ppm:g} ppm")
    return "; ".join(values)


def plan_summary(plan):
    is_link = isinstance(plan, LinkPlan)
    initial = []

    def connection_record(identity, configuration):
        return {"target": " ↔ ".join(identity), "nodes": list(identity), "link_id": list(identity),
                "detail": "" if configuration is None else _connection_text(configuration),
                "fields": "" if configuration is None else json.dumps(_field_values(configuration), indent=2, ensure_ascii=False)}

    def rule_record(node, rule):
        return {"target": node, "nodes": [node],
                "destination": [rule.destination.kind, rule.destination.identity],
                "detail": _rule_text(rule), "fields": json.dumps(_field_values(rule), indent=2, ensure_ascii=False)}

    if is_link:
        for identity, configuration in plan.initial_links.items():
            initial.append(connection_record(identity, configuration))
    else:
        for node, rules in plan.initial_rules.items():
            for rule in rules:
                initial.append(rule_record(node, rule))

    def operation_summary(operation):
        if is_link:
            record = connection_record(operation.link_id, operation.configuration)
        else:
            record = rule_record(operation.node_id, operation.rule)
        return {**record, "timestamp": operation.timestamp.isoformat(), "kind": operation.kind}

    return {"start": plan.start_time.isoformat(), "end": plan.end_time.isoformat(),
            "initial": initial, "operations": [operation_summary(op) for op in plan.operations],
            "context": [] if is_link else [operation_summary(op) for op in plan.context_operations]}


def archive_summary(path, expected, *, write_seconds):
    """Compare restored values with the caller's actual output, one plan at a time."""
    begun = perf_counter()
    restored = []
    equivalent = True
    missing = object()
    for original, loaded in zip_longest(expected, load_plans(path), fillvalue=missing):
        if original is missing or loaded is missing or original != loaded:
            equivalent = False
        if loaded is not missing:
            restored.append(plan_summary(loaded))
    return {"name": path.name, "href": path.name, "bytes": path.stat().st_size,
            "write_seconds": write_seconds, "read_seconds": perf_counter() - begun,
            "equivalent": equivalent, "plans": restored}


def write_plan_details(output, *, kind, streams, files=None):
    """Attach a plans/ subpage to one inspector, keeping each original batch intact.

    streams maps branch names to finite plan sequences. Geometry and model
    observations remain in the parent inspector. With files, read existing archives
    instead of making visualization own their persistence.
    """
    directory = Path(output) / "plans"
    archives = []
    for branch, values in streams.items():
        plans = tuple(values)
        path = directory / f"{branch}_{kind}.plan" if files is None else Path(files[branch])
        begun = perf_counter()
        if files is None:
            save_plans(path, iter(plans))
        archive = archive_summary(path, plans, write_seconds=perf_counter() - begun)
        archive["href"] = os.path.relpath(path, directory)
        archive["existing"] = files is not None
        archives.append({"branch": branch, **archive})
    data = {"format": "skyseed.plan-details/v1", "kind": kind, "archives": archives}
    write_page(data, directory, template=Path(__file__).with_name("plan_details.html"), data_name="plans")
    return data
