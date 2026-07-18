"""Base Layer extension that includes Satellite in formal lifecycle hooks."""

from __future__ import annotations

from seedemu.core import Emulator, Node, Scope, ScopeTier
from seedemu.layers.Base import Base, BaseFileTemplates

from skyseed_v2.entities import SATELLITE_REGISTRY_TYPE


class SatelliteBase(Base):
    """Extend the SEED Base Layer for first-class Satellite nodes.

    Layer: SEED integration. Examples or the specification Applier create it;
    Emulator render uses it. It is not a domain entity, scenario or Compiler.
    It is public from `skyseed_v2.seedemu_integration` because Satellite lifecycle requires a
    Base integration hook unavailable in upstream SEED.
    """

    def _prepare(self, emulator: Emulator) -> None:
        super()._prepare(emulator)
        registry = emulator.getRegistry()
        for (_, registry_type, _), node in registry.getAll().items():
            if registry_type != SATELLITE_REGISTRY_TYPE:
                continue
            for option in self.getAvailableOptions():
                node.setOption(option, Scope(ScopeTier.Global))

    def render(self, emulator: Emulator) -> None:
        super().render(emulator)
        for (_, registry_type, _), obj in emulator.getRegistry().getAll().items():
            if registry_type != SATELLITE_REGISTRY_TYPE:
                continue
            node: Node = obj
            ifinfo = ""
            for interface in node.getInterfaces():
                network = interface.getNet()
                latency, bandwidth, packet_drop = interface.getLinkProperties()
                ifinfo += (
                    f"{network.getName()}:{network.getPrefix()}:{latency}:"
                    f"{bandwidth}:{packet_drop}\n"
                )
            node.setFile("/ifinfo.txt", ifinfo)
            node.setFile("/interface_setup", BaseFileTemplates["interface_setup_script"])
            node.insertStartCommand(0, "/interface_setup")
            node.insertStartCommand(0, "chmod +x /interface_setup")
