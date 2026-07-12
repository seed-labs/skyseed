"""Typed inputs for a user-composed Skyseed experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShellConfig:
    shell_id: str
    plane_count: int
    satellites_per_plane: int
    cross_plane_slot_count: int

    @property
    def satellite_count(self) -> int:
        return self.plane_count * self.satellites_per_plane


@dataclass(frozen=True)
class GroundConfig:
    name: str
    ut_catalog: str
    ut_gateway_policy_catalog: str
    gateway_catalog: str
    gateway_binding_catalog: str
    zone_catalog: str
    ut_ids: tuple[str, ...]
    gateway_ids: tuple[str, ...]
    zone_ids: tuple[str, ...]
    qualification_start_time_utc: str | None = None
    qualification_duration_seconds: int | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    horizon_seconds: int = 240
    horizon_count: int = 4
    sample_interval_seconds: int = 10
    event_evaluation_interval_seconds: int | None = None
    runtime_mode: str = "operational_clean"
    forwarding_backend: str = "bpf_owned"

    @property
    def event_interval_seconds(self) -> int:
        return int(
            self.event_evaluation_interval_seconds
            if self.event_evaluation_interval_seconds is not None
            else self.sample_interval_seconds
        )

    @property
    def duration_seconds(self) -> int:
        return int(self.horizon_seconds) * int(self.horizon_count)


@dataclass(frozen=True)
class ExperimentConfig:
    profile_name: str
    example_name: str
    description: str
    constellation: ShellConfig
    ground: GroundConfig
    run_root: Path
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    start_time_utc: str = "2026-07-09T00:00:00Z"

    def resolve_profile(self) -> dict[str, Any]:
        from .resolver import resolve_profile

        return resolve_profile(self)

    def validate(self) -> tuple[str, ...]:
        issues: list[str] = []
        runtime = self.runtime
        if runtime.horizon_seconds <= 0:
            issues.append("horizon_seconds_must_be_positive")
        if runtime.horizon_count < 2:
            issues.append("horizon_count_must_cover_successor_handoff")
        if runtime.sample_interval_seconds <= 0:
            issues.append("sample_interval_seconds_must_be_positive")
        if runtime.horizon_seconds % runtime.sample_interval_seconds:
            issues.append("sample_interval_must_divide_horizon")
        if runtime.event_interval_seconds <= 0:
            issues.append("event_evaluation_interval_must_be_positive")
        if not self.ground.ut_ids:
            issues.append("ground_ut_selection_empty")
        if not self.ground.gateway_ids:
            issues.append("ground_gateway_selection_empty")
        if not self.ground.zone_ids:
            issues.append("ground_zone_selection_empty")
        if (
            self.ground.qualification_start_time_utc is not None
            and self.start_time_utc != self.ground.qualification_start_time_utc
        ):
            issues.append("experiment_start_time_outside_ground_qualification")
        if (
            self.ground.qualification_duration_seconds is not None
            and runtime.duration_seconds > self.ground.qualification_duration_seconds
        ):
            issues.append("runtime_duration_exceeds_ground_qualification_window")
        return tuple(issues)

    def summary(self) -> dict[str, Any]:
        return {
            "example": self.example_name,
            "profile": self.profile_name,
            "constellation_shells": [self.constellation.shell_id],
            "satellite_count": self.constellation.satellite_count,
            "selected_ut_count": len(self.ground.ut_ids),
            "selected_gateway_count": len(self.ground.gateway_ids),
            "selected_zone_count": len(self.ground.zone_ids),
            "ground_qualification_start_time_utc": (
                self.ground.qualification_start_time_utc
            ),
            "ground_qualification_duration_seconds": (
                self.ground.qualification_duration_seconds
            ),
            "horizon_count": self.runtime.horizon_count,
            "horizon_seconds": self.runtime.horizon_seconds,
            "sample_interval_seconds": self.runtime.sample_interval_seconds,
            "event_evaluation_interval_seconds": self.runtime.event_interval_seconds,
            "duration_seconds": self.runtime.duration_seconds,
            "forwarding_backend": self.runtime.forwarding_backend,
            "runtime_mode": self.runtime.runtime_mode,
            "validation_issues": list(self.validate()),
        }
