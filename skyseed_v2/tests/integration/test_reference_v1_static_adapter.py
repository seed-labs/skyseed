from dataclasses import fields
from json import dumps, loads
from pathlib import Path
from shutil import copyfile
from subprocess import run
from typing import Callable

import pytest

from examples.reference.v1_reference_snapshot import (
    PINNED_V1_REFERENCE_COMMIT,
    V1_REFERENCE_FILE_PATHS,
    V1ReferenceSnapshotError,
)
from examples.reference.v1_static_input_adapter import V1StaticInputAdapter
from skyseed_v2 import GroundStationBuilder, ground_station_id_for
from skyseed_v2.seed import SkyseedAutonomousSystem


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
V1_ROOT = REPOSITORY_ROOT / "skyseed_v1"
INVESTIGATION_REFERENCE_COMMIT = "06b19eb718a79a9113ee95bc045f10953c1d8ab7"


def adapter() -> V1StaticInputAdapter:
    return V1StaticInputAdapter(
        reference_root=V1_ROOT,
        pinned_commit=PINNED_V1_REFERENCE_COMMIT,
    )


def create_reference_repository(
    tmp_path: Path,
    mutate: Callable[[Path], None] | None = None,
) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    reference_root = repository / "skyseed_v1"
    for relative_path in V1_REFERENCE_FILE_PATHS:
        destination = reference_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(V1_ROOT / relative_path, destination)
    if mutate is not None:
        mutate(reference_root)
    run(("git", "init", "-q", str(repository)), check=True)
    run(("git", "-C", str(repository), "add", "."), check=True)
    run(
        (
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Skyseed Test",
            "-c",
            "user.email=skyseed-test@example.invalid",
            "commit",
            "-qm",
            "reference snapshot",
        ),
        check=True,
    )
    commit = run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return reference_root, commit


def load(adapter_value: V1StaticInputAdapter):
    return adapter_value.load(
        reference_shell_label="S4",
        logical_shell_id="scale-shell",
        operator="Starlink",
    )


def test_reference_adapter_returns_only_pinned_generic_static_specifications() -> None:
    adapter_value = adapter()
    result = load(adapter_value)
    generation = result.shell.generation
    audit = adapter_value.reference_audit
    assert generation is not None

    assert audit.pinned_commit == PINNED_V1_REFERENCE_COMMIT
    assert len(audit.file_blobs) == 3
    assert {item.relative_path for item in audit.file_blobs} == set(
        V1_REFERENCE_FILE_PATHS
    )
    assert result.shell.shell_id == "scale-shell"
    assert result.shell.altitude_km == 560.0
    assert result.shell.inclination_deg == 97.6
    assert generation.orbit_count == 6
    assert generation.satellites_per_orbit == 58
    assert generation.raan.step_deg == 60.0
    assert generation.phase.alternating_orbit_offset_deg == pytest.approx(
        180.0 / 58, abs=0.000001
    )
    assert len(result.ground_stations) == 12
    assert all(item.operator == "Starlink" for item in result.ground_stations)
    assert all(item.ground_station_id is None for item in result.ground_stations)
    assert all(
        not ground_station_id_for(item).startswith("gw")
        for item in result.ground_stations
    )
    assert all("epoch" not in field.name for field in fields(type(result.shell)))
    assert all(
        not hasattr(item, "service_address") for item in result.ground_stations
    )

    built = GroundStationBuilder().build(
        SkyseedAutonomousSystem(65000), result.ground_stations
    )
    assert len(built.ground_stations) == 12
    assert all(station.service_config == {} for station in built.ground_stations)


