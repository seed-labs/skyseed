"""Immutable complete base definition for one Simulator."""

from __future__ import annotations

from dataclasses import dataclass

from .input import SimulatorStaticInput
from .timeline import SimulationTimeline


@dataclass(frozen=True, slots=True)
class SimulationDefinition:
    """Bind one Simulator static input to explicit simulation time rules.

    Layer: immutable Simulator construction value. Users create it and one
    ``Simulator`` retains the exact value. It is not a mutable configuration
    bag, execution plan, backend context or runtime-resource registry. It is
    public from ``skyseed.simulator`` because it is the complete approved
    input to Simulator construction.
    """

    static_input: SimulatorStaticInput
    timeline: SimulationTimeline

    def __post_init__(self) -> None:
        if not isinstance(self.static_input, SimulatorStaticInput):
            raise TypeError("static_input must be a SimulatorStaticInput")
        if not isinstance(self.timeline, SimulationTimeline):
            raise TypeError("timeline must be a SimulationTimeline")


__all__ = ["SimulationDefinition"]
