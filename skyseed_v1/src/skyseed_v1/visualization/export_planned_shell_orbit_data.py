#!/usr/bin/env python3
# encoding: utf-8

"""Export the deterministic planned-shell orbit interchange data.

This file is intentionally self-contained so it can be shared outside the
Skyseed repository. It mirrors the planned-shell generation and propagation
logic used by Skyseed previews without importing project-local modules.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence


EARTH_RADIUS_KM = 6371.0
EARTH_MU_KM3_S2 = 398600.4418


@dataclass(frozen=True)
class PlannedShellPreset:
    shell_id: str
    plane_count: int
    satellites_per_plane: int
    altitude_km: float
    inclination_deg: float

    @property
    def satellite_count(self) -> int:
        return int(self.plane_count * self.satellites_per_plane)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shell_id": self.shell_id,
            "plane_count": self.plane_count,
            "satellites_per_plane": self.satellites_per_plane,
            "satellite_count": self.satellite_count,
            "altitude_km": self.altitude_km,
            "inclination_deg": self.inclination_deg,
        }


PLANNED_STARLINK_PHASE1_SHELL_PRESETS: Dict[str, PlannedShellPreset] = {
    "S1": PlannedShellPreset(
        "S1",
        plane_count=72,
        satellites_per_plane=22,
        altitude_km=550.0,
        inclination_deg=53.0,
    ),
    "S2": PlannedShellPreset(
        "S2",
        plane_count=72,
        satellites_per_plane=22,
        altitude_km=540.0,
        inclination_deg=53.2,
    ),
    "S3": PlannedShellPreset(
        "S3",
        plane_count=36,
        satellites_per_plane=20,
        altitude_km=570.0,
        inclination_deg=70.0,
    ),
    "S4": PlannedShellPreset(
        "S4",
        plane_count=6,
        satellites_per_plane=58,
        altitude_km=560.0,
        inclination_deg=97.6,
    ),
    "S5": PlannedShellPreset(
        "S5",
        plane_count=4,
        satellites_per_plane=43,
        altitude_km=560.0,
        inclination_deg=97.6,
    ),
}

ALL_SHELL_ID = "all"


@dataclass(frozen=True)
class ParsedOrbitRecord:
    satellite_name: str
    norad_id: int
    epoch_utc: str
    inclination_deg: float
    eccentricity: float
    mean_motion_rev_per_day: float
    raan_deg: float
    argument_of_perigee_deg: float
    mean_anomaly_deg: float
    line1: str
    line2: str
    plane_index: int | None = None
    satellite_index_in_plane: int | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShellCluster:
    inclination_bucket_deg: float
    altitude_bucket_km: float
    records: Sequence[ParsedOrbitRecord]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inclination_bucket_deg": self.inclination_bucket_deg,
            "altitude_bucket_km": self.altitude_bucket_km,
            "record_count": len(self.records),
            "norad_ids": [record.norad_id for record in self.records],
        }


@dataclass(frozen=True)
class ShellPlane:
    plane_id: str
    plane_index: int
    mean_raan_deg: float
    records: Sequence[ParsedOrbitRecord]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plane_id": self.plane_id,
            "plane_index": self.plane_index,
            "mean_raan_deg": round(self.mean_raan_deg, 6),
            "record_count": len(self.records),
            "norad_ids": [record.norad_id for record in self.records],
        }


@dataclass(frozen=True)
class ShellSelectionResult:
    selection_method: str
    cluster: ShellCluster
    selected_planes: Sequence[ShellPlane]
    selected_records: Sequence[ParsedOrbitRecord]
    manifest: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selection_method": self.selection_method,
            "cluster": self.cluster.to_dict(),
            "selected_planes": [plane.to_dict() for plane in self.selected_planes],
            "selected_records": [record.to_dict() for record in self.selected_records],
            "manifest": dict(self.manifest),
        }


@dataclass(frozen=True)
class PropagatedSatelliteState:
    satellite_node_id: str
    norad_id: int
    timestamp_utc: str
    x_km: float
    y_km: float
    z_km: float
    vx_km_s: float
    vy_km_s: float
    vz_km_s: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PropagatedOrbitSlice:
    slice_id: str
    relative_time_seconds: float
    timestamp_utc: str
    satellite_states: Dict[str, PropagatedSatelliteState]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "relative_time_seconds": self.relative_time_seconds,
            "timestamp_utc": self.timestamp_utc,
            "satellite_states": {
                node_id: state.to_dict()
                for node_id, state in sorted(self.satellite_states.items())
            },
        }


def planned_shell_preset(shell_id: str) -> PlannedShellPreset:
    normalized = str(shell_id).upper()
    if normalized not in PLANNED_STARLINK_PHASE1_SHELL_PRESETS:
        raise ValueError(f"unknown planned shell preset: {shell_id}")
    return PLANNED_STARLINK_PHASE1_SHELL_PRESETS[normalized]


def satellite_node_id(norad_id: int) -> str:
    return f"stl-{norad_id}"


def _mean_motion_to_altitude_km(mean_motion_rev_per_day: float) -> float:
    mean_motion_rad_per_second = mean_motion_rev_per_day * 2.0 * math.pi / 86400.0
    semi_major_axis_km = (
        EARTH_MU_KM3_S2 / (mean_motion_rad_per_second**2)
    ) ** (1.0 / 3.0)
    return semi_major_axis_km - EARTH_RADIUS_KM


def _altitude_km_to_mean_motion_rev_per_day(altitude_km: float) -> float:
    semi_major_axis_km = EARTH_RADIUS_KM + altitude_km
    mean_motion_rad_per_second = math.sqrt(
        EARTH_MU_KM3_S2 / (semi_major_axis_km**3)
    )
    return mean_motion_rad_per_second * 86400.0 / (2.0 * math.pi)


def _circular_angle_delta_deg(first: float, second: float) -> float:
    delta = abs((first - second) % 360.0)
    return min(delta, 360.0 - delta)


def _sample_evenly(
    records: Sequence[ParsedOrbitRecord],
    count: int,
) -> List[ParsedOrbitRecord]:
    if count >= len(records):
        return list(records)
    if count <= 0:
        return []
    sampled: List[ParsedOrbitRecord] = []
    seen_indices: set[int] = set()
    for index in range(count):
        target = round(index * (len(records) - 1) / max(1, count - 1))
        while target in seen_indices and target + 1 < len(records):
            target += 1
        seen_indices.add(target)
        sampled.append(records[target])
    return sampled


def generate_planned_shell_records(
    *,
    shell_id: str,
    epoch_utc: datetime | None = None,
) -> Sequence[ParsedOrbitRecord]:
    preset = planned_shell_preset(shell_id)
    epoch = (
        epoch_utc or datetime(2026, 1, 1, tzinfo=timezone.utc)
    ).astimezone(timezone.utc)
    epoch_text = epoch.isoformat().replace("+00:00", "Z")
    mean_motion = _altitude_km_to_mean_motion_rev_per_day(preset.altitude_km)
    preset_index = list(PLANNED_STARLINK_PHASE1_SHELL_PRESETS).index(
        preset.shell_id
    ) + 1
    base_norad_id = 900000 + (preset_index * 10000)
    records: List[ParsedOrbitRecord] = []
    for plane_index in range(preset.plane_count):
        raan_deg = (360.0 * plane_index) / preset.plane_count
        plane_phase_offset_deg = (
            180.0 * (plane_index % 2)
        ) / preset.satellites_per_plane
        for satellite_index in range(preset.satellites_per_plane):
            mean_anomaly_deg = (
                (360.0 * satellite_index) / preset.satellites_per_plane
                + plane_phase_offset_deg
            ) % 360.0
            norad_id = (
                base_norad_id
                + (plane_index * preset.satellites_per_plane)
                + satellite_index
                + 1
            )
            records.append(
                ParsedOrbitRecord(
                    satellite_name=(
                        f"STARLINK-{preset.shell_id}-P{plane_index + 1:02d}-"
                        f"S{satellite_index + 1:02d}"
                    ),
                    norad_id=norad_id,
                    epoch_utc=epoch_text,
                    inclination_deg=preset.inclination_deg,
                    eccentricity=0.0,
                    mean_motion_rev_per_day=round(mean_motion, 9),
                    raan_deg=round(raan_deg, 6),
                    argument_of_perigee_deg=0.0,
                    mean_anomaly_deg=round(mean_anomaly_deg, 6),
                    line1=f"PLANNED {preset.shell_id} {norad_id}",
                    line2=(
                        f"{preset.inclination_deg:.4f} {raan_deg:.4f} "
                        f"{mean_anomaly_deg:.4f} {mean_motion:.9f}"
                    ),
                    plane_index=plane_index + 1,
                    satellite_index_in_plane=satellite_index + 1,
                )
            )
    return tuple(records)


def cluster_shell_candidates(
    records: Sequence[ParsedOrbitRecord],
    *,
    inclination_bucket_deg: float = 0.5,
    altitude_bucket_km: float = 25.0,
) -> List[ShellCluster]:
    grouped: Dict[tuple[float, float], List[ParsedOrbitRecord]] = {}
    for record in records:
        altitude_km = _mean_motion_to_altitude_km(record.mean_motion_rev_per_day)
        inclination_bucket = (
            round(record.inclination_deg / inclination_bucket_deg)
            * inclination_bucket_deg
        )
        altitude_bucket = round(altitude_km / altitude_bucket_km) * altitude_bucket_km
        grouped.setdefault((inclination_bucket, altitude_bucket), []).append(record)
    return [
        ShellCluster(
            inclination_bucket_deg=key[0],
            altitude_bucket_km=key[1],
            records=tuple(
                sorted(
                    grouped[key],
                    key=lambda record: (
                        record.raan_deg,
                        record.mean_anomaly_deg,
                        record.norad_id,
                    ),
                )
            ),
        )
        for key in sorted(
            grouped,
            key=lambda item: (-len(grouped[item]), item[0], item[1]),
        )
    ]


def cluster_records_into_planes(
    records: Sequence[ParsedOrbitRecord],
    *,
    raan_gap_threshold_deg: float = 2.5,
) -> List[ShellPlane]:
    if not records:
        return []
    ordered = sorted(records, key=lambda record: (record.raan_deg, record.mean_anomaly_deg))
    groups: List[List[ParsedOrbitRecord]] = [[ordered[0]]]
    for record in ordered[1:]:
        if (
            _circular_angle_delta_deg(record.raan_deg, groups[-1][-1].raan_deg)
            <= raan_gap_threshold_deg
        ):
            groups[-1].append(record)
        else:
            groups.append([record])
    if (
        len(groups) > 1
        and _circular_angle_delta_deg(groups[0][0].raan_deg, groups[-1][-1].raan_deg)
        <= raan_gap_threshold_deg
    ):
        merged = groups[-1] + groups[0]
        groups = [merged] + groups[1:-1]
    planes: List[ShellPlane] = []
    for index, group in enumerate(
        sorted(
            groups,
            key=lambda item: (
                sum(record.raan_deg for record in item) / len(item),
                len(item),
            ),
        ),
        start=1,
    ):
        mean_raan = sum(record.raan_deg for record in group) / len(group)
        planes.append(
            ShellPlane(
                plane_id=f"shell-plane-{index:03d}",
                plane_index=index,
                mean_raan_deg=mean_raan % 360.0,
                records=tuple(
                    sorted(
                        group,
                        key=lambda record: (
                            record.mean_anomaly_deg,
                            record.norad_id,
                        ),
                    )
                ),
            )
        )
    return planes


def select_starlink_shell(
    records: Sequence[ParsedOrbitRecord],
    *,
    max_satellites: int,
) -> ShellSelectionResult:
    if max_satellites <= 0:
        raise ValueError("max_satellites must be positive")
    clusters = cluster_shell_candidates(records)
    if not clusters:
        raise ValueError("no records are available for shell selection")
    cluster = clusters[0]
    all_planes = cluster_records_into_planes(cluster.records)
    if not all_planes:
        raise ValueError("shell cluster did not produce any RAAN plane groupings")

    selected_plane_count = min(len(all_planes), max(3, max_satellites // 4))
    chosen_plane_indices = sorted(
        {
            min(
                len(all_planes) - 1,
                round(
                    index
                    * (len(all_planes) - 1)
                    / max(1, selected_plane_count - 1)
                ),
            )
            for index in range(selected_plane_count)
        }
    )
    selected_planes = [all_planes[index] for index in chosen_plane_indices]
    while len(selected_planes) < selected_plane_count:
        for plane in all_planes:
            if plane not in selected_planes:
                selected_planes.append(plane)
                break

    base_quota = max_satellites // len(selected_planes)
    extra = max_satellites % len(selected_planes)
    selected_records: List[ParsedOrbitRecord] = []
    selected_by_plane_id: Dict[str, List[ParsedOrbitRecord]] = {
        plane.plane_id: []
        for plane in all_planes
    }
    for plane_index, plane in enumerate(selected_planes):
        quota = base_quota + (1 if plane_index < extra else 0)
        plane_records = _sample_evenly(list(plane.records), quota)
        selected_records.extend(plane_records)
        selected_by_plane_id[plane.plane_id].extend(plane_records)

    selected_ids = {record.norad_id for record in selected_records}
    prioritized_planes = list(selected_planes) + [
        plane
        for plane in all_planes
        if plane not in selected_planes
    ]
    while len(selected_records) < min(max_satellites, len(cluster.records)):
        added_record = False
        for plane in prioritized_planes:
            next_record = next(
                (
                    record
                    for record in plane.records
                    if record.norad_id not in selected_ids
                ),
                None,
            )
            if next_record is None:
                continue
            selected_records.append(next_record)
            selected_ids.add(next_record.norad_id)
            selected_by_plane_id[plane.plane_id].append(next_record)
            added_record = True
            if len(selected_records) >= min(max_satellites, len(cluster.records)):
                break
        if not added_record:
            break

    selected_planes = [
        plane
        for plane in prioritized_planes
        if selected_by_plane_id[plane.plane_id]
    ]
    plane_manifests: List[Dict[str, Any]] = []
    for plane in selected_planes:
        plane_records = sorted(
            selected_by_plane_id[plane.plane_id],
            key=lambda record: (record.mean_anomaly_deg, record.norad_id),
        )
        plane_manifests.append(
            {
                "plane_id": plane.plane_id,
                "plane_index": plane.plane_index,
                "mean_raan_deg": round(plane.mean_raan_deg, 6),
                "available_record_count": len(plane.records),
                "selected_record_count": len(plane_records),
                "norad_ids": [record.norad_id for record in plane_records],
            }
        )

    selected_records = sorted(
        selected_records,
        key=lambda record: (
            next(
                plane.plane_index
                for plane in selected_planes
                if record in plane.records
            ),
            record.mean_anomaly_deg,
            record.norad_id,
        ),
    )
    selected_altitudes = [
        _mean_motion_to_altitude_km(record.mean_motion_rev_per_day)
        for record in selected_records
    ]
    manifest = {
        "selection_method": (
            "densest inclination/altitude cluster, then balanced RAAN plane sampling"
        ),
        "cluster_inclination_bucket_deg": cluster.inclination_bucket_deg,
        "cluster_altitude_bucket_km": cluster.altitude_bucket_km,
        "cluster_record_count": len(cluster.records),
        "plane_count_in_cluster": len(all_planes),
        "selected_plane_count": len(selected_planes),
        "selected_satellite_count": len(selected_records),
        "max_satellites_requested": max_satellites,
        "included_satellite_ids": [record.norad_id for record in selected_records],
        "parameter_bands": {
            "inclination_deg_min": round(
                min(record.inclination_deg for record in selected_records),
                6,
            ),
            "inclination_deg_max": round(
                max(record.inclination_deg for record in selected_records),
                6,
            ),
            "estimated_altitude_km_min": round(min(selected_altitudes), 3),
            "estimated_altitude_km_max": round(max(selected_altitudes), 3),
            "mean_motion_rev_per_day_min": round(
                min(record.mean_motion_rev_per_day for record in selected_records),
                9,
            ),
            "mean_motion_rev_per_day_max": round(
                max(record.mean_motion_rev_per_day for record in selected_records),
                9,
            ),
        },
        "plane_manifest": plane_manifests,
    }
    return ShellSelectionResult(
        selection_method=str(manifest["selection_method"]),
        cluster=cluster,
        selected_planes=tuple(selected_planes),
        selected_records=tuple(selected_records),
        manifest=manifest,
    )


def propagate_planned_shell_records(
    records: Sequence[ParsedOrbitRecord],
    *,
    start_time: datetime,
    step_seconds: float,
    slice_count: int,
    slice_id_prefix: str = "starlink-shell-slice",
) -> Sequence[PropagatedOrbitSlice]:
    if slice_count <= 0:
        raise ValueError("slice_count must be positive")
    if step_seconds <= 0.0:
        raise ValueError("step_seconds must be positive")
    utc_start = start_time.astimezone(timezone.utc)
    slices: List[PropagatedOrbitSlice] = []
    for slice_index in range(slice_count):
        timestamp = utc_start + timedelta(seconds=step_seconds * slice_index)
        satellite_states: Dict[str, PropagatedSatelliteState] = {}
        for record in records:
            epoch = datetime.fromisoformat(record.epoch_utc.replace("Z", "+00:00"))
            delta_seconds = (timestamp - epoch).total_seconds()
            semi_major_axis_km = EARTH_RADIUS_KM + _mean_motion_to_altitude_km(
                record.mean_motion_rev_per_day
            )
            true_anomaly_rad = math.radians(
                (
                    record.mean_anomaly_deg
                    + (
                        record.mean_motion_rev_per_day
                        * 360.0
                        * delta_seconds
                        / 86400.0
                    )
                )
                % 360.0
            )
            raan_rad = math.radians(record.raan_deg)
            inclination_rad = math.radians(record.inclination_deg)
            argument_of_perigee_rad = math.radians(record.argument_of_perigee_deg)
            orbital_speed_km_s = math.sqrt(EARTH_MU_KM3_S2 / semi_major_axis_km)

            cos_nu = math.cos(true_anomaly_rad)
            sin_nu = math.sin(true_anomaly_rad)
            perifocal_position = (
                semi_major_axis_km * cos_nu,
                semi_major_axis_km * sin_nu,
                0.0,
            )
            perifocal_velocity = (
                -orbital_speed_km_s * sin_nu,
                orbital_speed_km_s * cos_nu,
                0.0,
            )

            cos_raan = math.cos(raan_rad)
            sin_raan = math.sin(raan_rad)
            cos_inc = math.cos(inclination_rad)
            sin_inc = math.sin(inclination_rad)
            cos_arg = math.cos(argument_of_perigee_rad)
            sin_arg = math.sin(argument_of_perigee_rad)

            rotation = (
                (
                    cos_raan * cos_arg - sin_raan * sin_arg * cos_inc,
                    -cos_raan * sin_arg - sin_raan * cos_arg * cos_inc,
                    sin_raan * sin_inc,
                ),
                (
                    sin_raan * cos_arg + cos_raan * sin_arg * cos_inc,
                    -sin_raan * sin_arg + cos_raan * cos_arg * cos_inc,
                    -cos_raan * sin_inc,
                ),
                (
                    sin_arg * sin_inc,
                    cos_arg * sin_inc,
                    cos_inc,
                ),
            )

            def _rotate(vector: tuple[float, float, float]) -> tuple[float, float, float]:
                return (
                    (rotation[0][0] * vector[0])
                    + (rotation[0][1] * vector[1])
                    + (rotation[0][2] * vector[2]),
                    (rotation[1][0] * vector[0])
                    + (rotation[1][1] * vector[1])
                    + (rotation[1][2] * vector[2]),
                    (rotation[2][0] * vector[0])
                    + (rotation[2][1] * vector[1])
                    + (rotation[2][2] * vector[2]),
                )

            position_km = _rotate(perifocal_position)
            velocity_km_s = _rotate(perifocal_velocity)
            node_id = satellite_node_id(record.norad_id)
            satellite_states[node_id] = PropagatedSatelliteState(
                satellite_node_id=node_id,
                norad_id=record.norad_id,
                timestamp_utc=timestamp.isoformat().replace("+00:00", "Z"),
                x_km=round(position_km[0], 6),
                y_km=round(position_km[1], 6),
                z_km=round(position_km[2], 6),
                vx_km_s=round(velocity_km_s[0], 9),
                vy_km_s=round(velocity_km_s[1], 9),
                vz_km_s=round(velocity_km_s[2], 9),
            )
        slices.append(
            PropagatedOrbitSlice(
                slice_id=f"{slice_id_prefix}-{slice_index + 1:04d}",
                relative_time_seconds=round(step_seconds * slice_index, 6),
                timestamp_utc=timestamp.isoformat().replace("+00:00", "Z"),
                satellite_states=satellite_states,
            )
        )
    return tuple(slices)


def _parse_start_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _effective_slice_count(
    *,
    step_seconds: float,
    slice_count: int,
    horizon_seconds: float | None,
) -> int:
    if step_seconds <= 0.0:
        raise ValueError("--step-seconds must be positive")
    if horizon_seconds is None:
        return max(1, int(slice_count))
    if float(horizon_seconds) < 0.0:
        raise ValueError("--horizon-seconds must be non-negative")
    return max(1, int(math.floor(float(horizon_seconds) / step_seconds)) + 1)


def build_planned_shell_orbit_data(
    *,
    shell_id: str,
    max_satellites: int | None = None,
    start_time: datetime | None = None,
    step_seconds: float = 120.0,
    slice_count: int = 1,
    horizon_seconds: float | None = None,
) -> Dict[str, Any]:
    """Return the planned-shell orbit records and propagated slices Skyseed uses."""

    preset = planned_shell_preset(shell_id)
    selected_satellite_count = (
        preset.satellite_count if max_satellites is None else int(max_satellites)
    )
    if selected_satellite_count <= 0:
        raise ValueError("--max-satellites must be positive")
    if selected_satellite_count > preset.satellite_count:
        raise ValueError(
            f"--max-satellites {selected_satellite_count} exceeds planned shell "
            f"{preset.shell_id} capacity {preset.satellite_count}"
        )

    full_records = generate_planned_shell_records(shell_id=preset.shell_id)
    selection = select_starlink_shell(
        full_records,
        max_satellites=selected_satellite_count,
    )
    utc_start = (start_time or datetime.now(timezone.utc).replace(microsecond=0)).astimezone(
        timezone.utc
    )
    effective_slice_count = _effective_slice_count(
        step_seconds=float(step_seconds),
        slice_count=int(slice_count),
        horizon_seconds=horizon_seconds,
    )
    propagated_slices = propagate_planned_shell_records(
        selection.selected_records,
        start_time=utc_start,
        step_seconds=float(step_seconds),
        slice_count=effective_slice_count,
    )

    return {
        "schema_version": "skyseed-planned-shell-orbit-data-v1",
        "orbit_input_mode": "planned-shell",
        "orbit_input_kind": "planned_shell_circular_orbit",
        "orbit_source": f"starlink_phase1_{preset.shell_id}",
        "shell_preset": preset.to_dict(),
        "requested": {
            "shell_id": preset.shell_id,
            "max_satellites": selected_satellite_count,
            "start_time_utc": utc_start.isoformat().replace("+00:00", "Z"),
            "step_seconds": float(step_seconds),
            "slice_count": effective_slice_count,
            "horizon_seconds": (
                None if horizon_seconds is None else float(horizon_seconds)
            ),
        },
        "full_record_count": len(full_records),
        "selected_record_count": len(selection.selected_records),
        "shell_selection": selection.manifest,
        "selected_records": [
            record.to_dict()
            for record in selection.selected_records
        ],
        "propagated_positions": {
            "start_time_utc": utc_start.isoformat().replace("+00:00", "Z"),
            "step_seconds": float(step_seconds),
            "slice_count": effective_slice_count,
            "slices": [
                orbit_slice.to_dict()
                for orbit_slice in propagated_slices
            ],
        },
    }


def build_all_planned_shell_orbit_data(
    *,
    max_satellites: int | None = None,
    start_time: datetime | None = None,
    step_seconds: float = 120.0,
    slice_count: int = 1,
    horizon_seconds: float | None = None,
) -> Dict[str, Any]:
    """Return planned-shell orbit data for every configured shell preset."""

    utc_start = (start_time or datetime.now(timezone.utc).replace(microsecond=0)).astimezone(
        timezone.utc
    )
    shells = [
        build_planned_shell_orbit_data(
            shell_id=shell_id,
            max_satellites=max_satellites,
            start_time=utc_start,
            step_seconds=step_seconds,
            slice_count=slice_count,
            horizon_seconds=horizon_seconds,
        )
        for shell_id in sorted(PLANNED_STARLINK_PHASE1_SHELL_PRESETS)
    ]
    selected_record_count = sum(
        int(shell["selected_record_count"])
        for shell in shells
    )
    selected_records = [
        record
        for shell in shells
        for record in shell["selected_records"]
    ]
    included_satellite_ids = [
        record["norad_id"]
        for record in selected_records
    ]
    propagated_slices = []
    if shells:
        for slice_index, first_slice in enumerate(
            shells[0]["propagated_positions"]["slices"]
        ):
            satellite_states: Dict[str, Any] = {}
            for shell in shells:
                satellite_states.update(
                    shell["propagated_positions"]["slices"][slice_index][
                        "satellite_states"
                    ]
                )
            propagated_slices.append(
                {
                    "slice_id": first_slice["slice_id"],
                    "relative_time_seconds": first_slice["relative_time_seconds"],
                    "timestamp_utc": first_slice["timestamp_utc"],
                    "satellite_states": dict(sorted(satellite_states.items())),
                }
            )
    shell_selection = {
        "selection_method": "all planned shell presets",
        "cluster_inclination_bucket_deg": None,
        "cluster_altitude_bucket_km": None,
        "cluster_record_count": sum(
            int(shell["shell_selection"]["cluster_record_count"])
            for shell in shells
        ),
        "plane_count_in_cluster": sum(
            int(shell["shell_selection"]["plane_count_in_cluster"])
            for shell in shells
        ),
        "selected_plane_count": sum(
            int(shell["shell_selection"]["selected_plane_count"])
            for shell in shells
        ),
        "selected_satellite_count": selected_record_count,
        "max_satellites_requested": selected_record_count,
        "included_satellite_ids": included_satellite_ids,
        "parameter_bands": {
            "inclination_deg_min": round(
                min(record["inclination_deg"] for record in selected_records),
                6,
            ),
            "inclination_deg_max": round(
                max(record["inclination_deg"] for record in selected_records),
                6,
            ),
            "estimated_altitude_km_min": round(
                min(
                    shell["shell_selection"]["parameter_bands"][
                        "estimated_altitude_km_min"
                    ]
                    for shell in shells
                ),
                3,
            ),
            "estimated_altitude_km_max": round(
                max(
                    shell["shell_selection"]["parameter_bands"][
                        "estimated_altitude_km_max"
                    ]
                    for shell in shells
                ),
                3,
            ),
            "mean_motion_rev_per_day_min": round(
                min(
                    record["mean_motion_rev_per_day"]
                    for record in selected_records
                ),
                9,
            ),
            "mean_motion_rev_per_day_max": round(
                max(
                    record["mean_motion_rev_per_day"]
                    for record in selected_records
                ),
                9,
            ),
        },
        "plane_manifest": [
            {
                **plane_manifest,
                "plane_id": (
                    f"{shell['requested']['shell_id']}-{plane_manifest['plane_id']}"
                ),
            }
            for shell in shells
            for plane_manifest in shell["shell_selection"]["plane_manifest"]
        ],
    }

    return {
        "schema_version": "skyseed-planned-shell-orbit-data-v1",
        "orbit_input_mode": "planned-shell",
        "orbit_input_kind": "planned_shell_circular_orbit",
        "orbit_source": "starlink_phase1_all",
        "shell_preset": {
            "shell_id": ALL_SHELL_ID,
            "plane_count": sum(
                preset.plane_count
                for preset in PLANNED_STARLINK_PHASE1_SHELL_PRESETS.values()
            ),
            "satellites_per_plane": None,
            "satellite_count": selected_record_count,
            "altitude_km": None,
            "inclination_deg": None,
        },
        "requested": {
            "shell_id": ALL_SHELL_ID,
            "max_satellites": selected_record_count,
            "start_time_utc": utc_start.isoformat().replace("+00:00", "Z"),
            "step_seconds": float(step_seconds),
            "slice_count": shells[0]["requested"]["slice_count"] if shells else 0,
            "horizon_seconds": (
                None if horizon_seconds is None else float(horizon_seconds)
            ),
        },
        "full_record_count": sum(int(shell["full_record_count"]) for shell in shells),
        "selected_record_count": selected_record_count,
        "shell_selection": shell_selection,
        "selected_records": selected_records,
        "propagated_positions": {
            "start_time_utc": utc_start.isoformat().replace("+00:00", "Z"),
            "step_seconds": float(step_seconds),
            "slice_count": shells[0]["requested"]["slice_count"] if shells else 0,
            "slices": propagated_slices,
        },
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _strip_propagated_positions(payload: Dict[str, Any]) -> Dict[str, Any]:
    stripped = dict(payload)
    stripped.pop("propagated_positions", None)
    stripped["orbit_data_kind"] = "planned_shell_orbit_catalog"
    requested = dict(stripped.get("requested") or {})
    requested.pop("step_seconds", None)
    requested.pop("slice_count", None)
    requested.pop("horizon_seconds", None)
    stripped["requested"] = requested
    return stripped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export the deterministic planned-shell orbit records and propagated "
            "positions used by Skyseed planned-shell previews."
        )
    )
    parser.add_argument(
        "--shell-id",
        choices=tuple(sorted(PLANNED_STARLINK_PHASE1_SHELL_PRESETS)) + (ALL_SHELL_ID,),
        required=True,
        help="Planned shell preset to export: S1, S2, S3, S4, S5, or all.",
    )
    parser.add_argument(
        "--max-satellites",
        type=int,
        default=None,
        help=(
            "Selected satellite count. Defaults to the full shell capacity, or "
            "the S1-S5 total capacity when --shell-id all."
        ),
    )
    parser.add_argument(
        "--start-time",
        default=None,
        help="Propagation start time in ISO-8601 UTC. Default is current UTC time.",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=120.0,
        help="Seconds between propagated orbit slices.",
    )
    parser.add_argument(
        "--slice-count",
        type=int,
        default=1,
        help="Number of slices when --horizon-seconds is not set.",
    )
    parser.add_argument(
        "--horizon-seconds",
        type=float,
        default=None,
        help="Optional horizon; derives slice count as floor(horizon/step)+1.",
    )
    parser.add_argument(
        "--records-only",
        action="store_true",
        help=(
            "Write only the fixed planned-shell orbit records. This is the "
            "recommended form for sharing a stable satellite coordinate basis."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON to this path. Defaults to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    shell_id = str(args.shell_id).lower()
    start_time = _parse_start_time(args.start_time)
    if shell_id == ALL_SHELL_ID:
        payload = build_all_planned_shell_orbit_data(
            max_satellites=args.max_satellites,
            start_time=start_time,
            step_seconds=float(args.step_seconds),
            slice_count=int(args.slice_count),
            horizon_seconds=args.horizon_seconds,
        )
    else:
        payload = build_planned_shell_orbit_data(
            shell_id=str(args.shell_id),
            max_satellites=args.max_satellites,
            start_time=start_time,
            step_seconds=float(args.step_seconds),
            slice_count=int(args.slice_count),
            horizon_seconds=args.horizon_seconds,
        )
    if args.records_only:
        payload = _strip_propagated_positions(payload)
    if args.output is None:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _write_json(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
