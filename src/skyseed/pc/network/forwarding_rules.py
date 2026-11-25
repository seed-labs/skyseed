"""Logical destinations, node-local label matches and forwarding actions."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RouteDestination:
    kind: str  # node, endpoint, prefix, or a model-defined destination kind
    identity: str

    def __post_init__(self):
        if not self.kind or not self.identity:
            raise ValueError("destination requires kind and identity")


@dataclass(frozen=True, slots=True)
class LogicalExit:
    link_id: tuple[str, str]
    source: str

    def __post_init__(self):
        pair = tuple(sorted(self.link_id))
        if len(pair) != 2 or pair[0] == pair[1] or self.source not in pair:
            raise ValueError("logical exit requires a connection and its source endpoint")
        object.__setattr__(self, "link_id", pair)

    @property
    def peer(self):
        first, second = self.link_id
        return second if self.source == first else first


@dataclass(frozen=True, slots=True)
class ForwardingRule:
    destination: RouteDestination
    action: str
    exit: LogicalExit | None = None
    incoming_label: int | None = None
    outgoing_label: int | None = None
    data: Mapping = field(default_factory=dict)

    def __post_init__(self):
        shapes = {
            "forward": (True, False, False), "deliver": (False, False, False),
            "push": (True, False, True), "swap": (True, True, True),
            "pop_deliver": (False, True, False),
        }
        shape = (self.exit is not None, self.incoming_label is not None,
                 self.outgoing_label is not None)
        if self.action not in shapes or shape != shapes[self.action]:
            raise ValueError(f"inconsistent exit/labels for {self.action!r}")
        for label in (self.incoming_label, self.outgoing_label):
            if label is not None and (type(label) is not int or not 16 <= label < 2**20):
                raise ValueError("labels must be integers in the unreserved 20-bit range")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    @property
    def key(self):
        if self.incoming_label is not None:
            return ("label", self.incoming_label)
        return ("destination", self.destination.kind, self.destination.identity)


def indexed_rules(rules):
    """Build mutable node tables once; the result owner controls adoption."""
    tables = {}
    for node, values in rules.items():
        table = {}
        for rule in values:
            if rule.key in table:
                raise ValueError(f"duplicate forwarding match at {node!r}: {rule.key}")
            if rule.exit is not None and rule.exit.source != node:
                raise ValueError(f"forwarding exit has wrong owner at {node!r}")
            table[rule.key] = rule
        if table:
            tables[node] = table
    return tables


def frozen_rules(tables):
    return MappingProxyType({node: tuple(table.values()) for node, table in tables.items() if table})
