"""Parse explicit and generated satellite layouts."""
from __future__ import annotations
from pathlib import Path
from typing import Any

from ..._yaml import (
    _ConfigurationYamlError,
    aware_datetime as _aware_datetime,
    fields as _fields,
    mapping as _mapping,
    read_document as _read_document,
    sequence as _sequence,
)
from ..satellite_layout import (GeneratedOrbitLayout, InOrbitPhaseDistribution,
    OrbitConfig, RightAscensionDistribution, SatelliteConfig, ShellConfig)
from ._errors import StaticEmulatorConfigLoadError

SHELLS_SCHEMA_VERSION = "skyseed-shells/v1"

def _load_shells_file(
    raw_reference: Any,
    root: Path,
    context: str,
) -> tuple[ShellConfig, ...]:
    if not isinstance(raw_reference, str) or not raw_reference.strip():
        raise StaticEmulatorConfigLoadError(
            f"{context}.shells_file must be a relative path"
        )
    reference = Path(raw_reference)
    if reference.is_absolute():
        raise StaticEmulatorConfigLoadError(
            f"{context}.shells_file must be a relative path"
        )
    return load_shell_configs(root / reference)


def load_shell_configs(path: Path) -> tuple[ShellConfig, ...]:
    """Read independent shell YAML from a relative or absolute path."""
    try:
        return _read_shell_configs(path)
    except StaticEmulatorConfigLoadError:
        raise
    except (_ConfigurationYamlError, TypeError, ValueError) as error:
        raise StaticEmulatorConfigLoadError(str(error)) from error


def _read_shell_configs(path: Path) -> tuple[ShellConfig, ...]:
    path = Path(path).resolve()
    payload = _read_document(path, "shell data")
    _fields(
        payload,
        required={"schema_version", "shells"},
        context="shell data",
    )
    if payload["schema_version"] != SHELLS_SCHEMA_VERSION:
        raise StaticEmulatorConfigLoadError(
            "unsupported shell schema_version: "
            f"{payload['schema_version']!r}"
        )
    return tuple(
        _parse_shell(item, index, "shell data")
        for index, item in enumerate(
            _sequence(payload["shells"], "shells")
        )
    )


def _parse_shell(
    value: Any,
    index: int,
    parent: str,
) -> ShellConfig:
    context = f"{parent}.shells[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"shell_id", "altitude_km", "inclination_deg", "epoch_utc"},
        optional={"generation", "orbits"},
        context=context,
    )
    has_generation = "generation" in mapping
    has_orbits = "orbits" in mapping
    if has_generation == has_orbits:
        raise StaticEmulatorConfigLoadError(
            f"{context} must specify exactly one of generation or orbits"
        )
    return ShellConfig(
        shell_id=mapping["shell_id"],
        altitude_km=mapping["altitude_km"],
        inclination_deg=mapping["inclination_deg"],
        epoch_utc=_aware_datetime(mapping["epoch_utc"], f"{context}.epoch_utc"),
        generated_orbit_layout=(
            _parse_generation(mapping["generation"], context)
            if has_generation
            else None
        ),
        explicit_orbits=(
            tuple(
                _parse_orbit(item, orbit_index, context)
                for orbit_index, item in enumerate(
                    _sequence(mapping["orbits"], f"{context}.orbits")
                )
            )
            if has_orbits
            else ()
        ),
    )


def _parse_generation(value: Any, parent: str) -> GeneratedOrbitLayout:
    context = f"{parent}.generation"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"orbit_count", "satellites_per_orbit", "raan", "phase"},
        optional={
            "max_simultaneous_ground_station_accesses",
            "maximum_simultaneous_inter_satellite_links",
        },
        context=context,
    )
    raan = _mapping(mapping["raan"], f"{context}.raan")
    _fields(
        raan,
        required={"start_deg", "step_deg"},
        context=f"{context}.raan",
    )
    phase = _mapping(mapping["phase"], f"{context}.phase")
    _fields(
        phase,
        required={"start_deg", "step_deg"},
        optional={
            "alternating_orbit_offset_deg",
            "orbit_phase_step_deg",
        },
        context=f"{context}.phase",
    )
    phase_policy_fields = {
        "alternating_orbit_offset_deg",
        "orbit_phase_step_deg",
    }
    if len(phase_policy_fields.intersection(phase)) != 1:
        raise StaticEmulatorConfigLoadError(
            f"{context}.phase must specify exactly one of "
            "alternating_orbit_offset_deg or orbit_phase_step_deg"
        )
    return GeneratedOrbitLayout(
        orbit_count=mapping["orbit_count"],
        satellites_per_orbit=mapping["satellites_per_orbit"],
        right_ascension_distribution=RightAscensionDistribution(
            start_deg=raan["start_deg"],
            step_deg=raan["step_deg"],
        ),
        in_orbit_phase_distribution=InOrbitPhaseDistribution(
            start_deg=phase["start_deg"],
            step_deg=phase["step_deg"],
            alternating_orbit_offset_deg=phase.get(
                "alternating_orbit_offset_deg",
                0.0,
            ),
            orbit_phase_step_deg=phase.get("orbit_phase_step_deg"),
        ),
        max_simultaneous_ground_station_accesses=mapping.get(
            "max_simultaneous_ground_station_accesses"
        ),
        maximum_simultaneous_inter_satellite_links=mapping.get(
            "maximum_simultaneous_inter_satellite_links"
        ),
    )


def _parse_orbit(
    value: Any,
    index: int,
    parent: str,
) -> OrbitConfig:
    context = f"{parent}.orbits[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"orbit_id", "right_ascension_deg", "satellites"},
        context=context,
    )
    return OrbitConfig(
        orbit_id=mapping["orbit_id"],
        right_ascension_deg=mapping["right_ascension_deg"],
        satellites=tuple(
            _parse_satellite(item, satellite_index, context)
            for satellite_index, item in enumerate(
                _sequence(mapping["satellites"], f"{context}.satellites")
            )
        ),
    )


def _parse_satellite(
    value: Any,
    index: int,
    parent: str,
) -> SatelliteConfig:
    context = f"{parent}.satellites[{index}]"
    mapping = _mapping(value, context)
    _fields(
        mapping,
        required={"satellite_id", "index_in_orbit", "mean_anomaly_deg"},
        optional={
            "payload",
            "max_simultaneous_ground_station_accesses",
            "maximum_simultaneous_inter_satellite_links",
        },
        context=context,
    )
    payload = _mapping(mapping.get("payload", {}), f"{context}.payload")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in payload.items()
    ):
        raise StaticEmulatorConfigLoadError(
            f"{context}.payload must contain only string keys and values"
        )
    return SatelliteConfig(
        satellite_id=mapping["satellite_id"],
        index_in_orbit=mapping["index_in_orbit"],
        mean_anomaly_deg=mapping["mean_anomaly_deg"],
        payload_items=tuple(payload.items()),
        max_simultaneous_ground_station_accesses=mapping.get(
            "max_simultaneous_ground_station_accesses"
        ),
        maximum_simultaneous_inter_satellite_links=mapping.get(
            "maximum_simultaneous_inter_satellite_links"
        ),
    )
