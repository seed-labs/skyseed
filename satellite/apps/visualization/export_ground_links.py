#!/usr/bin/env python3
# encoding: utf-8

"""Standalone satellite-ground-station groundLinks prototype exporter.

Primary contract: generate or POST only the external groundLinks payload:
{"timestamp": "...", "interval": "...", "groundLinks": [[...]]}.

Planned-shell mode intentionally avoids Skyseed-internal imports so this
prototype can be copied to another host.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Sequence
from urllib import error, request


EARTH_RADIUS_KM = 6371.0
EARTH_MU_KM3_S2 = 398600.4418
DEFAULT_START_TIMESTAMP_MS = "1782439276134"


@dataclass(frozen=True)
class PlannedShellPreset:
    shell_id: str
    plane_count: int
    satellites_per_plane: int
    altitude_km: float
    inclination_deg: float

    @property
    def satellite_count(self) -> int:
        return self.plane_count * self.satellites_per_plane


PLANNED_STARLINK_PHASE1_SHELL_PRESETS: Dict[str, PlannedShellPreset] = {
    "S1": PlannedShellPreset("S1", 72, 22, 550.0, 53.0),
    "S2": PlannedShellPreset("S2", 72, 22, 540.0, 53.2),
    "S3": PlannedShellPreset("S3", 36, 20, 570.0, 70.0),
    "S4": PlannedShellPreset("S4", 6, 58, 560.0, 97.6),
    "S5": PlannedShellPreset("S5", 4, 43, 560.0, 97.6),
}


DEFAULT_GATEWAY_CATALOG: Sequence[Dict[str, Any]] = (
    {
        "gateway_id": "gw1",
        "name": "Reykjavik Gateway",
        "region": "north",
        "latitude_deg": 64.1466,
        "longitude_deg": -21.9426,
    },
    {
        "gateway_id": "gw2",
        "name": "Wellington Gateway",
        "region": "south",
        "latitude_deg": -41.2866,
        "longitude_deg": 174.7756,
    },
    {
        "gateway_id": "gw3",
        "name": "Hamilton Gateway",
        "region": "atlantic-west",
        "latitude_deg": 32.2948,
        "longitude_deg": -64.7814,
    },
    {
        "gateway_id": "gw4",
        "name": "Valletta Gateway",
        "region": "mediterranean",
        "latitude_deg": 35.8989,
        "longitude_deg": 14.5146,
    },
    {
        "gateway_id": "gw5",
        "name": "Nuuk Gateway",
        "region": "arctic",
        "latitude_deg": 64.1835,
        "longitude_deg": -51.7216,
    },
    {
        "gateway_id": "gw6",
        "name": "Ushuaia Gateway",
        "region": "antarctic",
        "latitude_deg": -54.8019,
        "longitude_deg": -68.3030,
    },
    {
        "gateway_id": "gw7",
        "name": "Seattle Gateway",
        "region": "pacific-northwest",
        "latitude_deg": 47.6062,
        "longitude_deg": -122.3321,
    },
    {
        "gateway_id": "gw8",
        "name": "Lisbon Gateway",
        "region": "atlantic-east",
        "latitude_deg": 38.7223,
        "longitude_deg": -9.1393,
    },
)


@dataclass(frozen=True)
class GroundStation:
    ground_station_id: str
    latitude_deg: float
    longitude_deg: float
    altitude_km: float = 0.0
    max_satellite_links: int | None = None


@dataclass(frozen=True)
class OrbitRecord:
    satellite_name: str
    norad_id: int
    epoch_utc: str
    inclination_deg: float
    eccentricity: float
    mean_motion_rev_per_day: float
    raan_deg: float
    argument_of_perigee_deg: float
    mean_anomaly_deg: float
    line1: str = ""
    line2: str = ""
    plane_index: int | None = None
    satellite_index_in_plane: int | None = None


@dataclass(frozen=True)
class NodePosition:
    x_km: float
    y_km: float
    z_km: float


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


@dataclass(frozen=True)
class PropagatedOrbitSlice:
    slice_id: str
    relative_time_seconds: float
    timestamp_utc: str
    satellite_states: Dict[str, PropagatedSatelliteState]


@dataclass(frozen=True)
class VisibleGroundLinkCandidate:
    satellite_id: str
    ground_station_id: str
    elevation_deg: float
    distance_km: float
    earth_clearance_km: float
    score: float = 0.0
    selection_reason: str = "candidate"
    retained_by_hysteresis: bool = False


@dataclass(frozen=True)
class GroundLinkSample:
    timestamp_utc: str
    links: Sequence[Dict[str, str]]
    candidates: Sequence[VisibleGroundLinkCandidate]
    selected_candidates: Sequence[VisibleGroundLinkCandidate]
    satellite_subpoints: Dict[str, Dict[str, float]]


@dataclass(frozen=True)
class GroundLinksResult:
    payload: Dict[str, Any]
    samples: Sequence[GroundLinkSample]
    stations: Sequence[GroundStation]
    satellite_id_format: str
    selection_metadata: Dict[str, Any]


def satellite_node_id(norad_id: int) -> str:
    return f"stl-{int(norad_id)}"


def _render_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_interval_seconds(value: str) -> float:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("interval must not be empty")
    if text.endswith("ms"):
        return float(text[:-2]) / 1000.0
    if text.endswith("s"):
        return float(text[:-1])
    return float(text)


def _parse_start_timestamp_ms(value: str | None) -> tuple[str, datetime]:
    if value is None or not str(value).strip():
        timestamp = datetime.now(timezone.utc)
        return str(int(timestamp.timestamp() * 1000)), timestamp
    text = str(value).strip()
    if text.isdigit():
        timestamp_ms = int(text)
        return text, datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_value = parsed.astimezone(timezone.utc)
    return str(int(utc_value.timestamp() * 1000)), utc_value


def _derive_steps(*, steps: int | None, duration: str | None, interval_seconds: float) -> int:
    if steps is not None:
        if int(steps) <= 0:
            raise ValueError("steps must be positive")
        return int(steps)
    if duration is None:
        return 3
    duration_seconds = _parse_interval_seconds(duration)
    if duration_seconds <= 0.0:
        raise ValueError("duration must be positive")
    return max(1, int(math.ceil(duration_seconds / interval_seconds)))


def _altitude_km_to_mean_motion_rev_per_day(altitude_km: float) -> float:
    semi_major_axis_km = EARTH_RADIUS_KM + altitude_km
    mean_motion_rad_per_second = math.sqrt(EARTH_MU_KM3_S2 / (semi_major_axis_km**3))
    return mean_motion_rad_per_second * 86400.0 / (2.0 * math.pi)


def _mean_motion_to_altitude_km(mean_motion_rev_per_day: float) -> float:
    mean_motion_rad_per_second = mean_motion_rev_per_day * 2.0 * math.pi / 86400.0
    semi_major_axis_km = (EARTH_MU_KM3_S2 / (mean_motion_rad_per_second**2)) ** (1.0 / 3.0)
    return semi_major_axis_km - EARTH_RADIUS_KM


def generate_planned_shell_records(
    *,
    shell_id: str,
    epoch_utc: datetime,
) -> Sequence[OrbitRecord]:
    normalized = str(shell_id).upper()
    if normalized not in PLANNED_STARLINK_PHASE1_SHELL_PRESETS:
        raise ValueError(f"unknown planned shell preset: {shell_id}")
    preset = PLANNED_STARLINK_PHASE1_SHELL_PRESETS[normalized]
    epoch = epoch_utc.astimezone(timezone.utc)
    mean_motion = _altitude_km_to_mean_motion_rev_per_day(preset.altitude_km)
    preset_index = list(PLANNED_STARLINK_PHASE1_SHELL_PRESETS).index(preset.shell_id) + 1
    base_norad_id = 900000 + (preset_index * 10000)
    records: List[OrbitRecord] = []
    for plane_index in range(preset.plane_count):
        raan_deg = (360.0 * plane_index) / preset.plane_count
        plane_phase_offset_deg = (180.0 * (plane_index % 2)) / preset.satellites_per_plane
        for satellite_index in range(preset.satellites_per_plane):
            mean_anomaly_deg = (
                (360.0 * satellite_index) / preset.satellites_per_plane
                + plane_phase_offset_deg
            ) % 360.0
            norad_id = base_norad_id + (plane_index * preset.satellites_per_plane) + satellite_index + 1
            records.append(
                OrbitRecord(
                    satellite_name=(
                        f"STARLINK-{preset.shell_id}-P{plane_index + 1:02d}-"
                        f"S{satellite_index + 1:02d}"
                    ),
                    norad_id=norad_id,
                    epoch_utc=_render_utc(epoch),
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


def _sample_evenly(items: Sequence[OrbitRecord], count: int) -> List[OrbitRecord]:
    if count >= len(items):
        return list(items)
    if count <= 0:
        return []
    selected: List[OrbitRecord] = []
    used: set[int] = set()
    for index in range(count):
        target = round(index * (len(items) - 1) / max(1, count - 1))
        while target in used and target + 1 < len(items):
            target += 1
        used.add(target)
        selected.append(items[target])
    return selected


def select_orbit_records(records: Sequence[OrbitRecord], *, max_satellites: int) -> Sequence[OrbitRecord]:
    if max_satellites <= 0:
        raise ValueError("max_satellites must be positive")
    if max_satellites >= len(records):
        return tuple(sorted(records, key=lambda record: record.norad_id))
    planned_records = [record for record in records if record.plane_index is not None]
    if len(planned_records) == len(records):
        planes: Dict[int, List[OrbitRecord]] = {}
        for record in planned_records:
            planes.setdefault(int(record.plane_index or 0), []).append(record)
        selected: List[OrbitRecord] = []
        plane_ids = sorted(planes)
        base_quota = max_satellites // len(plane_ids)
        extra = max_satellites % len(plane_ids)
        for offset, plane_id in enumerate(plane_ids):
            quota = base_quota + (1 if offset < extra else 0)
            selected.extend(
                _sample_evenly(
                    sorted(
                        planes[plane_id],
                        key=lambda record: (
                            record.satellite_index_in_plane or 0,
                            record.norad_id,
                        ),
                    ),
                    quota,
                )
            )
        return tuple(sorted(selected, key=lambda record: (record.plane_index or 0, record.norad_id)))
    return tuple(_sample_evenly(sorted(records, key=lambda record: record.norad_id), max_satellites))


def _parse_tle_epoch(epoch_token: str) -> datetime:
    stripped = epoch_token.strip()
    short_year = int(stripped[:2])
    year = 1900 + short_year if short_year >= 57 else 2000 + short_year
    day_of_year = float(stripped[2:])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1.0)


def parse_tle_catalog(raw_text: str) -> Sequence[OrbitRecord]:
    lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
    records: List[OrbitRecord] = []
    index = 0
    while index < len(lines):
        chunk = lines[index:index + 3]
        index += 3
        if len(chunk) != 3:
            break
        name_line, line1, line2 = chunk
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            continue
        try:
            norad_id = int(line1[2:7])
            if int(line2[2:7]) != norad_id:
                continue
            records.append(
                OrbitRecord(
                    satellite_name=name_line.strip(),
                    norad_id=norad_id,
                    epoch_utc=_render_utc(_parse_tle_epoch(line1[18:32])),
                    inclination_deg=float(line2[8:16]),
                    raan_deg=float(line2[17:25]),
                    eccentricity=float(f"0.{line2[26:33].strip()}"),
                    argument_of_perigee_deg=float(line2[34:42]),
                    mean_anomaly_deg=float(line2[43:51]),
                    mean_motion_rev_per_day=float(line2[52:63]),
                    line1=line1,
                    line2=line2,
                )
            )
        except Exception:
            continue
    return tuple(records)


def _load_orbit_records(
    *,
    planned_shell: str | None,
    tle_path: Path | None,
    max_satellites: int,
    start_time: datetime,
) -> tuple[str, Sequence[OrbitRecord]]:
    if planned_shell and tle_path:
        raise ValueError("use either --planned-shell or --tle, not both")
    if planned_shell:
        records = generate_planned_shell_records(
            shell_id=str(planned_shell).upper(),
            epoch_utc=start_time,
        )
        return "planned_shell", select_orbit_records(records, max_satellites=max_satellites)
    if tle_path is None:
        raise ValueError("one of --planned-shell or --tle is required")
    records = parse_tle_catalog(tle_path.read_text(encoding="utf-8"))
    if not records:
        raise ValueError(f"no valid TLE records found in {tle_path}")
    return "tle", select_orbit_records(records, max_satellites=max_satellites)


def _planned_position_velocity(record: OrbitRecord, timestamp: datetime) -> tuple[NodePosition, NodePosition]:
    epoch = datetime.fromisoformat(record.epoch_utc.replace("Z", "+00:00"))
    delta_seconds = (timestamp - epoch).total_seconds()
    semi_major_axis_km = EARTH_RADIUS_KM + _mean_motion_to_altitude_km(
        record.mean_motion_rev_per_day
    )
    true_anomaly_rad = math.radians(
        (
            record.mean_anomaly_deg
            + (record.mean_motion_rev_per_day * 360.0 * delta_seconds / 86400.0)
        )
        % 360.0
    )
    raan_rad = math.radians(record.raan_deg)
    inclination_rad = math.radians(record.inclination_deg)
    argument_of_perigee_rad = math.radians(record.argument_of_perigee_deg)
    orbital_speed_km_s = math.sqrt(EARTH_MU_KM3_S2 / semi_major_axis_km)

    argument_rad = argument_of_perigee_rad + true_anomaly_rad
    cos_raan = math.cos(raan_rad)
    sin_raan = math.sin(raan_rad)
    cos_inc = math.cos(inclination_rad)
    sin_inc = math.sin(inclination_rad)
    cos_arg = math.cos(argument_rad)
    sin_arg = math.sin(argument_rad)

    x_km = semi_major_axis_km * (cos_raan * cos_arg - sin_raan * sin_arg * cos_inc)
    y_km = semi_major_axis_km * (sin_raan * cos_arg + cos_raan * sin_arg * cos_inc)
    z_km = semi_major_axis_km * (sin_arg * sin_inc)

    vx_km_s = orbital_speed_km_s * (-cos_raan * sin_arg - sin_raan * cos_arg * cos_inc)
    vy_km_s = orbital_speed_km_s * (-sin_raan * sin_arg + cos_raan * cos_arg * cos_inc)
    vz_km_s = orbital_speed_km_s * (cos_arg * sin_inc)
    return (
        NodePosition(round(x_km, 6), round(y_km, 6), round(z_km, 6)),
        NodePosition(round(vx_km_s, 9), round(vy_km_s, 9), round(vz_km_s, 9)),
    )


def _propagate_planned_records(
    records: Sequence[OrbitRecord],
    *,
    start_time: datetime,
    step_seconds: float,
    slice_count: int,
) -> Sequence[PropagatedOrbitSlice]:
    slices: List[PropagatedOrbitSlice] = []
    utc_start = start_time.astimezone(timezone.utc)
    for slice_index in range(slice_count):
        timestamp = utc_start + timedelta(seconds=step_seconds * slice_index)
        satellite_states: Dict[str, PropagatedSatelliteState] = {}
        for record in records:
            position, velocity = _planned_position_velocity(record, timestamp)
            node_id = satellite_node_id(record.norad_id)
            satellite_states[node_id] = PropagatedSatelliteState(
                satellite_node_id=node_id,
                norad_id=record.norad_id,
                timestamp_utc=_render_utc(timestamp),
                x_km=position.x_km,
                y_km=position.y_km,
                z_km=position.z_km,
                vx_km_s=velocity.x_km,
                vy_km_s=velocity.y_km,
                vz_km_s=velocity.z_km,
            )
        slices.append(
            PropagatedOrbitSlice(
                slice_id=f"ground-links-slice-{slice_index + 1:04d}",
                relative_time_seconds=round(step_seconds * slice_index, 6),
                timestamp_utc=_render_utc(timestamp),
                satellite_states=satellite_states,
            )
        )
    return tuple(slices)


def _propagate_tle_records(
    records: Sequence[OrbitRecord],
    *,
    start_time: datetime,
    step_seconds: float,
    slice_count: int,
) -> Sequence[PropagatedOrbitSlice]:
    try:
        from sgp4.api import Satrec, jday  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("TLE propagation requires the optional sgp4 package") from exc

    satrecs = {record.norad_id: Satrec.twoline2rv(record.line1, record.line2) for record in records}
    slices: List[PropagatedOrbitSlice] = []
    utc_start = start_time.astimezone(timezone.utc)
    for slice_index in range(slice_count):
        timestamp = utc_start + timedelta(seconds=step_seconds * slice_index)
        second_with_fraction = timestamp.second + (timestamp.microsecond / 1_000_000.0)
        jd, fraction = jday(
            timestamp.year,
            timestamp.month,
            timestamp.day,
            timestamp.hour,
            timestamp.minute,
            second_with_fraction,
        )
        satellite_states: Dict[str, PropagatedSatelliteState] = {}
        for record in records:
            error_code, position_km, velocity_km_s = satrecs[record.norad_id].sgp4(jd, fraction)
            if error_code != 0:
                continue
            node_id = satellite_node_id(record.norad_id)
            satellite_states[node_id] = PropagatedSatelliteState(
                satellite_node_id=node_id,
                norad_id=record.norad_id,
                timestamp_utc=_render_utc(timestamp),
                x_km=round(float(position_km[0]), 6),
                y_km=round(float(position_km[1]), 6),
                z_km=round(float(position_km[2]), 6),
                vx_km_s=round(float(velocity_km_s[0]), 9),
                vy_km_s=round(float(velocity_km_s[1]), 9),
                vz_km_s=round(float(velocity_km_s[2]), 9),
            )
        slices.append(
            PropagatedOrbitSlice(
                slice_id=f"ground-links-slice-{slice_index + 1:04d}",
                relative_time_seconds=round(step_seconds * slice_index, 6),
                timestamp_utc=_render_utc(timestamp),
                satellite_states=satellite_states,
            )
        )
    return tuple(slices)


def _propagate_records(
    *,
    orbit_kind: str,
    records: Sequence[OrbitRecord],
    start_time: datetime,
    interval_seconds: float,
    steps: int,
) -> Sequence[PropagatedOrbitSlice]:
    if orbit_kind == "planned_shell":
        return _propagate_planned_records(
            records,
            start_time=start_time,
            step_seconds=interval_seconds,
            slice_count=steps,
        )
    return _propagate_tle_records(
        records,
        start_time=start_time,
        step_seconds=interval_seconds,
        slice_count=steps,
    )


def _coerce_ground_station_id(entry: Dict[str, Any]) -> str:
    for key in ("groundStationId", "ground_station_id", "gateway_id", "station_id", "id"):
        value = entry.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError(f"ground station entry is missing an id field: {entry}")


def _coerce_float(entry: Dict[str, Any], keys: Sequence[str], label: str) -> float:
    for key in keys:
        if key in entry and entry[key] is not None:
            return float(entry[key])
    raise ValueError(f"ground station entry is missing {label}: {entry}")


def _coerce_optional_positive_int(entry: Dict[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        if key not in entry or entry[key] is None:
            continue
        value = int(entry[key])
        if value <= 0:
            return None
        return value
    return None


def _catalog_entries_from_payload(payload: Any) -> Sequence[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("groundStations", "ground_stations", "gateways", "stations"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("ground station catalog must be a list or an object containing a station list")


def load_ground_station_catalog(path: Path | None = None) -> List[GroundStation]:
    entries: Sequence[Dict[str, Any]]
    if path is None:
        entries = DEFAULT_GATEWAY_CATALOG
    else:
        entries = _catalog_entries_from_payload(json.loads(path.read_text(encoding="utf-8")))
    stations = [
        GroundStation(
            ground_station_id=_coerce_ground_station_id(dict(entry)),
            latitude_deg=_coerce_float(dict(entry), ("latitude_deg", "latitude", "lat"), "latitude"),
            longitude_deg=_coerce_float(
                dict(entry),
                ("longitude_deg", "longitude", "lon", "lng"),
                "longitude",
            ),
            altitude_km=float(dict(entry).get("altitude_km", dict(entry).get("altitude", 0.0))),
            max_satellite_links=_coerce_optional_positive_int(
                dict(entry),
                (
                    "max_satellite_links",
                    "maxSatelliteLinks",
                    "antenna_count",
                    "antennaCount",
                    "capacity",
                ),
            ),
        )
        for entry in entries
    ]
    return sorted(stations, key=lambda item: item.ground_station_id)


def _select_ground_stations(
    stations: Sequence[GroundStation],
    *,
    selected_ids: str | None,
    max_ground_stations: int | None,
) -> List[GroundStation]:
    by_id = {station.ground_station_id: station for station in stations}
    if selected_ids:
        ids = [item.strip() for item in selected_ids.split(",") if item.strip()]
        missing = [station_id for station_id in ids if station_id not in by_id]
        if missing:
            raise ValueError(f"unknown ground station ids: {missing}")
        return [by_id[station_id] for station_id in ids]
    selected = list(stations)
    if max_ground_stations is not None:
        selected = selected[: max(0, int(max_ground_stations))]
    return selected


def _gmst_deg(timestamp: datetime) -> float:
    utc_value = timestamp.astimezone(timezone.utc)
    unix_seconds = utc_value.timestamp()
    julian_day = (unix_seconds / 86400.0) + 2440587.5
    centuries = (julian_day - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (julian_day - 2451545.0)
        + 0.000387933 * (centuries**2)
        - (centuries**3) / 38710000.0
    )
    return gmst % 360.0


def _ground_position_inertial(station: GroundStation, *, timestamp: datetime) -> NodePosition:
    latitude_rad = math.radians(station.latitude_deg)
    theta_rad = math.radians((_gmst_deg(timestamp) + station.longitude_deg) % 360.0)
    radius_km = EARTH_RADIUS_KM + station.altitude_km
    return NodePosition(
        x_km=radius_km * math.cos(latitude_rad) * math.cos(theta_rad),
        y_km=radius_km * math.cos(latitude_rad) * math.sin(theta_rad),
        z_km=radius_km * math.sin(latitude_rad),
    )


def _satellite_subpoint(position: NodePosition, *, timestamp: datetime) -> Dict[str, float]:
    radius_km = math.sqrt(position.x_km**2 + position.y_km**2 + position.z_km**2)
    latitude_deg = math.degrees(math.asin(max(-1.0, min(1.0, position.z_km / radius_km))))
    inertial_longitude_deg = math.degrees(math.atan2(position.y_km, position.x_km))
    longitude_deg = ((inertial_longitude_deg - _gmst_deg(timestamp) + 540.0) % 360.0) - 180.0
    return {
        "latitude_deg": round(latitude_deg, 6),
        "longitude_deg": round(longitude_deg, 6),
    }


def _distance_km(first: NodePosition, second: NodePosition) -> float:
    return math.sqrt(
        (first.x_km - second.x_km) ** 2
        + (first.y_km - second.y_km) ** 2
        + (first.z_km - second.z_km) ** 2
    )


def _earth_clearance_km(first: NodePosition, second: NodePosition) -> float:
    segment_x = second.x_km - first.x_km
    segment_y = second.y_km - first.y_km
    segment_z = second.z_km - first.z_km
    segment_norm_sq = segment_x**2 + segment_y**2 + segment_z**2
    if segment_norm_sq == 0.0:
        closest = first
    else:
        t = -(
            first.x_km * segment_x
            + first.y_km * segment_y
            + first.z_km * segment_z
        ) / segment_norm_sq
        if t <= 0.0 or t >= 1.0:
            return 999999999.0
        bounded_t = max(0.0, min(1.0, t))
        closest = NodePosition(
            x_km=first.x_km + bounded_t * segment_x,
            y_km=first.y_km + bounded_t * segment_y,
            z_km=first.z_km + bounded_t * segment_z,
        )
    closest_radius = math.sqrt(closest.x_km**2 + closest.y_km**2 + closest.z_km**2)
    return closest_radius - EARTH_RADIUS_KM


def _elevation_deg(owner_position: NodePosition, candidate_position: NodePosition) -> float:
    los_x = candidate_position.x_km - owner_position.x_km
    los_y = candidate_position.y_km - owner_position.y_km
    los_z = candidate_position.z_km - owner_position.z_km
    los_norm = math.sqrt(los_x**2 + los_y**2 + los_z**2)
    owner_norm = math.sqrt(
        owner_position.x_km**2 + owner_position.y_km**2 + owner_position.z_km**2
    )
    if los_norm == 0.0 or owner_norm == 0.0:
        return -90.0
    vertical_component = (
        los_x * owner_position.x_km
        + los_y * owner_position.y_km
        + los_z * owner_position.z_km
    ) / (los_norm * owner_norm)
    bounded_vertical = max(-1.0, min(1.0, vertical_component))
    return math.degrees(math.asin(bounded_vertical))


def _ground_station_capacity_map(
    stations: Sequence[GroundStation],
    *,
    override_capacity: int | None,
) -> Dict[str, int | None]:
    capacity_by_station: Dict[str, int | None] = {}
    for station in stations:
        if override_capacity is None:
            capacity_by_station[station.ground_station_id] = station.max_satellite_links
        elif int(override_capacity) <= 0:
            capacity_by_station[station.ground_station_id] = None
        else:
            capacity_by_station[station.ground_station_id] = int(override_capacity)
    return capacity_by_station


def _capacity_available(
    *,
    ground_station_id: str,
    assigned_count_by_station: Dict[str, int],
    capacity_by_station: Dict[str, int | None],
) -> bool:
    capacity = capacity_by_station.get(ground_station_id)
    if capacity is None:
        return True
    return assigned_count_by_station.get(ground_station_id, 0) < capacity


def _visible_ground_link_candidates(
    orbit_slice: PropagatedOrbitSlice,
    *,
    stations: Sequence[GroundStation],
    min_elevation_deg: float,
    earth_clearance_km: float,
    max_distance_km: float | None,
) -> tuple[List[VisibleGroundLinkCandidate], Dict[str, Dict[str, float]]]:
    timestamp = datetime.fromisoformat(orbit_slice.timestamp_utc.replace("Z", "+00:00"))
    station_positions = {
        station.ground_station_id: _ground_position_inertial(station, timestamp=timestamp)
        for station in stations
    }
    satellite_positions = {
        satellite_id: NodePosition(state.x_km, state.y_km, state.z_km)
        for satellite_id, state in orbit_slice.satellite_states.items()
    }
    subpoints = {
        satellite_id: _satellite_subpoint(position, timestamp=timestamp)
        for satellite_id, position in satellite_positions.items()
    }
    candidates: List[VisibleGroundLinkCandidate] = []
    for satellite_id in sorted(satellite_positions):
        satellite_position = satellite_positions[satellite_id]
        for station in stations:
            ground_position = station_positions[station.ground_station_id]
            elevation_deg = _elevation_deg(ground_position, satellite_position)
            if elevation_deg < min_elevation_deg:
                continue
            clearance_km = _earth_clearance_km(ground_position, satellite_position)
            if clearance_km < earth_clearance_km:
                continue
            distance_km = _distance_km(ground_position, satellite_position)
            if max_distance_km is not None and distance_km > max_distance_km:
                continue
            candidates.append(
                VisibleGroundLinkCandidate(
                    satellite_id=satellite_id,
                    ground_station_id=station.ground_station_id,
                    elevation_deg=elevation_deg,
                    distance_km=distance_km,
                    earth_clearance_km=clearance_km,
                )
            )
    return candidates, subpoints


def _candidate_sort_key(candidate: VisibleGroundLinkCandidate) -> tuple[float, float, str, str]:
    return (
        -candidate.score,
        -candidate.elevation_deg,
        candidate.distance_km,
        candidate.ground_station_id,
        candidate.satellite_id,
    )


def _scored_candidate(
    candidate: VisibleGroundLinkCandidate,
    *,
    score: float,
    selection_reason: str,
    retained_by_hysteresis: bool = False,
) -> VisibleGroundLinkCandidate:
    return VisibleGroundLinkCandidate(
        satellite_id=candidate.satellite_id,
        ground_station_id=candidate.ground_station_id,
        elevation_deg=candidate.elevation_deg,
        distance_km=candidate.distance_km,
        earth_clearance_km=candidate.earth_clearance_km,
        score=round(float(score), 6),
        selection_reason=selection_reason,
        retained_by_hysteresis=retained_by_hysteresis,
    )


def _score_candidate(
    candidate: VisibleGroundLinkCandidate,
    *,
    previous_ground_station_id: str | None,
    assigned_count_by_station: Dict[str, int],
    capacity_by_station: Dict[str, int | None],
    elevation_weight: float,
    distance_weight: float,
    handover_penalty: float,
    load_penalty: float,
) -> float:
    score = (candidate.elevation_deg * elevation_weight) - (
        candidate.distance_km * distance_weight
    )
    if previous_ground_station_id is not None and candidate.ground_station_id != previous_ground_station_id:
        score -= handover_penalty
    capacity = capacity_by_station.get(candidate.ground_station_id)
    current_load = assigned_count_by_station.get(candidate.ground_station_id, 0)
    if capacity is not None and capacity > 0:
        score -= load_penalty * (current_load / capacity)
    else:
        score -= load_penalty * current_load
    return score


def _rank_candidates_for_satellite(
    candidates: Sequence[VisibleGroundLinkCandidate],
    *,
    previous_ground_station_id: str | None,
    assigned_count_by_station: Dict[str, int],
    capacity_by_station: Dict[str, int | None],
    elevation_weight: float,
    distance_weight: float,
    handover_penalty: float,
    load_penalty: float,
) -> List[VisibleGroundLinkCandidate]:
    scored = [
        _scored_candidate(
            candidate,
            score=_score_candidate(
                candidate,
                previous_ground_station_id=previous_ground_station_id,
                assigned_count_by_station=assigned_count_by_station,
                capacity_by_station=capacity_by_station,
                elevation_weight=elevation_weight,
                distance_weight=distance_weight,
                handover_penalty=handover_penalty,
                load_penalty=load_penalty,
            ),
            selection_reason="ranked_by_score",
        )
        for candidate in candidates
    ]
    return sorted(scored, key=_candidate_sort_key)


def _select_all_visible_ground_links(
    candidates: Sequence[VisibleGroundLinkCandidate],
) -> List[VisibleGroundLinkCandidate]:
    return sorted(candidates, key=lambda item: (item.ground_station_id, item.satellite_id))


def _select_per_ground_station_best_links(
    candidates: Sequence[VisibleGroundLinkCandidate],
) -> List[VisibleGroundLinkCandidate]:
    candidates_by_station: Dict[str, List[VisibleGroundLinkCandidate]] = {}
    for candidate in candidates:
        candidates_by_station.setdefault(candidate.ground_station_id, []).append(candidate)
    selected = [
        sorted(station_candidates, key=_candidate_sort_key)[0]
        for station_candidates in candidates_by_station.values()
        if station_candidates
    ]
    return sorted(selected, key=lambda item: (item.ground_station_id, item.satellite_id))


def _select_per_satellite_best_links(
    candidates: Sequence[VisibleGroundLinkCandidate],
    *,
    previous_assignment_by_satellite: Dict[str, str],
    hold_steps_by_satellite: Dict[str, int],
    hysteresis_elevation_deg: float,
    capacity_by_station: Dict[str, int | None],
    top_k: int,
    min_hold_steps: int,
    elevation_weight: float,
    distance_weight: float,
    handover_penalty: float,
    load_penalty: float,
) -> List[VisibleGroundLinkCandidate]:
    candidates_by_satellite: Dict[str, List[VisibleGroundLinkCandidate]] = {}
    for candidate in candidates:
        candidates_by_satellite.setdefault(candidate.satellite_id, []).append(candidate)
    for satellite_id, satellite_candidates in list(candidates_by_satellite.items()):
        candidates_by_satellite[satellite_id] = sorted(
            satellite_candidates,
            key=lambda item: (-item.elevation_deg, item.distance_km, item.ground_station_id),
        )

    assigned_by_satellite: Dict[str, VisibleGroundLinkCandidate] = {}
    assigned_count_by_station: Dict[str, int] = {}

    for satellite_id in sorted(candidates_by_satellite):
        previous_station_id = previous_assignment_by_satellite.get(satellite_id)
        if previous_station_id is None:
            continue
        satellite_candidates = candidates_by_satellite[satellite_id]
        previous_candidate = next(
            (
                candidate
                for candidate in satellite_candidates
                if candidate.ground_station_id == previous_station_id
            ),
            None,
        )
        if previous_candidate is None:
            continue
        ranked_candidates = _rank_candidates_for_satellite(
            satellite_candidates,
            previous_ground_station_id=previous_station_id,
            assigned_count_by_station=assigned_count_by_station,
            capacity_by_station=capacity_by_station,
            elevation_weight=elevation_weight,
            distance_weight=distance_weight,
            handover_penalty=handover_penalty,
            load_penalty=load_penalty,
        )
        best_candidate = ranked_candidates[0]
        previous_ranked_candidate = next(
            (
                candidate
                for candidate in ranked_candidates
                if candidate.ground_station_id == previous_station_id
            ),
            None,
        )
        if previous_ranked_candidate is None:
            continue
        if hold_steps_by_satellite.get(satellite_id, 0) < min_hold_steps:
            retain_previous = True
        else:
            retain_previous = (
                previous_ranked_candidate.elevation_deg + hysteresis_elevation_deg
                >= best_candidate.elevation_deg
            )
        if not retain_previous:
            continue
        if not _capacity_available(
            ground_station_id=previous_station_id,
            assigned_count_by_station=assigned_count_by_station,
            capacity_by_station=capacity_by_station,
        ):
            continue
        assigned_by_satellite[satellite_id] = _scored_candidate(
            previous_ranked_candidate,
            score=previous_ranked_candidate.score,
            selection_reason="retained_by_min_hold" if hold_steps_by_satellite.get(satellite_id, 0) < min_hold_steps else "retained_by_hysteresis",
            retained_by_hysteresis=True,
        )
        assigned_count_by_station[previous_station_id] = (
            assigned_count_by_station.get(previous_station_id, 0) + 1
        )

    remaining_satellite_ids = [
        satellite_id
        for satellite_id in sorted(
            candidates_by_satellite,
            key=lambda item: _candidate_sort_key(candidates_by_satellite[item][0]),
        )
        if satellite_id not in assigned_by_satellite
    ]
    for satellite_id in remaining_satellite_ids:
        previous_station_id = previous_assignment_by_satellite.get(satellite_id)
        ranked_candidates = _rank_candidates_for_satellite(
            candidates_by_satellite[satellite_id],
            previous_ground_station_id=previous_station_id,
            assigned_count_by_station=assigned_count_by_station,
            capacity_by_station=capacity_by_station,
            elevation_weight=elevation_weight,
            distance_weight=distance_weight,
            handover_penalty=handover_penalty,
            load_penalty=load_penalty,
        )
        selected_count = 0
        for candidate in ranked_candidates:
            if not _capacity_available(
                ground_station_id=candidate.ground_station_id,
                assigned_count_by_station=assigned_count_by_station,
                capacity_by_station=capacity_by_station,
            ):
                continue
            key = satellite_id if top_k <= 1 else f"{satellite_id}#{selected_count + 1}"
            assigned_by_satellite[key] = _scored_candidate(
                candidate,
                score=candidate.score,
                selection_reason="selected_by_score",
            )
            assigned_count_by_station[candidate.ground_station_id] = (
                assigned_count_by_station.get(candidate.ground_station_id, 0) + 1
            )
            selected_count += 1
            if selected_count >= max(1, int(top_k)):
                break
    return sorted(assigned_by_satellite.values(), key=lambda item: (item.ground_station_id, item.satellite_id))


def _select_ground_link_candidates(
    candidates: Sequence[VisibleGroundLinkCandidate],
    *,
    selection_strategy: str,
    previous_assignment_by_satellite: Dict[str, str],
    hold_steps_by_satellite: Dict[str, int],
    hysteresis_elevation_deg: float,
    capacity_by_station: Dict[str, int | None],
    top_k: int,
    min_hold_steps: int,
    elevation_weight: float,
    distance_weight: float,
    handover_penalty: float,
    load_penalty: float,
) -> List[VisibleGroundLinkCandidate]:
    if selection_strategy == "all-visible":
        return [
            _scored_candidate(candidate, score=candidate.elevation_deg, selection_reason="all_visible")
            for candidate in _select_all_visible_ground_links(candidates)
        ]
    if selection_strategy == "per-ground-station-best":
        return [
            _scored_candidate(candidate, score=candidate.elevation_deg, selection_reason="selected_by_elevation")
            for candidate in _select_per_ground_station_best_links(candidates)
        ]
    if selection_strategy in {"per-satellite-best", "top-k-per-satellite"}:
        return _select_per_satellite_best_links(
            candidates,
            previous_assignment_by_satellite=previous_assignment_by_satellite,
            hold_steps_by_satellite=hold_steps_by_satellite,
            hysteresis_elevation_deg=hysteresis_elevation_deg,
            capacity_by_station=capacity_by_station,
            top_k=(top_k if selection_strategy == "top-k-per-satellite" else 1),
            min_hold_steps=min_hold_steps,
            elevation_weight=elevation_weight,
            distance_weight=distance_weight,
            handover_penalty=handover_penalty,
            load_penalty=load_penalty,
        )
    raise ValueError(f"unsupported selection strategy: {selection_strategy}")


def _render_satellite_id(satellite_node_id_value: str, *, satellite_id_format: str) -> str:
    if satellite_id_format == "node-id":
        return satellite_node_id_value
    if satellite_node_id_value.startswith("stl-"):
        return satellite_node_id_value.removeprefix("stl-")
    return satellite_node_id_value


def _render_clearance_km(value: float) -> float | None:
    if value > 100000000.0:
        return None
    return round(value, 3)


def build_ground_links_result(
    *,
    planned_shell: str | None = "S5",
    tle_path: Path | None = None,
    ground_station_catalog: Path | None = None,
    ground_station_ids: str | None = None,
    max_ground_stations: int | None = None,
    start_timestamp: str | None = DEFAULT_START_TIMESTAMP_MS,
    interval: str = "1s",
    steps: int | None = 3,
    duration: str | None = None,
    max_satellites: int = 24,
    min_elevation_deg: float = 5.0,
    satellite_id_format: str = "norad",
    selection_strategy: str = "per-satellite-best",
    top_k: int = 1,
    max_satellites_per_ground_station: int | None = None,
    hysteresis_elevation_deg: float = 2.0,
    earth_clearance_km: float = 0.0,
    max_distance_km: float | None = None,
    elevation_weight: float = 1.0,
    distance_weight: float = 0.002,
    handover_penalty: float = 3.0,
    load_penalty: float = 0.5,
    min_hold_steps: int = 2,
) -> GroundLinksResult:
    timestamp_ms, start_time = _parse_start_timestamp_ms(start_timestamp)
    interval_seconds = _parse_interval_seconds(interval)
    if interval_seconds <= 0.0:
        raise ValueError("interval must be positive")
    effective_steps = _derive_steps(steps=steps, duration=duration, interval_seconds=interval_seconds)
    stations = _select_ground_stations(
        load_ground_station_catalog(ground_station_catalog),
        selected_ids=ground_station_ids,
        max_ground_stations=max_ground_stations,
    )
    if not stations:
        raise ValueError("no ground stations selected")
    orbit_kind, records = _load_orbit_records(
        planned_shell=planned_shell,
        tle_path=tle_path,
        max_satellites=max_satellites,
        start_time=start_time,
    )
    propagated_slices = _propagate_records(
        orbit_kind=orbit_kind,
        records=records,
        start_time=start_time,
        interval_seconds=interval_seconds,
        steps=effective_steps,
    )
    capacity_by_station = _ground_station_capacity_map(
        stations,
        override_capacity=max_satellites_per_ground_station,
    )

    ground_links: List[List[Dict[str, str]]] = []
    samples: List[GroundLinkSample] = []
    previous_assignment_by_satellite: Dict[str, str] = {}
    hold_steps_by_satellite: Dict[str, int] = {}
    for orbit_slice in propagated_slices:
        candidates, subpoints = _visible_ground_link_candidates(
            orbit_slice,
            stations=stations,
            min_elevation_deg=float(min_elevation_deg),
            earth_clearance_km=float(earth_clearance_km),
            max_distance_km=max_distance_km,
        )
        selected_candidates = _select_ground_link_candidates(
            candidates,
            selection_strategy=selection_strategy,
            previous_assignment_by_satellite=previous_assignment_by_satellite,
            hold_steps_by_satellite=hold_steps_by_satellite,
            hysteresis_elevation_deg=float(hysteresis_elevation_deg),
            capacity_by_station=capacity_by_station,
            top_k=int(top_k),
            min_hold_steps=int(min_hold_steps),
            elevation_weight=float(elevation_weight),
            distance_weight=float(distance_weight),
            handover_penalty=float(handover_penalty),
            load_penalty=float(load_penalty),
        )
        next_assignment_by_satellite: Dict[str, str] = {}
        for candidate in sorted(selected_candidates, key=_candidate_sort_key):
            next_assignment_by_satellite.setdefault(
                candidate.satellite_id,
                candidate.ground_station_id,
            )
        next_hold_steps: Dict[str, int] = {}
        for satellite_id, ground_station_id in next_assignment_by_satellite.items():
            if previous_assignment_by_satellite.get(satellite_id) == ground_station_id:
                next_hold_steps[satellite_id] = hold_steps_by_satellite.get(satellite_id, 0) + 1
            else:
                next_hold_steps[satellite_id] = 0
        previous_assignment_by_satellite = next_assignment_by_satellite
        hold_steps_by_satellite = next_hold_steps
        links = [
            {
                "satelliteId": _render_satellite_id(
                    candidate.satellite_id,
                    satellite_id_format=satellite_id_format,
                ),
                "groundStationId": candidate.ground_station_id,
            }
            for candidate in selected_candidates
        ]
        sorted_links = sorted(links, key=lambda item: (item["groundStationId"], item["satelliteId"]))
        ground_links.append(sorted_links)
        samples.append(
            GroundLinkSample(
                timestamp_utc=orbit_slice.timestamp_utc,
                links=sorted_links,
                candidates=tuple(candidates),
                selected_candidates=tuple(selected_candidates),
                satellite_subpoints=subpoints,
            )
        )
    payload = {"timestamp": timestamp_ms, "interval": str(interval), "groundLinks": ground_links}
    return GroundLinksResult(
        payload=payload,
        samples=tuple(samples),
        stations=tuple(stations),
        satellite_id_format=satellite_id_format,
        selection_metadata={
            "strategy": selection_strategy,
            "topK": int(top_k),
            "minElevationDeg": float(min_elevation_deg),
            "hysteresisElevationDeg": float(hysteresis_elevation_deg),
            "earthClearanceKm": float(earth_clearance_km),
            "maxDistanceKm": max_distance_km,
            "elevationWeight": float(elevation_weight),
            "distanceWeight": float(distance_weight),
            "handoverPenalty": float(handover_penalty),
            "loadPenalty": float(load_penalty),
            "minHoldSteps": int(min_hold_steps),
            "maxSatellitesPerGroundStation": max_satellites_per_ground_station,
            "orbitKind": orbit_kind,
            "selectedSatelliteCount": len(records),
            "selectedGroundStationCount": len(stations),
        },
    )


def build_ground_links_payload(**kwargs: Any) -> Dict[str, Any]:
    return build_ground_links_result(**kwargs).payload


def post_payload(url: str, payload: Dict[str, Any], *, timeout_seconds: float = 15.0) -> int:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            print(f"POST succeeded: status={response.status}")
            if response_body:
                print(response_body)
            return 0
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        print(f"POST failed: status={exc.code}", file=sys.stderr)
        print(response_body, file=sys.stderr)
        return 1
    except error.URLError as exc:
        print(f"POST failed: {exc}", file=sys.stderr)
        return 1


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the satellite-ground-station groundLinks JSON payload for "
            "external consumers. Planned-shell mode is standalone and needs "
            "only Python standard libraries."
        ),
        epilog=(
            "Minimal dry run:\n"
            "  export_ground_links.py --planned-shell S4 --steps 3 --interval 1s\n\n"
            "Payload contract:\n"
            "  {\"timestamp\":\"...\",\"interval\":\"1s\",\"groundLinks\":[[{\"satelliteId\":\"...\",\"groundStationId\":\"...\"}]]}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    orbit = parser.add_mutually_exclusive_group()
    orbit.add_argument(
        "--planned-shell",
        choices=tuple(sorted(PLANNED_STARLINK_PHASE1_SHELL_PRESETS)),
        default=None,
        help="Use a deterministic planned shell preset. Default: S5.",
    )
    orbit.add_argument("--tle", type=Path, default=None, help="Path to a three-line TLE catalog.")
    parser.add_argument("--ground-station-catalog", type=Path, default=None, help="Ground station JSON catalog. Defaults to built-in demo stations.")
    parser.add_argument("--ground-station-ids", default=None, help="Comma-separated station ids to include.")
    parser.add_argument("--max-ground-stations", type=int, default=3, help="Demo/default catalog cap when ids are not specified.")
    parser.add_argument("--start-timestamp", default=DEFAULT_START_TIMESTAMP_MS, help="Start timestamp, epoch milliseconds or ISO-8601.")
    parser.add_argument("--interval", default="1s", help='Sampling interval such as "1s" or "30s".')
    parser.add_argument("--steps", type=int, default=None, help="Number of samples. Defaults to 3 unless --duration is set.")
    parser.add_argument("--duration", default=None, help="Optional duration; derives steps from interval.")
    parser.add_argument("--max-satellites", type=int, default=24, help="Prototype satellite sample cap.")
    parser.add_argument("--min-elevation-deg", type=float, default=5.0, help="Minimum satellite elevation for a candidate link.")
    parser.add_argument("--max-satellites-per-ground-station", type=int, default=None, help="Optional station capacity override; 0 means unlimited.")
    parser.add_argument("--hysteresis-elevation-deg", type=float, default=2.0, help="Handover hysteresis margin in degrees.")
    parser.add_argument("--earth-clearance-km", type=float, default=0.0, help="Minimum internal line-of-sight clearance above Earth.")
    parser.add_argument("--max-distance-km", type=float, default=None, help="Optional maximum satellite-ground distance.")
    parser.add_argument("--satellite-id-format", choices=("norad", "node-id"), default="norad", help="Payload satellite id format.")
    parser.add_argument(
        "--dry-run-json",
        nargs="?",
        const="-",
        default="-",
        help="Print JSON to stdout or write it to the optional path. This is the default mode.",
    )
    parser.add_argument("--post-url", default=None, help="POST payload URL. Omit for dry-run.")
    parser.add_argument("--post-timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_ground_links_result(
        planned_shell=(None if args.tle is not None else (args.planned_shell or "S5")),
        tle_path=args.tle,
        ground_station_catalog=args.ground_station_catalog,
        ground_station_ids=args.ground_station_ids,
        max_ground_stations=args.max_ground_stations,
        start_timestamp=args.start_timestamp,
        interval=args.interval,
        steps=args.steps,
        duration=args.duration,
        max_satellites=args.max_satellites,
        min_elevation_deg=args.min_elevation_deg,
        satellite_id_format=args.satellite_id_format,
        max_satellites_per_ground_station=args.max_satellites_per_ground_station,
        hysteresis_elevation_deg=args.hysteresis_elevation_deg,
        earth_clearance_km=args.earth_clearance_km,
        max_distance_km=args.max_distance_km,
    )
    if args.post_url:
        return post_payload(
            str(args.post_url),
            result.payload,
            timeout_seconds=float(args.post_timeout_seconds),
        )
    if args.dry_run_json == "-":
        print(json.dumps(result.payload, indent=2, sort_keys=True))
    else:
        _write_json(Path(args.dry_run_json), result.payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
