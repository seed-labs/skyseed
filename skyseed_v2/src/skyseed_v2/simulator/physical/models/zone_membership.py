"""Replaceable Zone membership contract and deterministic default algorithm."""
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING, Protocol, runtime_checkable
if TYPE_CHECKING:
    from ..world.zone import Zone, LatitudeLongitudeRectangle

_DISTANCE_TIE_TOLERANCE_RADIANS = 1e-12

@runtime_checkable
class ZoneMembershipModel(Protocol):
    """Define the narrow public algorithm contract for Zone membership.

    Layer: Simulator algorithm extension contract. ZoneLayout invokes it with
    owned Zones and validates its output. It is not a Zone owner, entity,
    configuration object, runtime registry or service assignment model.
    """

    def find_zones(
        self,
        zones: tuple[Zone, ...],
        latitude_deg: float,
        longitude_deg: float,
    ) -> Iterable[Zone]:
        """Return physical matches for the supplied owned Zones."""

    def resolve_zone(
        self,
        matches: tuple[Zone, ...],
        latitude_deg: float,
        longitude_deg: float,
    ) -> Zone:
        """Return one authoritative Zone from non-empty matches."""


@dataclass(frozen=True, slots=True)
class DefaultZoneMembershipModel:
    """Implement deterministic physical and authoritative Zone selection.

    Layer: stateless Simulator membership algorithm. A public ZoneLayout keeps
    the active instance and passes owned Zones into it. It is not a Zone owner,
    service assignment, position source or planning component.
    """

    def describe_configuration(self):
        return {}

    def find_zones(
        self,
        zones: tuple[Zone, ...],
        latitude_deg: float,
        longitude_deg: float,
    ) -> tuple[Zone, ...]:
        return tuple(
            sorted(
                (
                    zone
                    for zone in zones
                    if zone.contains(latitude_deg, longitude_deg)
                ),
                key=lambda zone: zone.zone_id,
            )
        )

    def resolve_zone(
        self,
        matches: tuple[Zone, ...],
        latitude_deg: float,
        longitude_deg: float,
    ) -> Zone:
        if not matches:
            raise LookupError("no Zone contains the coordinate")
        highest_priority = max(zone.priority for zone in matches)
        candidates = tuple(
            zone for zone in matches if zone.priority == highest_priority
        )
        distances = {
            zone.zone_id: _central_angle_to_center(
                latitude_deg, longitude_deg, zone.geometry
            )
            for zone in candidates
        }
        minimum_distance = min(distances.values())
        tied = (
            zone
            for zone in candidates
            if distances[zone.zone_id]
            <= minimum_distance + _DISTANCE_TIE_TOLERANCE_RADIANS
        )
        return min(tied, key=lambda zone: zone.zone_id)


def _central_angle_to_center(
    latitude_deg: float,
    longitude_deg: float,
    geometry: LatitudeLongitudeRectangle,
) -> float:
    center_latitude_deg, center_longitude_deg = geometry.representative_center
    latitude = radians(latitude_deg)
    center_latitude = radians(center_latitude_deg)
    delta_latitude = center_latitude - latitude
    delta_longitude = radians(
        (center_longitude_deg - longitude_deg + 180.0) % 360.0 - 180.0
    )
    haversine = sin(delta_latitude / 2.0) ** 2 + (
        cos(latitude)
        * cos(center_latitude)
        * sin(delta_longitude / 2.0) ** 2
    )
    return 2.0 * asin(sqrt(min(1.0, max(0.0, haversine))))


__all__ = ["ZoneMembershipModel", "DefaultZoneMembershipModel"]
