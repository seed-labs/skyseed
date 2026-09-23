"""Replaceable routing decisions; Planner owns their formal changes."""
from __future__ import annotations
from datetime import datetime
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from ...physical import PhysicalWorld
from ..values import OperationalConnection, ZoneServiceAssignment, RouteDemand, RouteOutcome


@runtime_checkable
class RoutePlanningModel(Protocol):
    """Define replaceable demand-bounded Route outcome planning.

    Layer: public Simulator planning Model contract. It is public because
    callers inject Route policy and ``Planner`` owns its optional resource
    shutdown boundary. It is not a physical path-feasibility model, forwarding
    compiler, or owner of PlannedOperationalState.

    Optional ``can_reuse_unchanged_inputs() -> True`` permits reuse; absence
    means compute every formal step. Within that permission only, optional
    ``depends_on_connection_parameters()`` defaults to True and refines
    parameter-only triggers. Topology, service and current demand changes
    always invalidate reuse. Timestamp supports time-dependent algorithms.

    Optional read-only ``route_work_statistics()`` and
    ``route_resource_statistics()`` return mappings of named numeric counters.
    Planner requests them only with an observer; work is requested only after
    evaluation. Missing statistics never affect the required routing contract.
    """

    def plan_route_outcomes(
        self,
        physical_world: PhysicalWorld,
        operational_connections: tuple[OperationalConnection, ...],
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        demands: tuple[RouteDemand, ...],
        previous_outcomes: tuple[RouteOutcome, ...],
        *, timestamp: datetime,
    ) -> RoutePlanningResult:
        """Plan outcomes under the selected algorithm's eligibility rules."""


@dataclass(frozen=True, slots=True)
class RoutePlanningResult:
    """Return a replacement snapshot of explicit Route outcomes.

    Omitted entries withdraw previous arrangements. Local entries may cover any
    known source/target, independently of requested complete-path demands.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable Route Models must return one validated contract to ``Planner``.
    It is not a forwarding table, backend intent set, or complete operational
    state.
    """

    outcomes: tuple[RouteOutcome, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
