from __future__ import annotations

from dataclasses import replace
from copy import copy
from hashlib import sha256
import inspect
import json
from pathlib import Path
import struct

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from skyseed_v2.backend.bpf import runner as runner_module
from skyseed_v2.backend.bpf.runner import BpfRunner
from skyseed_v2.backend.bpf.compiled_realization import (
    BpfCompiledActivationBoundary,
    BpfCompiledRealization,
    _IntentReference,
    _OrderingConstraint,
)
from skyseed_v2.backend.bpf.runner import _resolve_nominal_boundaries
from skyseed_v2.backend.bpf._runner.runtime_realization import (
    _empty_state,
    _materialize_boundary,
    _validate_boundary_lowering,
)
from skyseed_v2.backend.bpf._runner_artifact import (
    _HEADER,
    _INDEX,
    _RunnerArtifactWriter,
    _boundary_to_data,
    _decode_boundary_data,
    _open_runner_artifact,
    _read_runner_artifact_manifest_snapshot,
    _write_runner_artifact,
)
from skyseed_v2.simulator import planned_operational_state_fingerprint


@pytest.fixture(scope="module")
def realizations():
    simulator = create_complete_static_simulator(build_emulator())
    return run_complete_static_chain(simulator)[1]


def test_packed_artifact_opens_without_rich_realization_reconstruction(
    tmp_path: Path, realizations
) -> None:
    manifest_path = _write_runner_artifact(
        realizations, tmp_path / "artifact"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = _open_runner_artifact(manifest_path)
    try:
        assert manifest["format_version"] == 3
        assert manifest["encoding"] == "typed-packed-runtime-tables-v3"
        assert manifest["terminal"] is True
        assert manifest["semantic_validation"] == "complete-before-seal"
        assert manifest["binary"]["path"] == "runner-runtime.bin"
        assert {path.name for path in manifest_path.parent.iterdir()} == {
            "manifest.json",
            "runner-runtime.bin",
        }
        assert artifact.snapshot()["mmap"] is True
        assert (
            artifact.snapshot()["rich_compiled_realizations_reconstructed"]
            is False
        )
        expected = tuple(
            boundary
            for index, realization in enumerate(realizations)
            for boundary in _resolve_nominal_boundaries(
                (realization,), realization_index_offset=index
            )
        )
        boundaries = artifact.boundaries()
        assert not isinstance(boundaries, tuple)
        assert tuple(boundaries) == expected
        assert artifact.snapshot()["boundary_decode"] == "indexed-on-demand"
        demand = artifact.runtime_capacity_demand
        assert demand["boundary_count"] == len(expected)
        assert demand["generation_count"] >= 1
        assert demand["collision_audit"]["result"] == "passed"
        assert [value["key"] for value in demand["per_node"]] == sorted(
            value.value for value in artifact.physical_world_projection.node_ids
        )
    finally:
        artifact.close()


def test_runner_snapshot_is_detached_from_closed_artifact_mmap(
    tmp_path: Path, realizations
) -> None:
    manifest_path = _write_runner_artifact(
        realizations, tmp_path / "runner-snapshot"
    )
    runner = BpfRunner(
        compose_project_name="artifact-lifetime-proof",
        runtime_artifact=manifest_path,
    )
    runner._artifact = artifact = _open_runner_artifact(manifest_path)
    runner._artifact_snapshot = artifact.snapshot()
    runner._artifact_boundary_count = artifact.boundary_count
    runner._artifact_end = artifact.timeline_end

    before = runner.inspection_snapshot()
    runner.cleanup()

    assert artifact._mmap.closed is True
    after = runner.inspection_snapshot()
    assert after["runner"]["lifecycle_state"] == "cleaned"
    assert after["runner"]["frontiers"]["artifact_end"] == (
        before["runner"]["frontiers"]["artifact_end"]
    )
    assert after["runner"]["artifact"] == before["runner"]["artifact"]


def test_failed_runner_cleanup_can_snapshot_after_mmap_close(
    tmp_path: Path, realizations
) -> None:
    manifest_path = _write_runner_artifact(
        realizations, tmp_path / "failed-runner-snapshot"
    )
    runner = BpfRunner(
        compose_project_name="failed-artifact-lifetime-proof",
        runtime_artifact=manifest_path,
    )
    runner._artifact = artifact = _open_runner_artifact(manifest_path)
    runner._artifact_snapshot = artifact.snapshot()
    runner._artifact_boundary_count = artifact.boundary_count
    runner._artifact_end = artifact.timeline_end
    runner._state = "failed"

    runner.cleanup()

    assert artifact._mmap.closed is True
    snapshot = runner.inspection_snapshot()
    assert snapshot["runner"]["lifecycle_state"] == "cleaned"
    assert snapshot["runner"]["artifact"]["boundary_count"] == len(
        artifact._indexes
    )


def test_artifact_bytes_are_deterministic(tmp_path: Path, realizations) -> None:
    first = _write_runner_artifact(realizations, tmp_path / "first")
    second = _write_runner_artifact(realizations, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    assert (
        (first.parent / "runner-runtime.bin").read_bytes()
        == (second.parent / "runner-runtime.bin").read_bytes()
    )


def test_writer_consumes_complete_lineage_before_sealing(
    tmp_path: Path, realizations
) -> None:
    writer = _RunnerArtifactWriter(tmp_path / "artifact")
    for realization in realizations:
        writer.append(realization)
    assert not (tmp_path / "artifact/manifest.json").exists()
    manifest = writer.finalize()
    assert manifest.is_file()
    with pytest.raises(RuntimeError, match="finalized"):
        writer.append(realizations[-1])
    with pytest.raises(RuntimeError, match="finalized"):
        writer.finalize()


def test_binary_corruption_is_rejected_before_boundary_decode(
    tmp_path: Path, realizations
) -> None:
    manifest = _write_runner_artifact(realizations, tmp_path / "artifact")
    binary = manifest.parent / "runner-runtime.bin"
    content = bytearray(binary.read_bytes())
    content[-1] ^= 1
    binary.write_bytes(content)
    with pytest.raises(ValueError, match="length/hash"):
        _open_runner_artifact(manifest)


def test_manifest_version_and_seal_are_strict(
    tmp_path: Path, realizations
) -> None:
    manifest_path = _write_runner_artifact(realizations, tmp_path / "artifact")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["format_version"] = 999
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="version/seal"):
        _read_runner_artifact_manifest_snapshot(manifest_path)


@pytest.mark.parametrize(
    "defect",
    ("missing", "boolean", "wrong-node", "overflow"),
)
def test_runtime_capacity_demand_schema_is_strict(
    tmp_path: Path, realizations, defect: str
) -> None:
    manifest_path = _write_runner_artifact(
        realizations, tmp_path / defect
    )
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if defect == "missing":
        value.pop("runtime_capacity_demand")
    elif defect == "boolean":
        value["runtime_capacity_demand"]["boundary_count"] = True
    elif defect == "wrong-node":
        value["runtime_capacity_demand"]["per_node"][0]["key"] = (
            "65001/satellite/not-in-the-physical-world"
        )
    else:
        value["runtime_capacity_demand"]["per_node"][0][
            "cumulative_route_entries"
        ] = 1 << 32
    manifest_path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="manifest schema|capacity demand"):
        _open_runner_artifact(manifest_path)


