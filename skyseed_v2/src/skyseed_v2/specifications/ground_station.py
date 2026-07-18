"""Immutable GroundStation construction specifications."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite

from skyseed_v2.identifier import validate_identifier

from ._validation import _text
from .network import InternetExchangeAttachmentSpecification


@dataclass(frozen=True, slots=True)
class GroundStationSpecification:
    """Describe static GroundStation construction and IX attachment input.

    Layer: immutable input specification. Loader or programmatic callers create
    it; the Applier calls `create_ground_station`. It is not a GroundStation,
    Simulator input or runtime service and is public from specifications.
    """

    display_name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float | None
    operator: str
    station_type: str
    ground_station_id: str | None = None
    identity_key: str | None = None
    ix_attachments: tuple[InternetExchangeAttachmentSpecification, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "display_name", _text(self.display_name, "display_name")
        )
        object.__setattr__(self, "operator", _text(self.operator, "operator"))
        object.__setattr__(
            self, "station_type", _text(self.station_type, "station_type")
        )
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

        has_explicit_id = self.ground_station_id is not None
        has_identity_key = self.identity_key is not None
        if has_explicit_id == has_identity_key:
            raise ValueError(
                "specify exactly one of ground_station_id or identity_key"
            )
        if has_explicit_id:
            object.__setattr__(
                self,
                "ground_station_id",
                validate_identifier(
                    self.ground_station_id, "ground_station_id"
                ),
            )
        else:
            object.__setattr__(
                self, "identity_key", _text(self.identity_key, "identity_key")
            )

        attachments = tuple(self.ix_attachments)
        if any(
            not isinstance(item, InternetExchangeAttachmentSpecification)
            for item in attachments
        ):
            raise TypeError(
                "ix_attachments must contain "
                "InternetExchangeAttachmentSpecification values"
            )
        if len({item.ix_id for item in attachments}) != len(attachments):
            raise ValueError("ix_attachments contains a duplicate ix_id")
        object.__setattr__(
            self,
            "ix_attachments",
            tuple(sorted(attachments, key=lambda item: item.ix_id)),
        )


def ground_station_id_for(specification: GroundStationSpecification) -> str:
    """Resolve one v2-native stable ID without using input order or SEED state."""

    if not isinstance(specification, GroundStationSpecification):
        raise TypeError("specification must be a GroundStationSpecification")
    if specification.ground_station_id is not None:
        return specification.ground_station_id
    assert specification.identity_key is not None
    digest = sha256(specification.identity_key.encode("utf-8")).hexdigest()[:20]
    return f"ground-station-{digest}"
