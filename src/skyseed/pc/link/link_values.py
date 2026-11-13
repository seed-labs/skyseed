"""Logical connections, physical evidence and effective directional properties."""
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

from ..physical import LinkCandidate

LinkId = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ConnectionCandidate:
    """One endpoint pair, retaining each available Physical report separately."""
    endpoints: LinkId
    reports: Mapping[str, LinkCandidate]

    def __post_init__(self):
        endpoints = tuple(sorted(self.endpoints))
        if len(endpoints) != 2 or endpoints[0] == endpoints[1]:
            raise ValueError("a connection requires two distinct endpoints")
        reports = dict(self.reports)
        if not reports:
            raise ValueError("a connection needs at least one physical report")
        for source, report in reports.items():
            if source != report.node_id or tuple(sorted((source, report.peer_id))) != endpoints:
                raise ValueError(f"physical report does not belong to connection {endpoints}")
        object.__setattr__(self, "endpoints", endpoints)
        object.__setattr__(self, "reports", MappingProxyType(reports))

    @property
    def id(self):
        return self.endpoints

    @property
    def bidirectional(self):
        return len(self.reports) == 2


@dataclass(frozen=True, slots=True)
class LinkProperties:
    """Effective properties of one direction; loss is parts per million."""
    link_id: LinkId
    bandwidth_bps: float
    delay_s: float
    packet_loss_ppm: float = 0
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("bandwidth_bps", "delay_s", "packet_loss_ppm"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.packet_loss_ppm > 1_000_000:
            raise ValueError("packet_loss_ppm must not exceed 1000000")
        object.__setattr__(self, "link_id", tuple(self.link_id))
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class LinkConfiguration:
    candidate: ConnectionCandidate
    properties: LinkProperties
    reverse_properties: LinkProperties | None = None

    def __post_init__(self):
        for properties in (self.properties, self.reverse_properties):
            if properties is not None and properties.link_id != self.candidate.id:
                raise ValueError("properties and candidate have different connection IDs")

    @property
    def id(self):
        return self.candidate.id

    def properties_from(self, node_id):
        """None means no opportunity in this direction, never a fabricated link.

        properties applies to both directions unless reverse_properties overrides
        the second (lexically ordered) endpoint's outgoing direction.
        """
        if node_id not in self.candidate.endpoints:
            raise KeyError(node_id)
        if node_id not in self.candidate.reports:
            return None
        if node_id == self.candidate.endpoints[1] and self.reverse_properties is not None:
            return self.reverse_properties
        return self.properties


@dataclass(frozen=True, slots=True)
class LinkEvaluation:
    """Optional default-generator observation; the caller owns its retention."""
    timestamp: datetime
    candidates: tuple[ConnectionCandidate, ...]
    selected: Mapping[LinkId, LinkConfiguration]
    pending: Mapping[LinkId, datetime]

    def __post_init__(self):
        object.__setattr__(self, "selected", MappingProxyType(dict(self.selected)))
        object.__setattr__(self, "pending", MappingProxyType(dict(self.pending)))
