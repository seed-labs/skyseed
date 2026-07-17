"""Application builder for deterministic static constellation construction."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable

from skyseed_v2.domain import Orbit, OrbitParameters, Satellite, Shell
from skyseed_v2.seed.autonomous_system import SkyseedAutonomousSystem
from skyseed_v2.specifications import (
    OrbitSpecification,
    SatelliteSpecification,
    ShellSpecification,
)


@dataclass(frozen=True, slots=True)
class ConstellationBuildResult:
    """Entities created from one immutable specification snapshot."""

    shells: tuple[Shell, ...]
    orbits: tuple[Orbit, ...]
    satellites: tuple[Satellite, ...]


@dataclass(frozen=True, slots=True)
class _SatellitePlan:
    satellite_id: str
    index_in_orbit: int
    mean_anomaly_deg: float
    payload_items: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _OrbitPlan:
    orbit_id: str
    parameters: OrbitParameters
    satellites: tuple[_SatellitePlan, ...]


@dataclass(frozen=True, slots=True)
class _ShellPlan:
    shell_id: str
    orbits: tuple[_OrbitPlan, ...]


class ConstellationBuilder:
    """Create Shell/Orbit/Satellite entities outside domain and Compiler code."""

    def build(
        self,
        autonomous_system: SkyseedAutonomousSystem,
        specifications: ShellSpecification | Iterable[ShellSpecification],
    ) -> ConstellationBuildResult:
        if not isinstance(autonomous_system, SkyseedAutonomousSystem):
            raise TypeError("autonomous_system must be a SkyseedAutonomousSystem")
        if isinstance(specifications, ShellSpecification):
            snapshot = (specifications,)
        else:
            try:
                snapshot = tuple(specifications)
            except TypeError as error:
                raise TypeError(
                    "specifications must be a ShellSpecification or iterable"
                ) from error
        if not snapshot:
            raise ValueError("specifications must not be empty")
        if any(not isinstance(item, ShellSpecification) for item in snapshot):
            raise TypeError("specifications must contain ShellSpecification values")

        ordered = tuple(sorted(snapshot, key=lambda item: item.shell_id))
        if len({item.shell_id for item in ordered}) != len(ordered):
            raise ValueError("specifications contains a duplicate shell_id")
        plans = tuple(self._expand_shell(item) for item in ordered)
        self._preflight(autonomous_system, plans)

        shells: list[Shell] = []
        orbits: list[Orbit] = []
        satellites: list[Satellite] = []
        for shell_plan in plans:
            shell = autonomous_system.create_shell(shell_plan.shell_id)
            shells.append(shell)
            for orbit_plan in shell_plan.orbits:
                orbit = shell.create_orbit(
                    orbit_plan.orbit_id,
                    parameters=orbit_plan.parameters,
                )
                orbits.append(orbit)
                for satellite_plan in orbit_plan.satellites:
                    satellites.append(
                        orbit.create_satellite(
                            satellite_plan.satellite_id,
                            orbit_index=satellite_plan.index_in_orbit,
                            mean_anomaly_deg=satellite_plan.mean_anomaly_deg,
                            payload=dict(satellite_plan.payload_items),
                        )
                    )
        return ConstellationBuildResult(
            shells=tuple(shells),
            orbits=tuple(orbits),
            satellites=tuple(satellites),
        )

    @staticmethod
    def _preflight(
        autonomous_system: SkyseedAutonomousSystem,
        plans: tuple[_ShellPlan, ...],
    ) -> None:
        existing_shell_ids = {shell.shell_id for shell in autonomous_system.shells}
        candidate_shell_ids = {plan.shell_id for plan in plans}
        duplicate_shell_ids = existing_shell_ids & candidate_shell_ids
        if duplicate_shell_ids:
            duplicate = min(duplicate_shell_ids)
            raise ValueError(f"shell_id {duplicate!r} already exists")

        existing_satellite_ids = {
            satellite.satellite_id for satellite in autonomous_system.satellites()
        }
        candidate_satellite_ids = [
            satellite.satellite_id
            for shell in plans
            for orbit in shell.orbits
            for satellite in orbit.satellites
        ]
        if len(set(candidate_satellite_ids)) != len(candidate_satellite_ids):
            raise ValueError("specifications generate a duplicate satellite_id")
        duplicate_satellite_ids = existing_satellite_ids & set(
            candidate_satellite_ids
        )
        if duplicate_satellite_ids:
            duplicate = min(duplicate_satellite_ids)
            raise ValueError(f"satellite_id {duplicate!r} already exists")

    def _expand_shell(self, specification: ShellSpecification) -> _ShellPlan:
        if specification.generation is not None:
            return self._expand_generated_shell(specification)
        return _ShellPlan(
            shell_id=specification.shell_id,
            orbits=tuple(
                self._expand_explicit_orbit(specification, orbit)
                for orbit in specification.orbits
            ),
        )

    @staticmethod
    def _expand_explicit_orbit(
        shell: ShellSpecification,
        orbit: OrbitSpecification,
    ) -> _OrbitPlan:
        return _OrbitPlan(
            orbit_id=orbit.orbit_id,
            parameters=OrbitParameters(
                altitude_km=shell.altitude_km,
                inclination_deg=shell.inclination_deg,
                right_ascension_deg=orbit.right_ascension_deg,
            ),
            satellites=tuple(
                ConstellationBuilder._explicit_satellite_plan(satellite)
                for satellite in orbit.satellites
            ),
        )

    @staticmethod
    def _explicit_satellite_plan(
        satellite: SatelliteSpecification,
    ) -> _SatellitePlan:
        return _SatellitePlan(
            satellite_id=satellite.satellite_id,
            index_in_orbit=satellite.index_in_orbit,
            mean_anomaly_deg=satellite.mean_anomaly_deg,
            payload_items=satellite.payload_items,
        )

    @staticmethod
    def _expand_generated_shell(specification: ShellSpecification) -> _ShellPlan:
        generation = specification.generation
        assert generation is not None
        raan_step = (
            360.0 / generation.orbit_count
            if generation.raan.step_deg is None
            else generation.raan.step_deg
        )
        phase_step = (
            360.0 / generation.satellites_per_orbit
            if generation.phase.step_deg is None
            else generation.phase.step_deg
        )
        if not isfinite(raan_step) or not isfinite(phase_step):
            raise ValueError("generated angular steps must be finite")

        raan_values = tuple(
            (generation.raan.start_deg + index * raan_step) % 360.0
            for index in range(generation.orbit_count)
        )
        if len({round(value, 10) for value in raan_values}) != len(raan_values):
            raise ValueError("RAAN distribution generates duplicate Orbits")

        orbit_plans = []
        for orbit_index, right_ascension in enumerate(raan_values):
            orbit_id = f"{specification.shell_id}-orbit-{orbit_index + 1:03d}"
            orbit_start = (
                generation.phase.start_deg
                + (orbit_index % 2)
                * generation.phase.alternating_orbit_offset_deg
            ) % 360.0
            anomalies = tuple(
                (orbit_start + index * phase_step) % 360.0
                for index in range(generation.satellites_per_orbit)
            )
            if len({round(value, 10) for value in anomalies}) != len(anomalies):
                raise ValueError(
                    f"phase distribution generates duplicate Satellites in {orbit_id!r}"
                )
            satellites = tuple(
                _SatellitePlan(
                    satellite_id=(
                        f"{orbit_id}-satellite-{satellite_index + 1:04d}"
                    ),
                    index_in_orbit=satellite_index,
                    mean_anomaly_deg=mean_anomaly,
                    payload_items=(),
                )
                for satellite_index, mean_anomaly in enumerate(anomalies)
            )
            orbit_plans.append(
                _OrbitPlan(
                    orbit_id=orbit_id,
                    parameters=OrbitParameters(
                        altitude_km=specification.altitude_km,
                        inclination_deg=specification.inclination_deg,
                        right_ascension_deg=right_ascension,
                    ),
                    satellites=satellites,
                )
            )
        return _ShellPlan(
            shell_id=specification.shell_id,
            orbits=tuple(orbit_plans),
        )
