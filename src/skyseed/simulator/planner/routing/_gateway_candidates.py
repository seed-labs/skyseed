"""Private shared static Zone-to-PhysicalGroundStation candidate ranking."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from types import MappingProxyType

from skyseed.shared import NodeId

from ...physical import PhysicalGroundStation, PhysicalWorld


_MEAN_EARTH_RADIUS_M = 6_371_008.8


def _rank_gateway_candidates(
    physical_world: PhysicalWorld,
    *,
    gateway_candidate_count: int,
    gateway_candidate_max_distance_m: float | None,
):
    ground_stations = tuple(
        sorted(
            (
                station
                for autonomous_system in physical_world.autonomous_systems
                for station in autonomous_system.ground_stations
            ),
            key=lambda station: _node_key(station.node_id),
        )
    )
    try:
        zones = physical_world.get_zone_layout().zones
    except RuntimeError:
        zones = ()
    candidates: dict[str, tuple[NodeId, ...]] = {}
    for zone in zones:
        latitude_deg, longitude_deg = zone.representative_point
        ranked = sorted(
            (
                (
                    _great_circle_distance_m(
                        latitude_deg, longitude_deg, station
                    ),
                    _node_key(station.node_id),
                    station.node_id,
                )
                for station in ground_stations
            ),
            key=lambda item: (item[0], item[1]),
        )
        if gateway_candidate_max_distance_m is not None:
            ranked = [
                item
                for item in ranked
                if item[0] <= gateway_candidate_max_distance_m
            ]
        candidates[zone.zone_id] = tuple(
            item[2] for item in ranked[:gateway_candidate_count]
        )
    return MappingProxyType(dict(sorted(candidates.items())))


def _great_circle_distance_m(
    latitude_deg: float,
    longitude_deg: float,
    station: PhysicalGroundStation,
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
