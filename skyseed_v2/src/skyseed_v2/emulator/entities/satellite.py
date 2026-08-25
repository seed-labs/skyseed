"""Define a static Satellite entity with SEED Node capability."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from seedemu.core import ExtensionNode

from skyseed_v2.shared.identifier import validate_identifier

from .validation import _validate_angle


class Satellite(ExtensionNode):
    """Represent one Satellite and the same instance's SEED Node capability.

    Layer: approved Emulator entity plus SEED capability.
    `Orbit.create_satellite` creates it; its Orbit/AS, SEED lifecycle, Simulator
    input generation and the standard SEED compiler use it. It is not a Config,
    container or runtime topology state. It is root public because it is an
    approved user-visible entity.
    """

    def __init__(
        self,
        *,
        satellite_id: str,
        shell_id: str,
        orbit_id: str,
        orbit_index: int,
        mean_anomaly_deg: float,
        asn: int,
        payload: Mapping[str, str] | None = None,
        max_simultaneous_ground_station_accesses: int | None = None,
    ) -> None:
        self._initialize_static_state(
            satellite_id=satellite_id,
            shell_id=shell_id,
            orbit_id=orbit_id,
            orbit_index=orbit_index,
            mean_anomaly_deg=mean_anomaly_deg,
            payload=payload or {},
            max_simultaneous_ground_station_accesses=(
                max_simultaneous_ground_station_accesses
            ),
        )
        self._initialize_seed_node(asn)
        self._initialize_skyseed_identity()

    def _initialize_static_state(
        self,
        *,
        satellite_id: str,
        shell_id: str,
        orbit_id: str,
        orbit_index: int,
        mean_anomaly_deg: float,
        payload: Mapping[str, str],
        max_simultaneous_ground_station_accesses: int | None,
    ) -> None:
        self._satellite_id = validate_identifier(satellite_id, "satellite_id")
        self._shell_id = validate_identifier(shell_id, "shell_id")
        self._orbit_id = validate_identifier(orbit_id, "orbit_id")
        if not isinstance(orbit_index, int) or orbit_index < 0:
            raise ValueError("orbit_index must be a non-negative integer")
        copied_payload = dict(payload)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in copied_payload.items()
        ):
            raise TypeError("payload keys and values must be strings")

        self._orbit_index = orbit_index
        self._mean_anomaly_deg = _validate_angle(
            mean_anomaly_deg, "mean_anomaly_deg"
        )
        self._payload = MappingProxyType(copied_payload)
        if max_simultaneous_ground_station_accesses is not None and (
            not isinstance(max_simultaneous_ground_station_accesses, int)
            or isinstance(max_simultaneous_ground_station_accesses, bool)
            or max_simultaneous_ground_station_accesses <= 0
        ):
            raise ValueError(
                "max_simultaneous_ground_station_accesses must be a "
                "positive integer"
            )
        self._max_simultaneous_ground_station_accesses = (
            max_simultaneous_ground_station_accesses
        )

    def _initialize_seed_node(self, asn: int) -> None:
        if not isinstance(asn, int) or asn <= 0:
            raise ValueError("asn must be a positive integer")
        super().__init__(name=self.satellite_id, asn=asn)

    def _initialize_skyseed_identity(self) -> None:
        self.setDisplayName(self.satellite_id)
        self.setLabel("skyseed.entity_type", "Satellite")
        self.setLabel("skyseed.satellite_id", self.satellite_id)
        self.setLabel("skyseed.shell_id", self.shell_id)
        self.setLabel("skyseed.orbit_id", self.orbit_id)

    @property
    def satellite_id(self) -> str:
        return self._satellite_id

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def orbit_id(self) -> str:
        return self._orbit_id

    @property
    def orbit_index(self) -> int:
        return self._orbit_index

    @property
    def mean_anomaly_deg(self) -> float:
        return self._mean_anomaly_deg

    @property
    def payload(self) -> Mapping[str, str]:
        return self._payload

    @property
    def max_simultaneous_ground_station_accesses(self) -> int | None:
        return self._max_simultaneous_ground_station_accesses
