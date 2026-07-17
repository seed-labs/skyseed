"""AutonomousSystem extension that owns the static Satellite hierarchy."""

from __future__ import annotations

from seedemu.core import AutonomousSystem, Emulator

from skyseed_v2.contracts import SatelliteDescriptor
from skyseed_v2.domain import SATELLITE_REGISTRY_TYPE, Satellite, Shell
from skyseed_v2.domain.satellite import validate_identifier
from skyseed_v2.projection import SatelliteDescriptorProjector

class SatelliteAutonomousSystem(AutonomousSystem):
    """SEED AS extension owning the unique static Satellite instances."""

    def __init__(self, asn: int, subnet_template: str = "10.{}.0.0/16") -> None:
        super().__init__(asn, subnet_template)
        self._shells: dict[str, Shell] = {}
        self._satellite_ids: set[str] = set()
        self._descriptor_projector = SatelliteDescriptorProjector()

    @property
    def shells(self) -> tuple[Shell, ...]:
        return tuple(self._shells.values())

    def create_shell(self, shell_id: str) -> Shell:
        shell_id = validate_identifier(shell_id, "shell_id")
        if shell_id in self._shells:
            raise ValueError(f"shell_id {shell_id!r} already exists")
        shell = Shell(
            shell_id,
            asn=self.getAsn(),
            satellite_ids=self._satellite_ids,
        )
        self._shells[shell_id] = shell
        return shell

    def get_shell(self, shell_id: str) -> Shell:
        try:
            return self._shells[shell_id]
        except KeyError as error:
            raise KeyError(f"shell {shell_id!r} does not exist") from error

    def satellites(self) -> tuple[Satellite, ...]:
        return tuple(
            satellite
            for shell in self._shells.values()
            for satellite in shell.iter_satellites()
        )

    def get_satellite(self, satellite_id: str) -> Satellite:
        for satellite in self.satellites():
            if satellite.satellite_id == satellite_id:
                return satellite
        raise KeyError(f"satellite {satellite_id!r} does not exist")

    def satellite_descriptors(self) -> tuple[SatelliteDescriptor, ...]:
        return tuple(
            self._descriptor_projector.project(satellite)
            for satellite in self.satellites()
        )

    def registerNodes(self, emulator: Emulator) -> None:
        super().registerNodes(emulator)
        registry = emulator.getRegistry()
        scope = str(self.getAsn())
        for satellite in self.satellites():
            registry.register(
                scope,
                SATELLITE_REGISTRY_TYPE,
                satellite.satellite_id,
                satellite,
            )

    def inheritOptions(self, emulator: Emulator) -> None:
        super().inheritOptions(emulator)
        for satellite in self.satellites():
            self.handDown(satellite)

    def configure(self, emulator: Emulator) -> None:
        super().configure(emulator)
        for satellite in self.satellites():
            satellite.configure(emulator)
