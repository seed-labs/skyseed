"""Stable logical node identity shared across Skyseed layers."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.identifier import validate_identifier


_SATELLITE_ENTITY_KIND = "satellite"
_GROUND_STATION_ENTITY_KIND = "ground-station"
_ENTITY_KINDS = frozenset(
    {_SATELLITE_ENTITY_KIND, _GROUND_STATION_ENTITY_KIND}
)


@dataclass(frozen=True, slots=True)
class NodeId:
    """Identify one logical node across Emulator, Simulator and Compiler.

    Layer: neutral cross-layer contract value. Simulator-input generation
    creates it; static inputs, future Simulator state and plans, and the
    Compiler-side Emulator node index use it. It is not an Emulator entity
    property, Registry key, container ID, Compose service, namespace,
    interface name or Simulator-local sequence number. It is public from this
    direct module because multiple architectural layers share the contract.
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


__all__ = ["NodeId"]