def test_nonterminal_and_truncated_artifacts_are_rejected(
    tmp_path: Path, realizations
) -> None:
    nonterminal = _write_runner_artifact(
        realizations, tmp_path / "nonterminal"
    )
    value = json.loads(nonterminal.read_text(encoding="utf-8"))
    value["terminal"] = False
    nonterminal.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="version/seal"):
        _open_runner_artifact(nonterminal)

    truncated = _write_runner_artifact(realizations, tmp_path / "truncated")
    binary = truncated.parent / "runner-runtime.bin"
    binary.write_bytes(binary.read_bytes()[:-1])
    with pytest.raises(ValueError, match="length/hash"):
        _open_runner_artifact(truncated)


def test_lazy_record_hash_rejects_coherently_resealed_payload_corruption(
    tmp_path: Path, realizations
) -> None:
    manifest_path = _write_runner_artifact(realizations, tmp_path / "artifact")
    binary_path = manifest_path.parent / "runner-runtime.bin"
    content = bytearray(binary_path.read_bytes())
    content[-1] ^= 1
    header = list(_HEADER.unpack_from(content, 0))
    header[-1] = sha256(content[_HEADER.size:]).digest()
    _HEADER.pack_into(content, 0, *header)
    binary_path.write_bytes(content)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["binary"]["sha256"] = sha256(content).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    artifact = _open_runner_artifact(manifest_path)
    try:
        with pytest.raises(ValueError, match="record hash"):
            artifact.boundary(artifact.boundary_count - 1)
    finally:
        artifact.close()


