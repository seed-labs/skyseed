"""Versioned logical and physical plans; no model, Scene or runtime loading."""
import json
import os
from collections.abc import Mapping
from datetime import datetime
from itertools import chain
from pathlib import Path
from tempfile import NamedTemporaryFile

from .physical import LinkCandidate
from .physical.physical_results import utc_time
from .link import ConnectionCandidate, LinkProperties, LinkConfiguration, LinkOperation, LinkPlan
from .network import RouteDestination, LogicalExit, ForwardingRule, RouteOperation, RoutePlan
from .compiler.physical_plan import BackendInstruction, PhysicalOperation, PhysicalPlan


_FORMAT = "skyseed.plans"
_VERSION = 1
_PLAN_TYPES = {LinkPlan: "link", RoutePlan: "route", PhysicalPlan: "physical"}


def _data_record(value):
    """Tag compound values so tuples and mapping keys survive JSON exactly."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("plan data mappings require string keys")
        return {"mapping": {key: _data_record(item) for key, item in value.items()}}
    if isinstance(value, tuple):
        return {"tuple": [_data_record(item) for item in value]}
    if isinstance(value, list):
        return [_data_record(item) for item in value]
    if isinstance(value, datetime):
        return {"datetime": utc_time(value).isoformat()}
    raise TypeError(f"unsupported plan data type: {type(value).__name__}")


def _read_data(value):
    if isinstance(value, list):
        return [_read_data(item) for item in value]
    if isinstance(value, dict):
        if value.keys() == {"mapping"}:
            return {key: _read_data(item) for key, item in value["mapping"].items()}
        if value.keys() == {"tuple"}:
            return tuple(_read_data(item) for item in value["tuple"])
        if value.keys() == {"datetime"}:
            return utc_time(datetime.fromisoformat(value["datetime"]))
        raise ValueError("unknown plan data representation")
    return value


def _properties_record(properties):
    if properties is None:
        return None
    return {"bandwidth_bps": properties.bandwidth_bps, "delay_s": properties.delay_s,
            "packet_loss_ppm": properties.packet_loss_ppm, "data": _data_record(properties.data)}


def _configuration_record(configuration):
    reports = []
    for report in configuration.candidate.reports.values():
        reports.append({"node_id": report.node_id, "peer_id": report.peer_id,
                        "distance_m": report.distance_m, "propagation_delay_s": report.propagation_delay_s,
                        "elevation_deg": report.elevation_deg, "data": _data_record(report.data)})
    return {"endpoints": configuration.id, "reports": reports,
            "properties": _properties_record(configuration.properties),
            "reverse_properties": _properties_record(configuration.reverse_properties)}


def _read_configuration(record):
    endpoints = tuple(record["endpoints"])
    reports = {}
    for item in record["reports"]:
        report = LinkCandidate(item["node_id"], item["peer_id"], item["distance_m"],
                               item["propagation_delay_s"], item["elevation_deg"], _read_data(item["data"]))
        if report.node_id in reports:
            raise ValueError("duplicate physical report in plan file")
        reports[report.node_id] = report

    def properties(item):
        if item is None:
            return None
        return LinkProperties(endpoints, item["bandwidth_bps"], item["delay_s"],
                              item["packet_loss_ppm"], _read_data(item["data"]))

    return LinkConfiguration(ConnectionCandidate(endpoints, reports), properties(record["properties"]),
                             properties(record["reverse_properties"]))


def _rule_record(rule):
    exit_record = None
    if rule.exit is not None:
        exit_record = {"link_id": rule.exit.link_id, "source": rule.exit.source}
    return {"destination": {"kind": rule.destination.kind, "identity": rule.destination.identity},
            "action": rule.action, "exit": exit_record,
            "incoming_label": rule.incoming_label, "outgoing_label": rule.outgoing_label,
            "data": _data_record(rule.data)}


def _read_rule(record):
    destination = RouteDestination(**record["destination"])
    logical_exit = None if record["exit"] is None else LogicalExit(**record["exit"])
    return ForwardingRule(destination, record["action"], logical_exit,
                          record["incoming_label"], record["outgoing_label"], _read_data(record["data"]))


def _route_operation_record(operation):
    return {"timestamp": operation.timestamp.isoformat(), "node_id": operation.node_id,
            "kind": operation.kind, "rule": _rule_record(operation.rule)}


def _read_route_operation(record):
    return RouteOperation(datetime.fromisoformat(record["timestamp"]), record["node_id"],
                          record["kind"], _read_rule(record["rule"]))


def _plan_record(plan):
    record = {"start_time": plan.start_time.isoformat(), "end_time": plan.end_time.isoformat()}
    if isinstance(plan, PhysicalPlan):
        record.update({"stream": plan.stream, "forwarding_backend": plan.forwarding_backend,
                       "deployment_id": plan.deployment_id, "backend_abi": plan.backend_abi,
                       "targets": _data_record(plan.targets)})
        for name in ("initial_operations", "operations", "context_operations"):
            record[name] = [_physical_operation_record(operation) for operation in getattr(plan, name)]
    elif isinstance(plan, LinkPlan):
        record["initial_links"] = [_configuration_record(value) for value in plan.initial_links.values()]
        operations = []
        for operation in plan.operations:
            configuration = None
            if operation.configuration is not None:
                configuration = _configuration_record(operation.configuration)
            operations.append({"timestamp": operation.timestamp.isoformat(), "kind": operation.kind,
                               "link_id": operation.link_id, "configuration": configuration})
        record["operations"] = operations
    else:
        record["initial_rules"] = {node: [_rule_record(rule) for rule in rules]
                                   for node, rules in plan.initial_rules.items()}
        record["operations"] = [_route_operation_record(operation) for operation in plan.operations]
        record["context_operations"] = [_route_operation_record(operation) for operation in plan.context_operations]
    return record


def _physical_operation_record(operation):
    return {"timestamp": operation.timestamp.isoformat(), "kind": operation.kind,
            "target": _data_record(operation.target),
            "instructions": [{"opcode": instruction.opcode, "node_id": instruction.node_id,
                              "parameters": _data_record(instruction.parameters),
                              "requires": instruction.requires}
                             for instruction in operation.instructions]}


def _read_physical_operation(record):
    instructions = tuple(BackendInstruction(item["opcode"], item["node_id"],
                                            _read_data(item["parameters"]), tuple(item["requires"]))
                         for item in record["instructions"])
    return PhysicalOperation(datetime.fromisoformat(record["timestamp"]), record["kind"],
                             _read_data(record["target"]), instructions)


def _read_plan(record, kind):
    start = datetime.fromisoformat(record["start_time"])
    end = datetime.fromisoformat(record["end_time"])
    if kind == "physical":
        sequences = [tuple(_read_physical_operation(item) for item in record[name])
                     for name in ("initial_operations", "operations", "context_operations")]
        return PhysicalPlan(start, end, record["stream"], record["forwarding_backend"],
                            record["deployment_id"], _read_data(record["targets"]), *sequences,
                            backend_abi=record["backend_abi"])
    if kind == "link":
        initial = {}
        for item in record["initial_links"]:
            configuration = _read_configuration(item)
            if configuration.id in initial:
                raise ValueError("duplicate initial connection in plan file")
            initial[configuration.id] = configuration
        operations = []
        for item in record["operations"]:
            configuration = None if item["configuration"] is None else _read_configuration(item["configuration"])
            operations.append(LinkOperation(datetime.fromisoformat(item["timestamp"]), item["kind"],
                                            tuple(item["link_id"]), configuration))
        return LinkPlan(start, end, initial, tuple(operations))
    initial = {node: tuple(_read_rule(rule) for rule in rules)
               for node, rules in record["initial_rules"].items()}
    return RoutePlan(start, end, initial,
                     tuple(_read_route_operation(item) for item in record["operations"]),
                     tuple(_read_route_operation(item) for item in record["context_operations"]))


def save_plans(path, plans):
    """Consume one finite, homogeneous plan sequence and replace path on success.

    Each record carries its initial state and any delivery context. Saving does
    not combine overlapping route batches or emit their context as new updates.
    """
    path = Path(path)
    plans = iter(plans)
    empty = object()
    first = next(plans, empty)
    if first is not empty and type(first) not in _PLAN_TYPES:
        raise TypeError(f"unsupported plan type: {type(first).__name__}")
    plan_type = None if first is empty else _PLAN_TYPES[type(first)]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    count = 0
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)

            def write(record):
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
                stream.write("\n")

            write({"format": _FORMAT, "version": _VERSION, "plan_type": plan_type})
            sequence = () if first is empty else chain((first,), plans)
            for plan in sequence:
                if type(plan) is not type(first):
                    raise TypeError("one plan file must contain only one plan type")
                write(_plan_record(plan))
                count += 1
            # The terminator detects truncation even at a complete JSON-line boundary.
            write({"end": count})
        os.replace(temporary, path)
    except Exception as error:
        error.add_note(f"while saving {path}, after {count} complete plans")
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_plans(path):
    """Yield restored plans in file order, without a Scene or active planner.

    Reading is incremental; fully consume the iterator to check file completion.
    """
    path = Path(path)
    with path.open(encoding="utf-8") as stream:
        line_number = 1
        try:
            header = json.loads(stream.readline())
            if header.get("format") != _FORMAT or header.get("version") != _VERSION:
                raise ValueError("unsupported plan file format/version")
            kind = header["plan_type"]
            if kind not in ("link", "route", "physical", None):
                raise ValueError(f"unsupported plan type: {kind!r}")
            count = 0
            for line_number, line in enumerate(stream, start=2):
                record = json.loads(line)
                if "end" in record:
                    if record != {"end": count}:
                        raise ValueError("plan file completion count does not match")
                    if stream.read(1):
                        raise ValueError("content after plan file completion")
                    return
                if kind is None:
                    raise ValueError("an empty plan file cannot contain plan records")
                plan = _read_plan(record, kind)
                count += 1
                yield plan
            raise ValueError("incomplete plan file: missing completion record")
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError(f"cannot read plan file {path}, line {line_number}: {error}") from error
