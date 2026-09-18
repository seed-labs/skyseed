"""Consume compilation results and seal deterministic execution materials."""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from time import perf_counter
from typing import Mapping, Sequence
from skyseed_v2.shared.recording import PerformanceRecorder, record_performance
from skyseed_v2.simulator import OperationalConnectionId
from skyseed_v2.backend.bpf.compiler.result import BpfCompilationResult
from skyseed_v2.backend.bpf.execution_materials.actions import (
    _PhysicalWorldProjection,
    _ResourceIntent,
)
from skyseed_v2.backend.bpf.execution_materials.mechanics import _RuntimeCapacityDemandAccumulator
from skyseed_v2.backend.bpf.execution_materials.codec import (
    _ARTIFACT_TYPE,
    _FORMAT_VERSION,
    _ENCODING,
    _BINARY_NAME,
    _MANIFEST_NAME,
    _MAGIC,
    _HEADER,
    _INDEX,
)
from skyseed_v2.backend.bpf.execution_materials.boundaries import _resolve_nominal_boundaries
from skyseed_v2.backend.bpf.execution_materials.codec import (
    _boundary_to_data,
    _node_to_data,
    _connection_to_data,
    _sequence_sha256,
    _pack,
    _canonical_json_bytes,
)


def _write_runner_artifact(
    results: Sequence[BpfCompilationResult],
    directory: Path,
    *,
    execution_observer: PerformanceRecorder | None = None,
) -> Path:
    writer = _RunnerArtifactWriter(
        directory, execution_observer=execution_observer
    )
    try:
        for compilation_result in results:
            writer.append(compilation_result)
        return writer.finalize()
    finally:
        writer.close()


