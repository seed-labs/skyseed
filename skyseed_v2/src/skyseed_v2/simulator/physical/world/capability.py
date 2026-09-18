"""Define immutable static physical capability values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterSatelliteLinkCapability:
    """State one PhysicalSatellite's authoritative simultaneous-ISL limit.

    Layer: immutable Simulator PhysicalWorld capability value. PhysicalWorld
    materialization creates it from explicit static input and one PhysicalSatellite
    owns it. It is not an optical-terminal inventory, direction/steering
    model, candidate-search policy, selected degree or operational state. It
    is public from ``skyseed_v2.simulator.physical`` and the Simulator facade
    because physical/planning consumers read the authoritative limit.
    """

    maximum_simultaneous_links: int

    def __post_init__(self) -> None:
        value = self.maximum_simultaneous_links
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                "maximum_simultaneous_links must be a positive integer"
            )


__all__ = ["InterSatelliteLinkCapability"]
