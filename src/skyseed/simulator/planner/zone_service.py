"""Zone assignment contract and highest-elevation selection."""
from __future__ import annotations
from datetime import datetime
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from skyseed.shared import NodeId
from collections.abc import Mapping
from ..physical import PhysicalWorld, ZoneServiceCandidate
from ._scopes import _validated_satellite_scopes, _validate_scope_world
from .values import ZoneServiceAssignment, _node_key


@runtime_checkable
class ZoneServicePlanningModel(Protocol):
    """Define the replaceable policy that serves or explicitly unserves Zones.

    Layer: public Simulator planning Model contract. It is public because
    callers may inject Zone assignment policy and return decisions through a stable boundary. It is not physical Zone visibility,
    Route planning, or a static Zone-layout owner.
    """

    def assign_zone_service(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[ZoneServiceCandidate, ...],
        previous_assignments: tuple[ZoneServiceAssignment, ...],
        *, timestamp: datetime,
    ) -> ZoneServicePlanningResult:
        """Choose an explicit assignment for every Zone."""


@dataclass(frozen=True, slots=True)
class ZoneServicePlanningResult:
    """Return complete Zone assignments.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable Zone Models must return data that ``Planner`` can validate and
    publish. It is not a complete PlannedOperationalState, physical candidate
    set, or backend mutation list.
    """

    assignments: tuple[ZoneServiceAssignment, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignments", tuple(self.assignments))


class HighestElevationZoneServicePlanningModel:
    """Assign each Zone to its deterministic highest-elevation candidate.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied implementation of the Zone service
    contract. It is not a physical-candidate generator, static Zone owner, or
    Route planner.
    """

    __slots__ = ("_world", "_zones", "_zone_ids", "_satellite_ids", "_satellite_scopes")

    def __init__(self, *, satellite_scopes: Mapping[NodeId, str] | None = None) -> None:
        self._satellite_scopes = _validated_satellite_scopes(satellite_scopes)
        self._world = None
        self._zones = ()
        self._zone_ids = frozenset()
        self._satellite_ids = frozenset()

    def describe_configuration(self):
        return {"satellite_scopes": self._satellite_scopes}

    def assign_zone_service(
        self,
        physical_world: PhysicalWorld,
        candidates: tuple[ZoneServiceCandidate, ...],
        previous_assignments: tuple[ZoneServiceAssignment, ...],
        *, timestamp: datetime,
    ) -> ZoneServicePlanningResult:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if self._world is not physical_world:
            _validate_scope_world(physical_world, self._satellite_scopes)
            try:
                zones = physical_world.get_zone_layout().zones
            except RuntimeError:
                zones = ()
            self._zones = tuple(sorted(zones, key=lambda value: value.zone_id))
            self._zone_ids = frozenset(zone.zone_id for zone in zones)
            self._satellite_ids = frozenset(satellite.node_id for system in physical_world.autonomous_systems
                for shell in system.shells for orbit in shell.orbits for satellite in orbit.satellites)
            self._world = physical_world
        zones = self._zones
        best: dict[str, ZoneServiceCandidate] = {}
        zone_ids = self._zone_ids
        for candidate in candidates:
            if not isinstance(candidate, ZoneServiceCandidate):
                raise TypeError("candidates must contain Zone candidate values")
            if candidate.zone_id not in zone_ids:
                raise ValueError("Zone candidate is foreign to PhysicalWorld")
            if candidate.satellite_id not in self._satellite_ids:
                raise ValueError("Zone candidate Satellite is foreign to PhysicalWorld")
            if candidate.elevation_deg is None:
                raise ValueError("HighestElevationZoneServicePlanningModel requires elevation_deg")
            key = self._satellite_scopes.get(candidate.satellite_id, ""), candidate.zone_id
            current = best.get(key)
            if current is None or (
                -candidate.elevation_deg,
                _node_key(candidate.satellite_id),
            ) < (
                -current.elevation_deg,
                _node_key(current.satellite_id),
            ):
                best[key] = candidate
        assignments = []
        scopes = sorted(set(self._satellite_scopes.values())) if self._satellite_scopes else [""]
        for scope in scopes:
            for zone in zones:
                key = scope, zone.zone_id
                satellite_id = None if key not in best else best[key].satellite_id
                assignments.append(ZoneServiceAssignment(zone.zone_id, satellite_id, scope))
        return ZoneServicePlanningResult(tuple(assignments))
