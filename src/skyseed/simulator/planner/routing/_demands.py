"""Shared geographical gateway association and demand construction, without an owner."""
from math import isfinite

from ._gateway_candidates import _rank_gateway_candidates
from ...physical import PhysicalWorld
from ..values import (GroundStationRouteDemandTarget, ZoneRouteDemandTarget,
                     RouteDemand, ZoneServiceAssignment, _route_demand_key)


class _GatewayDemandConstruction:
    __slots__ = (
        "_gateway_candidate_count", "_gateway_candidate_max_distance_m",
        "_prepared_physical_world", "_gateway_candidates_by_zone",
        "_demands_by_service", "_demand_assignments", "_last_demands", "_construction_work",
    )

    def _initialize_demands(self, gateway_candidate_count, gateway_candidate_max_distance_m):
        if (
            isinstance(gateway_candidate_count, bool)
            or not isinstance(gateway_candidate_count, int)
            or gateway_candidate_count <= 0
        ):
            raise ValueError("gateway_candidate_count must be positive")
        if gateway_candidate_max_distance_m is not None:
            if isinstance(gateway_candidate_max_distance_m, bool):
                raise ValueError(
                    "gateway_candidate_max_distance_m must be positive or None"
                )
            converted = float(gateway_candidate_max_distance_m)
            if not isfinite(converted) or converted <= 0.0:
                raise ValueError(
                    "gateway_candidate_max_distance_m must be positive or None"
                )
            gateway_candidate_max_distance_m = converted
        self._gateway_candidate_count = gateway_candidate_count
        self._gateway_candidate_max_distance_m = gateway_candidate_max_distance_m
        self._prepared_physical_world = None
        self._gateway_candidates_by_zone = None
        self._demands_by_service = {}
        self._demand_assignments = None
        self._last_demands = ()
        self._construction_work = {}

    def _create_route_demands(
        self,
        physical_world: PhysicalWorld,
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
    ) -> tuple[RouteDemand, ...]:
        candidates_by_zone = self._prepare_gateway_candidates(physical_world)
        identities = tuple((value.service_key, value.satellite_id) for value in zone_service_assignments)
        if identities == self._demand_assignments:
            self._construction_work["route_demands_reused"] = len(self._last_demands)
            return self._last_demands
        self._construction_work["route_demands_reused"] = 0
        demands = set()
        current_services = {}
        for assignment in zone_service_assignments:
            previous = self._demands_by_service.get(assignment.service_key)
            if previous is not None and previous[0] == assignment.satellite_id:
                values = previous[1]
                self._construction_work["route_demands_reused"] += len(values)
            else:
                values = []
                for gateway_id in candidates_by_zone.get(assignment.zone_id, ()):
                    if assignment.satellite_id is not None:
                        values.append(RouteDemand(assignment.satellite_id,
                            GroundStationRouteDemandTarget(gateway_id, assignment.network_scope)))
                    values.append(RouteDemand(gateway_id, ZoneRouteDemandTarget(assignment.zone_id, assignment.network_scope)))
                values = tuple(values)
            current_services[assignment.service_key] = (assignment.satellite_id, values)
            demands.update(values)
        self._demands_by_service = current_services
        self._demand_assignments = identities
        self._last_demands = tuple(sorted(demands, key=_route_demand_key))
        return self._last_demands

    def _bind_world(self, physical_world: PhysicalWorld):
        if self._prepared_physical_world is not None and self._prepared_physical_world is not physical_world:
            raise ValueError("RoutePlanningModel cannot be reused with another PhysicalWorld")
        self._prepared_physical_world = physical_world

    def _prepare_gateway_candidates(self, physical_world: PhysicalWorld):
        self._bind_world(physical_world)
        if self._gateway_candidates_by_zone is None:
            self._gateway_candidates_by_zone = _rank_gateway_candidates(
                physical_world,
                gateway_candidate_count=self._gateway_candidate_count,
                gateway_candidate_max_distance_m=self._gateway_candidate_max_distance_m,
            )
        return self._gateway_candidates_by_zone
