from __future__ import annotations

from copy import copy
from datetime import timedelta
import gzip
from hashlib import sha256
import json
from pathlib import Path

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.experiment._realization_handoff import (
    _PersistedRealizationSupplier,
    _RealizationHandoffWriter,
    _canonical_json_bytes,
    _deterministic_gzip,
    _realization_from_data,
    _realization_to_data,
    _read_persisted_realization_sequence,
    _sequence_sha256,
    _validate_sequence_pair,
    _write_realization_handoff,
)


@pytest.fixture(scope="module")
def realizations():
    simulator = create_complete_static_simulator(build_emulator())
    return run_complete_static_chain(simulator)[1]


def _rewrite_payload_with_coherent_checksums(
    manifest_path, mutate, *, index: int = 0
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["realizations"][index]
    path = manifest_path.parent / entry["path"]
    data = json.loads(gzip.decompress(path.read_bytes()))
    mutate(data)
    raw = _canonical_json_bytes(data)
    compressed = _deterministic_gzip(raw)
    path.write_bytes(compressed)
    entry.update(
        {
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(raw),
            "sha256": sha256(compressed).hexdigest(),
            "uncompressed_sha256": sha256(raw).hexdigest(),
        }
    )
    manifest["sequence_sha256"] = _sequence_sha256(
        manifest["realizations"]
    )
    manifest_path.write_bytes(
        _canonical_json_bytes(manifest, trailing_newline=True)
    )


def _assert_semantic_tamper_rejected(
    manifest_path, mutate, expected_cause: str
) -> None:
    _rewrite_payload_with_coherent_checksums(manifest_path, mutate)
    with pytest.raises(ValueError, match="incompatible") as error:
        _read_persisted_realization_sequence(manifest_path)
    assert error.value.__cause__ is not None
    assert expected_cause in str(error.value.__cause__)


def _other_activation_timestamp(data, current: str) -> str:
    return next(
        item["timestamp"]
        for item in data["compiled_activation_boundaries"]
        if item["timestamp"] != current
    )


def _tamper_intent_family(data, family: str) -> None:
    values = data[family]
    assert values
    first = values[0]
    if family == "resource_realizations":
        first["operation"] = "invalid"
    elif family == "parameter_realizations":
        first["delay_us"] = -1
    elif family == "forwarding_realizations":
        first["operation"] = "remove"
    else:
        first["phase"] = "retirement"


def test_data_only_handoff_round_trips_without_planner_objects(
    tmp_path, realizations
) -> None:
    manifest_path = _write_realization_handoff(realizations, tmp_path / "handoff")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["format_version"] == 3
    assert manifest["encoding"] == "deterministic-gzip-json-v1"
    assert manifest["data_only"] is True
    assert all(value["path"].endswith(".json.gz") for value in manifest["realizations"])
    assert "pickle" not in manifest_path.read_text(encoding="utf-8").lower()
    assert _read_persisted_realization_sequence(manifest_path) == realizations


def test_handoff_persists_compact_cadence_and_precedence_folding(
    realizations,
) -> None:
    data = _realization_to_data(realizations[0])

    assert data["observation_interval"] == {
        "days": 0,
        "seconds": 10,
        "microseconds": 0,
    }
    assert data["semantic_precedence_folding"]
    assert "source_semantic_event_precedences" not in data
    assert all(
        set(item) == {"timestamp", "transition_fingerprint"}
        for item in data["compiled_activation_boundaries"]
    )


def test_handoff_bytes_are_deterministic(tmp_path, realizations) -> None:
    first = _write_realization_handoff(realizations, tmp_path / "first")
    second = _write_realization_handoff(realizations, tmp_path / "second")
    first_files = {
        value.name: value.read_bytes() for value in first.parent.iterdir()
    }
    second_files = {
        value.name: value.read_bytes() for value in second.parent.iterdir()
    }
    assert first_files == second_files
    for name, payload in first_files.items():
        if name.endswith(".gz"):
            assert payload[4:8] == b"\0\0\0\0"
            assert payload[9] == 255


def test_incremental_writer_and_supplier_preserve_lineage(
    tmp_path, realizations
) -> None:
    writer = _RealizationHandoffWriter(tmp_path / "rolling")
    for realization in realizations:
        writer.append(realization)
    supplier = _PersistedRealizationSupplier(writer.finalize())

    assert supplier.snapshot()["consumed_count"] == 0
    assert supplier() == realizations[0]
    assert supplier.snapshot()["consumed_count"] == 1
    assert supplier() == realizations[1]
    assert supplier() is None
    assert supplier.snapshot()["terminal_returned"] is True


def test_old_pickle_manifest_is_rejected(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "bpf-compiled-realization-sequence",
                "format_version": 1,
                "encoding": "gzip-pickle-v1",
                "realizations": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fields|incompatible"):
        _PersistedRealizationSupplier(path)


def test_payload_tampering_is_rejected(tmp_path, realizations) -> None:
    manifest = _write_realization_handoff(realizations, tmp_path / "tamper")
    payload = manifest.parent / "realization-00000.json.gz"
    content = bytearray(payload.read_bytes())
    content[-1] ^= 1
    payload.write_bytes(content)
    with pytest.raises(ValueError, match="checksum"):
        _PersistedRealizationSupplier(manifest)


def test_runner_pulls_perform_no_persistence_or_compiler_work(
    tmp_path, realizations, monkeypatch
) -> None:
    manifest = _write_realization_handoff(realizations, tmp_path / "eager")
    supplier = _PersistedRealizationSupplier(manifest)

    def reject_late_work(*args, **kwargs):
        raise AssertionError("Runner pull performed forbidden late work")

    monkeypatch.setattr(Path, "read_bytes", reject_late_work)
    monkeypatch.setattr(gzip, "decompress", reject_late_work)
    monkeypatch.setattr(
        "skyseed_v2.backend.bpf.compiler._validate_compiled_realization_lowering",
        reject_late_work,
    )

    assert tuple(supplier() for _ in realizations) == realizations
    assert supplier() is None


@pytest.mark.parametrize(
    ("family", "expected_cause"),
    (
        ("resource_realizations", "valid _ResourceOperation"),
        ("parameter_realizations", "outside their canonical ranges"),
        ("forwarding_realizations", "operation differs"),
        ("zone_edge_realizations", "phase differs"),
    ),
)
def test_coherently_rechecksummed_backend_intent_tampering_is_rejected(
    tmp_path, realizations, family, expected_cause
) -> None:
    manifest = _write_realization_handoff(
        realizations, tmp_path / family
    )

    _assert_semantic_tamper_rejected(
        manifest,
        lambda data: _tamper_intent_family(data, family),
        expected_cause,
    )


def test_coherently_rechecksummed_constraint_tampering_is_rejected(
    tmp_path, realizations
) -> None:
    manifest = _write_realization_handoff(
        realizations, tmp_path / "constraints"
    )

    def mutate(data) -> None:
        assert data["constraints"]
        data["constraints"][0]["predecessor"]["index"] = 10**9

    _assert_semantic_tamper_rejected(
        manifest,
        mutate,
        "unknown compiled intent",
    )


@pytest.mark.parametrize(
    ("field", "expected_cause"),
    (
        (
            "observation_interval",
            "activation timestamps differ from observation interval",
        ),
        (
            "semantic_precedence_folding",
            "semantic_precedence_folding is not canonical",
        ),
    ),
)
def test_coherently_rechecksummed_source_plan_tampering_is_rejected(
    tmp_path, realizations, field, expected_cause
) -> None:
    manifest = _write_realization_handoff(
        realizations, tmp_path / field
    )

    def mutate(data) -> None:
        if field == "observation_interval":
            data[field]["seconds"] += 1
        else:
            assert data[field]
            data[field][0]["count"] = -1

    _assert_semantic_tamper_rejected(manifest, mutate, expected_cause)


def test_sequence_requires_exact_equal_cadence_and_next_boundary(
    realizations,
) -> None:
    first, second = realizations
    wrong_interval = copy(second)
    object.__setattr__(
        wrong_interval,
        "observation_interval",
        second.observation_interval + timedelta(microseconds=1),
    )
    with pytest.raises(ValueError, match="lineage"):
        _validate_sequence_pair(first, wrong_interval)

    wrong_start = copy(second)
    object.__setattr__(
        wrong_start,
        "start_timestamp",
        second.start_timestamp + timedelta(microseconds=1),
    )
    with pytest.raises(ValueError, match="lineage"):
        _validate_sequence_pair(first, wrong_start)


def test_manifest_path_cannot_escape_handoff_root(tmp_path, realizations) -> None:
    manifest_path = _write_realization_handoff(realizations, tmp_path / "escape")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["realizations"][0]["path"] = "../outside.json.gz"
    manifest["sequence_sha256"] = _sequence_sha256(manifest["realizations"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="leaves its handoff root"):
        _PersistedRealizationSupplier(manifest_path)


def test_handoff_root_symlink_is_rejected(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _RealizationHandoffWriter(link)


def test_realization_codec_rejects_duplicate_intended_forwarding_key(
    realizations,
) -> None:
    data = _realization_to_data(realizations[0])
    data["intended_forwarding"].append(data["intended_forwarding"][0])
    with pytest.raises(ValueError, match="duplicate key"):
        _realization_from_data(data)


def test_realization_codec_rejects_inconsistent_zone_phase(realizations) -> None:
    data = _realization_to_data(realizations[0])
    assert data["zone_edge_realizations"]
    data["zone_edge_realizations"][0]["phase"] = "retirement"
    with pytest.raises(ValueError, match="Zone edge phase"):
        _realization_from_data(data)