def test_investigation_and_pinned_commits_have_identical_reference_blobs() -> None:
    ancestor = run(
        (
            "git",
            "-C",
            str(REPOSITORY_ROOT),
            "merge-base",
            "--is-ancestor",
            INVESTIGATION_REFERENCE_COMMIT,
            PINNED_V1_REFERENCE_COMMIT,
        )
    )
    assert ancestor.returncode == 0
    for relative_path in V1_REFERENCE_FILE_PATHS:
        repository_path = f"skyseed_v1/{relative_path}"
        investigation_blob = run(
            (
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "rev-parse",
                f"{INVESTIGATION_REFERENCE_COMMIT}:{repository_path}",
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        pinned_blob = run(
            (
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "rev-parse",
                f"{PINNED_V1_REFERENCE_COMMIT}:{repository_path}",
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert pinned_blob == investigation_blob


def test_reference_adapter_is_independent_of_json_record_order(
    tmp_path: Path,
) -> None:
    def reverse_records(reference_root: Path) -> None:
        orbit_path = (
            reference_root
            / "data/orbits/planned_shell_orbit_data_s1_s5.json"
        )
        binding_path = (
            reference_root
            / "data/ground/s4_visualization_gateway_binding_12.json"
        )
        orbit_payload = loads(orbit_path.read_text())
        binding_payload = loads(binding_path.read_text())
        orbit_payload["selected_records"].reverse()
        orbit_path.write_text(dumps(orbit_payload))
        binding_path.write_text(dumps(list(reversed(binding_payload))))

    reference_root, commit = create_reference_repository(
        tmp_path, reverse_records
    )
    original = load(adapter())
    reordered = load(
        V1StaticInputAdapter(
            reference_root=reference_root,
            pinned_commit=commit,
        )
    )

    assert reordered == original


def test_reference_snapshot_rejects_unknown_commit_and_worktree_drift(
    tmp_path: Path,
) -> None:
    reference_root, commit = create_reference_repository(tmp_path)
    with pytest.raises(V1ReferenceSnapshotError, match="commit lookup"):
        V1StaticInputAdapter(
            reference_root=reference_root,
            pinned_commit="0" * 40,
        )

    adapter_value = V1StaticInputAdapter(
        reference_root=reference_root,
        pinned_commit=commit,
    )
    gateway_path = reference_root / "data/ground/starlink_gateways.json"
    gateway_path.write_text(gateway_path.read_text() + "\n")
    with pytest.raises(V1ReferenceSnapshotError, match="drifted"):
        load(adapter_value)


def test_reference_snapshot_rejects_missing_file(tmp_path: Path) -> None:
    reference_root, commit = create_reference_repository(tmp_path)
    adapter_value = V1StaticInputAdapter(
        reference_root=reference_root,
        pinned_commit=commit,
    )
    (
        reference_root
        / "data/ground/s4_visualization_gateway_binding_12.json"
    ).unlink()
    with pytest.raises(V1ReferenceSnapshotError, match="file is missing"):
        load(adapter_value)


def test_reference_adapter_fails_closed_on_invalid_or_missing_data(
    tmp_path: Path,
) -> None:
    def invalidate_orbits(reference_root: Path) -> None:
        (
            reference_root
            / "data/orbits/planned_shell_orbit_data_s1_s5.json"
        ).write_text("not-json")

    invalid_root, invalid_commit = create_reference_repository(
        tmp_path / "invalid", invalidate_orbits
    )
    with pytest.raises(ValueError, match="valid JSON"):
        load(
            V1StaticInputAdapter(
                reference_root=invalid_root,
                pinned_commit=invalid_commit,
            )
        )

    def reference_unknown_gateway(reference_root: Path) -> None:
        binding_path = (
            reference_root
            / "data/ground/s4_visualization_gateway_binding_12.json"
        )
        binding_payload = loads(binding_path.read_text())
        binding_payload[0]["visualization_gateway_id"] = "missing-source"
        binding_path.write_text(dumps(binding_payload))

    missing_root, missing_commit = create_reference_repository(
        tmp_path / "missing", reference_unknown_gateway
    )
    with pytest.raises(ValueError, match="unknown catalog id"):
        load(
            V1StaticInputAdapter(
                reference_root=missing_root,
                pinned_commit=missing_commit,
            )
        )
