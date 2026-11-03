"""SEED Base extended with an additional AS factory."""
from seedemu.layers import Base

from .sky_as import SkyAS
from .scene_identity import scene_nodes


class SkyBase(Base):
    def createSkyAutonomousSystem(self, asn: int) -> SkyAS:
        if isinstance(asn, bool) or not isinstance(asn, int) or not 0 < asn < 2**32:
            raise ValueError("asn must be a positive 32-bit integer")
        if asn in self.getAsns():
            raise ValueError(f"AS {asn} already exists")
        autonomous_system = SkyAS(asn)
        self.setAutonomousSystem(autonomous_system)
        return autonomous_system

    def configure(self, emulator):
        scene_nodes(self)  # Fail before any registry or node mutations.
        super().configure(emulator)
