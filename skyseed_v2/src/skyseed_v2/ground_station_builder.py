"""Application builder for static GroundStation specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from skyseed_v2.domain import GroundStation, GroundStationLocation
from skyseed_v2.seed.autonomous_system import SkyseedAutonomousSystem
from skyseed_v2.specifications import (
    GroundStationSpecification,
    ground_station_id_for,
)


@dataclass(frozen=True, slots=True)
class GroundStationBuildResult:
    """GroundStations created from one immutable specification snapshot."""

    ground_stations: tuple[GroundStation, ...]


class GroundStationBuilder:
    """Create GroundStations through their owning Skyseed AS."""

    def build(
        self,
        autonomous_system: SkyseedAutonomousSystem,
        specifications: GroundStationSpecification
        | Iterable[GroundStationSpecification],
    ) -> GroundStationBuildResult:
        if not isinstance(autonomous_system, SkyseedAutonomousSystem):
            raise TypeError("autonomous_system must be a SkyseedAutonomousSystem")
        if isinstance(specifications, GroundStationSpecification):
            snapshot = (specifications,)
        else:
            try:
                snapshot = tuple(specifications)
            except TypeError as error:
                raise TypeError(
                    "specifications must be a GroundStationSpecification or iterable"
                ) from error
        if not snapshot:
            raise ValueError("specifications must not be empty")
        if any(
            not isinstance(item, GroundStationSpecification) for item in snapshot
        ):
            raise TypeError(
                "specifications must contain GroundStationSpecification values"
            )

        ordered = tuple(sorted(snapshot, key=ground_station_id_for))
        candidate_ids = [ground_station_id_for(item) for item in ordered]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("specifications resolve to a duplicate ground_station_id")
        existing_ids = set(autonomous_system.getRouters())
        duplicates = existing_ids & set(candidate_ids)
        if duplicates:
            duplicate = min(duplicates)
            raise ValueError(f"ground_station_id {duplicate!r} already exists")

        created = tuple(
            autonomous_system.create_ground_station(
                ground_station_id,
                location=GroundStationLocation(
                    latitude_deg=specification.latitude_deg,
                    longitude_deg=specification.longitude_deg,
                    elevation_m=specification.elevation_m,
                ),
                station_type=specification.station_type,
                operator=specification.operator,
                display_name=specification.display_name,
            )
            for specification, ground_station_id in zip(ordered, candidate_ids)
        )
        return GroundStationBuildResult(ground_stations=created)
