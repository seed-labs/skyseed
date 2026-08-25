"""Built-in deterministic minimum-hop Route algorithm."""

from __future__ import annotations

from datetime import datetime

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared import NodeId

from ..slicing._routing_policy import (
    _eligible_shortest_next_hops,
    _prepare_routing_policy_context,
)
from ..slicing import ConnectionSlice
from ..slicing import (
    GroundStationRouteTarget,
    RouteSlice,
    RouteTarget,
    ZoneRouteTarget,
)
from ..topology import Topology


class HopCountRouteModel:
    """Compute deterministic sparse next hops with reverse BFS.

    Layer: built-in stateless Simulator Model. Users explicitly create it and
    one Slicer invokes it. It is not a Connection source, full-path store,
    routing backend or fallback and is public from ``skyseed_v2.simulator``.
    """

    __slots__ = ()

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
        routing_context = _prepare_routing_policy_context(
            topology, connection_slice
        )

        target_terminals: list[tuple[RouteTarget, NodeId]] = []
        for autonomous_system in topology.autonomous_systems:
            for ground_station in autonomous_system.ground_stations:
                target_terminals.append(
                    (
                        GroundStationRouteTarget(ground_station.node_id),
                        ground_station.node_id,
                    )
                )
        for zone_id, service_satellite in (
            connection_slice.zone_service_assignments.items()
        ):
            try:
                node = topology.get_satellite(service_satellite)
            except KeyError as error:
                raise ValueError(
                    "Zone service Satellite is foreign to Topology"
                ) from error
            target_terminals.append(
                (ZoneRouteTarget(zone_id), node.node_id)
            )

        entries: dict[NodeId, dict[RouteTarget, NodeId]] = {}
        for target, terminal in target_terminals:
            choices_by_source = _eligible_shortest_next_hops(
                routing_context,
                target,
                terminal,
            )
            for source, choices in choices_by_source.items():
                if not choices:
                    raise ValueError("reverse BFS produced a corrupt route tree")
                entries.setdefault(source, {})[target] = choices[0]
        return RouteSlice(entries)


__all__ = ["HopCountRouteModel"]
