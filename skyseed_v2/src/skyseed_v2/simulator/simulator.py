"""Top-level Simulator composition root and explicit Topology lifecycle."""

from __future__ import annotations

from .definition import SimulationDefinition
from .timeline import SimulationTimeline
from .topology import Topology


class Simulator:
    """Own one definition and coordinate explicit subsystem construction.

    Layer: top-level Simulator composition root and facade. Users create it from
    one ``SimulationDefinition`` and explicitly request Topology construction.
    It is not an Emulator wrapper, planner, compiler, runner or runtime-resource
    locator. It is public from ``skyseed_v2.simulator`` as the canonical Simu
    entry point.
    """

    __slots__ = ("_definition", "_topology")

    def __init__(self, definition: SimulationDefinition) -> None:
        if not isinstance(definition, SimulationDefinition):
            raise TypeError("definition must be a SimulationDefinition")
        self._definition = definition
        self._topology: Topology | None = None

    def get_definition(self) -> SimulationDefinition:
        return self._definition

    def get_timeline(self) -> SimulationTimeline:
        return self._definition.timeline

    def create_topology(self) -> Topology:
        if self._topology is not None:
            raise RuntimeError("Topology has already been created")
        topology = Topology._materialize(self._definition.static_input)
        self._topology = topology
        return topology

    def get_topology(self) -> Topology:
        if self._topology is None:
            raise RuntimeError("Topology has not been created")
        return self._topology


__all__ = ["Simulator"]
