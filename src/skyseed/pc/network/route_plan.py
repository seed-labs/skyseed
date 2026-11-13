"""Independently readable intervals of adopted node forwarding rules."""
from dataclasses import dataclass, field
from datetime import datetime
from heapq import merge
from typing import Mapping

from ..physical.physical_results import utc_time
from .forwarding_rules import ForwardingRule, indexed_rules, frozen_rules


@dataclass(frozen=True, slots=True)
class RouteOperation:
    timestamp: datetime
    node_id: str
    kind: str
    rule: ForwardingRule

    def __post_init__(self):
        object.__setattr__(self, "timestamp", utc_time(self.timestamp))
        if self.kind not in ("install", "replace", "withdraw"):
            raise ValueError(f"unknown route operation {self.kind!r}")
        if self.rule.exit is not None and self.rule.exit.source != self.node_id:
            raise ValueError("route operation and logical exit have different owners")

    def apply(self, tables):
        table = tables.setdefault(self.node_id, {})
        key = self.rule.key
        if self.kind == "install":
            if key in table:
                raise ValueError(f"cannot install existing rule at {self.node_id!r}: {key}")
        elif key not in table:
            raise ValueError(f"cannot {self.kind} absent rule at {self.node_id!r}: {key}")
        if self.kind == "withdraw":
            del table[key]
            if not table:
                del tables[self.node_id]
        else:
            table[key] = self.rule


@dataclass(frozen=True, slots=True)
class RoutePlan:
    start_time: datetime
    end_time: datetime
    initial_rules: Mapping[str, tuple[ForwardingRule, ...]] = field(default_factory=dict)
    operations: tuple[RouteOperation, ...] = ()
    context_operations: tuple[RouteOperation, ...] = ()
    _end_rules: Mapping = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        start, end = utc_time(self.start_time), utc_time(self.end_time)
        if start >= end:
            raise ValueError("RoutePlan requires start_time < end_time")
        tables = indexed_rules(self.initial_rules)
        initial = frozen_rules(tables)
        operations = tuple(self.operations)
        context = tuple(self.context_operations)
        for sequence in (context, operations):
            previous = start
            for operation in sequence:
                if not previous <= operation.timestamp < end:
                    raise ValueError("route operations must be ordered within [start, end)")
                previous = operation.timestamp
        for operation in merge(context, operations, key=lambda item: item.timestamp):
            operation.apply(tables)
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)
        object.__setattr__(self, "initial_rules", initial)
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "context_operations", context)
        object.__setattr__(self, "_end_rules", frozen_rules(tables))

    @property
    def end_rules(self):
        return self._end_rules

    def iter_state_operations(self):
        """Read known state evolution; context precedes new updates at ties."""
        return merge(self.context_operations, self.operations, key=lambda item: item.timestamp)

    def states_at(self, timestamps):
        tables = indexed_rules(self.initial_rules)
        operations = iter(self.iter_state_operations())
        operation = next(operations, None)
        previous = self.start_time
        for timestamp in timestamps:
            timestamp = utc_time(timestamp)
            if not previous <= timestamp < self.end_time:
                raise ValueError("route query times must ascend within coverage")
            while operation is not None and operation.timestamp <= timestamp:
                operation.apply(tables)
                operation = next(operations, None)
            yield frozen_rules(tables)
            previous = timestamp

    def state_at(self, timestamp):
        return next(self.states_at([timestamp]))

    def slice(self, start_time, end_time):
        start, end = utc_time(start_time), utc_time(end_time)
        if not self.start_time <= start < end <= self.end_time:
            raise ValueError("route slice must be inside coverage")
        tables = indexed_rules(self.initial_rules)
        for operation in self.iter_state_operations():
            if operation.timestamp < start:
                operation.apply(tables)
            else:
                break
        operations = tuple(op for op in self.operations if start <= op.timestamp < end)
        context = tuple(op for op in self.context_operations if start <= op.timestamp < end)
        return RoutePlan(start, end, frozen_rules(tables), operations, context)
