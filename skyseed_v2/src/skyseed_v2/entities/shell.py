"""Shell ownership and Orbit construction."""

from __future__ import annotations

from typing import Iterable

from .orbit import Orbit
from .orbit_parameters import OrbitParameters
from .satellite import Satellite
from .validation import validate_identifier


class Shell:
    """Own the Orbits belonging to one Skyseed AS Shell.

    Layer: approved Emulator entity. `SkyseedAutonomousSystem.create_shell`
    creates it; handwritten callers or the Applier create Orbits through it. It
    is not a constellation, population or specification. It is root public
    because it is the approved Orbit ownership entry point.
    """

    def __init__(
        self,
        shell_id: str,
        *,
        asn: int,
        satellite_ids: set[str] | None = None,
    ) -> None:
        self._shell_id = validate_identifier(shell_id, "shell_id")
        self._orbits: dict[str, Orbit] = {}
        self._satellite_ids = (
            satellite_ids if satellite_ids is not None else set()
        )
        self._asn = asn

    @property
    def shell_id(self) -> str:
        return self._shell_id

    @property
    def orbits(self) -> tuple[Orbit, ...]:
        return tuple(self._orbits.values())

    def create_orbit(
        self,
        orbit_id: str,
        *,
        parameters: OrbitParameters,
    ) -> Orbit:
        """Create and own one Orbit through the public entity entry point."""

        orbit_id = validate_identifier(orbit_id, "orbit_id")
        if orbit_id in self._orbits:
            raise ValueError(
                f"orbit_id {orbit_id!r} already exists in shell "
                f"{self.shell_id!r}"
            )
        orbit = Orbit(
            orbit_id=orbit_id,
            shell_id=self.shell_id,
            parameters=parameters,
            satellite_ids=self._satellite_ids,
            asn=self._asn,
        )
        self._orbits[orbit_id] = orbit
        return orbit

    def get_orbit(self, orbit_id: str) -> Orbit:
        try:
            return self._orbits[orbit_id]
        except KeyError as error:
            raise KeyError(
                f"orbit {orbit_id!r} does not exist in shell {self.shell_id!r}"
            ) from error

    def iter_satellites(self) -> Iterable[Satellite]:
        for orbit in self._orbits.values():
            yield from orbit.satellites
