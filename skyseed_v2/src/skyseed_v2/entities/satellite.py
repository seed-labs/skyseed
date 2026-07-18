"""Static Satellite entity with first-class SEED Node capability."""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping

from seedemu.core import Node, Scope, ScopeTier, ScopeType

from skyseed_v2.identifier import validate_identifier

from .orbit_parameters import OrbitParameters
from .validation import _validate_angle


SATELLITE_REGISTRY_TYPE = "satnode"
SATELLITE_ROLE_LABEL = "Satellite"


class SatelliteRole(Enum):
    """Name the Satellite-only SEED integration role.

    Layer: integration value. Satellite creates and uses it; Compiler metadata
    observes it. It is not an Emulator entity or Simulator input and is public
    only from the entities package for integration inspection.
    """

    SATELLITE = SATELLITE_ROLE_LABEL


class Satellite(Node):
    """Represent one Satellite and the same instance's SEED Node capability.

    Layer: approved Emulator entity plus SEED capability.
    `Orbit.create_satellite` creates it; its Orbit/AS, SEED lifecycle, Simulator
    application conversion and Compiler use it. It is not a specification,
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
        orbit_parameters: OrbitParameters,
        mean_anomaly_deg: float,
        asn: int,
        payload: Mapping[str, str] | None = None,
    ) -> None:
        self._initialize_static_state(
            satellite_id=satellite_id,
            shell_id=shell_id,
            orbit_id=orbit_id,
            orbit_index=orbit_index,
            orbit_parameters=orbit_parameters,
            mean_anomaly_deg=mean_anomaly_deg,
            payload=payload or {},
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
        orbit_parameters: OrbitParameters,
        mean_anomaly_deg: float,
        payload: Mapping[str, str],
    ) -> None:
        self._satellite_id = validate_identifier(satellite_id, "satellite_id")
        self._shell_id = validate_identifier(shell_id, "shell_id")
        self._orbit_id = validate_identifier(orbit_id, "orbit_id")
        if not isinstance(orbit_index, int) or orbit_index < 0:
            raise ValueError("orbit_index must be a non-negative integer")
        if not isinstance(orbit_parameters, OrbitParameters):
            raise TypeError("orbit_parameters must be an OrbitParameters value")
        copied_payload = dict(payload)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in copied_payload.items()
        ):
            raise TypeError("payload keys and values must be strings")

        self._orbit_index = orbit_index
        self._orbit_parameters = orbit_parameters
        self._mean_anomaly_deg = _validate_angle(
            mean_anomaly_deg, "mean_anomaly_deg"
        )
        self._payload = MappingProxyType(copied_payload)

    def _initialize_seed_node(self, asn: int) -> None:
        if not isinstance(asn, int) or asn <= 0:
            raise ValueError("asn must be a positive integer")
        super().__init__(
            name=self.satellite_id,
            role=SatelliteRole.SATELLITE,
            asn=asn,
        )

    def _initialize_skyseed_identity(self) -> None:
        self.setDisplayName(self.satellite_id)

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
    def orbit_parameters(self) -> OrbitParameters:
        return self._orbit_parameters

    @property
    def mean_anomaly_deg(self) -> float:
        return self._mean_anomaly_deg

    @property
    def payload(self) -> Mapping[str, str]:
        return self._payload

    @property
    def registry_type(self) -> str:
        """Stable primary Registry identity used by integration adapters."""

        return SATELLITE_REGISTRY_TYPE

    def scope(self) -> Scope:
        # SEED has no public ScopeType extension registry. ANY preserves
        # global/AS/node option precedence without claiming Host identity.
        return Scope(
            ScopeTier.Node,
            node_type=ScopeType.ANY,
            node_id=self.getName(),
            as_id=self.getAsn(),
        )
