"""Private codecs for Simulator/Planner historical semantic objects."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from skyseed_v2.shared import NodeId
from .slicing.connection import (
    Connection,
    ConnectionId,
    ConnectionKind,
    DirectionalConnectionParameters,
)
from .slicing.difference import (
    ConnectionDirection,
    DifferenceOperation,
    DirectionalConnectionParametersPatch,
    RoutePatch,
    TopologyDifference,
    ZoneServiceAssignmentPatch,
)
from .slicing.route import (
    GroundStationRouteTarget,
    RouteKey,
    ZoneRouteTarget,
)


def _difference_data(value: TopologyDifference) -> dict[str, object]:
    if not isinstance(value, TopologyDifference):
        raise TypeError("value must be a TopologyDifference")
    return {
        "previous_slice_id": value.previous_slice_id,
        "previous_timestamp": (
            None
            if value.previous_timestamp is None
            else value.previous_timestamp.isoformat()
        ),
        "current_slice_id": value.current_slice_id,
        "current_timestamp": value.current_timestamp.isoformat(),
        "added_connections": [_connection_data(item) for item in value.added_connections],
        "removed_connection_ids": [
            _connection_id_data(item) for item in value.removed_connection_ids
        ],
        "connection_parameter_patches": [
            {
                "connection_id": _connection_id_data(item.connection_id),
                "direction": item.direction.value,
                "bandwidth_bps": item.bandwidth_bps,
                "delay_us": item.delay_us,
                "packet_loss_ppm": item.packet_loss_ppm,
            }
            for item in value.connection_parameter_patches
        ],
        "zone_service_assignment_patches": [
            {
                "zone_id": item.zone_id,
                "operation": item.operation.value,
                "service_satellite_id": (
                    None
                    if item.service_satellite_id is None
                    else _node_id_data(item.service_satellite_id)
                ),
            }
            for item in value.zone_service_assignment_patches
        ],
        "route_patches": [
            {
                "route_key": _route_key_data(item.route_key),
                "operation": item.operation.value,
                "next_hop": (
                    None if item.next_hop is None else _node_id_data(item.next_hop)
                ),
            }
            for item in value.route_patches
        ],
    }


def _difference_from_data(value: Mapping[str, object]) -> TopologyDifference:
    return TopologyDifference(
        previous_slice_id=int(value["previous_slice_id"]),
        previous_timestamp=(
            None
            if value["previous_timestamp"] is None
            else datetime.fromisoformat(str(value["previous_timestamp"]))
        ),
        current_slice_id=int(value["current_slice_id"]),
        current_timestamp=datetime.fromisoformat(str(value["current_timestamp"])),
        added_connections=tuple(
            _connection_from_data(item)
            for item in _mapping_records(value["added_connections"])
        ),
        removed_connection_ids=tuple(
            _connection_id_from_data(item)
            for item in _mapping_records(value["removed_connection_ids"])
        ),
        connection_parameter_patches=tuple(
            DirectionalConnectionParametersPatch(
                connection_id=_connection_id_from_data(
                    _mapping(item["connection_id"])
                ),
                direction=ConnectionDirection(str(item["direction"])),
                bandwidth_bps=_optional_int(item["bandwidth_bps"]),
                delay_us=_optional_int(item["delay_us"]),
                packet_loss_ppm=_optional_int(item["packet_loss_ppm"]),
            )
            for item in _mapping_records(value["connection_parameter_patches"])
        ),
        zone_service_assignment_patches=tuple(
            ZoneServiceAssignmentPatch(
                zone_id=str(item["zone_id"]),
                operation=DifferenceOperation(str(item["operation"])),
                service_satellite_id=(
                    None
                    if item["service_satellite_id"] is None
                    else _node_id_from_data(_mapping(item["service_satellite_id"]))
                ),
            )
            for item in _mapping_records(value["zone_service_assignment_patches"])
        ),
        route_patches=tuple(
            RoutePatch(
                route_key=_route_key_from_data(_mapping(item["route_key"])),
                operation=DifferenceOperation(str(item["operation"])),
                next_hop=(
                    None
                    if item["next_hop"] is None
                    else _node_id_from_data(_mapping(item["next_hop"]))
                ),
            )
            for item in _mapping_records(value["route_patches"])
        ),
    )


def _planning_decision_data(value: object) -> dict[str, object]:
    from .planning.values import PlanningDecision

    if not isinstance(value, PlanningDecision):
        raise TypeError("value must be a PlanningDecision")
    return {
        "source_difference_ids": list(value.source_difference_ids),
        "decision": value.decision,
        "decided_by": value.decided_by,
        "reason": value.reason,
    }


def _planning_decision_from_data(
    value: Mapping[str, object],
) -> object:
    from .planning.values import PlanningDecision

    ids = value["source_difference_ids"]
    if not isinstance(ids, list):
        raise ValueError("PlanningDecision history IDs must be a list")
    return PlanningDecision(
        source_difference_ids=tuple(int(item) for item in ids),
        decision=str(value["decision"]),
        decided_by=str(value["decided_by"]),
        reason=str(value["reason"]),
    )


def _node_id_data(value: NodeId) -> dict[str, object]:
    return {
        "asn": value.asn,
        "entity_kind": value.entity_kind,
        "entity_id": value.entity_id,
    }


def _node_id_from_data(value: Mapping[str, object]) -> NodeId:
    return NodeId(
        asn=int(value["asn"]),
        entity_kind=str(value["entity_kind"]),
        entity_id=str(value["entity_id"]),
    )


def _connection_id_data(value: ConnectionId) -> dict[str, object]:
    return {
        "kind": value.kind.value,
        "endpoint_a": _node_id_data(value.endpoint_a),
        "endpoint_b": _node_id_data(value.endpoint_b),
    }


def _connection_id_from_data(value: Mapping[str, object]) -> ConnectionId:
    return ConnectionId(
        ConnectionKind(str(value["kind"])),
        _node_id_from_data(_mapping(value["endpoint_a"])),
        _node_id_from_data(_mapping(value["endpoint_b"])),
    )


def _parameters_data(value: DirectionalConnectionParameters) -> dict[str, int]:
    return {
        "bandwidth_bps": value.bandwidth_bps,
        "delay_us": value.delay_us,
        "packet_loss_ppm": value.packet_loss_ppm,
    }


def _parameters_from_data(
    value: Mapping[str, object],
) -> DirectionalConnectionParameters:
    return DirectionalConnectionParameters(
        bandwidth_bps=int(value["bandwidth_bps"]),
        delay_us=int(value["delay_us"]),
        packet_loss_ppm=int(value["packet_loss_ppm"]),
    )


def _connection_data(value: Connection) -> dict[str, object]:
    return {
        "connection_id": _connection_id_data(value.connection_id),
        "endpoint_a_to_b": _parameters_data(value.endpoint_a_to_b),
        "endpoint_b_to_a": _parameters_data(value.endpoint_b_to_a),
    }


def _connection_from_data(value: Mapping[str, object]) -> Connection:
    return Connection(
        _connection_id_from_data(_mapping(value["connection_id"])),
        _parameters_from_data(_mapping(value["endpoint_a_to_b"])),
        _parameters_from_data(_mapping(value["endpoint_b_to_a"])),
    )


def _route_key_data(value: RouteKey) -> dict[str, object]:
    target = value.target
    if isinstance(target, GroundStationRouteTarget):
        target_data: dict[str, object] = {
            "kind": "ground-station",
            "ground_station_id": _node_id_data(target.ground_station_id),
        }
    else:
        assert isinstance(target, ZoneRouteTarget)
        target_data = {"kind": "zone", "zone_id": target.zone_id}
    return {"source": _node_id_data(value.source), "target": target_data}


def _route_key_from_data(value: Mapping[str, object]) -> RouteKey:
    target = _mapping(value["target"])
    kind = target["kind"]
    route_target = (
        GroundStationRouteTarget(
            _node_id_from_data(_mapping(target["ground_station_id"]))
        )
        if kind == "ground-station"
        else ZoneRouteTarget(str(target["zone_id"]))
    )
    return RouteKey(_node_id_from_data(_mapping(value["source"])), route_target)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("durable history value must be a mapping")
    return value


def _mapping_records(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError("durable history record collection must be a list")
    return tuple(_mapping(item) for item in value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)
