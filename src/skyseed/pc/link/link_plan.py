"""An independently readable interval of logical connection updates."""
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from ..physical.physical_results import utc_time
from .link_values import LinkId, LinkConfiguration


@dataclass(frozen=True, slots=True)
class LinkOperation:
    timestamp: datetime
    kind: str
    link_id: LinkId
    configuration: LinkConfiguration | None = None

    def __post_init__(self):
        object.__setattr__(self, "timestamp", utc_time(self.timestamp))
        object.__setattr__(self, "link_id", tuple(self.link_id))
        if self.kind not in {"establish", "withdraw", "update"}:
            raise ValueError(f"unknown link operation {self.kind!r}")
        if self.kind == "withdraw":
            if self.configuration is not None:
                raise ValueError("withdraw must not contain a configuration")
        elif self.configuration is None or self.configuration.id != self.link_id:
            raise ValueError("establish/update requires a matching configuration")

    def apply(self, links):
        if self.kind == "establish":
            if self.link_id in links:
                raise ValueError(f"cannot establish an existing connection {self.link_id}")
            links[self.link_id] = self.configuration
        else:
            if self.link_id not in links:
                raise ValueError(f"cannot {self.kind} an absent connection {self.link_id}")
            if self.kind == "withdraw":
                del links[self.link_id]
            else:
                links[self.link_id] = self.configuration


@dataclass(frozen=True, slots=True)
class LinkPlan:
    """Coverage is [start_time, end_time); operations at start belong here.

    initial_links is the carried configuration before the start-time operations.
    state_at(start_time) applies those operations and returns the actual state.
    end_links is the carried state for the next interval, without its operations.
    """
    start_time: datetime
    end_time: datetime
    initial_links: Mapping[LinkId, LinkConfiguration] = field(default_factory=dict)
    operations: tuple[LinkOperation, ...] = ()
    _end_links: Mapping = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        start = utc_time(self.start_time)
        end = utc_time(self.end_time)
        if start >= end:
            raise ValueError("LinkPlan requires start_time < end_time")
        initial = dict(self.initial_links)
        for identity, configuration in initial.items():
            if identity != configuration.id:
                raise ValueError("initial link key and configuration ID differ")
        operations = tuple(self.operations)
        links = dict(initial)
        previous = start
        for operation in operations:
            if not previous <= operation.timestamp < end:
                raise ValueError("operations must be ordered within [start_time, end_time)")
            operation.apply(links)
            previous = operation.timestamp
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)
        object.__setattr__(self, "initial_links", MappingProxyType(initial))
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "_end_links", MappingProxyType(links))

    @property
    def end_links(self):
        return self._end_links

    def states_at(self, timestamps):
        """Read ascending query times in one pass over this plan's operations."""
        links = dict(self.initial_links)
        index = 0
        previous = self.start_time
        for timestamp in timestamps:
            timestamp = utc_time(timestamp)
            if not previous <= timestamp < self.end_time:
                raise ValueError("query times must ascend within the plan interval")
            while index < len(self.operations) and self.operations[index].timestamp <= timestamp:
                self.operations[index].apply(links)
                index += 1
            yield MappingProxyType(dict(links))
            previous = timestamp

    def state_at(self, timestamp):
        return next(self.states_at([timestamp]))

    def slice(self, start_time, end_time):
        start = utc_time(start_time)
        end = utc_time(end_time)
        if not self.start_time <= start < end <= self.end_time:
            raise ValueError("slice must be inside the plan interval")
        initial = dict(self.initial_links)
        operations = []
        for operation in self.operations:
            if operation.timestamp < start:
                operation.apply(initial)
            elif operation.timestamp < end:
                operations.append(operation)
            else:
                break
        return LinkPlan(start, end, initial, tuple(operations))
