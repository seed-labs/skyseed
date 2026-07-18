"""Backend-independent logical identities for Simulator static inputs."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.identifier import validate_identifier


SATELLITE_ENTITY_KIND = "satellite"
GROUND_STATION_ENTITY_KIND = "ground-station"
_ENTITY_KINDS = frozenset(
    {SATELLITE_ENTITY_KIND, GROUND_STATION_ENTITY_KIND}
)


@dataclass(frozen=True, slots=True)
class SimulationNodeId:
    """Identify one static Simulator input independently of runtime resources.

    Layer: immutable Emulator-to-Simulator boundary value. Simulation input
    types create and use it; future Simulator readers may use it for lookup. It
    is not a Registry, container or execution-target identity. It is public
    because every supported static entity input shares this identity scheme.
    """

    asn: int
    entity_kind: str
    entity_id: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.asn, bool)
            or not isinstance(self.asn, int)
            or self.asn <= 0
        ):
            raise ValueError("asn must be a positive integer")
        if (
            not isinstance(self.entity_kind, str)
            or self.entity_kind not in _ENTITY_KINDS
        ):
            raise ValueError(
                "entity_kind must be one of: "
                + ", ".join(sorted(_ENTITY_KINDS))
            )
        object.__setattr__(
            self,
            "entity_id",
            validate_identifier(self.entity_id, "entity_id"),
        )

    @property
    def value(self) -> str:
        return f"{self.asn}/{self.entity_kind}/{self.entity_id}"
