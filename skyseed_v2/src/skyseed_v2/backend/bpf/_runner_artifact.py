"""Build and open the deterministic precomputed-only Runner artifact.

The historical gzip/JSON realization handoff deliberately no longer exists.
Compilation objects are consumed while generating this artifact and are never
reconstructed by the live Runner process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import heapq
import json
import mmap
import os
from pathlib import Path
import struct
import tempfile
from time import perf_counter
from typing import Any, Mapping, Sequence, TypeAlias

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    OperationalConnectionKind,
    ZoneRouteDemandTarget,
)

from .compiled_realization import (
    BpfCompiledRealization,
    _AttachmentDemand,
    _ForwardingBinding,
    _ForwardingIntent,
    _ForwardingKey,
    _MutationOperation,
    _PhysicalWorldProjection,
    _QdiscIntent,
    _ResourceIntent,
    _ResourceOperation,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
)
from ._runner.runtime_realization import (
    _CapacityAuditError,
    _RuntimeCapacityDemandAccumulator,
    _validate_runtime_capacity_demand,
)


_ARTIFACT_TYPE = "skyseed-v2-runner-runtime-artifact"
_FORMAT_VERSION = 3
_ENCODING = "typed-packed-runtime-tables-v3"
_BINARY_NAME = "runner-runtime.bin"
_MANIFEST_NAME = "manifest.json"
_MAGIC = b"SKYRUN3\0"
_HEADER = struct.Struct("<8sHHIQQ32s")
_INDEX = struct.Struct("<QQII32s")
_MAX_BOUNDARIES = 10_000_000
_MAX_RECORD_BYTES = 1 << 30
_MAX_CONTAINER_ITEMS = 10_000_000
_MAX_DEPTH = 64

_Intent: TypeAlias = (
    _ResourceIntent | _QdiscIntent | _ForwardingIntent | _ZoneEdgeIntent
)
_Reference: TypeAlias = tuple[int, str, int]
_DOMAIN_ATTRIBUTES = (
    ("resource", "resource_realizations"),
    ("parameter", "parameter_realizations"),
    ("forwarding", "forwarding_realizations"),
    ("zone-edge", "zone_edge_realizations"),
)
_DOMAIN_RANK = {
    domain: rank for rank, (domain, _) in enumerate(_DOMAIN_ATTRIBUTES)
}


@dataclass(frozen=True, slots=True)
class _BoundaryOperation:
    realization_index: int
    domain: str
    index: int
    intent: _Intent

    @property
    def reference(self) -> _Reference:
        return self.realization_index, self.domain, self.index


@dataclass(frozen=True, slots=True)
class _NominalBoundary:
    """Represent one dependency-ordered Runner execution boundary."""

    nominal_time: datetime
    operations: tuple[_BoundaryOperation, ...]
    physical_world_projection: _PhysicalWorldProjection
    source_realization_indexes: tuple[int, ...]
    coverage: tuple[tuple[str, str, str], ...]
    inspected_constraint_count: int = field(compare=False)
    source_constraint_count: int
    applied_constraint_count: int
    outgoing_constraint_count: int
    later_constraint_count: int = field(compare=False)
    constraint_reasons: tuple[tuple[str, int], ...]
    constraint_edges: tuple[tuple[_Reference, _Reference, str], ...]
    operation_counts: tuple[tuple[str, int], ...]
    order_sha256: str


def _operation_inventory(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    realization_index_offset: int = 0,
) -> dict[_Reference, _BoundaryOperation]:
    result: dict[_Reference, _BoundaryOperation] = {}
    for realization_index, realization in enumerate(
        realizations, start=realization_index_offset
    ):
        for domain, attribute in _DOMAIN_ATTRIBUTES:
            for index, intent in enumerate(getattr(realization, attribute)):
                operation = _BoundaryOperation(
                    realization_index, domain, index, intent
                )
                result[operation.reference] = operation
    return result


def _connection_key(value: object) -> tuple[str, str, str]:
    connection_id = getattr(value, "connection_id")
    return (
        connection_id.kind.value,
        connection_id.endpoint_a.value,
        connection_id.endpoint_b.value,
    )


def _route_key(intent: _ForwardingIntent) -> tuple[str, str, str]:
    target = intent.route_key.target
    if isinstance(target, GroundStationRouteDemandTarget):
        target_key = ("ground-station", target.ground_station_id.value)
    elif isinstance(target, ZoneRouteDemandTarget):
        target_key = ("zone", target.zone_id)
    else:  # pragma: no cover - RouteKey owns this validation
        raise TypeError("compiled forwarding has an unknown route target")
    return intent.route_key.source.value, *target_key


def _stable_operation_key(operation: _BoundaryOperation) -> tuple[object, ...]:
    intent = operation.intent
    if isinstance(intent, _ResourceIntent):
        semantic = (*_connection_key(intent), intent.operation.value)
    elif isinstance(intent, _QdiscIntent):
        semantic = (
            *_connection_key(intent),
            intent.source.value,
            intent.target.value,
            intent.reason,
        )
    elif isinstance(intent, _ForwardingIntent):
        semantic = (*_route_key(intent), intent.operation.value)
    elif isinstance(intent, _ZoneEdgeIntent):
        semantic = (
            intent.zone_id,
            intent.phase.value,
            intent.operation.value,
        )
    else:  # pragma: no cover - inventory is closed above
        raise TypeError("unknown compiled boundary operation")
    if isinstance(intent, _ResourceIntent):
        phase = 0 if intent.operation is _ResourceOperation.ESTABLISH else 5
    elif isinstance(intent, _QdiscIntent):
        phase = 1
    elif (
        isinstance(intent, _ZoneEdgeIntent)
        and intent.phase is _ZoneEdgePhase.READINESS
    ):
        phase = 2
    elif isinstance(intent, _ForwardingIntent):
        phase = 3
    elif isinstance(intent, _ZoneEdgeIntent):
        phase = 4
    else:  # pragma: no cover - inventory is closed above
        raise TypeError("unknown compiled boundary operation")
    return (phase, _DOMAIN_RANK[operation.domain], *semantic)


def _operation_label(operation: _BoundaryOperation) -> str:
    return "|".join(
        (
            operation.intent.nominal_time.isoformat(),
            *(str(value) for value in _stable_operation_key(operation)),
        )
    )


def _resolve_nominal_boundaries(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    realization_index_offset: int = 0,
) -> tuple[_NominalBoundary, ...]:
    """Normalize compiled work into dependency-ordered Runner boundaries."""

    if not realizations:
        raise ValueError("nominal boundary resolution requires compiled work")
    inventory = _operation_inventory(
        realizations, realization_index_offset=realization_index_offset
    )
    declarations: dict[datetime, int] = {}
    for realization_index, realization in enumerate(
        realizations, start=realization_index_offset
    ):
        timestamps = realization.activation_timestamps
        if not timestamps:
            raise ValueError("compiled realization has no activation boundaries")
        if timestamps != tuple(sorted(set(timestamps))):
            raise ValueError(
                "compiled activation boundaries must be unique and ordered"
            )
        if (
            timestamps[0] != realization.start_timestamp
            or timestamps[-1] != realization.end_timestamp
        ):
            raise ValueError(
                "compiled activation boundaries differ from realization interval"
            )
        for timestamp in timestamps:
            if timestamp in declarations:
                raise ValueError(
                    "compiled realizations duplicate a formal activation boundary"
                )
            declarations[timestamp] = realization_index
        if any(
            intent.nominal_time not in set(timestamps)
            for attribute in (
                "resource_realizations",
                "parameter_realizations",
                "forwarding_realizations",
                "zone_edge_realizations",
            )
            for intent in getattr(realization, attribute)
        ):
            raise ValueError(
                "compiled operation has no declared activation boundary"
            )
    nominal_times = tuple(sorted(declarations))
    return tuple(
        _resolve_nominal_boundary(
            realizations,
            inventory=inventory,
            nominal_time=nominal_time,
            declared_realization_index=declarations[nominal_time],
            realization_index_offset=realization_index_offset,
        )
        for nominal_time in nominal_times
    )


def _resolve_nominal_boundary(
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    inventory: dict[_Reference, _BoundaryOperation],
    nominal_time: datetime,
    declared_realization_index: int,
    realization_index_offset: int = 0,
) -> _NominalBoundary:
    selected = {
        reference: operation
        for reference, operation in inventory.items()
        if operation.intent.nominal_time == nominal_time
    }
    selected_realization_indexes = {declared_realization_index}
    if any(
        operation.realization_index != declared_realization_index
        for operation in selected.values()
    ):
        raise ValueError("activation boundary contains foreign realization work")
    source_constraint_count = sum(
        len(realizations[index - realization_index_offset].constraints)
        for index in selected_realization_indexes
    )
    stable_keys = [_stable_operation_key(value) for value in selected.values()]
    if len(set(stable_keys)) != len(stable_keys):
        raise ValueError(
            "nominal boundary contains operations without a unique semantic key"
        )

    successors: dict[_Reference, set[_Reference]] = {
        reference: set() for reference in selected
    }
    indegree = {reference: 0 for reference in selected}
    inspected = 0
    applied = 0
    outgoing = 0
    later = 0
    reasons: dict[str, int] = {}
    constraint_edges: list[tuple[_Reference, _Reference, str]] = []
    for realization_index, realization in enumerate(
        realizations, start=realization_index_offset
    ):
        for constraint in realization.constraints:
            inspected += 1
            predecessor = (
                realization_index,
                constraint.predecessor.domain,
                constraint.predecessor.index,
            )
            successor = (
                realization_index,
                constraint.successor.domain,
                constraint.successor.index,
            )
            try:
                predecessor_operation = inventory[predecessor]
                successor_operation = inventory[successor]
            except KeyError as error:
                raise ValueError(
                    "compiled constraint references an unknown operation"
                ) from error
            predecessor_selected = predecessor in selected
            successor_selected = successor in selected
            if predecessor_selected and successor_selected:
                if successor not in successors[predecessor]:
                    successors[predecessor].add(successor)
                    indegree[successor] += 1
                    applied += 1
                    reasons[constraint.reason] = (
                        reasons.get(constraint.reason, 0) + 1
                    )
                    constraint_edges.append(
                        (predecessor, successor, constraint.reason)
                    )
                continue
            if successor_selected:
                if predecessor_operation.intent.nominal_time > nominal_time:
                    raise ValueError(
                        "nominal boundary depends on later compiled work"
                    )
                later += 1
                continue
            if predecessor_selected:
                if successor_operation.intent.nominal_time < nominal_time:
                    raise ValueError(
                        "compiled constraint points behind the active frontier"
                    )
                outgoing += 1
            else:
                if predecessor_operation.intent.nominal_time > (
                    successor_operation.intent.nominal_time
                ):
                    raise ValueError(
                        "compiled constraint reverses nominal time"
                    )
                later += 1

    ready: list[tuple[tuple[object, ...], _Reference]] = []
    for reference, degree in indegree.items():
        if degree == 0:
            heapq.heappush(
                ready, (_stable_operation_key(selected[reference]), reference)
            )
    ordered = []
    while ready:
        _, reference = heapq.heappop(ready)
        operation = selected[reference]
        ordered.append(operation)
        for successor in successors[reference]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(
                    ready,
                    (_stable_operation_key(selected[successor]), successor),
                )
    if len(ordered) != len(selected):
        raise ValueError("nominal boundary dependency constraints contain a cycle")

    source_indexes = (declared_realization_index,)
    projection = realizations[
        declared_realization_index - realization_index_offset
    ]._physical_world_projection
    if any(
        realizations[index - realization_index_offset]._physical_world_projection
        != projection
        for index in source_indexes
    ):
        raise ValueError("one boundary spans different PhysicalWorld projections")
    operation_counts = tuple(
        (domain, sum(value.domain == domain for value in ordered))
        for domain, _ in _DOMAIN_ATTRIBUTES
    )
    order_payload = "\n".join(_operation_label(value) for value in ordered)
    return _NominalBoundary(
        nominal_time=nominal_time,
        operations=tuple(ordered),
        physical_world_projection=projection,
        source_realization_indexes=source_indexes,
        coverage=tuple(
            (
                realizations[index - realization_index_offset].source_plan_id,
                realizations[
                    index - realization_index_offset
                ].start_timestamp.isoformat(),
                realizations[
                    index - realization_index_offset
                ].end_timestamp.isoformat(),
            )
            for index in source_indexes
        ),
        inspected_constraint_count=inspected,
        source_constraint_count=source_constraint_count,
        applied_constraint_count=applied,
        outgoing_constraint_count=outgoing,
        later_constraint_count=later,
        constraint_reasons=tuple(sorted(reasons.items())),
        constraint_edges=tuple(constraint_edges),
        operation_counts=operation_counts,
        order_sha256=sha256(order_payload.encode("utf-8")).hexdigest(),
    )


def _write_runner_artifact(
    realizations: Sequence[BpfCompiledRealization],
    directory: Path,
    *,
    execution_observer: _ExecutionObserver | None = None,
) -> Path:
    writer = _RunnerArtifactWriter(
        directory, execution_observer=execution_observer
    )
    for realization in realizations:
        writer.append(realization)
    return writer.finalize()


class _RunnerArtifactWriter:
    """Consume rich Compiler output and seal one compact Runner artifact."""

    def __init__(
        self,
        directory: Path,
        *,
        execution_observer: _ExecutionObserver | None = None,
    ) -> None:
        root = Path(directory)
        if root.is_symlink():
            raise ValueError("Runner artifact root must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise FileExistsError(f"Runner artifact directory is not empty: {root}")
        if execution_observer is not None and not isinstance(
            execution_observer, _ExecutionObserver
        ):
            raise TypeError("execution_observer must be an _ExecutionObserver")
        self._root = root.resolve()
        self._observer = execution_observer
        self._previous: BpfCompiledRealization | None = None
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
        self._finalized = False

    def append(self, realization: BpfCompiledRealization) -> None:
        """Normalize one value now; retain no rich planned-state history."""

        total_started = perf_counter()
        if self._finalized:
            raise RuntimeError("Runner artifact is already finalized")
        if not isinstance(realization, BpfCompiledRealization):
            raise TypeError("Runner artifact requires BpfCompiledRealization values")
        validation_started = perf_counter()
        _validate_sequence_pair(self._previous, realization)
        projection = realization._physical_world_projection
        zone_prefixes = tuple(realization.zone_prefixes)
        if self._projection is None:
            self._projection = projection
            self._zone_prefixes = zone_prefixes
            self._capacity_demand = _RuntimeCapacityDemandAccumulator(
                projection, tuple(prefix for _, prefix in zone_prefixes)
            )
        elif projection != self._projection or zone_prefixes != self._zone_prefixes:
            raise ValueError("Runner artifact static PhysicalWorld metadata changed")

        realization_index = len(self._lineage)
        boundaries = _resolve_nominal_boundaries(
            (realization,), realization_index_offset=realization_index
        )
        if self._previous_boundary_time is not None:
            if boundaries[0].nominal_time <= self._previous_boundary_time:
                raise ValueError("Runner artifact boundaries are not a strict suffix")
        self._record_observation(
            "semantic-normalization", validation_started, realization_index,
            realization, {"activation_boundaries": len(boundaries)},
        )
        packing_started = perf_counter()
        boundary_start = len(self._record_index)
        packed_bytes = 0
        for boundary in boundaries:
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
            packed = _pack(_boundary_to_data(boundary))
            decoded = _decode_boundary_data(
                _unpack(packed), physical_world_projection=projection
            )
            if decoded != boundary:
                raise ValueError("Runner artifact packed boundary changed semantics")
            self._record_stream.write(packed)
            self._record_index.append(
                (len(packed), len(boundary.operations), sha256(packed).digest())
            )
            packed_bytes += len(packed)
        self._previous_boundary_time = boundaries[-1].nominal_time
        self._lineage.append(
            {
                "index": realization_index,
                "source_plan_id": realization.source_plan_id,
                "source_previous_plan_id": realization.source_previous_plan_id,
                "physical_world_fingerprint": realization.physical_world_fingerprint,
                "input_state_fingerprint": realization.input_state_fingerprint,
                "output_state_fingerprint": realization.output_state_fingerprint,
                "start_timestamp": realization.start_timestamp.isoformat(),
                "end_timestamp": realization.end_timestamp.isoformat(),
                "boundary_start": boundary_start,
                "boundary_count": len(boundaries),
            }
        )
        self._record_observation(
            "typed-packing", packing_started, realization_index, realization,
            {"records": len(boundaries), "packed_bytes": packed_bytes},
        )
        self._previous = realization
        self._record_observation(
            "writer-total", total_started, realization_index, realization,
            {"realizations": 1}, duration_role="rollup",
        )

    def finalize(self) -> Path:
        """Validate all packed records and atomically publish binary then seal."""

        if self._finalized:
            raise RuntimeError("Runner artifact is already finalized")
        if (
            not self._record_index
            or self._projection is None
            or self._zone_prefixes is None
            or self._capacity_demand is None
        ):
            raise ValueError("Runner artifact requires at least one realization")
        self._finalized = True
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
        _write_bytes_atomic(
            manifest_path, _canonical_json_bytes(manifest, trailing_newline=True)
        )
        return manifest_path

    def _record_observation(
        self, stage: str, started: float, index: int,
        realization: BpfCompiledRealization, work: Mapping[str, int | float],
        *, duration_role: str = "stage",
    ) -> None:
        if self._observer is not None:
            self._observer.record(
                component="runner-artifact-writer", stage=stage,
                phase=("bootstrap" if index == 0 else "steady"),
                duration_seconds=perf_counter() - started,
                duration_role=duration_role,
                scope={"realization_index": index, "plan_id": realization.source_plan_id},
                work=work, started_monotonic=started,
            )


class _DecodedIdentities:
    """Immutable value reuse owned by one open finite artifact."""

    def __init__(self, nodes=()):
        self.nodes = {(node.asn, node.entity_kind, node.entity_id): node for node in nodes}
        self.connections = {}
        self.timestamps = {}


class _RunnerArtifact:
    """Own one verified read-only mmap and lazily decode compact boundaries."""

    def __init__(self, manifest_path: Path) -> None:
        snapshot = _read_runner_artifact_manifest_snapshot(manifest_path)
        self._manifest_path = Path(manifest_path).resolve()
        self._snapshot = snapshot
        self._identities = _DecodedIdentities(snapshot["physical_world_projection"].node_ids)
        binary_path = self._manifest_path.parent / snapshot["binary_path"]
        self._stream = binary_path.open("rb")
        try:
            self._mmap = mmap.mmap(self._stream.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                self._indexes = self._validate_binary()
            except BaseException:
                self._mmap.close()
                raise
        except BaseException:
            self._stream.close()
            raise

    @property
    def physical_world_projection(self) -> _PhysicalWorldProjection:
        return self._snapshot["physical_world_projection"]

    @property
    def zone_prefixes(self) -> tuple[str, ...]:
        return tuple(value[1] for value in self._snapshot["zone_prefixes"])

    @property
    def boundary_count(self) -> int:
        return len(self._indexes)

    @property
    def realization_count(self) -> int:
        return int(self._snapshot["realization_count"])

    @property
    def sequence_sha256(self) -> str:
        return str(self._snapshot["sequence_sha256"])

    @property
    def timeline_end(self) -> str:
        """Return the sealed formal end without decoding a boundary record."""

        return str(self._snapshot["timeline"]["end"])

    @property
    def physical_world_node_count(self) -> int:
        """Return immutable inspection metadata detached from the mmap."""

        return len(self._snapshot["physical_world_projection"].node_ids)

    @property
    def logical_ports(self) -> Mapping[OperationalConnectionId, tuple[int, int]]:
        return dict(self._snapshot["logical_ports"])

    @property
    def runtime_capacity_demand(self) -> dict[str, Any]:
        """Return the detached sealed mechanical capacity envelope."""

        return {
            **self._snapshot["runtime_capacity_demand"],
            "collision_audit": dict(
                self._snapshot["runtime_capacity_demand"]["collision_audit"]
            ),
            "per_node": [
                dict(value)
                for value in self._snapshot["runtime_capacity_demand"]["per_node"]
            ],
            "terminal": dict(
                self._snapshot["runtime_capacity_demand"]["terminal"]
            ),
        }

    def boundary(self, index: int):
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("Runner artifact boundary index must be an int")
        try:
            offset, length, expected_operations, digest = self._indexes[index]
        except IndexError as error:
            raise IndexError("Runner artifact boundary index is out of range") from error
        packed = self._mmap[offset:offset + length]
        if sha256(packed).digest() != digest:
            raise ValueError("Runner artifact binary record hash differs")
        value = _decode_boundary_data(
            _unpack(packed),
            physical_world_projection=self.physical_world_projection,
            identities=self._identities,
        )
        if len(value.operations) != expected_operations:
            raise ValueError("Runner artifact operation count differs")
        return value

    def boundaries(self) -> Sequence[object]:
        """Expose an indexed view that decodes only the requested boundary."""

        return _RunnerBoundarySequence(self)

    def snapshot(self) -> dict[str, object]:
        return {
            "artifact_type": _ARTIFACT_TYPE, "format_version": _FORMAT_VERSION,
            "encoding": _ENCODING, "realization_count": self.realization_count,
            "boundary_count": self.boundary_count,
            "sequence_sha256": self.sequence_sha256, "mmap": True,
            "boundary_decode": "indexed-on-demand",
            "rich_compiled_realizations_reconstructed": False,
            "runtime_capacity_demand": self.runtime_capacity_demand,
        }

    def close(self) -> None:
        self._mmap.close()
        self._stream.close()
        self._identities.nodes.clear()
        self._identities.connections.clear()
        self._identities.timestamps.clear()

    def _validate_binary(self) -> tuple[tuple[int, int, int, bytes], ...]:
        size = len(self._mmap)
        if size < _HEADER.size:
            raise ValueError("Runner artifact binary header is truncated")
        magic, version, header_size, count, index_offset, data_offset, body_hash = _HEADER.unpack_from(self._mmap, 0)
        if magic != _MAGIC or version != _FORMAT_VERSION or header_size != _HEADER.size:
            raise ValueError("Runner artifact binary version/header is unsupported")
        if count != self._snapshot["boundary_count"] or count > _MAX_BOUNDARIES:
            raise ValueError("Runner artifact binary boundary count differs")
        expected_data_offset = _HEADER.size + count * _INDEX.size
        if index_offset != _HEADER.size or data_offset != expected_data_offset or data_offset > size:
            raise ValueError("Runner artifact binary table offsets are invalid")
        # The manifest's whole-file SHA-256 already authenticates this header,
        # body hash, index, and payload in one streaming pass.  Keep per-record
        # hashes for lazy boundary verification instead of rescanning the full
        # payload during open.
        if body_hash != bytes.fromhex(self._snapshot["binary_body_sha256"]):
            raise ValueError("Runner artifact binary body hash differs")
        result = []
        expected_offset = data_offset
        for index in range(count):
            offset, length, operations, reserved, digest = _INDEX.unpack_from(self._mmap, index_offset + index * _INDEX.size)
            if reserved != 0 or length == 0 or length > _MAX_RECORD_BYTES or offset != expected_offset or offset + length > size:
                raise ValueError("Runner artifact binary record bounds are invalid")
            result.append((offset, length, operations, bytes(digest)))
            expected_offset = offset + length
        if expected_offset != size:
            raise ValueError("Runner artifact binary has unindexed trailing bytes")
        return tuple(result)


class _RunnerBoundarySequence(Sequence[object]):
    """Small sequence facade over the artifact's fixed mmap index."""

    def __init__(self, artifact: _RunnerArtifact) -> None:
        self._artifact = artifact

    def __len__(self) -> int:
        return self._artifact.boundary_count

    def __getitem__(self, index: int | slice) -> object:
        if isinstance(index, slice):
            return tuple(
                self._artifact.boundary(value)
                for value in range(*index.indices(len(self)))
            )
        return self._artifact.boundary(index)


