"""Translate selected v1 data files into generic v2 static specifications.

This experiment adapter owns all knowledge of the historical JSON shapes.  It
does not import v1 modules, expose v1 dictionaries, or define a public v2 file
format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from json import JSONDecodeError, loads
from math import isfinite, pi
from pathlib import Path
from typing import Any

from examples.reference.v1_reference_snapshot import (
    GitCommandRunner,
    PINNED_V1_REFERENCE_COMMIT,
    V1ReferenceSnapshotAudit,
    validate_v1_reference_snapshot,
)
from skyseed_v2.configuration import (
    GeneratedOrbitLayout,
    GroundStationConfig,
    InOrbitPhaseDistribution,
    RightAscensionDistribution,
    ShellConfig,
)


EARTH_MU_KM3_S2 = 398600.4418
REFERENCE_EARTH_RADIUS_KM = 6371.0
ANGLE_TOLERANCE_DEG = 0.00002


@dataclass(frozen=True, slots=True)
class ReferenceStaticScenarioSpecifications:
    """Typed output of the reference-only translation boundary."""

    shell: ShellConfig
    ground_stations: tuple[GroundStationConfig, ...]


def _read_json(path: Path) -> Any:
    try:
        return loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read reference input {path}") from error
    except JSONDecodeError as error:
        raise ValueError(f"reference input is not valid JSON: {path}") from error


def _required_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _required_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a non-empty JSON list")
    return value


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite number") from error
    if not isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _circular_delta(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _mean_motion_to_altitude_km(mean_motion_rev_per_day: float) -> float:
    mean_motion_rad_per_second = mean_motion_rev_per_day * 2.0 * pi / 86400.0
    semi_major_axis_km = (
        EARTH_MU_KM3_S2 / (mean_motion_rad_per_second**2)
    ) ** (1.0 / 3.0)
    return semi_major_axis_km - REFERENCE_EARTH_RADIUS_KM


class V1StaticInputAdapter:
    """Read historical data only at the reference/experiment boundary."""

    def __init__(
        self,
        *,
        reference_root: Path,
        pinned_commit: str = PINNED_V1_REFERENCE_COMMIT,
        snapshot_command_runner: GitCommandRunner | None = None,
    ) -> None:
        self._reference_root = Path(reference_root).resolve()
        self._pinned_commit = pinned_commit
        self._snapshot_command_runner = snapshot_command_runner
        self._reference_audit = self._validate_reference_snapshot()
        self._orbit_catalog_path = (
            self._reference_root
            / "data/orbits/planned_shell_orbit_data_s1_s5.json"
        )
        self._gateway_catalog_path = (
            self._reference_root / "data/ground/starlink_gateways.json"
        )
        self._gateway_binding_path = (
            self._reference_root
            / "data/ground/s4_visualization_gateway_binding_12.json"
        )

    @property
    def reference_audit(self) -> V1ReferenceSnapshotAudit:
        return self._reference_audit

    def _validate_reference_snapshot(self) -> V1ReferenceSnapshotAudit:
        return validate_v1_reference_snapshot(
            self._reference_root,
            pinned_commit=self._pinned_commit,
            command_runner=self._snapshot_command_runner,
        )

    def load(
        self,
        *,
        reference_shell_label: str,
        logical_shell_id: str,
        operator: str,
        station_type: str = "gateway",
    ) -> ReferenceStaticScenarioSpecifications:
        self._reference_audit = self._validate_reference_snapshot()
        return ReferenceStaticScenarioSpecifications(
            shell=self._load_shell(
                reference_shell_label=reference_shell_label,
                logical_shell_id=logical_shell_id,
            ),
            ground_stations=self._load_ground_stations(
                operator=operator,
                station_type=station_type,
            ),
        )

    def _load_shell(
        self,
        *,
        reference_shell_label: str,
        logical_shell_id: str,
    ) -> ShellConfig:
        shell_label = _text(reference_shell_label, "reference_shell_label").upper()
        payload = _required_mapping(
            _read_json(self._orbit_catalog_path), "orbit catalog"
        )
        raw_records = _required_list(
            payload.get("selected_records"), "orbit catalog selected_records"
        )
        prefix = f"STARLINK-{shell_label}-"
        records = [
            _required_mapping(record, "orbit record")
            for record in raw_records
            if isinstance(record, dict)
            and str(record.get("satellite_name", "")).startswith(prefix)
        ]
        if not records:
            raise ValueError(f"orbit catalog contains no {shell_label} records")

        records_by_plane: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            plane_index = _positive_integer(
                record.get("plane_index"), "plane_index"
            )
            records_by_plane.setdefault(plane_index, []).append(record)
        plane_indices = sorted(records_by_plane)
        if plane_indices != list(range(1, len(plane_indices) + 1)):
            raise ValueError("orbit catalog plane_index values must be contiguous")

        satellites_per_plane = len(records_by_plane[plane_indices[0]])
        if satellites_per_plane <= 0 or any(
            len(records_by_plane[index]) != satellites_per_plane
            for index in plane_indices
        ):
            raise ValueError("orbit catalog planes must have equal non-zero sizes")

        inclination_values = [
            _finite_number(record.get("inclination_deg"), "inclination_deg")
            for record in records
        ]
        inclination = inclination_values[0]
        if any(abs(value - inclination) > ANGLE_TOLERANCE_DEG for value in inclination_values):
            raise ValueError("orbit catalog contains multiple inclination values")

        mean_motion_values = [
            _finite_number(
                record.get("mean_motion_rev_per_day"),
                "mean_motion_rev_per_day",
            )
            for record in records
        ]
        mean_motion = mean_motion_values[0]
        if mean_motion <= 0.0 or any(
            abs(value - mean_motion) > 0.000000001
            for value in mean_motion_values
        ):
            raise ValueError("orbit catalog contains invalid mean motion values")

        orbit_count = len(plane_indices)
        raan_step = 360.0 / orbit_count
        phase_step = 360.0 / satellites_per_plane
        plane_starts: list[float] = []
        first_raan: float | None = None
        for zero_based_plane, plane_index in enumerate(plane_indices):
            plane_records = sorted(
                records_by_plane[plane_index],
                key=lambda record: _positive_integer(
                    record.get("satellite_index_in_plane"),
                    "satellite_index_in_plane",
                ),
            )
            satellite_indices = [
                _positive_integer(
                    record.get("satellite_index_in_plane"),
                    "satellite_index_in_plane",
                )
                for record in plane_records
            ]
            if satellite_indices != list(range(1, satellites_per_plane + 1)):
                raise ValueError(
                    "satellite_index_in_plane values must be contiguous per plane"
                )
            raan = _finite_number(plane_records[0].get("raan_deg"), "raan_deg")
            if first_raan is None:
                first_raan = raan
            expected_raan = (first_raan + zero_based_plane * raan_step) % 360.0
            if any(
                _circular_delta(
                    _finite_number(record.get("raan_deg"), "raan_deg"),
                    expected_raan,
                )
                > ANGLE_TOLERANCE_DEG
                for record in plane_records
            ):
                raise ValueError("orbit catalog RAAN values are not uniformly distributed")
            start = _finite_number(
                plane_records[0].get("mean_anomaly_deg"), "mean_anomaly_deg"
            ) % 360.0
            plane_starts.append(start)
            for satellite_offset, record in enumerate(plane_records):
                expected_anomaly = (start + satellite_offset * phase_step) % 360.0
                actual_anomaly = _finite_number(
                    record.get("mean_anomaly_deg"), "mean_anomaly_deg"
                )
                if _circular_delta(actual_anomaly, expected_anomaly) > ANGLE_TOLERANCE_DEG:
                    raise ValueError(
                        "orbit catalog mean anomalies are not uniformly distributed"
                    )

        assert first_raan is not None
        alternating_offset = (
            0.0
            if orbit_count == 1
            else (plane_starts[1] - plane_starts[0]) % 360.0
        )
        for zero_based_plane, start in enumerate(plane_starts):
            expected_start = (
                plane_starts[0]
                + (zero_based_plane % 2) * alternating_offset
            ) % 360.0
            if _circular_delta(start, expected_start) > ANGLE_TOLERANCE_DEG:
                raise ValueError(
                    "orbit catalog does not follow one alternating phase offset"
                )

        return ShellConfig(
            shell_id=logical_shell_id,
            altitude_km=round(_mean_motion_to_altitude_km(mean_motion), 6),
            inclination_deg=inclination,
            epoch_utc=datetime(1970, 1, 1, tzinfo=timezone.utc),
            generated_orbit_layout=GeneratedOrbitLayout(
                orbit_count=orbit_count,
                satellites_per_orbit=satellites_per_plane,
                right_ascension_distribution=RightAscensionDistribution(
                    start_deg=first_raan % 360.0,
                    step_deg=raan_step,
                ),
                in_orbit_phase_distribution=InOrbitPhaseDistribution(
                    start_deg=plane_starts[0],
                    step_deg=phase_step,
                    alternating_orbit_offset_deg=alternating_offset,
                ),
            ),
        )

    def _load_ground_stations(
        self,
        *,
        operator: str,
        station_type: str,
    ) -> tuple[GroundStationConfig, ...]:
        normalized_operator = _text(operator, "ground station operator")
        normalized_station_type = _text(
            station_type, "ground station type"
        )
        raw_catalog = _required_list(
            _read_json(self._gateway_catalog_path), "gateway catalog"
        )
        raw_bindings = _required_list(
            _read_json(self._gateway_binding_path), "gateway binding"
        )
        catalog_by_id: dict[str, dict[str, Any]] = {}
        for raw_entry in raw_catalog:
            entry = _required_mapping(raw_entry, "gateway catalog entry")
            source_id = _text(entry.get("id"), "gateway catalog id")
            if source_id in catalog_by_id:
                raise ValueError(f"duplicate gateway catalog id: {source_id}")
            catalog_by_id[source_id] = entry

        specifications: list[GroundStationConfig] = []
        selected_source_ids: set[str] = set()
        for raw_binding in raw_bindings:
            binding = _required_mapping(raw_binding, "gateway binding entry")
            source_id = _text(
                binding.get("visualization_gateway_id"),
                "visualization_gateway_id",
            )
            if source_id in selected_source_ids:
                raise ValueError(f"duplicate bound gateway source id: {source_id}")
            selected_source_ids.add(source_id)
            if source_id not in catalog_by_id:
                raise ValueError(
                    f"gateway binding references unknown catalog id: {source_id}"
                )
            source = catalog_by_id[source_id]
            display_name = _text(source.get("name"), "gateway name")
            latitude = _finite_number(source.get("latitude"), "gateway latitude")
            longitude = _finite_number(
                source.get("longitude"), "gateway longitude"
            )
            elevation = _finite_number(
                source.get("altitudeMeters", 0.0), "gateway altitudeMeters"
            )
            if _circular_delta(
                _finite_number(binding.get("longitude_deg"), "binding longitude_deg")
                % 360.0,
                longitude % 360.0,
            ) > ANGLE_TOLERANCE_DEG or abs(
                _finite_number(binding.get("latitude_deg"), "binding latitude_deg")
                - latitude
            ) > ANGLE_TOLERANCE_DEG:
                raise ValueError(
                    f"gateway binding coordinates disagree with catalog: {source_id}"
                )
            identity_key = "|".join(
                (
                    normalized_operator.casefold(),
                    display_name.casefold(),
                    f"{latitude:.8f}",
                    f"{longitude:.8f}",
                    f"{elevation:.3f}",
                )
            )
            specifications.append(
                GroundStationConfig(
                    display_name=display_name,
                    latitude_deg=latitude,
                    longitude_deg=longitude,
                    elevation_m=elevation,
                    operator=normalized_operator,
                    station_type=normalized_station_type,
                    identity_key=identity_key,
                )
            )
        return tuple(
            sorted(
                specifications,
                key=lambda specification: specification.identity_key or "",
            )
        )