def test_binary_offsets_are_bounded_even_with_coherent_outer_hash(
    tmp_path: Path, realizations
) -> None:
    manifest_path = _write_runner_artifact(realizations, tmp_path / "artifact")
    binary_path = manifest_path.parent / "runner-runtime.bin"
    content = bytearray(binary_path.read_bytes())
    header = list(_HEADER.unpack_from(content, 0))
    first = list(_INDEX.unpack_from(content, _HEADER.size))
    first[0] = len(content) + 1
    _INDEX.pack_into(content, _HEADER.size, *first)
    header[-1] = sha256(content[_HEADER.size:]).digest()
    _HEADER.pack_into(content, 0, *header)
    binary_path.write_bytes(content)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["binary"]["sha256"] = sha256(content).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="record bounds"):
        _open_runner_artifact(manifest_path)


def test_artifact_root_and_manifest_reject_symlinks(
    tmp_path: Path, realizations
) -> None:
    root_link = tmp_path / "root-link"
    root_link.symlink_to(tmp_path / "target", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _RunnerArtifactWriter(root_link)

    manifest = _write_runner_artifact(realizations, tmp_path / "artifact")
    link = tmp_path / "manifest-link.json"
    link.symlink_to(manifest)
    with pytest.raises(ValueError, match="symlink"):
        _open_runner_artifact(link)


def test_lineage_break_is_rejected_before_seal(
    tmp_path: Path, realizations
) -> None:
    if len(realizations) < 2:
        pytest.skip("fixture has one realization")
    writer = _RunnerArtifactWriter(tmp_path / "artifact")
    writer.append(realizations[0])
    with pytest.raises(ValueError, match="lineage"):
        writer.append(realizations[0])


def test_duplicate_formal_boundary_is_rejected(realizations) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _resolve_nominal_boundaries((realizations[0], realizations[0]))


def _normalization_fixture(original, constraints):
    """Isolate normalization from the Compiler's already-folded constraints."""
    value = copy(original)
    times = tuple(original.start_timestamp + i * original.observation_interval
                  for i in range(3))
    for name, data in {
        "end_timestamp": times[-1],
        "compiled_activation_boundaries": tuple(
            BpfCompiledActivationBoundary(time, f"normalization-{i}")
            for i, time in enumerate(times)
        ),
        "resource_realizations": tuple(
            replace(intent, nominal_time=times[0 if i < 2 else 1])
            for i, intent in enumerate((
                *original.resource_realizations[:2], original.resource_realizations[0]
            ))
        ),
        "parameter_realizations": (), "forwarding_realizations": (),
        "zone_edge_realizations": (),
        "constraints": tuple(
            _OrderingConstraint(_IntentReference("resource", a),
                                _IntentReference("resource", b), reason)
            for a, b, reason in constraints
        ),
    }.items():
        object.__setattr__(value, name, data)
    assert len(value.resource_realizations) == 3
    return value


def test_normalization_preserves_duplicate_and_cross_boundary_counts(realizations):
    value = _normalization_fixture(realizations[0], (
        (0, 1, "local"), (0, 1, "duplicate-local"),
        (1, 2, "future"), (1, 2, "duplicate-future"),
    ))
    first, second, empty = _resolve_nominal_boundaries(
        (value,), realization_index_offset=7
    )
    assert [op.index for op in first.operations] == [0, 1]
    assert [op.index for op in second.operations] == [2]
    assert empty.operations == ()
    assert first.constraint_edges == (((7, "resource", 0), (7, "resource", 1), "local"),)
    assert first.constraint_reasons == (("local", 1),)
    assert [b.inspected_constraint_count for b in (first, second, empty)] == [4, 4, 4]
    assert [b.source_constraint_count for b in (first, second, empty)] == [4, 4, 4]
    assert [b.applied_constraint_count for b in (first, second, empty)] == [1, 0, 0]
    assert [b.outgoing_constraint_count for b in (first, second, empty)] == [2, 0, 0]
    assert [b.later_constraint_count for b in (first, second, empty)] == [0, 4, 4]
    assert all(b.source_realization_indexes == (7,) for b in (first, second, empty))


@pytest.mark.parametrize("constraints,message", (
    (((0, 99, "missing"),), "unknown operation"),
    (((2, 0, "reverse"),), "depends on later compiled work"),
    (((0, 1, "forward"), (1, 0, "cycle")), "contain a cycle"),
))
def test_normalization_rejects_invalid_dependency_graph(realizations, constraints, message):
    value = _normalization_fixture(realizations[0], constraints)
    with pytest.raises(ValueError, match=message):
        _resolve_nominal_boundaries((value,))


def test_representative_artifact_preserves_complete_boundary_semantics(
    tmp_path: Path, realizations
) -> None:
    original = realizations[0]
    no_op_time = original.end_timestamp + original.observation_interval
    output_state = replace(original.output_planned_state, timestamp=no_op_time)
    representative = BpfCompiledRealization._create(
        source_plan_id=original.source_plan_id,
        source_previous_plan_id=original.source_previous_plan_id,
        physical_world_fingerprint=original.physical_world_fingerprint,
        input_state_fingerprint=original.input_state_fingerprint,
        output_state_fingerprint=planned_operational_state_fingerprint(output_state),
        observation_interval=original.observation_interval,
        start_timestamp=original.start_timestamp,
        end_timestamp=no_op_time,
        output_planned_state=output_state,
        compiled_activation_boundaries=(
            *original.compiled_activation_boundaries,
            BpfCompiledActivationBoundary(no_op_time, "synthetic-no-op"),
        ),
        resource_realizations=original.resource_realizations,
        parameter_realizations=original.parameter_realizations,
        forwarding_realizations=original.forwarding_realizations,
        zone_edge_realizations=original.zone_edge_realizations,
        constraints=original.constraints,
        semantic_precedence_folding=original.semantic_precedence_folding,
        intended_state=original._intended_state,
        physical_world_projection=original._physical_world_projection,
    )
    sequence = (representative,)
    expected = tuple(
        boundary
        for index, realization in enumerate(sequence)
        for boundary in _resolve_nominal_boundaries(
            (realization,), realization_index_offset=index
        )
    )
    manifest = _write_runner_artifact(sequence, tmp_path / "artifact")
    artifact = _open_runner_artifact(manifest)
    try:
        actual = tuple(artifact.boundaries())
        assert actual == expected
        assert len(actual) > 1
        assert any(not boundary.operations for boundary in actual)
        assert {
            operation.domain for boundary in actual for operation in boundary.operations
        } == {"resource", "parameter", "forwarding", "zone-edge"}
        assert any(boundary.constraint_edges for boundary in actual)
        assert all(
            0 <= _validate_boundary_lowering(boundary)
            <= boundary.applied_constraint_count
            for boundary in actual
        )
        assert all(boundary.physical_world_projection == expected[0].physical_world_projection for boundary in actual)
        assert artifact.zone_prefixes == tuple(
            prefix for _, prefix in representative.zone_prefixes
        )
        assert [value.source_realization_indexes for value in actual] == [
            value.source_realization_indexes for value in expected
        ]
        assert [value.coverage for value in actual] == [
            value.coverage for value in expected
        ]

        expected_state = _empty_state()
        actual_state = _empty_state()
        for expected_boundary, actual_boundary in zip(expected, actual, strict=True):
            expected_state = _materialize_boundary(expected_state, expected_boundary)
            actual_state = _materialize_boundary(actual_state, actual_boundary)
            assert actual_state == expected_state

        no_op = expected[-1].__class__(
            nominal_time=expected[-1].nominal_time,
            operations=(),
            physical_world_projection=expected[-1].physical_world_projection,
            source_realization_indexes=expected[-1].source_realization_indexes,
            coverage=expected[-1].coverage,
            inspected_constraint_count=0,
            source_constraint_count=0,
            applied_constraint_count=0,
            outgoing_constraint_count=0,
            later_constraint_count=0,
            constraint_reasons=(),
            constraint_edges=(),
            operation_counts=(("resource", 0), ("parameter", 0), ("forwarding", 0), ("zone-edge", 0)),
            order_sha256=sha256(b"").hexdigest(),
        )
        assert _decode_boundary_data(
            _boundary_to_data(no_op),
            physical_world_projection=no_op.physical_world_projection,
        ) == no_op
        assert _materialize_boundary(actual_state, no_op) == actual_state
    finally:
        artifact.close()

    binary = manifest.parent / "runner-runtime.bin"
    assert b"ifindex" not in binary.read_bytes()
    runner_source = inspect.getsource(runner_module)
    assert "compile_bpf(" not in runner_source
    assert "from skyseed_v2.simulator.planning" not in runner_source


def test_artifact_stores_unique_stable_nonzero_endpoint_ports(tmp_path, realizations):
    path = _write_runner_artifact(realizations, tmp_path / "logical-ports")
    manifest = json.loads(path.read_text())
    ports = [port for record in manifest["logical_ports"] for port in record[1:]]
    assert ports and len(set(ports)) == len(ports)
    assert all(type(port) is int and 0 < port <= 0xFFFFFFFF for port in ports)
    artifact = _open_runner_artifact(path)
    try:
        expected = artifact.logical_ports
        assert len(expected) * 2 == len(ports)
        for boundary in artifact.boundaries():
            for operation in boundary.operations:
                if operation.domain == "resource":
                    assert operation.intent.connection_id in expected
        assert artifact.logical_ports == expected
    finally:
        artifact.close()


@pytest.mark.parametrize("bad_port", [0, -1, True, 2**32])
def test_artifact_rejects_invalid_logical_ports_before_runtime(tmp_path, realizations, bad_port):
    path = _write_runner_artifact(realizations, tmp_path / "invalid-ports")
    manifest = json.loads(path.read_text())
    manifest["logical_ports"][0][1] = bad_port
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="logical port"):
        _open_runner_artifact(path)


def test_decoded_immutable_identity_reuse_is_artifact_local(tmp_path, realizations):
    manifest = _write_runner_artifact(realizations, tmp_path / "artifact")
    first = _open_runner_artifact(manifest)
    other = _open_runner_artifact(manifest)
    try:
        a, again, separate = first.boundary(0), first.boundary(0), other.boundary(0)
        assert a == again == separate
        assert a.nominal_time is again.nominal_time
        assert a.nominal_time is not separate.nominal_time
        connections = [operation.intent.connection_id for operation in a.operations
                       if hasattr(operation.intent, "connection_id")]
        repeated = [operation.intent.connection_id for operation in again.operations
                    if hasattr(operation.intent, "connection_id")]
        independent = [operation.intent.connection_id for operation in separate.operations
                       if hasattr(operation.intent, "connection_id")]
        assert connections
        assert all(x is y and x is not z for x, y, z in zip(connections, repeated, independent))
        nodes = {node: node for node in first.physical_world_projection.node_ids}
        assert all(connection.endpoint_a is nodes[connection.endpoint_a] for connection in connections)
    finally:
        first.close()
        other.close()
    assert not first._identities.nodes and not first._identities.connections and not first._identities.timestamps
