"""Define immutable physical-link requests, facts, and their Model boundary."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from skyseed_v2.shared import NodeId
from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.shared.time import normalize_utc_datetime


_NodePair = tuple[NodeId, NodeId]


@dataclass(frozen=True, slots=True)
class PhysicalLinkRequest:
    """Bound the physical relationships evaluated for one PhysicalPositionSnapshot.

    Layer: immutable physical query value. Network Planner composition creates
    it and a ``PhysicalLinkModel`` consumes it; an injected ISL role supplies
    only the bounded inter-satellite pair scope. It is not a hardware envelope,
    Connection selection, acquisition state, Route demand, or runtime state.
    It is public from ``skyseed_v2.simulator.physical`` so planners can bound
    physical work without owning geometry.
    """

    inter_satellite_pairs: tuple[_NodePair, ...] = ()
    include_ground_station_access: bool = True
    include_zone_service: bool = True
    ground_station_candidate_limit: int | None = None
    ground_station_minimum_contact_duration: timedelta = timedelta(0)
    inter_satellite_minimum_contact_duration: timedelta = timedelta(0)
    retained_ground_station_pairs: tuple[_NodePair, ...] = ()
    cross_plane_neighbor_count: int | None = None
    retained_inter_satellite_pairs: tuple[_NodePair, ...] = ()

    def __post_init__(self) -> None:
        for name in ("ground_station_minimum_contact_duration", "inter_satellite_minimum_contact_duration"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value < timedelta(0):
                raise ValueError(f"{name} must be a non-negative timedelta")
        for name in ("ground_station_candidate_limit", "cross_plane_neighbor_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be positive or None")
        for name, first_kind in (("retained_ground_station_pairs", "ground-station"),
                                 ("retained_inter_satellite_pairs", "satellite")):
            values = tuple(getattr(self, name))
            for first, second in values:
                _require_node_kind(first, first_kind, name)
                _require_node_kind(second, "satellite", name)
                if first == second:
                    raise ValueError("retained pair endpoints must be distinct")
            object.__setattr__(self, name, tuple(sorted(set(values), key=lambda p: (_node_key(p[0]), _node_key(p[1])))))
        if not isinstance(self.include_ground_station_access, bool):
            raise TypeError("include_ground_station_access must be a bool")
        if not isinstance(self.include_zone_service, bool):
            raise TypeError("include_zone_service must be a bool")
        try:
            raw_pairs = tuple(self.inter_satellite_pairs)
        except TypeError as error:
            raise TypeError(
                "inter_satellite_pairs must be an iterable of NodeId pairs"
            ) from error

        pairs: set[_NodePair] = set()
        for pair in raw_pairs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError(
                    "inter_satellite_pairs must contain two-item tuples"
                )
            first, second = pair
            _require_node_kind(first, "satellite", "ISL endpoint")
            _require_node_kind(second, "satellite", "ISL endpoint")
            if first == second:
                raise ValueError("ISL request endpoints must be distinct")
            pairs.add(_canonical_pair(first, second))
        object.__setattr__(
            self,
            "inter_satellite_pairs",
            tuple(
                sorted(
                    pairs,
                    key=lambda value: (
                        _node_key(value[0]),
                        _node_key(value[1]),
                    ),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class PhysicalContactWindow:
    """A proven usable physical interval, not a selected connection lifetime.

    Layer: public immutable Simulator physical evidence value.
    The physical link Model creates this immutable value for Planner admission.
    ``end_timestamp=None`` means an analytical all-future proof; a finite end
    is a prediction coverage bound, never an asserted link-loss event. Absence
    of a window on a candidate means future feasibility is unknown.
    """

    start_timestamp: datetime
    end_timestamp: datetime | None

    def __post_init__(self) -> None:
        start = normalize_utc_datetime(self.start_timestamp, "start_timestamp")
        end = (None if self.end_timestamp is None else
               normalize_utc_datetime(self.end_timestamp, "end_timestamp"))
        if end is not None and end < start:
            raise ValueError("contact end must not precede its start")
        object.__setattr__(self, "start_timestamp", start)
        object.__setattr__(self, "end_timestamp", end)

    def covers(self, start: datetime, end: datetime) -> bool:
        """Return whether the complete requested interval has physical proof."""
        return (self.start_timestamp <= start <= end and
                (self.end_timestamp is None or end <= self.end_timestamp))


@dataclass(frozen=True, slots=True)
class InterSatelliteLinkCandidate:
    """Record one requested ISL relationship that is physically feasible.

    Layer: immutable physical fact. ``PhysicalLinkModel`` creates it and the
    Network Planner reads it. It is not selected/acquiring/operational state,
    a terminal identity, Route evidence, or a hardware-policy claim and is
    public from ``skyseed_v2.simulator.physical``.
    """

    endpoint_a: NodeId
    endpoint_b: NodeId
    same_plane: bool | None = None
    cyclic_plane_separation: int | None = None
    distance_m: float | None = None
    propagation_delay_us: int | None = None
    contact_window: PhysicalContactWindow | None = None

    def __post_init__(self) -> None:
        if self.contact_window is not None and not isinstance(
            self.contact_window, PhysicalContactWindow
        ):
            raise TypeError("contact_window must be PhysicalContactWindow or None")
        _require_node_kind(self.endpoint_a, "satellite", "endpoint_a")
        _require_node_kind(self.endpoint_b, "satellite", "endpoint_b")
        if self.endpoint_a == self.endpoint_b:
            raise ValueError("ISL candidate endpoints must be distinct")
        first, second = _canonical_pair(self.endpoint_a, self.endpoint_b)
        object.__setattr__(self, "endpoint_a", first)
        object.__setattr__(self, "endpoint_b", second)
        if self.same_plane is not None and not isinstance(self.same_plane, bool):
            raise TypeError("same_plane must be a bool or None")
        if self.cyclic_plane_separation is not None and (
            isinstance(self.cyclic_plane_separation, bool)
            or not isinstance(self.cyclic_plane_separation, int)
        ):
            raise TypeError("cyclic_plane_separation must be an integer or None")
        _normalize_measures(self)


@dataclass(frozen=True, slots=True)
class GroundStationAccessCandidate:
    """Record one physically feasible PhysicalGroundStation/PhysicalSatellite relationship.

    Layer: immutable physical fact. ``PhysicalLinkModel`` creates it and the
    Network Planner reads it. It is not an allocated access Connection,
    serving winner, acquisition state, or effective parameter and is public
    from ``skyseed_v2.simulator.physical``.
    """

    ground_station_id: NodeId
    satellite_id: NodeId
    distance_m: float | None = None
    elevation_deg: float | None = None
    propagation_delay_us: int | None = None
    contact_window: PhysicalContactWindow | None = None

    def __post_init__(self) -> None:
        if self.contact_window is not None and not isinstance(
            self.contact_window, PhysicalContactWindow
        ):
            raise TypeError("contact_window must be PhysicalContactWindow or None")
        _require_node_kind(
            self.ground_station_id,
            "ground-station",
            "ground_station_id",
        )
        _require_node_kind(self.satellite_id, "satellite", "satellite_id")
        _normalize_measures(self)


@dataclass(frozen=True, slots=True)
class ZoneServiceCandidate:
    """Record one physically feasible Zone/PhysicalSatellite relationship.

    Layer: immutable physical fact. ``PhysicalLinkModel`` creates it and the
    Network Planner reads it. It is not a selected service assignment, Route,
    Connection, or forwarding decision and is public from
    ``skyseed_v2.simulator.physical``.
    """

    zone_id: str
    satellite_id: NodeId
    distance_m: float | None = None
    elevation_deg: float | None = None
    propagation_delay_us: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "zone_id", validate_identifier(self.zone_id, "zone_id")
        )
        _require_node_kind(self.satellite_id, "satellite", "satellite_id")
        _normalize_measures(self)


@dataclass(frozen=True, slots=True)
class PhysicalLinkWorkCounts:
    """Describe bounded physical candidate work for one observation.

    Layer: immutable physical evidence. ``PhysicalLinkModel`` creates it and
    inspection/performance consumers read it. It is not selection evidence,
    lifecycle state, durable history, or planner policy and is public from
    ``skyseed_v2.simulator.physical``.
    """

    inter_satellite_pairs_requested: int = 0
    inter_satellite_pairs_evaluated: int = 0
    inter_satellite_pairs_feasible: int = 0
    ground_station_pairs_requested: int = 0
    ground_station_pairs_evaluated: int = 0
    ground_station_pairs_feasible: int = 0
    zone_service_pairs_requested: int = 0
    zone_service_pairs_evaluated: int = 0
    zone_service_pairs_feasible: int = 0
    ground_station_pairs_not_returned: int = 0
    inter_satellite_pairs_not_returned: int = 0

    def __post_init__(self) -> None:
        for name in ("ground_station_pairs_not_returned", "inter_satellite_pairs_not_returned"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        for prefix in (
            "inter_satellite_pairs",
            "ground_station_pairs",
            "zone_service_pairs",
        ):
            requested = getattr(self, f"{prefix}_requested")
            evaluated = getattr(self, f"{prefix}_evaluated")
            feasible = getattr(self, f"{prefix}_feasible")
            for field_name, value in (
                (f"{prefix}_requested", requested),
                (f"{prefix}_evaluated", evaluated),
                (f"{prefix}_feasible", feasible),
            ):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise ValueError(
                        f"{field_name} must be a non-negative integer"
                    )
            if feasible > evaluated or evaluated > requested:
                raise ValueError(
                    f"{prefix} counts must satisfy feasible <= evaluated "
                    "<= requested"
                )


@dataclass(frozen=True, slots=True)
class PhysicalLinkQueryResult:
    """Hold sparse physical-link facts for one exact PhysicalPositionSnapshot timestamp.

    Layer: immutable physical-domain observation. A ``PhysicalLinkModel``
    creates it from one caller-supplied ``PhysicalPositionSnapshot`` and the Network
    Planner consumes it. It is not selected/operational Connection state,
    acquisition state, a serving winner, effective parameters, Route, or
    forwarding state. It is public from ``skyseed_v2.simulator.physical`` as
    the exact physical-fact boundary consumed by planning.
    """

    timestamp: datetime
    inter_satellite_candidates: tuple[InterSatelliteLinkCandidate, ...] = ()
    ground_station_access_candidates: tuple[
        GroundStationAccessCandidate, ...
    ] = ()
    zone_service_candidates: tuple[ZoneServiceCandidate, ...] = ()
    work_counts: PhysicalLinkWorkCounts | None = None
    ground_station_search_complete: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timestamp",
            normalize_utc_datetime(self.timestamp, "timestamp"),
        )
        inter_satellite = _validated_candidates(
            self.inter_satellite_candidates,
            InterSatelliteLinkCandidate,
            "inter_satellite_candidates",
            lambda value: (
                _node_key(value.endpoint_a),
                _node_key(value.endpoint_b),
            ),
        )
        ground_station = _validated_candidates(
            self.ground_station_access_candidates,
            GroundStationAccessCandidate,
            "ground_station_access_candidates",
            lambda value: (
                _node_key(value.ground_station_id),
                _node_key(value.satellite_id),
            ),
        )
        zone = _validated_candidates(
            self.zone_service_candidates,
            ZoneServiceCandidate,
            "zone_service_candidates",
            lambda value: (value.zone_id, _node_key(value.satellite_id)),
        )
        if self.work_counts is not None and not isinstance(self.work_counts, PhysicalLinkWorkCounts):
            raise TypeError("work_counts must be PhysicalLinkWorkCounts or None")
        if self.ground_station_search_complete is not None and not isinstance(self.ground_station_search_complete, bool):
            raise TypeError("ground_station_search_complete must be bool or None")
        object.__setattr__(self, "inter_satellite_candidates", inter_satellite)
        object.__setattr__(
            self, "ground_station_access_candidates", ground_station
        )
        object.__setattr__(self, "zone_service_candidates", zone)


def _validated_candidates(
    values: Iterable[object],
    expected_type: type,
    field_name: str,
    key,
) -> tuple:
    try:
        copied = tuple(values)
    except TypeError as error:
        raise TypeError(f"{field_name} must be iterable") from error
    if any(not isinstance(value, expected_type) for value in copied):
        raise TypeError(f"{field_name} contains an invalid value")
    keyed = sorted(((key(value), value) for value in copied),
                   key=lambda item: item[0])
    identities = tuple(identity for identity, _ in keyed)
    if len(set(identities)) != len(identities):
        raise ValueError(f"{field_name} contains a duplicate relationship")
    return tuple(value for _, value in keyed)


def _canonical_pair(first: NodeId, second: NodeId) -> _NodePair:
    return (first, second) if _node_key(first) < _node_key(second) else (second, first)


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


def _require_node_kind(
    node_id: NodeId, expected_kind: str, field_name: str
) -> None:
    if not isinstance(node_id, NodeId):
        raise TypeError(f"{field_name} must be a NodeId")
    if node_id.entity_kind != expected_kind:
        raise ValueError(f"{field_name} must identify a {expected_kind}")


def _normalize_measures(candidate) -> None:
    # Measures describe the author's physical assumptions, not geometric admission.
    for name in ("distance_m", "elevation_deg"):
        value = getattr(candidate, name, None)
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite number or None")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be a finite number or None") from error
        if not isfinite(number):
            raise ValueError(f"{name} must be a finite number or None")
        object.__setattr__(candidate, name, number)
    delay = candidate.propagation_delay_us
    if delay is not None and (isinstance(delay, bool) or not isinstance(delay, int)):
        raise TypeError("propagation_delay_us must be an integer or None")


__all__ = [
    "GroundStationAccessCandidate",
    "InterSatelliteLinkCandidate",
    "PhysicalLinkQueryResult",
    "PhysicalLinkRequest",
    "PhysicalContactWindow",
    "PhysicalLinkWorkCounts",
    "ZoneServiceCandidate",
]
