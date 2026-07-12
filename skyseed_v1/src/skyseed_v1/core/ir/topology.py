"""Backend-neutral topology pipeline intermediate representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ZoneGenerationInput:
    """Zone policy input associated with one orbit/topology slice."""

    membership_mode: str
    zones: Dict[str, Dict[str, Any]]
    ut_zone_membership: Dict[str, str]


@dataclass(frozen=True)
class OrbitTopologySlice:
    """One ordered, pre-visibility topology input slice."""

    time_slice: Dict[str, Any]
    nodes: Dict[str, Dict[str, Any]]
    candidate_links: List[Dict[str, Any]]
    zone_generation_input: ZoneGenerationInput
    planner_hints: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisibilitySlice:
    """Active links and visibility evidence for one topology slice."""

    active_links: List[Dict[str, Any]]
    visibility_model: str
    candidate_visibility: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True)
class ZoneProjection:
    """Stable UT membership and serving ownership for one slice."""

    zones: Dict[str, Dict[str, Any]]
    ut_zone_membership: Dict[str, str]
    zone_generator: str


__all__ = [
    "OrbitTopologySlice",
    "VisibilitySlice",
    "ZoneGenerationInput",
    "ZoneProjection",
]
