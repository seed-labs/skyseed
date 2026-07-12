"""Backend-neutral planning interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

from skyseed_v1.core.ir.contracts import RuntimeUpdatePlan, TopologySnapshot


class Planner(ABC):
    """Replaceable ``TopologySnapshot`` to ``RuntimeUpdatePlan`` policy."""

    planner_name: str
    planner_kind: str
    planner_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "planner_name": self.planner_name,
            "planner_kind": self.planner_kind,
            "planner_version": self.planner_version,
        }

    @abstractmethod
    def build_runtime_update_plan(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> RuntimeUpdatePlan:
        """Build one full-state semantic plan without applying backend state."""


__all__ = ["Planner"]
