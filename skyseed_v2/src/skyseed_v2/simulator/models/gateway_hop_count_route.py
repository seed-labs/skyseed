"""Built-in deterministic Gateway-scoped minimum-hop Route algorithm."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from math import asin, cos, isfinite, radians, sin, sqrt
from types import MappingProxyType

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.time import normalize_utc_datetime

from ..slicing import (
    ConnectionSlice,
    GroundStationRouteTarget,
    RouteSlice,
    RouteTarget,
    ZoneRouteTarget,
)
from ..slicing._routing_policy import (
    _eligible_shortest_next_hops,
    _prepare_routing_policy_context,
)
from ..topology import GroundStation, Topology


_MEAN_EARTH_RADIUS_M = 6_371_008.8


class GatewayHopCountRouteModel:
    """Route only fixed Zone/Gateway demands over current minimum-hop paths.

    Layer: built-in stateful-preparation Simulator Model. Users explicitly
    create it and one Slicer invokes it. Static geographic candidate selection
    is Model policy; it is not a Topology fact, dynamic handover rule, traffic
    selector, routing backend or fallback. The class is public from
    ``skyseed_v2.simulator``.
    """

    __slots__ = (
        "_gateway_candidate_count",
        "_gateway_candidate_max_distance_m",
        "_prepared_topology",
        "_gateway_candidates_by_zone",
    )

    def __init__(
        self,
        *,
        gateway_candidate_count: int,
        gateway_candidate_max_distance_m: float | None,
    ) -> None:
        if (
            isinstance(gateway_candidate_count, bool)
            or not isinstance(gateway_candidate_count, int)
            or gateway_candidate_count <= 0
        ):
            raise ValueError("gateway_candidate_count must be a positive integer")
        if gateway_candidate_max_distance_m is None:
            maximum_distance = None
        else:
            if isinstance(gateway_candidate_max_distance_m, bool):
                raise ValueError(
                    "gateway_candidate_max_distance_m must be a positive "
                    "finite number or None"
                )
            maximum_distance = float(gateway_candidate_max_distance_m)
            if not isfinite(maximum_distance) or maximum_distance <= 0.0:
                raise ValueError(
                    "gateway_candidate_max_distance_m must be a positive "
                    "finite number or None"
                )
        self._gateway_candidate_count = gateway_candidate_count
        self._gateway_candidate_max_distance_m = maximum_distance
        self._prepared_topology: Topology | None = None
        self._gateway_candidates_by_zone: Mapping[
            str, tuple[NodeId, ...]
        ] | None = None

    def compute_route_slice(
        self,
        topology: Topology,
        timestamp: datetime,
        connection_slice: ConnectionSlice,
        previous_route_slice: RouteSlice,
    ) -> RouteSlice:
        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        normalize_utc_datetime(timestamp, "timestamp")
        if not isinstance(connection_slice, ConnectionSlice):
            raise TypeError("connection_slice must be a ConnectionSlice")
        if not isinstance(previous_route_slice, RouteSlice):
            raise TypeError("previous_route_slice must be a RouteSlice")

        candidates_by_zone = self._prepare_gateway_candidates(topology)
        routing_context = _prepare_routing_policy_context(
            topology, connection_slice
        )
        entries: dict[NodeId, dict[RouteTarget, NodeId]] = {}

        zones_by_gateway: dict[NodeId, set[NodeId]] = {}
        zones_by_service_satellite: dict[
            NodeId, list[tuple[str, tuple[NodeId, ...]]]
        ] = {}
        for zone_id, service_satellite in (
            connection_slice.zone_service_assignments.items()
        ):
            gateway_ids = candidates_by_zone.get(zone_id, ())
            zones_by_service_satellite.setdefault(
                service_satellite, []
            ).append((zone_id, gateway_ids))
            for gateway_id in gateway_ids:
                zones_by_gateway.setdefault(gateway_id, set()).add(
                    service_satellite
                )

        for gateway_id in sorted(zones_by_gateway, key=_node_key):
            target = GroundStationRouteTarget(gateway_id)
            choices_by_source = _eligible_shortest_next_hops(
                routing_context,
                target,
                gateway_id,
                required_sources=frozenset(zones_by_gateway[gateway_id]),
            )
            for service_satellite in sorted(
                zones_by_gateway[gateway_id], key=_node_key
            ):
                _materialize_reachable_path(
                    entries,
                    choices_by_source,
                    service_satellite,
                    target,
                    gateway_id,
                )

        for service_satellite in sorted(
            zones_by_service_satellite, key=_node_key
        ):
            zone_demands = zones_by_service_satellite[service_satellite]
            required_gateways = frozenset(
                gateway_id
                for _zone_id, gateway_ids in zone_demands
                for gateway_id in gateway_ids
            )
            choices_by_source = _eligible_shortest_next_hops(
                routing_context,
                ZoneRouteTarget(zone_demands[0][0]),
                service_satellite,
                required_sources=required_gateways,
            )
            for zone_id, gateway_ids in zone_demands:
                target = ZoneRouteTarget(zone_id)
                for gateway_id in gateway_ids:
                    _materialize_reachable_path(
                        entries,
                        choices_by_source,
                        gateway_id,
                        target,
                        service_satellite,
                    )

        return RouteSlice(entries)

    def _prepare_gateway_candidates(
        self, topology: Topology
    ) -> Mapping[str, tuple[NodeId, ...]]:
        if self._prepared_topology is not None:
            if self._prepared_topology is not topology:
                raise ValueError(
                    "GatewayHopCountRouteModel cannot be reused with another "
                    "Topology"
                )
            assert self._gateway_candidates_by_zone is not None
            return self._gateway_candidates_by_zone

        ground_stations = tuple(
            sorted(
                (
                    station
                    for autonomous_system in topology.autonomous_systems
                    for station in autonomous_system.ground_stations
                ),
                key=lambda station: _node_key(station.node_id),
            )
        )
        try:
            zones = topology.get_zone_layout().zones
        except RuntimeError:
            zones = ()
        candidates: dict[str, tuple[NodeId, ...]] = {}
        for zone in zones:
            latitude_deg, longitude_deg = zone.representative_point
            ranked = sorted(
                (
                    (
                        _great_circle_distance_m(
                            latitude_deg,
                            longitude_deg,
                            station,
                        ),
                        _node_key(station.node_id),
                        station.node_id,
                    )
                    for station in ground_stations
                ),
                key=lambda item: (item[0], item[1]),
            )
            if self._gateway_candidate_max_distance_m is not None:
                ranked = [
                    item
                    for item in ranked
                    if item[0] <= self._gateway_candidate_max_distance_m
                ]
            candidates[zone.zone_id] = tuple(
                item[2]
                for item in ranked[: self._gateway_candidate_count]
            )
        prepared = MappingProxyType(dict(sorted(candidates.items())))
        self._prepared_topology = topology
        self._gateway_candidates_by_zone = prepared
        return prepared


def _materialize_reachable_path(
    entries: dict[NodeId, dict[RouteTarget, NodeId]],
    choices_by_source: Mapping[NodeId, tuple[NodeId, ...]],
    source: NodeId,
    target: RouteTarget,
    terminal: NodeId,
) -> None:
    current = source
    path_entries: list[tuple[NodeId, NodeId]] = []
    seen = {source}
    while current != terminal:
        choices = choices_by_source.get(current, ())
        if not choices:
            return
        next_hop = choices[0]
        if next_hop in seen:
            raise ValueError("minimum-hop routing tree contains a loop")
        path_entries.append((current, next_hop))
        seen.add(next_hop)
        current = next_hop
    for route_source, next_hop in path_entries:
        target_entries = entries.setdefault(route_source, {})
        existing = target_entries.get(target)
        if existing is not None and existing != next_hop:
            raise ValueError("minimum-hop routing tree is inconsistent")
        target_entries[target] = next_hop


def _great_circle_distance_m(
    latitude_deg: float,
    longitude_deg: float,
    station: GroundStation,
) -> float:
    first_latitude = radians(latitude_deg)
    second_latitude = radians(station.latitude_deg)
    delta_latitude = second_latitude - first_latitude
    delta_longitude = radians(station.longitude_deg - longitude_deg)
    haversine = sin(delta_latitude / 2.0) ** 2 + (
        cos(first_latitude)
        * cos(second_latitude)
        * sin(delta_longitude / 2.0) ** 2
    )
    central_angle = 2.0 * asin(
        sqrt(min(1.0, max(0.0, haversine)))
    )
    return _MEAN_EARTH_RADIUS_M * central_angle


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


__all__ = ["GatewayHopCountRouteModel"]
