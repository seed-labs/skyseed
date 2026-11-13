"""Incrementally accept batches without waiting for a future input watermark."""
from dataclasses import dataclass, field
import heapq
from itertools import count

from ..pc.compiler.physical_plan import PhysicalPlan


@dataclass
class ScheduledGroup:
    timestamp: object
    links: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    targets: dict = field(default_factory=dict)
    prefixes: set = field(default_factory=set)


def instruction_record(instruction):
    parameters = dict(instruction.parameters)
    # Research annotations remain in the plan; the kernel consumes ABI fields.
    parameters.pop("data", None)
    return {"opcode": instruction.opcode, "node_id": instruction.node_id,
            "parameters": parameters, "requires": list(instruction.requires)}


class PlanQueue:
    """Known operations are executable even while the next batch is unavailable.

    Delivery preserves each node's order. Different nodes may have overlapping
    tails; a later batch may therefore insert an earlier event for another node.
    """
    def __init__(self):
        self.pending = []
        self.serial = count()
        self.seen = set()
        self.latest_by_owner = {}
        self.previous_start = self.final_end = None
        self.backend = self.deployment = None

    @property
    def timestamp(self):
        return self.pending[0][0] if self.pending else None

    def accept(self, plan):
        if not isinstance(plan, PhysicalPlan):
            raise TypeError("Runner consumes PhysicalPlan values")
        if plan.backend_abi != "skyseed.physical.v1":
            raise ValueError(f"unsupported physical ABI {plan.backend_abi}")
        if self.backend is None:
            self.backend, self.deployment = plan.forwarding_backend, plan.deployment_id
        if plan.forwarding_backend != self.backend or plan.deployment_id != self.deployment:
            raise ValueError("one Runner requires one backend and deployment identity")
        if self.previous_start is not None and plan.start_time < self.previous_start:
            raise ValueError("physical batches must ascend by start_time")
        self.previous_start = plan.start_time
        self.final_end = plan.end_time if self.final_end is None else max(self.final_end, plan.end_time)
        operations = plan.operations
        if plan.stream not in self.seen:
            operations = (*plan.initial_operations, *plan.context_operations, *operations)
            self.seen.add(plan.stream)
        # Restoration context and new work can interleave within the first batch.
        for operation in sorted(operations, key=lambda item: item.timestamp):
            target = tuple(operation.target) if plan.stream == "link" else operation.target
            owner = plan.stream, target
            previous = self.latest_by_owner.get(owner)
            if previous is not None and operation.timestamp < previous:
                raise ValueError(f"physical operations must preserve per-owner order: {owner}")
            self.latest_by_owner[owner] = operation.timestamp
            self._push(operation.timestamp, plan.stream, operation)
        self._push(plan.start_time, "targets", plan.targets)

    def _push(self, timestamp, kind, value):
        heapq.heappush(self.pending, (timestamp, next(self.serial), kind, value))

    def finish(self):
        if self.final_end is not None:
            self._push(self.final_end, "boundary", None)

    def pop(self):
        group = ScheduledGroup(self.timestamp)
        while self.pending and self.timestamp == group.timestamp:
            _, _, kind, value = heapq.heappop(self.pending)
            if kind == "targets":
                group.targets.update((node["node_id"], node) for node in value["nodes"])
                group.prefixes.update(value.get("address_pools", ()))
                group.prefixes.update(item["prefix"] for item in value["destinations"]
                                      if item["kind"] == "prefix")
            elif kind != "boundary":
                destination = group.links if kind == "link" else group.routes
                destination.append(value)
        return group


def groups(plans):
    """Offline ordered iterator. Runner instead accepts batches incrementally."""
    queue = PlanQueue()
    for plan in plans:
        while queue.timestamp is not None and queue.timestamp < plan.start_time:
            yield queue.pop()
        queue.accept(plan)
    queue.finish()
    while queue.timestamp is not None:
        yield queue.pop()
