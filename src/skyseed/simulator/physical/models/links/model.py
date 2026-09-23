"""Replaceable physical-link query contract."""
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from ...queries import PhysicalPositionSnapshot, PhysicalLinkRequest, PhysicalLinkQueryResult
if TYPE_CHECKING:
    from ...world import PhysicalWorld

@runtime_checkable
class PhysicalLinkModel(Protocol):
    """Define physical feasibility evaluation for one bounded frame request.

    Layer: replaceable physical algorithm Protocol. A planning owner invokes
    it with one PhysicalWorld/frame/request and consumes its immutable observation.
    It is not a candidate-enumeration policy, Connection selector, lifecycle
    owner, planning service, or Position source and is public from
    ``skyseed.simulator.physical``.
    """

    def compute_observation(
        self,
        physical_world: PhysicalWorld,
        physical_frame: PhysicalPositionSnapshot,
        request: PhysicalLinkRequest,
    ) -> PhysicalLinkQueryResult:
        """Return sparse facts whose timestamp equals ``physical_frame``."""


__all__ = ["PhysicalLinkModel"]