def _open_runner_artifact(manifest_path: Path) -> _RunnerArtifact:
    return _RunnerArtifact(manifest_path)


def _read_runner_artifact_manifest_snapshot(manifest_path: Path) -> dict[str, Any]:
    raw = Path(manifest_path)
    if raw.is_symlink():
        raise ValueError("Runner artifact manifest must not be a symlink")
    path = raw.resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Runner artifact manifest is unavailable or malformed") from error
    expected = {
        "artifact_type", "format_version", "encoding", "terminal",
        "semantic_validation", "binary", "realization_count", "sequence_sha256",
        "physical_world", "zone_prefixes", "runtime_capacity_demand", "logical_ports",
        "timeline", "lineage",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise ValueError("Runner artifact manifest schema differs")
    if manifest["artifact_type"] != _ARTIFACT_TYPE or manifest["format_version"] != _FORMAT_VERSION or manifest["encoding"] != _ENCODING or manifest["terminal"] is not True or manifest["semantic_validation"] != "complete-before-seal":
        raise ValueError("Runner artifact manifest/version/seal is unsupported")
    binary = manifest["binary"]
    if not isinstance(binary, dict) or set(binary) != {"path", "bytes", "sha256", "header_bytes", "index_entry_bytes", "boundary_count", "operation_count"}:
        raise ValueError("Runner artifact binary manifest schema differs")
    if binary["path"] != _BINARY_NAME:
        raise ValueError("Runner artifact binary path differs")
    binary_path = path.parent / _BINARY_NAME
    if binary_path.is_symlink() or not binary_path.is_file():
        raise ValueError("Runner artifact binary is unavailable")
    binary_sha256, binary_body_sha256 = _sha256_file_and_body(
        binary_path, body_offset=_HEADER.size
    )
    if binary_path.stat().st_size != binary["bytes"] or binary_sha256 != binary["sha256"]:
        raise ValueError("Runner artifact binary length/hash differs")
    if isinstance(manifest["realization_count"], bool) or not isinstance(manifest["realization_count"], int) or manifest["realization_count"] <= 0:
        raise ValueError("Runner artifact realization_count is invalid")
    boundary_count = binary["boundary_count"]
    if isinstance(boundary_count, bool) or not isinstance(boundary_count, int) or not 0 < boundary_count <= _MAX_BOUNDARIES:
        raise ValueError("Runner artifact boundary count is invalid")
    lineage = manifest["lineage"]
    if not isinstance(lineage, list) or len(lineage) != manifest["realization_count"]:
        raise ValueError("Runner artifact lineage count differs")
    if _sequence_sha256(lineage) != manifest["sequence_sha256"]:
        raise ValueError("Runner artifact lineage hash differs")
    physical = manifest["physical_world"]
    if not isinstance(physical, dict) or set(physical) != {"nodes", "zones", "fingerprint"}:
        raise ValueError("Runner artifact PhysicalWorld metadata differs")
    projection = _PhysicalWorldProjection(
        tuple(_node_from_data(value) for value in physical["nodes"]),
        tuple(str(value) for value in physical["zones"]),
    )
    logical_ports = {}
    allocated = set()
    for record in _list(manifest["logical_ports"], "logical_ports"):
        if not isinstance(record, list) or len(record) != 3:
            raise ValueError("Runner artifact logical port schema differs")
        connection = _connection_from_data(record[0])
        ports = tuple(record[1:])
        if (connection in logical_ports
            or connection.endpoint_a not in projection.node_ids
            or connection.endpoint_b not in projection.node_ids
            or any(type(port) is not int or not 0 < port <= 0xFFFFFFFF
                   or port in allocated for port in ports)
            or ports[0] == ports[1]):
            raise ValueError("Runner artifact logical port identity is invalid")
        logical_ports[connection] = ports
        allocated.update(ports)
    zone_prefixes = tuple((str(value[0]), str(value[1])) for value in manifest["zone_prefixes"] if isinstance(value, list) and len(value) == 2)
    if len(zone_prefixes) != len(manifest["zone_prefixes"]):
        raise ValueError("Runner artifact Zone prefix metadata differs")
    try:
        capacity_demand = _validate_runtime_capacity_demand(
            manifest["runtime_capacity_demand"],
            expected_node_keys=tuple(node.value for node in projection.node_ids),
            expected_boundary_count=boundary_count,
        )
    except _CapacityAuditError as error:
        raise ValueError("Runner artifact runtime capacity demand differs") from error
    return {
        "format_version": _FORMAT_VERSION, "encoding": _ENCODING,
        "realization_count": manifest["realization_count"],
        "boundary_count": boundary_count, "operation_count": binary["operation_count"],
        "sequence_sha256": manifest["sequence_sha256"],
        "binary_path": _BINARY_NAME, "binary_sha256": binary["sha256"],
        "binary_body_sha256": binary_body_sha256,
        "physical_world_fingerprint": physical["fingerprint"],
        "physical_world_projection": projection, "zone_prefixes": zone_prefixes,
        "runtime_capacity_demand": capacity_demand, "logical_ports": logical_ports,
        "timeline": manifest["timeline"], "lineage": tuple(dict(value) for value in lineage),
    }


def _boundary_to_data(boundary: object) -> dict[str, object]:
    return {
        "time": boundary.nominal_time.isoformat(),
        "operations": [_operation_to_data(value) for value in boundary.operations],
        "source_indexes": list(boundary.source_realization_indexes),
        "coverage": [list(value) for value in boundary.coverage],
        "inspected": boundary.inspected_constraint_count,
        "source_constraints": boundary.source_constraint_count,
        "applied": boundary.applied_constraint_count,
        "outgoing": boundary.outgoing_constraint_count,
        "later": boundary.later_constraint_count,
        "reasons": [list(value) for value in boundary.constraint_reasons],
        "edges": [[list(a), list(b), reason] for a, b, reason in boundary.constraint_edges],
        "counts": [list(value) for value in boundary.operation_counts],
        "order_sha256": boundary.order_sha256,
    }


def _decode_boundary_data(
    value: object,
    *,
    physical_world_projection: _PhysicalWorldProjection,
    identities: _DecodedIdentities | None = None,
):
    record = _dict(value, "boundary")
    operations = tuple(_BoundaryOperation(int(item[0]), str(item[1]), int(item[2]), _intent_from_data(item[3], identities)) for item in _list(record["operations"], "operations"))
    return _NominalBoundary(
        nominal_time=_timestamp(record["time"], identities), operations=operations,
        physical_world_projection=physical_world_projection,
        source_realization_indexes=tuple(int(item) for item in record["source_indexes"]),
        coverage=tuple(tuple(str(part) for part in item) for item in record["coverage"]),
        inspected_constraint_count=int(record["inspected"]),
        source_constraint_count=int(record["source_constraints"]),
        applied_constraint_count=int(record["applied"]),
        outgoing_constraint_count=int(record["outgoing"]),
        later_constraint_count=int(record["later"]),
        constraint_reasons=tuple((str(item[0]), int(item[1])) for item in record["reasons"]),
        constraint_edges=tuple((tuple(item[0]), tuple(item[1]), str(item[2])) for item in record["edges"]),
        operation_counts=tuple((str(item[0]), int(item[1])) for item in record["counts"]),
        order_sha256=str(record["order_sha256"]),
    )


def _operation_to_data(operation: object) -> list[object]:
    return [operation.realization_index, operation.domain, operation.index, _intent_to_data(operation.intent)]


def _intent_to_data(intent: object) -> list[object]:
    if isinstance(intent, _ResourceIntent):
        return ["resource", intent.nominal_time.isoformat(), intent.operation.value, _connection_to_data(intent.connection_id)]
    if isinstance(intent, _QdiscIntent):
        return ["qdisc", intent.nominal_time.isoformat(), _connection_to_data(intent.connection_id), _node_to_data(intent.source), _node_to_data(intent.target), intent.bandwidth_bps, intent.delay_us, intent.packet_loss_ppm, intent.reason]
    if isinstance(intent, _ForwardingIntent):
        return ["forwarding", intent.nominal_time.isoformat(), intent.operation.value, _forwarding_key_to_data(intent.route_key), None if intent.previous is None else _binding_to_data(intent.previous), None if intent.current is None else _binding_to_data(intent.current)]
    if isinstance(intent, _ZoneEdgeIntent):
        return ["zone-edge", intent.nominal_time.isoformat(), intent.operation.value, intent.phase.value, intent.zone_id, intent.packet_prefix, None if intent.previous_service_satellite_id is None else _node_to_data(intent.previous_service_satellite_id), None if intent.service_satellite_id is None else _node_to_data(intent.service_satellite_id)]
    raise TypeError("unknown normalized Runner intent")


def _intent_from_data(value: object, identities: _DecodedIdentities | None = None) -> object:
    item = _list(value, "intent")
    kind = item[0]
    timestamp = _timestamp(item[1], identities)
    if kind == "resource" and len(item) == 4:
        connection = _connection_from_data(item[3], identities)
        return _ResourceIntent(timestamp, _ResourceOperation(item[2]), connection, (_AttachmentDemand(connection, connection.endpoint_a, connection.endpoint_b), _AttachmentDemand(connection, connection.endpoint_b, connection.endpoint_a)))
    if kind == "qdisc" and len(item) == 9:
        return _QdiscIntent(timestamp, _connection_from_data(item[2], identities), _node_from_data(item[3], identities), _node_from_data(item[4], identities), int(item[5]), int(item[6]), int(item[7]), str(item[8]))
    if kind == "forwarding" and len(item) == 6:
        return _ForwardingIntent(timestamp, _MutationOperation(item[2]), _forwarding_key_from_data(item[3], identities), None if item[4] is None else _binding_from_data(item[4], identities), None if item[5] is None else _binding_from_data(item[5], identities))
    if kind == "zone-edge" and len(item) == 8:
        return _ZoneEdgeIntent(timestamp, _MutationOperation(item[2]), _ZoneEdgePhase(item[3]), str(item[4]), str(item[5]), None if item[6] is None else _node_from_data(item[6], identities), None if item[7] is None else _node_from_data(item[7], identities))
    raise ValueError("Runner artifact intent schema differs")


def _node_to_data(value: NodeId) -> list[object]:
    return [value.asn, value.entity_kind, value.entity_id]


def _node_from_data(value: object, identities: _DecodedIdentities | None = None) -> NodeId:
    item = _list(value, "node")
    if len(item) != 3:
        raise ValueError("Runner artifact node schema differs")
    key = (int(item[0]), str(item[1]), str(item[2]))
    if identities is None:
        return NodeId(*key)
    if key not in identities.nodes:
        identities.nodes[key] = NodeId(*key)
    return identities.nodes[key]


def _connection_to_data(value: OperationalConnectionId) -> list[object]:
    return [value.kind.value, _node_to_data(value.endpoint_a), _node_to_data(value.endpoint_b)]


def _connection_from_data(value: object, identities: _DecodedIdentities | None = None) -> OperationalConnectionId:
    item = _list(value, "connection")
    if len(item) != 3:
        raise ValueError("Runner artifact connection schema differs")
    key = (OperationalConnectionKind(item[0]), _node_from_data(item[1], identities), _node_from_data(item[2], identities))
    if identities is None:
        return OperationalConnectionId(*key)
    if key not in identities.connections:
        identities.connections[key] = OperationalConnectionId(*key)
    return identities.connections[key]


def _target_to_data(value: object) -> list[object]:
    if isinstance(value, GroundStationRouteDemandTarget):
        return ["ground-station", _node_to_data(value.ground_station_id)]
    if isinstance(value, ZoneRouteDemandTarget):
        return ["zone", value.zone_id]
    raise TypeError("unknown forwarding target")


def _target_from_data(value: object, identities: _DecodedIdentities | None = None) -> object:
    item = _list(value, "target")
    if len(item) != 2:
        raise ValueError("Runner artifact target schema differs")
    if item[0] == "ground-station":
        return GroundStationRouteDemandTarget(_node_from_data(item[1], identities))
    if item[0] == "zone":
        return ZoneRouteDemandTarget(str(item[1]))
    raise ValueError("Runner artifact forwarding target kind differs")


def _forwarding_key_to_data(value: _ForwardingKey) -> list[object]:
    return [_node_to_data(value.source), _target_to_data(value.target)]


def _forwarding_key_from_data(value: object, identities: _DecodedIdentities | None = None) -> _ForwardingKey:
    item = _list(value, "forwarding key")
    return _ForwardingKey(_node_from_data(item[0], identities), _target_from_data(item[1], identities))


def _binding_to_data(value: _ForwardingBinding) -> list[object]:
    return [_forwarding_key_to_data(value.route_key), _node_to_data(value.next_hop), _connection_to_data(value.connection_id), value.packet_prefix]


def _binding_from_data(value: object, identities: _DecodedIdentities | None = None) -> _ForwardingBinding:
    item = _list(value, "binding")
    return _ForwardingBinding(_forwarding_key_from_data(item[0], identities), _node_from_data(item[1], identities), _connection_from_data(item[2], identities), None if item[3] is None else str(item[3]))


def _validate_sequence_pair(previous: BpfCompiledRealization | None, value: BpfCompiledRealization) -> None:
    if previous is None:
        if value.source_previous_plan_id is not None or value.input_state_fingerprint is not None:
            raise ValueError("Runner artifact must begin with a bootstrap Plan")
    elif value.source_previous_plan_id != previous.source_plan_id or value.input_state_fingerprint != previous.output_state_fingerprint or value.observation_interval != previous.observation_interval or value.start_timestamp != previous.end_timestamp + value.observation_interval or previous.physical_world_fingerprint != value.physical_world_fingerprint or previous._physical_world_projection != value._physical_world_projection:
        raise ValueError("Runner artifact sequence breaks Plan/state/PhysicalWorld lineage")


def _sequence_sha256(entries: Sequence[Mapping[str, object]]) -> str:
    return sha256(_canonical_json_bytes(list(entries))).hexdigest()


def _pack(value: object) -> bytes:
    output = bytearray()
    def emit(item: object, depth: int) -> None:
        if depth > _MAX_DEPTH:
            raise ValueError("Runner artifact packed value is too deeply nested")
        if item is None: output.extend(b"n")
        elif item is False: output.extend(b"f")
        elif item is True: output.extend(b"t")
        elif isinstance(item, int): output.extend(b"i" + struct.pack("<q", item))
        elif isinstance(item, str):
            raw = item.encode("utf-8"); output.extend(b"s" + struct.pack("<I", len(raw)) + raw)
        elif isinstance(item, (list, tuple)):
            if len(item) > _MAX_CONTAINER_ITEMS: raise ValueError("Runner artifact packed sequence is too large")
            output.extend(b"l" + struct.pack("<I", len(item)))
            for child in item: emit(child, depth + 1)
        elif isinstance(item, dict):
            if any(not isinstance(key, str) for key in item): raise TypeError("Runner artifact dictionary keys must be strings")
            keys = sorted(item); output.extend(b"d" + struct.pack("<I", len(keys)))
            for key in keys: emit(key, depth + 1); emit(item[key], depth + 1)
        else: raise TypeError(f"Runner artifact cannot pack {type(item).__name__}")
    emit(value, 0)
    return bytes(output)


def _unpack(value: bytes | memoryview) -> object:
    data = memoryview(value); cursor = 0
    def take(size: int) -> memoryview:
        nonlocal cursor
        if size < 0 or cursor + size > len(data): raise ValueError("Runner artifact packed value is truncated")
        result = data[cursor:cursor + size]; cursor += size; return result
    def read(depth: int) -> object:
        if depth > _MAX_DEPTH: raise ValueError("Runner artifact packed value is too deeply nested")
        tag = bytes(take(1))
        if tag == b"n": return None
        if tag == b"f": return False
        if tag == b"t": return True
        if tag == b"i": return struct.unpack("<q", take(8))[0]
        if tag == b"s":
            length = struct.unpack("<I", take(4))[0]
            try: return bytes(take(length)).decode("utf-8")
            except UnicodeDecodeError as error: raise ValueError("Runner artifact string is not UTF-8") from error
        if tag in {b"l", b"d"}:
            count = struct.unpack("<I", take(4))[0]
            if count > _MAX_CONTAINER_ITEMS: raise ValueError("Runner artifact packed container is too large")
            if tag == b"l": return [read(depth + 1) for _ in range(count)]
            result: dict[str, object] = {}; prior: str | None = None
            for _ in range(count):
                key = read(depth + 1)
                if not isinstance(key, str) or (prior is not None and key <= prior): raise ValueError("Runner artifact dictionary keys are not canonical")
                result[key] = read(depth + 1); prior = key
            return result
        raise ValueError("Runner artifact packed value has an unknown type tag")
    result = read(0)
    if cursor != len(data): raise ValueError("Runner artifact packed value has trailing bytes")
    return result


def _dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict): raise ValueError(f"Runner artifact {label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list): raise ValueError(f"Runner artifact {label} must be a list")
    return value


def _timestamp(value: object, identities: _DecodedIdentities | None = None) -> datetime:
    if not isinstance(value, str): raise ValueError("Runner artifact timestamp must be text")
    if identities is not None and value in identities.timestamps:
        return identities.timestamps[value]
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None: raise ValueError("Runner artifact timestamp must be timezone-aware")
    if identities is not None:
        identities.timestamps[value] = result
    return result


def _sha256_file_and_body(
    path: Path, *, body_offset: int
) -> tuple[str, str]:
    digest = sha256()
    body_digest = sha256()
    cursor = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            body_start = max(0, body_offset - cursor)
            if body_start < len(chunk):
                body_digest.update(chunk[body_start:])
            cursor += len(chunk)
    return digest.hexdigest(), body_digest.hexdigest()


def _canonical_json_bytes(value: object, *, trailing_newline: bool = False) -> bytes:
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return (text + ("\n" if trailing_newline else "")).encode("utf-8")


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


__all__: list[str] = []
