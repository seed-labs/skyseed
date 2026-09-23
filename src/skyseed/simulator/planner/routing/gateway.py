"""Destination-grouped hop-count routing over operational connections."""
from __future__ import annotations
from datetime import datetime
from collections.abc import Mapping
from skyseed.shared import NodeId
from skyseed.shared._shutdown import _shutdown_owned
from ...physical import PhysicalWorld
from .._scopes import (
    _validated_satellite_scopes,
    _validate_scope_world,
    _connections_by_scope,
    _group_by_scope,
)
from ..values import (OperationalConnection, ZoneServiceAssignment, RouteDemand, RouteOutcome,
    GroundStationRouteDemandTarget, SatelliteRouteDemandTarget, ReachableRouteOutcome, UnreachableRouteOutcome,
    RouteUnreachableReason, _route_demand_key)
from .model import RoutePlanningResult
from ._demands import _GatewayDemandConstruction
from ._computation import _GroupedRouteEngine, _RoutePathRequest
from ._graph import _prepare_routing_core_context


class GatewayHopCountRoutePlanningModel(_GatewayDemandConstruction):
    """Plan demand-bounded hop-count Routes over operational Connections.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a supplied Route Model with deterministic worker
    lifecycle. It is not a physical-candidate router, forwarding compiler, or
    owner of Planner publication state.
    """

    __slots__ = (
        "_closed", "_satellite_scopes", "_scope_models", "_route_engine",
        "_routing_edges", "_routing_context", "_requests", "_scope_world",
    )

    def __init__(
        self,
        *,
        satellite_scopes: Mapping[NodeId, str] | None = None,
        gateway_candidate_count: int = 1,
        gateway_candidate_max_distance_m: float | None = None,
    ) -> None:
        self._initialize_demands(gateway_candidate_count, gateway_candidate_max_distance_m)
        self._satellite_scopes = _validated_satellite_scopes(satellite_scopes)
        self._closed = False
        self._scope_models = {}
        self._route_engine = _GroupedRouteEngine()
        self._routing_edges = None
        self._routing_context = None
        self._requests = ()
        self._scope_world = None

    def can_reuse_unchanged_inputs(self) -> bool:
        return True

    def depends_on_connection_parameters(self) -> bool:
        """Declare whether parameter-only changes can affect route decisions."""
        return False

    def describe_configuration(self):
        return {"satellite_scopes": self._satellite_scopes,
                "gateway_candidate_count": self._gateway_candidate_count,
                "gateway_candidate_max_distance_m": self._gateway_candidate_max_distance_m}

    def create_route_demands(
        self,
        physical_world: PhysicalWorld,
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        *, timestamp: datetime,
    ) -> tuple[RouteDemand, ...]:
        """Construct the shared geographical gateway demands."""
        if self._closed:
            raise RuntimeError("Route Model is shut down")
        return self._create_route_demands(physical_world, zone_service_assignments)

    def shutdown(self, *, wait: bool = True) -> None:
        self._closed = True
        _shutdown_owned((self._route_engine, *self._scope_models.values()), wait=wait)

    def _active_compute_worker_count(self) -> int:
        if self._scope_models:
            return sum(model._active_compute_worker_count() for model in self._scope_models.values())
        if not self._route_engine.executor_lifecycle.executor_live:
            return 0
        return self._route_engine._executor_worker_count

    def route_resource_statistics(self) -> Mapping[str, int]:
        """Read current route worker/resource counts without mutation."""
        if self._scope_models:
            return {f"{scope}.{key}": value for scope, model in self._scope_models.items()
                    for key, value in model.route_resource_statistics().items()}
        observation = self._route_engine.last_compute_observation
        return {
            "route_pool_workers": self._active_compute_worker_count(),
            "last_route_compute_workers": 0 if observation is None else observation.selected_worker_count,
            "route_worker_limit": 0 if observation is None else observation.configured_maximum_workers,
        }

    def route_work_statistics(self) -> Mapping[str, int]:
        """Read bounded counters from the last route evaluation without mutation."""
        if self._scope_models:
            totals = dict(self._construction_work)
            for model in self._scope_models.values():
                for key, value in model.route_work_statistics().items():
                    totals[key] = totals.get(key, 0) + value
            return totals
        observation = self._route_engine.last_compute_observation
        if observation is None:
            return {}
        return {
            **self._construction_work,
            "route_path_requests_reused": observation.reused_request_count,
            "route_path_requests_computed": sum(v.request_count for v in observation.worker_activities),
            "route_destination_jobs": sum(v.non_empty_job_count for v in observation.worker_activities),
        }

    def plan_route_outcomes(
        self,
        physical_world: PhysicalWorld,
        operational_connections: tuple[OperationalConnection, ...],
        zone_service_assignments: tuple[ZoneServiceAssignment, ...],
        demands: tuple[RouteDemand, ...],
        previous_outcomes: tuple[RouteOutcome, ...],
        *, timestamp: datetime,
    ) -> RoutePlanningResult:
        if self._closed:
            raise RuntimeError("Route Model is shut down")
        self._bind_world(physical_world)
        if self._satellite_scopes:
            if self._scope_world is not physical_world:
                _validate_scope_world(physical_world, self._satellite_scopes)
                self._scope_world = physical_world
            scopes = set(self._satellite_scopes.values())
            if any(d.target.network_scope not in scopes for d in demands):
                raise ValueError("Route demand has unknown network scope")
            outcomes = []
            connections_by_scope = _connections_by_scope(operational_connections, self._satellite_scopes)
            demands_by_scope = _group_by_scope(demands, lambda v: v.target.network_scope)
            assignments_by_scope = _group_by_scope(zone_service_assignments, lambda v: v.network_scope)
            previous_by_scope = _group_by_scope(previous_outcomes, lambda v: v.demand.target.network_scope)
            for scope in sorted(scopes):
                model = self._scope_models.setdefault(scope, None)
                if model is None:
                    model = self._scope_models[scope] = GatewayHopCountRoutePlanningModel(
                        gateway_candidate_count=self._gateway_candidate_count,
                        gateway_candidate_max_distance_m=self._gateway_candidate_max_distance_m)
                connections = tuple(connections_by_scope.get(scope, ()))
                scoped_demands = tuple(demands_by_scope.get(scope, ()))
                if any(d.source.entity_kind == "satellite" and self._satellite_scopes[d.source] != scope for d in scoped_demands):
                    raise ValueError("Route source is outside its network scope")
                for demand in scoped_demands:
                    if isinstance(demand.target, SatelliteRouteDemandTarget):
                        physical_world.get_satellite(demand.target.satellite_id)
                        if self._satellite_scopes[demand.target.satellite_id] != scope:
                            raise ValueError("Route target is outside its network scope")
                result = model.plan_route_outcomes(physical_world, connections,
                    tuple(assignments_by_scope.get(scope, ())),
                    scoped_demands, tuple(previous_by_scope.get(scope, ())), timestamp=timestamp)
                outcomes.extend(result.outcomes)
            return RoutePlanningResult(tuple(outcomes))
        previous_by_demand: dict[RouteDemand, RouteOutcome] = {}
        for previous in previous_outcomes:
            if not isinstance(
                previous,
                (ReachableRouteOutcome, UnreachableRouteOutcome),
            ):
                raise TypeError(
                    "previous_outcomes must contain Route outcomes"
                )
            if previous.demand in previous_by_demand:
                raise ValueError("previous_outcomes contains a duplicate")
            previous_by_demand[previous.demand] = previous
        edges = tuple(
            (
                value.connection_id.endpoint_a,
                value.connection_id.endpoint_b,
            )
            for value in operational_connections
        )
        if self._routing_edges != edges:
            self._routing_context = _prepare_routing_core_context(physical_world, edges, previous=self._routing_context)
            self._routing_edges = edges
        context = self._routing_context
        assignments = {
            value.service_key: value.satellite_id
            for value in zone_service_assignments
        }
        ordered_demands = tuple(sorted(demands, key=_route_demand_key))
        old_requests = self._requests if isinstance(self._requests, dict) else {}
        current_requests = {}
        requests, unresolved = [], {}
        reused = 0
        for index, demand in enumerate(ordered_demands):
            ground_target = isinstance(demand.target, GroundStationRouteDemandTarget)
            terminal = (demand.target.ground_station_id if ground_target
                        else demand.target.satellite_id if isinstance(demand.target, SatelliteRouteDemandTarget)
                        else assignments.get(demand.target.service_key))
            if terminal is None:
                unresolved[index] = RouteUnreachableReason.ZONE_UNSERVED
                continue
            key = demand, index, terminal
            request = old_requests.get(key)
            if request is None:
                physical_world.get_node(demand.source)
                if ground_target:
                    physical_world.get_ground_station(terminal)
                elif isinstance(demand.target, SatelliteRouteDemandTarget):
                    physical_world.get_satellite(terminal)
                request = _RoutePathRequest(index, ground_target, demand.source, terminal)
            else:
                reused += 1
            current_requests[key] = request
            requests.append(request)
        self._requests = current_requests
        self._construction_work["route_request_objects_reused"] = reused
        self._construction_work["route_request_objects_constructed"] = len(requests)-reused
        paths = self._route_engine.compute(context, tuple(requests))
        outcomes: list[RouteOutcome] = []
        reused_outcomes = 0
        for index, demand in enumerate(ordered_demands):
            reason = unresolved.get(index)
            path = paths.get(index)
            previous = previous_by_demand.pop(demand, None)
            if reason is None and (path is None or len(path) < 2):
                reason = RouteUnreachableReason.NO_ELIGIBLE_PATH
            if (reason is not None and isinstance(previous, UnreachableRouteOutcome) and previous.reason is reason
                or reason is None and isinstance(previous, ReachableRouteOutcome) and previous.path == path):
                outcome = previous
                reused_outcomes += 1
            elif reason is not None:
                outcome = UnreachableRouteOutcome(demand, reason)
            else:
                outcome = ReachableRouteOutcome(demand, path[1], path)
            outcomes.append(outcome)
        self._construction_work["route_outcome_objects_reused"] = reused_outcomes
        self._construction_work["route_outcome_objects_constructed"] = len(outcomes) - reused_outcomes
        return RoutePlanningResult(tuple(outcomes))