class _RunnerArtifactWriter:
    """Consume rich Compiler output and seal one compact Runner artifact."""

    def __init__(
        self,
        directory: Path,
        *,
        execution_observer: PerformanceRecorder | None = None,
        continuation: _RunnerArtifactWriter | None = None,
        final: bool = True,
    ) -> None:
        if type(final) is not bool:
            raise TypeError("final must be a bool")
        if continuation is not None and (
            not isinstance(continuation, _RunnerArtifactWriter)
            or not continuation._finalized or continuation._failed
            or continuation._delivery is None or continuation._delivery["final"]
            or continuation._continued
        ):
            raise ValueError("continuation requires one unconsumed nonfinal sealed batch")
        root = Path(directory)
        if root.is_symlink():
            raise ValueError("Runner artifact root must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise FileExistsError(f"Runner artifact directory is not empty: {root}")
        self._root = root.resolve()
        self._observer = execution_observer
        self._previous: BpfCompilationResult | None = None
        self._previous_boundary_time: datetime | None = None
        descriptor, staging_name = tempfile.mkstemp(
            dir=self._root, prefix=".runner-records."
        )
        self._record_path = Path(staging_name)
        self._record_stream = os.fdopen(descriptor, "wb")
        self._record_index: list[tuple[int, int, bytes]] = []
        self._lineage: list[dict[str, object]] = []
        self._projection: _PhysicalWorldProjection | None = None
        self._zone_prefixes: tuple[tuple[str, str], ...] | None = None
        self._capacity_demand: _RuntimeCapacityDemandAccumulator | None = None
        self._logical_ports: dict[OperationalConnectionId, tuple[int, int]] = {}
        self._sealed_metadata = None
        self._finalized = False
        self._failed = False
        self._continued = False
        self._delivery = None
        if continuation is not None or not final:
            self._delivery = {
                "index": 0 if continuation is None else continuation._delivery["index"] + 1,
                "previous_manifest_sha256": None if continuation is None else continuation._sealed_metadata["manifest_sha256"],
                "final": final,
                "prior_generation": None if continuation is None else continuation._capacity_demand._total_generations - 1,
                "prior_end": None if continuation is None else continuation._previous_boundary_time.isoformat(),
                "prior_plan_id": None if continuation is None else continuation._previous.source_plan_id,
                "prior_state_fingerprint": None if continuation is None else continuation._previous.output_state_fingerprint,
                "observation_interval_seconds": None,
            }
        if continuation is not None:
            self._previous = continuation._previous
            self._previous_boundary_time = continuation._previous_boundary_time
            self._projection = continuation._projection
            self._zone_prefixes = continuation._zone_prefixes
            self._logical_ports = dict(continuation._logical_ports)
            self._capacity_demand = continuation._capacity_demand
            self._capacity_demand.begin_batch()
            continuation._continued = True
            continuation._previous = None

    def append(self, compilation_result: BpfCompilationResult) -> None:
        if self._finalized:
            raise RuntimeError("Runner artifact is already finalized")
        if self._failed:
            raise RuntimeError('Runner artifact writer previously failed')
        try:
            self._append(compilation_result)
        except Exception:
            self._failed = True
            self.close()
            raise

    def close(self) -> None:
        """Close staging resources; failed or abandoned writers cannot seal."""
        if not self._record_stream.closed:
            self._record_stream.close()
        if self._record_path.exists():
            self._record_path.unlink()
        if not self._finalized:
            self._failed = True

    def _append(self, compilation_result: BpfCompilationResult) -> None:
        """Normalize one value now; retain no rich planned-state history."""

        total_started = perf_counter()
        if self._finalized:
            raise RuntimeError("Runner artifact is already finalized")
        if not isinstance(compilation_result, BpfCompilationResult):
            raise TypeError("Runner artifact requires BpfCompilationResult values")
        validation_started = perf_counter()
        _validate_sequence_pair(self._previous, compilation_result)
        if self._delivery is not None:
            self._delivery["observation_interval_seconds"] = compilation_result.observation_interval.total_seconds()
        projection = compilation_result._physical_world_projection
        zone_prefixes = tuple(compilation_result.zone_prefixes)
        if self._projection is None:
            self._projection = projection
            self._zone_prefixes = zone_prefixes
            self._capacity_demand = _RuntimeCapacityDemandAccumulator(
                projection, tuple(prefix for _, prefix in zone_prefixes)
            )
        elif projection != self._projection or zone_prefixes != self._zone_prefixes:
            raise ValueError("Runner artifact static PhysicalWorld metadata changed")

        realization_index = len(self._lineage)
        self._record_observation("sequence-verification", validation_started,
            realization_index, compilation_result, {"realizations": 1})
        normalization_started = perf_counter()
        boundaries = _resolve_nominal_boundaries(
            (compilation_result,), realization_index_offset=realization_index
        )
        if self._previous_boundary_time is not None:
            if boundaries[0].nominal_time <= self._previous_boundary_time:
                raise ValueError("Runner artifact boundaries are not a strict suffix")
        self._record_observation(
            "semantic-normalization", normalization_started, realization_index,
            compilation_result, {"activation_boundaries": len(boundaries)},
        )
        capacity_seconds = encoding_seconds = hashing_seconds = writing_seconds = 0.0
        boundary_start = len(self._record_index)
        packed_bytes = 0
        for boundary in boundaries:
            stage_started = perf_counter()
            assert self._capacity_demand is not None
            self._capacity_demand.add(boundary)
            for operation in boundary.operations:
                if isinstance(operation.intent, _ResourceIntent):
                    connection = operation.intent.connection_id
                    if connection not in self._logical_ports:
                        first = 2 * len(self._logical_ports) + 1
                        if first + 1 > 0xFFFFFFFF:
                            raise ValueError("Runner logical port identity capacity exceeded")
                        self._logical_ports[connection] = (first, first + 1)
            capacity_seconds += perf_counter() - stage_started
            stage_started = perf_counter()
            packed = _pack(_boundary_to_data(boundary))
            encoding_seconds += perf_counter() - stage_started
            stage_started = perf_counter()
            digest = sha256(packed).digest()
            hashing_seconds += perf_counter() - stage_started
            stage_started = perf_counter()
            self._record_stream.write(packed)
            writing_seconds += perf_counter() - stage_started
            self._record_index.append((len(packed), len(boundary.operations), digest))
            packed_bytes += len(packed)
        self._previous_boundary_time = boundaries[-1].nominal_time
        self._lineage.append(
            {
                "index": realization_index,
                "source_plan_id": compilation_result.source_plan_id,
                "source_previous_plan_id": compilation_result.source_previous_plan_id,
                "physical_world_fingerprint": compilation_result.physical_world_fingerprint,
                "input_state_fingerprint": compilation_result.input_state_fingerprint,
                "output_state_fingerprint": compilation_result.output_state_fingerprint,
                "start_timestamp": compilation_result.start_timestamp.isoformat(),
                "end_timestamp": compilation_result.end_timestamp.isoformat(),
                "boundary_start": boundary_start,
                "boundary_count": len(boundaries),
            }
        )
        if self._observer is not None:
            for stage, duration in (
                ('capacity-and-logical-port-derivation', capacity_seconds),
                ('typed-encoding', encoding_seconds), ('record-hashing', hashing_seconds),
                ('record-file-writing', writing_seconds),
            ):
                record_performance(self._observer,component='runner-artifact-writer', stage=stage,
                    phase='one-shot', duration_seconds=duration,
                    scope={'realization_index': realization_index},
                    work={'records': len(boundaries), 'packed_bytes': packed_bytes})
        self._previous = compilation_result
        self._record_observation(
            "writer-total", total_started, realization_index, compilation_result,
            {"realizations": 1}, duration_role="rollup",
        )

    def finalize(self) -> Path:
        """Seal the generated sequence with binary integrity and lineage hashes."""

        if self._failed:
            raise RuntimeError("failed Runner artifact writer cannot seal")

        if self._finalized:
            raise RuntimeError("Runner artifact is already finalized")
        if (
            not self._record_index
            or self._projection is None
            or self._zone_prefixes is None
            or self._capacity_demand is None
        ):
            raise ValueError("Runner artifact requires at least one compilation result")
        if self._delivery is None or self._delivery["final"]:
            self._previous = None
        self._record_stream.flush()
        os.fsync(self._record_stream.fileno())
        self._record_stream.close()
        operation_count = sum(value[1] for value in self._record_index)

        index_offset = _HEADER.size
        data_offset = index_offset + len(self._record_index) * _INDEX.size
        indexes: list[bytes] = []
        cursor = data_offset
        for length, record_operation_count, digest in self._record_index:
            indexes.append(
                _INDEX.pack(cursor, length, record_operation_count, 0, digest)
            )
            cursor += length
        body_digest = sha256()
        for index in indexes:
            body_digest.update(index)
        with self._record_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                body_digest.update(chunk)
        header = _HEADER.pack(
            _MAGIC, _FORMAT_VERSION, _HEADER.size, len(self._record_index),
            index_offset, data_offset, body_digest.digest(),
        )
        binary_path = self._root / _BINARY_NAME
        binary_size, binary_sha256 = _write_binary_atomic(
            binary_path,
            header=header,
            indexes=indexes,
            records=self._record_path,
        )
        self._record_path.unlink()
        manifest = {
            "artifact_type": _ARTIFACT_TYPE,
            "format_version": _FORMAT_VERSION,
            "encoding": _ENCODING,
            "terminal": True,
            "semantic_validation": "complete-before-seal",
            "binary": {
                "path": _BINARY_NAME,
                "bytes": binary_size,
                "sha256": binary_sha256,
                "header_bytes": _HEADER.size,
                "index_entry_bytes": _INDEX.size,
                "boundary_count": len(self._record_index),
                "operation_count": operation_count,
            },
            "realization_count": len(self._lineage),
            "sequence_sha256": _sequence_sha256(self._lineage),
            "physical_world": {
                "nodes": [_node_to_data(value) for value in self._projection.node_ids],
                "zones": list(self._projection.zone_ids),
                "network_scopes": list(self._projection.network_scopes),
                "fingerprint": self._lineage[0]["physical_world_fingerprint"],
            },
            "zone_prefixes": [list(value) for value in self._zone_prefixes],
            "runtime_capacity_demand": self._capacity_demand.snapshot(),
            "logical_ports": [
                [_connection_to_data(connection), *ports]
                for connection, ports in self._logical_ports.items()
            ],
            "timeline": {
                "start": self._lineage[0]["start_timestamp"],
                "end": self._lineage[-1]["end_timestamp"],
                "boundary_count": len(self._record_index),
            },
            "lineage": self._lineage,
        }
        manifest_path = self._root / _MANIFEST_NAME
        if self._delivery is not None:
            manifest["delivery"] = self._delivery
        manifest_bytes = _canonical_json_bytes(manifest, trailing_newline=True)
        _write_bytes_atomic(manifest_path, manifest_bytes)
        self._sealed_metadata = {
            "manifest_path": manifest_path,
            "manifest_sha256": sha256(manifest_bytes).hexdigest(),
            "manifest": deepcopy(manifest),
        }
        self._finalized = True
        return manifest_path

    def sealed_metadata(self) -> dict:
        """Return completed seal facts without reopening or verifying our output.

        The manifest owns counts, lineage, delivery, capacity and binary hashes.
        This query is available only after the terminal manifest was published.
        """
        if self._sealed_metadata is None:
            raise RuntimeError("execution materials have not been sealed")
        return deepcopy(self._sealed_metadata)

    def _record_observation(
        self, stage: str, started: float, index: int,
        compilation_result: BpfCompilationResult, work: Mapping[str, int | float],
        *, duration_role: str = "stage",
    ) -> None:
        if self._observer is not None:
            record_performance(self._observer,
                component="runner-artifact-writer", stage=stage,
                phase=("bootstrap" if index == 0 else "steady"),
                duration_seconds=perf_counter() - started,
                duration_role=duration_role,
                scope={"realization_index": index, "plan_id": compilation_result.source_plan_id},
                work=work, started_monotonic=started,
            )


def _validate_sequence_pair(previous: BpfCompilationResult | None, value: BpfCompilationResult) -> None:
    if previous is None:
        if value.source_previous_plan_id is not None or value.input_state_fingerprint is not None:
            raise ValueError("Runner artifact must begin with a bootstrap Plan")
    elif value.source_previous_plan_id != previous.source_plan_id or value.input_state_fingerprint != previous.output_state_fingerprint or value.observation_interval != previous.observation_interval or value.start_timestamp != previous.end_timestamp + value.observation_interval or previous.physical_world_fingerprint != value.physical_world_fingerprint or previous._physical_world_projection != value._physical_world_projection:
        raise ValueError("Runner artifact sequence breaks Plan/state/PhysicalWorld lineage")


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    destination = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True); raise


def _write_binary_atomic(
    path: Path,
    *,
    header: bytes,
    indexes: Sequence[bytes],
    records: Path,
) -> tuple[int, str]:
    """Publish a large artifact without materializing a second in-memory copy."""

    destination = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}."
    )
    temporary = Path(temporary_name)
    digest = sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            for value in (header, *indexes):
                output.write(value)
                digest.update(value)
                size += len(value)
            with Path(records).open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()
