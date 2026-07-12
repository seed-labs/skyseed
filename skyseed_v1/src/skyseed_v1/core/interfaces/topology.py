"""Replaceable topology production interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from skyseed_v1.core.ir.contracts import TopologySnapshot
from skyseed_v1.core.ir.topology import (
    OrbitTopologySlice,
    VisibilitySlice,
    ZoneProjection,
)


class OrbitTopologyProducer(ABC):
    """Produce ordered constellation/topology inputs."""

    producer_name: str

    @abstractmethod
    def iter_orbit_slices(self) -> Sequence[OrbitTopologySlice]:
        """Return ordered inputs for visibility and zone processing."""


class VisibilityModel(ABC):
    """Select active visibility relationships from candidate topology."""

    model_name: str

    @abstractmethod
    def evaluate(self, orbit_slice: OrbitTopologySlice) -> VisibilitySlice:
        """Evaluate one slice without applying backend state."""


class ZoneGenerator(ABC):
    """Assign stable UT membership and active zone ownership."""

    generator_name: str

    @abstractmethod
    def generate(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
    ) -> ZoneProjection:
        """Return the planner-facing zone projection."""


class TopologySlicer(ABC):
    """Build a stable planner-facing topology snapshot."""

    slicer_name: str

    @abstractmethod
    def build_snapshot(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
        zone_projection: ZoneProjection,
    ) -> TopologySnapshot:
        """Convert one processed input slice into a snapshot contract."""


__all__ = [
    "OrbitTopologyProducer",
    "TopologySlicer",
    "VisibilityModel",
    "ZoneGenerator",
]
