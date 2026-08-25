"""Define immutable Route values and their Model contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, TypeAlias, runtime_checkable

from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.shared import NodeId

from .connection import ConnectionSlice
from ..topology import Topology


@dataclass(frozen=True, slots=True)
class GroundStationRouteTarget:
    """Identify one GroundStation route destination.

    Layer: immutable Simulator route-target value. RouteKey owns it and route
    consumers query it. It is not a service node, label or backend identity and
    is public from ``skyseed_v2.simulator``.
    """

    ground_station_id: NodeId

    def __post_init__(self) -> None:
        _require_node_id(self.ground_station_id, "ground_station_id")
        if self.ground_station_id.entity_kind != "ground-station":
            raise ValueError("ground_station_id must identify a GroundStation")


@dataclass(frozen=True, slots=True)
class ZoneRouteTarget:
    """Identify one Zone route destination by stable Zone string identity.

    Layer: immutable Simulator route-target value. RouteKey owns it and route
    consumers query it. It is not a Satellite, UT, label or backend identity
    and is public from ``skyseed_v2.simulator``.
    """

    zone_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "zone_id", validate_identifier(self.zone_id, "zone_id")
        )


RouteTarget: TypeAlias = GroundStationRouteTarget | ZoneRouteTarget


@dataclass(frozen=True, slots=True)
class RouteKey:
    """Identify one sparse next-hop route entry.

    Layer: immutable Simulator dynamic key. RouteSlice indexes by it and route
    consumers construct it implicitly through queries. It is not a full path,
    metric or timestamp and is public from ``skyseed_v2.simulator``.
    """

    source: NodeId
    target: RouteTarget

    def __post_init__(self) -> None:
        _require_node_id(self.source, "source")
        _require_target(self.target)


@dataclass(frozen=True, slots=True, init=False)
class RouteSlice:
    """Own one Slice's deeply immutable sparse next-hop table.

    Layer: immutable Simulator dynamic aggregate. RouteModel creates it and
    TopologySlice retains it. It is not a full-path store, routing backend or
    Slice identity and is public from ``skyseed_v2.simulator``.
    """

    routes: Mapping[NodeId, Mapping[RouteTarget, NodeId]] = field(
        repr=False, compare=True, hash=False
    )

    def __init__(
        self,
        routes: Mapping[NodeId, Mapping[RouteTarget, NodeId]]
        | Iterable[tuple[RouteKey, NodeId]] = (),
    ) -> None:
        nested: dict[NodeId, dict[RouteTarget, NodeId]] = {}
        if isinstance(routes, Mapping):
            for source, targets in routes.items():
                _require_node_id(source, "route source")
                if not isinstance(targets, Mapping):
                    raise TypeError(
                        "route target collections must be mappings"
                    )
                target_routes = nested.setdefault(source, {})
                for target, next_hop in targets.items():
                    _require_target(target)
                    _require_node_id(next_hop, "next_hop")
                    target_routes[target] = next_hop
        else:
            for route_key, next_hop in routes:
                if not isinstance(route_key, RouteKey):
                    raise TypeError("routes must contain RouteKey values")
                _require_node_id(next_hop, "next_hop")
                target_routes = nested.setdefault(route_key.source, {})
                if route_key.target in target_routes:
                    raise ValueError(f"duplicate RouteKey: {route_key!r}")
                target_routes[route_key.target] = next_hop

        ordered = {}
        for source in sorted(nested, key=_node_key):
            ordered[source] = MappingProxyType(
                dict(
                    sorted(
                        nested[source].items(),
                        key=lambda item: _target_key(item[0]),
                    )
                )
            )
        object.__setattr__(self, "routes", MappingProxyType(ordered))

    def get_next_hop(
        self,
        source: NodeId,
        target: RouteTarget,
    ) -> NodeId:
        _require_node_id(source, "source")
        _require_target(target)
        try:
            return self.routes[source][target]
        except KeyError as error:
            raise KeyError(
                f"Route is unreachable: {source.value} -> {target!r}"
            ) from error

    def get_routes_from(
        self,
        source: NodeId,
    ) -> Mapping[RouteTarget, NodeId]:
        _require_node_id(source, "source")
        return self.routes.get(source, MappingProxyType({}))


@runtime_checkable
class RouteModel(Protocol):
    """Define the public one-timestamp Route algorithm boundary.

    Layer: replaceable Simulator algorithm contract. Slicer invokes an
    explicitly supplied implementation. It is not a Connection algorithm,
    backend, path store or fallback and is public from
    ``skyseed_v2.simulator``.
    """

    def compute_route_slice(
        self,
        topology: Topology,
        timestamp: datetime,
        connection_slice: ConnectionSlice,
        previous_route_slice: RouteSlice,
    ) -> RouteSlice:
        """Return one complete sparse next-hop result."""


def _require_node_id(node_id: NodeId, field_name: str) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError(f"{field_name} must be a NodeId")


def _require_target(target: RouteTarget) -> None:
    if not isinstance(target, (GroundStationRouteTarget, ZoneRouteTarget)):
        raise TypeError(
            "target must be a GroundStationRouteTarget or ZoneRouteTarget"
        )


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _target_key(
    target: RouteTarget,
) -> tuple[str, int | str, str, str]:
    if isinstance(target, GroundStationRouteTarget):
        node = target.ground_station_id
        return "G", node.asn, node.entity_kind, node.entity_id
    return "Z", target.zone_id, "", ""


__all__ = [
    "GroundStationRouteTarget",
    "RouteKey",
    "RouteModel",
    "RouteSlice",
    "RouteTarget",
    "ZoneRouteTarget",
]
