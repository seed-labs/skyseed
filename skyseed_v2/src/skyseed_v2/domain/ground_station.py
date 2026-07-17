"""Static GroundStation with first-class SEED Router capability."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from seedemu.core import Router
from seedemu.core.enums import NodeRole

from skyseed_v2.domain.validation import validate_identifier


GROUND_STATION_REGISTRY_TYPE = "rnode"
GROUND_STATION_ROLE_LABEL = "GroundStation"


@dataclass(frozen=True, slots=True)
class GroundStationLocation:
    """Geodetic location fixed for one static Emulator run."""

    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None = None

    def __post_init__(self) -> None:
        latitude = float(self.latitude_deg)
        longitude = float(self.longitude_deg)
        elevation = (
            None if self.elevation_m is None else float(self.elevation_m)
        )
        if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        if not isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")
        if elevation is not None and not isfinite(elevation):
            raise ValueError("elevation_m must be finite when provided")
        object.__setattr__(self, "latitude_deg", latitude)
        object.__setattr__(self, "longitude_deg", longitude)
        object.__setattr__(self, "elevation_m", elevation)


class GroundStation(Router):
    """The one user-visible GroundStation and its SEED Router capability."""

    def __init__(
        self,
        *,
        ground_station_id: str,
        asn: int,
        location: GroundStationLocation,
        station_type: str,
        operator: str,
        display_name: str | None = None,
        service_config: Mapping[str, str] | None = None,
        routing_backend: str = "bird",
    ) -> None:
        self._ground_station_id = validate_identifier(
            ground_station_id, "ground_station_id"
        )
        if not isinstance(location, GroundStationLocation):
            raise TypeError("location must be a GroundStationLocation")
        self._location = location
        self._station_type = self._validate_text(station_type, "station_type")
        self._operator = self._validate_text(operator, "operator")
        copied_config = dict(service_config or {})
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in copied_config.items()
        ):
            raise TypeError("service_config keys and values must be strings")
        self._service_config = MappingProxyType(copied_config)

        if not isinstance(asn, int) or asn <= 0:
            raise ValueError("asn must be a positive integer")
        super().__init__(
            name=self.ground_station_id,
            role=NodeRole.Router,
            asn=asn,
            routingBackend=routing_backend,
        )
        self.setDisplayName(display_name or self.ground_station_id)
        self.setGeo(location.latitude_deg, location.longitude_deg)

    @staticmethod
    def _validate_text(value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()

    @property
    def ground_station_id(self) -> str:
        return self._ground_station_id

    @property
    def location(self) -> GroundStationLocation:
        return self._location

    @property
    def station_type(self) -> str:
        return self._station_type

    @property
    def operator(self) -> str:
        return self._operator

    @property
    def service_config(self) -> Mapping[str, str]:
        return self._service_config

    @property
    def registry_type(self) -> str:
        """Stable primary Registry identity independent of border state."""

        return GROUND_STATION_REGISTRY_TYPE
