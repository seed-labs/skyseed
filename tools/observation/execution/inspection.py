"""Reconstruct adopted BPF instructions from saved plans and execution receipts."""
from collections import defaultdict, deque


def applied_events(plans, records):
    """Resolve successful operations once. Missing matches are an evidence error.

    Future plans only describe operands. Only acknowledgements activate them.
    An operation's completion is the finest link resolution in the journal;
    intermediate netlink calls and per-packet traversal were not captured.
    """
    clock = next(record for record in records if record["kind"] == "clock")
    origin = clock["origin"]
    links, routes = defaultdict(deque), defaultdict(deque)
    route_batches = defaultdict(int)
    nodes, targets, destinations = {}, {}, {}
    seen = set()
    for plan in plans:
        for target in plan.targets["nodes"]:
            nodes[target["node_id"]] = dict(target)
        for target in plan.targets["links"]:
            targets[tuple(target["link_id"])] = dict(target)
        for destination in plan.targets["destinations"]:
            destinations[(destination["kind"], destination["identity"])] = dict(destination)
        operations = list(plan.operations)
        if plan.stream not in seen:
            operations = [*plan.initial_operations, *plan.context_operations, *operations]
            seen.add(plan.stream)
        batch_keys = set()
        for operation in sorted(operations, key=lambda value: value.timestamp):
            instructions = []
            for instruction in operation.instructions:
                parameters = dict(instruction.parameters)
                # The viewer displays semantic fields, not duplicate ABI bytes.
                for name in ("data", "key_hex_le", "value_hex_le", "table_abi"):
                    parameters.pop(name, None)
                instructions.append({"opcode": instruction.opcode, "node": instruction.node_id,
                                     "parameters": parameters})
            timestamp = operation.timestamp.isoformat()
            if plan.stream == "link":
                links[timestamp, tuple(operation.target), operation.kind].append(instructions)
            else:
                key = timestamp, operation.target
                routes[key].extend(instructions)
                batch_keys.add(key)
        for key in batch_keys:
            route_batches[key] += 1

    events = []
    for record in records:
        if record["kind"] not in ("link", "route"):
            continue
        if record["kind"] == "link":
            key = record["scheduled"], tuple(record["target"]), record["operation"]
            instructions = links[key].popleft()
        elif "adoptions" in record:
            instructions = []
            for adoption in record["adoptions"]:
                for node in adoption["nodes"]:
                    key = adoption["scheduled"], node
                    pending = routes[key]
                    if not pending:
                        raise KeyError(f"no frozen instructions for adoption {key}")
                    if "instruction_counts" in adoption:
                        count = adoption["instruction_counts"][node]
                    else:
                        if route_batches[key] != 1:
                            raise ValueError(f"old journal has ambiguous same-time adoption {key}")
                        count = len(pending)
                    for _ in range(count):
                        instructions.append(pending.popleft())
        else:
            # This reader handles versioned BPF publication receipts.
            raise ValueError("runtime replay requires BPF generation records")
        events.append({"time": record["activated"] - origin, "kind": record["kind"],
                       "record": record, "instructions": instructions})
    events.sort(key=lambda event: event["time"])
    return {"clock": clock, "events": events, "nodes": list(nodes.values()),
            "links": list(targets.values()), "destinations": list(destinations.values())}
