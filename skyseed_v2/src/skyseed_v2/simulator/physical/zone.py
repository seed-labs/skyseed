"""Define PhysicalWorld-owned Zone geometry, layout and membership rules."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from math import asin, atan2, cos, isfinite, pi, radians, sin, sqrt
from numbers import Real
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from skyseed_v2.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition,
    ZoneLayoutDefinition,
)
from skyseed_v2.shared.identifier import validate_identifier


_DISTANCE_TIE_TOLERANCE_RADIANS = 1e-12


def _finite_coordinate(value: Real, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a finite number")
    converted = float(value)
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted


def _validated_coordinates(
    latitude_deg: Real, longitude_deg: Real
) -> tuple[float, float]:
    latitude = _finite_coordinate(latitude_deg, "latitude_deg")
    longitude = _finite_coordinate(longitude_deg, "longitude_deg")
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude_deg must be between -90 and 90")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude_deg must be between -180 and 180")
    return latitude, _canonical_longitude(longitude)


def _canonical_longitude(longitude_deg: float) -> float:
    canonical = (longitude_deg + 180.0) % 360.0 - 180.0
    return 0.0 if canonical == 0.0 else canonical


@dataclass(frozen=True, slots=True)
class LatitudeLongitudeRectangle:
    """Represent one canonical static latitude/longitude rectangle.

    Layer: immutable Simulator Zone geometry. PhysicalWorld materialization creates
    it and one public Zone owns it. It is not a generic geometry interface,
    coordinate value type, Earth model or dynamic position service.
    """

    south_latitude_deg: float
    north_latitude_deg: float
    west_longitude_deg: float
    east_longitude_deg: float

    def __post_init__(self) -> None:
        definition = LatitudeLongitudeRectangleDefinition(
            south_latitude_deg=self.south_latitude_deg,
            north_latitude_deg=self.north_latitude_deg,
            west_longitude_deg=self.west_longitude_deg,
            east_longitude_deg=self.east_longitude_deg,
        )
        object.__setattr__(
            self, "south_latitude_deg", definition.south_latitude_deg
        )
        object.__setattr__(
            self, "north_latitude_deg", definition.north_latitude_deg
        )
        object.__setattr__(
            self, "west_longitude_deg", definition.west_longitude_deg
        )
        object.__setattr__(
            self, "east_longitude_deg", definition.east_longitude_deg
        )

    def contains(self, latitude_deg: Real, longitude_deg: Real) -> bool:
        latitude, longitude = _validated_coordinates(
            latitude_deg, longitude_deg
        )
        if not self.south_latitude_deg <= latitude <= self.north_latitude_deg:
            return False
        if latitude in (-90.0, 90.0):
            return True
        if self.west_longitude_deg < self.east_longitude_deg:
            return (
                self.west_longitude_deg
                <= longitude
                <= self.east_longitude_deg
            )
        return (
            longitude >= self.west_longitude_deg
            or longitude <= self.east_longitude_deg
        )

    @property
    def representative_center(self) -> tuple[float, float]:
        latitude = (
            self.south_latitude_deg + self.north_latitude_deg
        ) / 2.0
        longitude_span = (
            self.east_longitude_deg - self.west_longitude_deg
        ) % 360.0
        longitude = _canonical_longitude(
            self.west_longitude_deg + longitude_span / 2.0
        )
        return latitude, longitude


@dataclass(frozen=True, slots=True)
class SphericalCap:
    """Represent one immutable spherical surface range.

    Layer: Simulator geographic query value. Connection models create it and
    ZoneLayout consumes it for coarse static-geometry filtering. It is not a
    service assignment, Earth model or Position and is public from
    ``skyseed_v2.simulator``.
    """

    center: tuple[float, float]
    angular_radius_deg: float

    def __post_init__(self) -> None:
        if not isinstance(self.center, tuple) or len(self.center) != 2:
            raise TypeError("center must be a (latitude_deg, longitude_deg) tuple")
        center = _validated_coordinates(*self.center)
        radius = _finite_coordinate(
            self.angular_radius_deg, "angular_radius_deg"
        )
        if not 0.0 <= radius <= 180.0:
            raise ValueError("angular_radius_deg must be between 0 and 180")
        object.__setattr__(self, "center", center)
        object.__setattr__(self, "angular_radius_deg", radius)


@dataclass(frozen=True, slots=True)
class Zone:
    """Represent one Simulator-owned immutable static Zone.

    Layer: immutable Simulator domain entity. ZoneLayout owns this public value
    and delegates geometry membership to it. It is not a membership algorithm,
    service-satellite assignment, runtime state or planning object.
    """

    zone_id: str
    priority: int
    geometry: LatitudeLongitudeRectangle

    def __post_init__(self) -> None:
        validate_identifier(self.zone_id, "zone_id")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if not isinstance(self.geometry, LatitudeLongitudeRectangle):
            raise TypeError("geometry must be a LatitudeLongitudeRectangle")

    def contains(self, latitude_deg: Real, longitude_deg: Real) -> bool:
        return self.geometry.contains(latitude_deg, longitude_deg)

    @property
    def representative_point(self) -> tuple[float, float]:
        return self.geometry.representative_center


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


@dataclass(frozen=True, slots=True, init=False)
class ZoneLayout:
    """Own immutable Zones and mediate all static Zone membership queries.

    Layer: Simulator-owned static aggregate and query owner. PhysicalWorld
    materialization creates this public object and delegates facade calls to it.
    It is not a Definition, dynamic position store or mutable Model registry.
    """

    _zones: tuple[Zone, ...]
    _zones_by_id: Mapping[str, Zone] = field(
        repr=False, compare=False, hash=False
    )
    _membership_model: ZoneMembershipModel = field(
        repr=False, compare=False, hash=False
    )
    _allow_overlap: bool

    def __init__(self) -> None:
        raise TypeError("ZoneLayout is created by PhysicalWorld materialization")

    @classmethod
    def _materialize(
        cls,
        definition: ZoneLayoutDefinition,
        membership_model: ZoneMembershipModel | None = None,
    ) -> ZoneLayout:
        if not isinstance(definition, ZoneLayoutDefinition):
            raise TypeError(
                "zone_layout_definition must be a ZoneLayoutDefinition"
            )
        model: ZoneMembershipModel = (
            DefaultZoneMembershipModel()
            if membership_model is None
            else membership_model
        )
        if not isinstance(model, ZoneMembershipModel):
            raise TypeError(
                "zone_membership_model must implement ZoneMembershipModel"
            )
        zones = tuple(
            Zone(
                zone_id=zone_definition.zone_id,
                priority=zone_definition.priority,
                geometry=LatitudeLongitudeRectangle(
                    south_latitude_deg=(
                        zone_definition.geometry.south_latitude_deg
                    ),
                    north_latitude_deg=(
                        zone_definition.geometry.north_latitude_deg
                    ),
                    west_longitude_deg=(
                        zone_definition.geometry.west_longitude_deg
                    ),
                    east_longitude_deg=(
                        zone_definition.geometry.east_longitude_deg
                    ),
                ),
            )
            for zone_definition in definition.zones
        )
        instance = object.__new__(cls)
        object.__setattr__(instance, "_zones", zones)
        object.__setattr__(
            instance,
            "_zones_by_id",
            MappingProxyType({zone.zone_id: zone for zone in zones}),
        )
        object.__setattr__(instance, "_membership_model", model)
        object.__setattr__(instance, "_allow_overlap", definition.allow_overlap)
        return instance

    @property
    def zones(self) -> tuple[Zone, ...]:
        return self._zones

    @property
    def allow_overlap(self) -> bool:
        return self._allow_overlap

    def get_membership_model(self) -> ZoneMembershipModel:
        """Return the exact fixed membership Model owned by this layout."""

        return self._membership_model

    def get_zone(self, zone_id: str) -> Zone:
        if not isinstance(zone_id, str):
            raise TypeError("zone_id must be a string")
        try:
            return self._zones_by_id[zone_id]
        except KeyError as error:
            raise KeyError(f"Zone not found: {zone_id}") from error

    def find_zones(
        self, latitude_deg: Real, longitude_deg: Real
    ) -> tuple[Zone, ...]:
        latitude, longitude = _validated_coordinates(
            latitude_deg, longitude_deg
        )
        try:
            raw_matches = tuple(
                self._membership_model.find_zones(
                    self._zones, latitude, longitude
                )
            )
        except TypeError as error:
            raise TypeError(
                "ZoneMembershipModel.find_zones must return an iterable of Zones"
            ) from error
        owned_by_identity = {id(zone): zone for zone in self._zones}
        seen: set[int] = set()
        matches: list[Zone] = []
        for zone in raw_matches:
            identity = id(zone)
            if (
                identity not in owned_by_identity
                or owned_by_identity[identity] is not zone
            ):
                raise ValueError(
                    "ZoneMembershipModel returned a Zone not owned by ZoneLayout"
                )
            if identity in seen:
                raise ValueError("ZoneMembershipModel returned a duplicate Zone")
            seen.add(identity)
            matches.append(zone)
        return tuple(sorted(matches, key=lambda zone: zone.zone_id))

    def resolve_zone(self, latitude_deg: Real, longitude_deg: Real) -> Zone:
        latitude, longitude = _validated_coordinates(
            latitude_deg, longitude_deg
        )
        matches = self.find_zones(latitude, longitude)
        if not matches:
            raise LookupError("no Zone contains the coordinate")
        winner = self._membership_model.resolve_zone(
            matches, latitude, longitude
        )
        if not any(winner is match for match in matches):
            raise ValueError(
                "ZoneMembershipModel authoritative result must be a physical match"
            )
        return winner

    def find_intersecting_zones(
        self,
        spherical_cap: SphericalCap,
    ) -> tuple[Zone, ...]:
        if not isinstance(spherical_cap, SphericalCap):
            raise TypeError("spherical_cap must be a SphericalCap")
        radius = radians(spherical_cap.angular_radius_deg)
        return tuple(
            zone
            for zone in self._zones
            if _minimum_angle_to_rectangle(
                spherical_cap.center[0],
                spherical_cap.center[1],
                zone.geometry,
            )
            <= radius + _DISTANCE_TIE_TOLERANCE_RADIANS
        )


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
        _canonical_longitude(center_longitude_deg - longitude_deg)
    )
    haversine = sin(delta_latitude / 2.0) ** 2 + (
        cos(latitude)
        * cos(center_latitude)
        * sin(delta_longitude / 2.0) ** 2
    )
    return 2.0 * asin(sqrt(min(1.0, max(0.0, haversine))))


def _minimum_angle_to_rectangle(
    latitude_deg: float,
    longitude_deg: float,
    geometry: LatitudeLongitudeRectangle,
) -> float:
    if geometry.contains(latitude_deg, longitude_deg):
        return 0.0

    latitude = radians(latitude_deg)
    longitude = radians(longitude_deg)
    south = radians(geometry.south_latitude_deg)
    north = radians(geometry.north_latitude_deg)
    longitude_intervals = _unwrapped_longitude_intervals(
        longitude,
        radians(geometry.west_longitude_deg),
        radians(geometry.east_longitude_deg),
    )
    candidates: list[float] = []

    for west, east in longitude_intervals:
        nearest_longitude = min(max(longitude, west), east)
        candidates.extend(
            (
                _central_angle_radians(
                    latitude, longitude, south, nearest_longitude
                ),
                _central_angle_radians(
                    latitude, longitude, north, nearest_longitude
                ),
            )
        )
        for boundary_longitude in (west, east):
            delta_longitude = boundary_longitude - longitude
            optimum_latitude = atan2(
                sin(latitude),
                cos(latitude) * cos(delta_longitude),
            )
            optimum_latitude = min(max(optimum_latitude, south), north)
            candidates.append(
                _central_angle_radians(
                    latitude,
                    longitude,
                    optimum_latitude,
                    boundary_longitude,
                )
            )
    return min(candidates)


def _unwrapped_longitude_intervals(
    reference: float,
    west: float,
    east: float,
) -> tuple[tuple[float, float], ...]:
    span = (east - west) % (2.0 * pi)
    if span == 0.0:
        span = 2.0 * pi
    intervals = []
    for offset in (-2.0 * pi, 0.0, 2.0 * pi):
        start = west + offset
        intervals.append((start, start + span))
    return tuple(intervals)


def _central_angle_radians(
    first_latitude: float,
    first_longitude: float,
    second_latitude: float,
    second_longitude: float,
) -> float:
    delta_latitude = second_latitude - first_latitude
    delta_longitude = second_longitude - first_longitude
    haversine = sin(delta_latitude / 2.0) ** 2 + (
        cos(first_latitude)
        * cos(second_latitude)
        * sin(delta_longitude / 2.0) ** 2
    )
    return 2.0 * asin(sqrt(min(1.0, max(0.0, haversine))))


__all__ = [
    "DefaultZoneMembershipModel",
    "LatitudeLongitudeRectangle",
    "SphericalCap",
    "Zone",
    "ZoneLayout",
    "ZoneMembershipModel",
]
