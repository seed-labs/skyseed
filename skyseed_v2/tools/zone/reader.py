"""Read formal Topology/Zone APIs into a detached Tool-owned snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from skyseed_v2.simulator import Topology

from .probe_read_plan import (
    RectangleDisplaySnapshot,
    ZoneDisplaySnapshot,
    select_zone_probes,
)


@dataclass(frozen=True, slots=True)
class ZoneRead:
    """Hold detached values returned by one exact Topology/Zone read."""

    snapshot: dict[str, Any]


def read_zones(topology: Topology) -> ZoneRead:
    """Call all formal membership APIs and retain no domain object."""

    if not isinstance(topology, Topology):
        raise TypeError("topology must be a Topology")
    ground_stations = [
        {
            "asn": autonomous_system.asn,
            "ground_station_id": ground_station.ground_station_id,
            "node_id": ground_station.node_id.value,
            "latitude_deg": ground_station.latitude_deg,
            "longitude_deg": ground_station.longitude_deg,
            "elevation_m": ground_station.elevation_m,
            "station_type": ground_station.station_type,
            "operator": ground_station.operator,
        }
        for autonomous_system in topology.autonomous_systems
        for ground_station in autonomous_system.ground_stations
    ]
    try:
        layout = topology.get_zone_layout()
    except RuntimeError:
        return ZoneRead(
            {
                "format_version": 1,
                "tool": "zone",
                "status": "PASS",
                "zone_layout_configured": False,
                "model_type": "unknown",
                "model_is_default": False,
                "allow_overlap": None,
                "zones": [],
                "probes": [],
                "ground_stations": ground_stations,
                "counts": {},
            }
        )

    model_type = type(layout.get_membership_model()).__name__
    owned_by_id = {zone.zone_id: zone for zone in layout.zones}
    owned_identities = {id(zone): zone for zone in layout.zones}
    display_zones = tuple(
        ZoneDisplaySnapshot(
            zone_id=zone.zone_id,
            geometry=RectangleDisplaySnapshot(
                south_latitude_deg=zone.geometry.south_latitude_deg,
                north_latitude_deg=zone.geometry.north_latitude_deg,
                west_longitude_deg=zone.geometry.west_longitude_deg,
                east_longitude_deg=zone.geometry.east_longitude_deg,
            ),
        )
        for zone in layout.zones
    )
    zones = [
        {
            "zone_id": zone.zone_id,
            "priority": zone.priority,
            "geometry": {
                "south_latitude_deg": zone.geometry.south_latitude_deg,
                "north_latitude_deg": zone.geometry.north_latitude_deg,
                "west_longitude_deg": zone.geometry.west_longitude_deg,
                "east_longitude_deg": zone.geometry.east_longitude_deg,
                "wrapped": (
                    zone.geometry.west_longitude_deg
                    > zone.geometry.east_longitude_deg
                ),
                "representative_center": list(
                    zone.geometry.representative_center
                ),
            },
        }
        for zone in layout.zones
    ]

    evaluated = []
    for index, probe in enumerate(
        select_zone_probes(display_zones),
        start=1,
    ):
        latitude = float(probe["latitude_deg"])
        longitude = float(probe["longitude_deg"])
        matches = ()
        winner = None
        find_error: BaseException | None = None
        resolve_error: BaseException | None = None
        try:
            matches = tuple(topology.find_zones(latitude, longitude))
        except Exception as error:
            find_error = error
        try:
            winner = topology.resolve_zone(latitude, longitude)
        except Exception as error:
            resolve_error = error

        reasons: list[str] = []
        if find_error is not None:
            reasons.append(
                f"Topology.find_zones raised {_error_text(find_error)}"
            )
        if resolve_error is not None:
            reasons.append(
                f"Topology.resolve_zone raised {_error_text(resolve_error)}"
            )
        origins = probe["origins"]
        assert isinstance(origins, list)
        source_ids = sorted(
            {
                source_id
                for origin in origins
                for source_id in origin["source_zone_ids"]
            }
        )
        for source_id in source_ids:
            source = owned_by_id.get(source_id)
            if source is None:
                reasons.append(
                    f"probe source Zone is not layout-owned: {source_id}"
                )
            elif not any(match is source for match in matches):
                reasons.append(
                    f"source Zone is absent from physical matches: {source_id}"
                )
        for match in matches:
            if (
                id(match) not in owned_identities
                or owned_identities[id(match)] is not match
            ):
                reasons.append(
                    "Topology.find_zones returned a Zone outside current "
                    "ZoneLayout"
                )
        if winner is not None and not any(
            winner is match for match in matches
        ):
            reasons.append(
                "Topology.resolve_zone winner is not identical to a "
                "physical match"
            )
        if winner is None and resolve_error is None:
            reasons.append(
                "Topology.resolve_zone returned no authoritative Zone"
            )

        evaluated.append(
            {
                "probe_id": f"probe-{index:04d}",
                "latitude_deg": latitude,
                "longitude_deg": longitude,
                "origins": origins,
                "source_zone_ids": source_ids,
                "categories": sorted(
                    {str(origin["category"]) for origin in origins}
                ),
                "physical_matches": [
                    {
                        "zone_id": match.zone_id,
                        "priority": match.priority,
                        "representative_center": list(
                            match.geometry.representative_center
                        ),
                    }
                    for match in matches
                ],
                "authoritative_result": (
                    None
                    if winner is None
                    else {
                        "zone_id": winner.zone_id,
                        "priority": winner.priority,
                        "representative_center": list(
                            winner.geometry.representative_center
                        ),
                    }
                ),
                "invariant_status": (
                    "PASS" if not reasons else "FAIL"
                ),
                "resolution_status": (
                    "OBSERVED" if winner is not None else "FAIL"
                ),
                "failure_reasons": reasons,
                "formal_call_chain": (
                    "Topology.find_zones / resolve_zone → ZoneLayout → "
                    "ZoneMembershipModel → Zone.contains → "
                    "LatitudeLongitudeRectangle.contains"
                ),
            }
        )

    return ZoneRead(
        {
            "format_version": 1,
            "tool": "zone",
            "status": "PASS",
            "zone_layout_configured": True,
            "model_type": model_type,
            "model_is_default": (
                model_type == "DefaultZoneMembershipModel"
            ),
            "allow_overlap": layout.allow_overlap,
            "zones": zones,
            "probes": evaluated,
            "ground_stations": ground_stations,
            "counts": {},
        }
    )


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


__all__ = ["ZoneRead", "read_zones"]
