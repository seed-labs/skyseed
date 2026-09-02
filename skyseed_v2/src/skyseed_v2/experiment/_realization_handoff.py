"""Persist deterministic data-only BPF realization handoffs."""

from __future__ import annotations

import gzip
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
from typing import Any, Mapping, Sequence

from skyseed_v2.shared import NodeId
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    GroundStationRouteDemandTarget,
    OperationalConnectionId,
    OperationalConnectionKind,
    ZoneRouteDemandTarget,
)
from skyseed_v2.simulator.planning.evidence import (
    planned_operational_state_from_data,
    planned_operational_state_to_data,
)

from ..backend.bpf.compiled_realization import (
    BpfCompiledActivationBoundary,
    BpfCompiledRealization,
    _AttachmentDemand,
    _ForwardingBinding,
    _ForwardingIntent,
    _ForwardingKey,
    _IntendedState,
    _IntentReference,
    _MutationOperation,
    _OrderingConstraint,
    _PhysicalWorldProjection,
    _QdiscIntent,
    _QdiscState,
    _ResourceIntent,
    _ResourceOperation,
    _ResourceState,
    _ZoneEdgeIntent,
    _ZoneEdgePhase,
    _ZoneEdgeState,
)


_ARTIFACT_TYPE = "bpf-compiled-realization-sequence"
_FORMAT_VERSION = 3
_ENCODING = "deterministic-gzip-json-v1"
_REALIZATION_SCHEMA = "skyseed-v2-bpf-compiled-realization-v2"


def _write_realization_handoff(
    realizations: Sequence[BpfCompiledRealization],
    directory: Path,
    *,
    execution_observer: _ExecutionObserver | None = None,
) -> Path:
    """Write one bounded deterministic file per immutable realization."""

    writer = _RealizationHandoffWriter(
        directory, execution_observer=execution_observer
    )
    for realization in realizations:
        writer.append(realization)
    return writer.finalize()


class _RealizationHandoffWriter:
    """Incrementally persist the canonical immutable realization stream."""

    def __init__(
        self,
        directory: Path,
        *,
        execution_observer: _ExecutionObserver | None = None,
    ) -> None:
        root = Path(directory)
        if root.is_symlink():
            raise ValueError("realization handoff root must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise FileExistsError(
                f"realization handoff directory is not empty: {root}"
            )
        self._root = root.resolve()
        self._entries: list[dict[str, object]] = []
        self._previous: BpfCompiledRealization | None = None
        self._finalized = False
        if execution_observer is not None and not isinstance(
            execution_observer, _ExecutionObserver
        ):
            raise TypeError("execution_observer must be an _ExecutionObserver")
        self._execution_observer = execution_observer

    def append(self, realization: BpfCompiledRealization) -> None:
        """Write one value without retaining historical live objects."""

        total_started = perf_counter()
        if self._finalized:
            raise RuntimeError("realization handoff is already finalized")
        if not isinstance(realization, BpfCompiledRealization):
            raise TypeError(
                "realization handoff requires BpfCompiledRealization values"
            )
        validation_started = perf_counter()
        _validate_sequence_pair(self._previous, realization)
        index = len(self._entries)
        self._record(
            "structural-validation",
            validation_started,
            index=index,
            realization=realization,
            work={"activation_boundaries": len(realization.activation_timestamps)},
        )
        encoding_started = perf_counter()
        raw = _canonical_json_bytes(_realization_to_data(realization))
        compressed = _deterministic_gzip(raw)
        self._record(
            "encoding-and-compression",
            encoding_started,
            index=index,
            realization=realization,
            work={
                "uncompressed_bytes": len(raw),
                "compressed_bytes": len(compressed),
            },
        )
        name = f"realization-{index:05d}.json.gz"
        persistence_started = perf_counter()
        _write_bytes_atomic(self._root / name, compressed)
        self._record(
            "durable-persistence",
            persistence_started,
            index=index,
            realization=realization,
            work={"output_bytes": len(compressed), "files": 1},
        )
        entry = {
            "index": index,
            "path": name,
            "source_plan_id": realization.source_plan_id,
            "source_previous_plan_id": realization.source_previous_plan_id,
            "physical_world_fingerprint": (
                realization.physical_world_fingerprint
            ),
            "input_state_fingerprint": realization.input_state_fingerprint,
            "output_state_fingerprint": realization.output_state_fingerprint,
            "start_timestamp": realization.start_timestamp.isoformat(),
            "end_timestamp": realization.end_timestamp.isoformat(),
            "activation_count": len(realization.activation_timestamps),
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(raw),
            "sha256": sha256(compressed).hexdigest(),
            "uncompressed_sha256": sha256(raw).hexdigest(),
        }
        self._entries.append(entry)
        self._previous = realization
        self._record(
            "writer-total",
            total_started,
            index=index,
            realization=realization,
            work={"realizations": 1},
            duration_role="rollup",
        )

    def finalize(self) -> Path:
        """Seal and checksum the complete lineage-preserving sequence."""

        if self._finalized:
            raise RuntimeError("realization handoff is already finalized")
        if not self._entries:
            raise ValueError(
                "realization handoff requires at least one realization"
            )
        self._finalized = True
        manifest = {
            "artifact_type": _ARTIFACT_TYPE,
            "format_version": _FORMAT_VERSION,
            "encoding": _ENCODING,
            "data_only": True,
            "realization_count": len(self._entries),
            "terminal": True,
            "sequence_sha256": _sequence_sha256(self._entries),
            "realizations": self._entries,
        }
        manifest_path = self._root / "manifest.json"
        _write_bytes_atomic(
            manifest_path,
            _canonical_json_bytes(manifest, trailing_newline=True),
        )
        return manifest_path

    def _record(
        self,
        stage: str,
        started: float,
        *,
        index: int,
        realization: BpfCompiledRealization,
        work: Mapping[str, int | float],
        duration_role: str = "stage",
    ) -> None:
        if self._execution_observer is None:
            return
        self._execution_observer.record(
            component="realization-handoff-writer",
            stage=stage,
            phase="steady",
            duration_seconds=perf_counter() - started,
            duration_role=duration_role,
            scope={
                "realization_index": index,
                "plan_id": realization.source_plan_id,
            },
            work=work,
            started_monotonic=started,
        )


class _PersistedRealizationSupplier:
    """Eagerly validate a bounded handoff, then serve immutable values.

    Construction is deliberately outside the Runner lifecycle. Decompression,
    structural decoding, checksums, references, and sequence validation all
    finish before Runner receives this callable.
    Each Runner pull is therefore a bounded tuple lookup rather than hidden
    compilation or persistence work.
    """

    def __init__(self, manifest_path: Path) -> None:
        raw_path = Path(manifest_path)
        if raw_path.is_symlink():
            raise ValueError("realization handoff manifest must not be a symlink")
        self._manifest_path = raw_path.resolve()
        self._root = self._manifest_path.parent
        manifest_snapshot = _read_persisted_realization_manifest_snapshot(
            self._manifest_path
        )
        self._entries = manifest_snapshot["entries"]
        self._sequence_sha256 = manifest_snapshot["sequence_sha256"]
        self._values = self._read_and_validate_sequence()
        self._index = 0
        self._terminal_returned = False

    def __call__(self) -> BpfCompiledRealization | None:
        if self._index >= len(self._entries):
            self._terminal_returned = True
            return None
        value = self._values[self._index]
        self._index += 1
        return value

    def validated_sequence(self) -> tuple[BpfCompiledRealization, ...]:
        """Return the already validated immutable bounded sequence."""

        return self._values

    def _read_and_validate_sequence(
        self,
    ) -> tuple[BpfCompiledRealization, ...]:
        values: list[BpfCompiledRealization] = []
        previous: BpfCompiledRealization | None = None
        for index, entry in enumerate(self._entries):
            value = self._read_and_validate_entry(
                entry,
                index=index,
                previous=previous,
            )
            values.append(value)
            previous = value
        return tuple(values)

    def _read_and_validate_entry(
        self,
        entry: dict[str, Any],
        *,
        index: int,
        previous: BpfCompiledRealization | None,
    ) -> BpfCompiledRealization:
        relative = _safe_relative_path(entry["path"])
        path = self._root / relative
        if path.is_symlink():
            raise ValueError(
                f"persisted BPF realization must not be a symlink: {relative}"
            )
        try:
            compressed = path.read_bytes()
        except OSError as error:
            raise RuntimeError(
                f"persisted BPF realization is unavailable: {relative}"
            ) from error
        if (
            len(compressed) != entry["compressed_bytes"]
            or sha256(compressed).hexdigest() != entry["sha256"]
        ):
            raise ValueError(
                f"persisted BPF realization checksum differs: {relative}"
            )
        try:
            raw = gzip.decompress(compressed)
        except OSError as error:
            raise ValueError(
                f"persisted BPF realization compression is invalid: {relative}"
            ) from error
        if (
            len(raw) != entry["uncompressed_bytes"]
            or sha256(raw).hexdigest() != entry["uncompressed_sha256"]
        ):
            raise ValueError(
                f"persisted BPF realization payload checksum differs: {relative}"
            )
        try:
            data = json.loads(raw)
            value = _realization_from_data(
                _mapping(data, "persisted BPF realization"),
                previous=previous,
            )
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(
                f"persisted BPF realization is incompatible: {relative}"
            ) from error
        _validate_entry(value, entry, index=index)
        _validate_sequence_pair(previous, value)
        return value

    def snapshot(self) -> dict[str, object]:
        return {
            "manifest": str(self._manifest_path),
            "format_version": _FORMAT_VERSION,
            "encoding": _ENCODING,
            "sequence_sha256": self._sequence_sha256,
            "realization_count": len(self._entries),
            "validated_count": len(self._values),
            "validation_mode": "eager-before-runner",
            "consumed_count": self._index,
            "terminal_returned": self._terminal_returned,
        }

def _read_persisted_realization_manifest_snapshot(
    manifest_path: Path,
) -> dict[str, Any]:
    """Validate only bounded manifest metadata, without decoding payloads.

    Runtime assembly uses this view to publish execution metadata. Runner still
    constructs :class:`_PersistedRealizationSupplier` and eagerly validates
    every compressed payload before its lifecycle begins.
    """

    raw_path = Path(manifest_path)
    if raw_path.is_symlink():
        raise ValueError("realization handoff manifest must not be a symlink")
    resolved = raw_path.resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "persisted BPF realization manifest is invalid"
        ) from error
    try:
        manifest = _record(
            value,
            {
                "artifact_type",
                "format_version",
                "encoding",
                "data_only",
                "realization_count",
                "terminal",
                "sequence_sha256",
                "realizations",
            },
            "persisted BPF realization manifest",
        )
        if (
            manifest["artifact_type"] != _ARTIFACT_TYPE
            or manifest["format_version"] != _FORMAT_VERSION
            or manifest["encoding"] != _ENCODING
            or manifest["data_only"] is not True
            or manifest["terminal"] is not True
        ):
            raise ValueError(
                "persisted BPF realization manifest is incompatible"
            )
        raw_entries = manifest["realizations"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError(
                "persisted BPF realization manifest has no sequence"
            )
        entries = tuple(_entry_record(entry) for entry in raw_entries)
        if manifest["realization_count"] != len(entries):
            raise ValueError("persisted BPF realization count differs")
        if manifest["sequence_sha256"] != _sequence_sha256(entries):
            raise ValueError(
                "persisted BPF realization sequence checksum differs"
            )
        for index, entry in enumerate(entries):
            if entry["index"] != index:
                raise ValueError(
                    "persisted BPF realization indexes are not continuous"
                )
            relative = _safe_relative_path(entry["path"])
            candidate = resolved.parent / relative
            if candidate.is_symlink():
                raise ValueError(
                    "persisted BPF realization must not be a symlink: "
                    f"{relative}"
                )
    except (TypeError, KeyError) as error:
        raise ValueError(
            "persisted BPF realization manifest is incompatible"
        ) from error
    return {
        "manifest": str(resolved),
        "format_version": _FORMAT_VERSION,
        "encoding": _ENCODING,
        "sequence_sha256": manifest["sequence_sha256"],
        "realization_count": len(entries),
        "entries": entries,
        "validation_mode": "manifest-only-for-runtime-assembly",
    }


def _read_persisted_realization_sequence(
    manifest_path: Path,
) -> tuple[BpfCompiledRealization, ...]:
    """Materialize one bounded trusted data-only handoff."""

    supplier = _PersistedRealizationSupplier(manifest_path)
    values = []
    while (value := supplier()) is not None:
        values.append(value)
    return tuple(values)


def _realization_to_data(
    value: BpfCompiledRealization,
) -> dict[str, object]:
    if not isinstance(value, BpfCompiledRealization):
        raise TypeError("value must be a BpfCompiledRealization")
    return {
        "schema": _REALIZATION_SCHEMA,
        "source_plan_id": value.source_plan_id,
        "source_previous_plan_id": value.source_previous_plan_id,
        "physical_world_fingerprint": value.physical_world_fingerprint,
        "input_state_fingerprint": value.input_state_fingerprint,
        "output_state_fingerprint": value.output_state_fingerprint,
        "observation_interval": _timedelta_to_data(
            value.observation_interval
        ),
        "start_timestamp": value.start_timestamp.isoformat(),
        "end_timestamp": value.end_timestamp.isoformat(),
        "output_planned_state": planned_operational_state_to_data(
            value.output_planned_state
        ),
        "compiled_activation_boundaries": [
            {
                "timestamp": item.timestamp.isoformat(),
                "transition_fingerprint": item.transition_fingerprint,
            }
            for item in value.compiled_activation_boundaries
        ],
        "resource_realizations": [
            _resource_intent_to_data(item)
            for item in value.resource_realizations
        ],
        "parameter_realizations": [
            _qdisc_intent_to_data(item)
            for item in value.parameter_realizations
        ],
        "forwarding_realizations": [
            _forwarding_intent_to_data(item)
            for item in value.forwarding_realizations
        ],
        "zone_edge_realizations": [
            _zone_edge_intent_to_data(item)
            for item in value.zone_edge_realizations
        ],
        "constraints": [
            _constraint_to_data(item) for item in value.constraints
        ],
        "semantic_precedence_folding": [
            {"mode": mode, "count": count}
            for mode, count in value.semantic_precedence_folding
        ],
        "intended_forwarding": [
            _binding_to_data(item) for item in value.intended_forwarding
        ],
        "zone_prefixes": [
            {"zone_id": zone_id, "packet_prefix": packet_prefix}
            for zone_id, packet_prefix in value.zone_prefixes
        ],
        "physical_world_projection": {
            "node_ids": [
                _node_to_data(item)
                for item in value._physical_world_projection.node_ids
            ],
            "zone_ids": list(value._physical_world_projection.zone_ids),
        },
    }


def _realization_from_data(
    value: Mapping[str, object],
    *,
    previous: BpfCompiledRealization | None = None,
) -> BpfCompiledRealization:
    record = _record(
        value,
        {
            "schema",
            "source_plan_id",
            "source_previous_plan_id",
            "physical_world_fingerprint",
            "input_state_fingerprint",
            "output_state_fingerprint",
            "observation_interval",
            "start_timestamp",
            "end_timestamp",
            "output_planned_state",
            "compiled_activation_boundaries",
            "resource_realizations",
            "parameter_realizations",
            "forwarding_realizations",
            "zone_edge_realizations",
            "constraints",
            "semantic_precedence_folding",
            "intended_forwarding",
            "zone_prefixes",
            "physical_world_projection",
        },
        "BpfCompiledRealization",
    )
    if record["schema"] != _REALIZATION_SCHEMA:
        raise ValueError("BpfCompiledRealization schema is unsupported")
    boundaries = tuple(
        _activation_boundary_from_data(item)
        for item in _records(
            record["compiled_activation_boundaries"],
            "compiled_activation_boundaries",
        )
    )
    output_state = planned_operational_state_from_data(
        _mapping(record["output_planned_state"], "output_planned_state")
    )
    projection_record = _record(
        record["physical_world_projection"],
        {"node_ids", "zone_ids"},
        "physical_world_projection",
    )
    projection = _PhysicalWorldProjection(
        tuple(
            _node_from_data(item)
            for item in _records(projection_record["node_ids"], "node_ids")
        ),
        tuple(
            _string(item, "zone_id")
            for item in _list(projection_record["zone_ids"], "zone_ids")
        ),
    )
    zone_prefix_records = _records(record["zone_prefixes"], "zone_prefixes")
    zone_prefixes = tuple(
        (
            _string(item["zone_id"], "zone_id"),
            _string(item["packet_prefix"], "packet_prefix"),
        )
        for item in (
            _record(
                raw,
                {"zone_id", "packet_prefix"},
                "zone_prefix",
            )
            for raw in zone_prefix_records
        )
    )
    if tuple(zone_id for zone_id, _ in zone_prefixes) != projection.zone_ids:
        raise ValueError("Zone prefixes differ from PhysicalWorld projection")
    prefix_by_zone = dict(zone_prefixes)
    resources = tuple(
        _ResourceState(connection)
        for connection in output_state.operational_connections
    )
    qdiscs = tuple(
        _QdiscState(identifier, source, target, parameters)
        for connection in output_state.operational_connections
        for identifier in (connection.connection_id,)
        for source, target, parameters in (
            (
                identifier.endpoint_a,
                identifier.endpoint_b,
                connection.endpoint_a_to_b,
            ),
            (
                identifier.endpoint_b,
                identifier.endpoint_a,
                connection.endpoint_b_to_a,
            ),
        )
    )
    zone_edges = tuple(
        _ZoneEdgeState(
            assignment.zone_id,
            prefix_by_zone[assignment.zone_id],
            assignment.satellite_id,
        )
        for assignment in output_state.zone_service_assignments
        if assignment.satellite_id is not None
    )
    constraints = tuple(
        _constraint_from_data(item)
        for item in _records(record["constraints"], "constraints")
    )
    folding = tuple(
        (
            _string(item["mode"], "semantic precedence fold mode"),
            _integer(item["count"], "semantic precedence fold count"),
        )
        for item in (
            _record(
                raw,
                {"mode", "count"},
                "semantic precedence folding",
            )
            for raw in _records(
                record["semantic_precedence_folding"],
                "semantic_precedence_folding",
            )
        )
    )
    resource_intents = tuple(
        _resource_intent_from_data(item)
        for item in _records(
            record["resource_realizations"], "resource_realizations"
        )
    )
    qdisc_intents = tuple(
        _qdisc_intent_from_data(item)
        for item in _records(
            record["parameter_realizations"], "parameter_realizations"
        )
    )
    forwarding_intents = tuple(
        _forwarding_intent_from_data(item)
        for item in _records(
            record["forwarding_realizations"], "forwarding_realizations"
        )
    )
    zone_edge_intents = tuple(
        _zone_edge_intent_from_data(item)
        for item in _records(
            record["zone_edge_realizations"], "zone_edge_realizations"
        )
    )
    _validate_constraint_references(
        constraints,
        resource_count=len(resource_intents),
        parameter_count=len(qdisc_intents),
        forwarding_count=len(forwarding_intents),
        zone_edge_count=len(zone_edge_intents),
    )
    intended_forwarding = tuple(
        _binding_from_data(item)
        for item in _records(
            record["intended_forwarding"], "intended_forwarding"
        )
    )
    _validate_intended_forwarding(
        output_state,
        intended_forwarding,
        projection=projection,
        zone_prefixes=prefix_by_zone,
    )
    result = BpfCompiledRealization._create(
        source_plan_id=_string(record["source_plan_id"], "source_plan_id"),
        source_previous_plan_id=_optional_string(
            record["source_previous_plan_id"], "source_previous_plan_id"
        ),
        physical_world_fingerprint=_string(
            record["physical_world_fingerprint"],
            "physical_world_fingerprint",
        ),
        input_state_fingerprint=_optional_string(
            record["input_state_fingerprint"], "input_state_fingerprint"
        ),
        output_state_fingerprint=_string(
            record["output_state_fingerprint"], "output_state_fingerprint"
        ),
        observation_interval=_timedelta_from_data(
            _mapping(record["observation_interval"], "observation_interval")
        ),
        start_timestamp=_timestamp(record["start_timestamp"], "start_timestamp"),
        end_timestamp=_timestamp(record["end_timestamp"], "end_timestamp"),
        output_planned_state=output_state,
        compiled_activation_boundaries=boundaries,
        resource_realizations=resource_intents,
        parameter_realizations=qdisc_intents,
        forwarding_realizations=forwarding_intents,
        zone_edge_realizations=zone_edge_intents,
        constraints=constraints,
        semantic_precedence_folding=folding,
        intended_state=_IntendedState(
            resources,
            qdiscs,
            intended_forwarding,
            zone_edges,
            zone_prefixes,
        ),
        physical_world_projection=projection,
    )
    return result


def _activation_boundary_from_data(
    value: Mapping[str, object],
) -> BpfCompiledActivationBoundary:
    record = _record(
        value,
        {"timestamp", "transition_fingerprint"},
        "BpfCompiledActivationBoundary",
    )
    return BpfCompiledActivationBoundary(
        _timestamp(record["timestamp"], "timestamp"),
        _string(record["transition_fingerprint"], "transition_fingerprint"),
    )


def _resource_intent_to_data(value: _ResourceIntent) -> dict[str, object]:
    if value.attachments != _attachments(value.connection_id):
        raise ValueError("resource intent attachment projection differs")
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "connection_id": _connection_id_to_data(value.connection_id),
    }


def _resource_intent_from_data(
    value: Mapping[str, object],
) -> _ResourceIntent:
    record = _record(
        value,
        {"nominal_time", "operation", "connection_id"},
        "resource intent",
    )
    identifier = _connection_id_from_data(
        _mapping(record["connection_id"], "connection_id")
    )
    return _ResourceIntent(
        _timestamp(record["nominal_time"], "nominal_time"),
        _ResourceOperation(_string(record["operation"], "operation")),
        identifier,
        _attachments(identifier),
    )


def _qdisc_intent_to_data(value: _QdiscIntent) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "connection_id": _connection_id_to_data(value.connection_id),
        "source": _node_to_data(value.source),
        "target": _node_to_data(value.target),
        "bandwidth_bps": value.bandwidth_bps,
        "delay_us": value.delay_us,
        "packet_loss_ppm": value.packet_loss_ppm,
        "reason": value.reason,
    }


def _qdisc_intent_from_data(value: Mapping[str, object]) -> _QdiscIntent:
    record = _record(
        value,
        {
            "nominal_time",
            "connection_id",
            "source",
            "target",
            "bandwidth_bps",
            "delay_us",
            "packet_loss_ppm",
            "reason",
        },
        "qdisc intent",
    )
    return _QdiscIntent(
        _timestamp(record["nominal_time"], "nominal_time"),
        _connection_id_from_data(
            _mapping(record["connection_id"], "connection_id")
        ),
        _node_from_data(_mapping(record["source"], "source")),
        _node_from_data(_mapping(record["target"], "target")),
        _integer(record["bandwidth_bps"], "bandwidth_bps"),
        _integer(record["delay_us"], "delay_us"),
        _integer(record["packet_loss_ppm"], "packet_loss_ppm"),
        _string(record["reason"], "reason"),
    )


def _forwarding_intent_to_data(
    value: _ForwardingIntent,
) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "route_key": _forwarding_key_to_data(value.route_key),
        "previous": (
            None if value.previous is None else _binding_to_data(value.previous)
        ),
        "current": (
            None if value.current is None else _binding_to_data(value.current)
        ),
    }


def _forwarding_intent_from_data(
    value: Mapping[str, object],
) -> _ForwardingIntent:
    record = _record(
        value,
        {"nominal_time", "operation", "route_key", "previous", "current"},
        "forwarding intent",
    )
    route_key = _forwarding_key_from_data(
        _mapping(record["route_key"], "route_key")
    )
    previous = _optional_binding_from_data(record["previous"], "previous")
    current = _optional_binding_from_data(record["current"], "current")
    if previous is not None and previous.route_key != route_key:
        raise ValueError("previous forwarding binding uses another key")
    if current is not None and current.route_key != route_key:
        raise ValueError("current forwarding binding uses another key")
    return _ForwardingIntent(
        _timestamp(record["nominal_time"], "nominal_time"),
        _MutationOperation(_string(record["operation"], "operation")),
        route_key,
        previous,
        current,
    )


def _zone_edge_intent_to_data(value: _ZoneEdgeIntent) -> dict[str, object]:
    return {
        "nominal_time": value.nominal_time.isoformat(),
        "operation": value.operation.value,
        "phase": value.phase.value,
        "zone_id": value.zone_id,
        "packet_prefix": value.packet_prefix,
        "previous_service_satellite_id": (
            None
            if value.previous_service_satellite_id is None
            else _node_to_data(value.previous_service_satellite_id)
        ),
        "service_satellite_id": (
            None
            if value.service_satellite_id is None
            else _node_to_data(value.service_satellite_id)
        ),
    }


def _zone_edge_intent_from_data(
    value: Mapping[str, object],
) -> _ZoneEdgeIntent:
    record = _record(
        value,
        {
            "nominal_time",
            "operation",
            "phase",
            "zone_id",
            "packet_prefix",
            "previous_service_satellite_id",
            "service_satellite_id",
        },
        "Zone edge intent",
    )
    return _ZoneEdgeIntent(
        _timestamp(record["nominal_time"], "nominal_time"),
        _MutationOperation(_string(record["operation"], "operation")),
        _ZoneEdgePhase(_string(record["phase"], "phase")),
        _string(record["zone_id"], "zone_id"),
        _string(record["packet_prefix"], "packet_prefix"),
        _optional_node_from_data(
            record["previous_service_satellite_id"],
            "previous_service_satellite_id",
        ),
        _optional_node_from_data(
            record["service_satellite_id"], "service_satellite_id"
        ),
    )


def _constraint_to_data(value: _OrderingConstraint) -> dict[str, object]:
    return {
        "predecessor": _intent_reference_to_data(value.predecessor),
        "successor": _intent_reference_to_data(value.successor),
        "reason": value.reason,
    }


def _constraint_from_data(
    value: Mapping[str, object],
) -> _OrderingConstraint:
    record = _record(
        value,
        {"predecessor", "successor", "reason"},
        "ordering constraint",
    )
    predecessor = _intent_reference_from_data(
        _mapping(record["predecessor"], "predecessor")
    )
    successor = _intent_reference_from_data(
        _mapping(record["successor"], "successor")
    )
    if predecessor == successor:
        raise ValueError("ordering constraint cannot be a self-edge")
    return _OrderingConstraint(
        predecessor,
        successor,
        _string(record["reason"], "reason"),
    )


def _validate_intended_forwarding(
    output_state,
    values: tuple[_ForwardingBinding, ...],
    *,
    projection: _PhysicalWorldProjection,
    zone_prefixes: Mapping[str, str],
) -> None:
    actual: dict[_ForwardingKey, _ForwardingBinding] = {}
    for binding in values:
        prior = actual.setdefault(binding.route_key, binding)
        if prior != binding:
            raise ValueError("intended forwarding contains a conflicting key")
    if len(actual) != len(values):
        raise ValueError("intended forwarding contains a duplicate key")

    node_ids = frozenset(projection.node_ids)
    connection_ids = {
        connection.connection_id
        for connection in output_state.operational_connections
    }
    for binding in values:
        if (
            binding.route_key.source not in node_ids
            or binding.next_hop not in node_ids
            or binding.connection_id not in connection_ids
            or {
                binding.route_key.source,
                binding.next_hop,
            }
            != {
                binding.connection_id.endpoint_a,
                binding.connection_id.endpoint_b,
            }
        ):
            raise ValueError(
                "intended forwarding references an unknown runtime resource"
            )
        target = binding.route_key.target
        if isinstance(target, GroundStationRouteDemandTarget):
            expected_prefix = None
        elif isinstance(target, ZoneRouteDemandTarget):
            try:
                expected_prefix = zone_prefixes[target.zone_id]
            except KeyError as error:
                raise ValueError(
                    "intended forwarding references an unknown Zone"
                ) from error
        else:  # pragma: no cover - forwarding key validates the target union
            raise TypeError("intended forwarding target is unsupported")
        if binding.packet_prefix != expected_prefix:
            raise ValueError("intended forwarding packet prefix differs")


def _intent_reference_to_data(value: _IntentReference) -> dict[str, object]:
    return {"domain": value.domain, "index": value.index}


def _intent_reference_from_data(
    value: Mapping[str, object],
) -> _IntentReference:
    record = _record(
        value, {"domain", "index"}, "intent reference"
    )
    return _IntentReference(
        _string(record["domain"], "domain"),
        _integer(record["index"], "index"),
    )


def _binding_to_data(value: _ForwardingBinding) -> dict[str, object]:
    return {
        "route_key": _forwarding_key_to_data(value.route_key),
        "next_hop": _node_to_data(value.next_hop),
        "connection_id": _connection_id_to_data(value.connection_id),
        "packet_prefix": value.packet_prefix,
    }


def _binding_from_data(value: Mapping[str, object]) -> _ForwardingBinding:
    record = _record(
        value,
        {"route_key", "next_hop", "connection_id", "packet_prefix"},
        "forwarding binding",
    )
    binding = _ForwardingBinding(
        _forwarding_key_from_data(
            _mapping(record["route_key"], "route_key")
        ),
        _node_from_data(_mapping(record["next_hop"], "next_hop")),
        _connection_id_from_data(
            _mapping(record["connection_id"], "connection_id")
        ),
        _optional_string(record["packet_prefix"], "packet_prefix"),
    )
    if {
        binding.route_key.source,
        binding.next_hop,
    } != {
        binding.connection_id.endpoint_a,
        binding.connection_id.endpoint_b,
    }:
        raise ValueError("forwarding binding contradicts its Connection")
    return binding


def _optional_binding_from_data(
    value: object, label: str
) -> _ForwardingBinding | None:
    return None if value is None else _binding_from_data(_mapping(value, label))


def _forwarding_key_to_data(value: _ForwardingKey) -> dict[str, object]:
    target = value.target
    if isinstance(target, GroundStationRouteDemandTarget):
        target_data = {
            "kind": "ground-station",
            "ground_station_id": _node_to_data(target.ground_station_id),
        }
    elif isinstance(target, ZoneRouteDemandTarget):
        target_data = {"kind": "zone", "zone_id": target.zone_id}
    else:  # pragma: no cover - planning type owns this union
        raise TypeError("unsupported forwarding target")
    return {"source": _node_to_data(value.source), "target": target_data}


def _forwarding_key_from_data(
    value: Mapping[str, object],
) -> _ForwardingKey:
    record = _record(value, {"source", "target"}, "forwarding key")
    target = _mapping(record["target"], "target")
    kind = _string(target.get("kind"), "target.kind")
    if kind == "ground-station":
        target_record = _record(
            target,
            {"kind", "ground_station_id"},
            "GroundStation target",
        )
        route_target = GroundStationRouteDemandTarget(
            _node_from_data(
                _mapping(
                    target_record["ground_station_id"],
                    "ground_station_id",
                )
            )
        )
    elif kind == "zone":
        target_record = _record(
            target, {"kind", "zone_id"}, "Zone target"
        )
        route_target = ZoneRouteDemandTarget(
            _string(target_record["zone_id"], "zone_id")
        )
    else:
        raise ValueError("forwarding target kind is unsupported")
    return _ForwardingKey(
        _node_from_data(_mapping(record["source"], "source")),
        route_target,
    )


def _connection_id_to_data(
    value: OperationalConnectionId,
) -> dict[str, object]:
    return {
        "kind": value.kind.value,
        "endpoint_a": _node_to_data(value.endpoint_a),
        "endpoint_b": _node_to_data(value.endpoint_b),
    }


def _connection_id_from_data(
    value: Mapping[str, object],
) -> OperationalConnectionId:
    record = _record(
        value,
        {"kind", "endpoint_a", "endpoint_b"},
        "OperationalConnectionId",
    )
    return OperationalConnectionId(
        OperationalConnectionKind(_string(record["kind"], "kind")),
        _node_from_data(_mapping(record["endpoint_a"], "endpoint_a")),
        _node_from_data(_mapping(record["endpoint_b"], "endpoint_b")),
    )


def _attachments(
    identifier: OperationalConnectionId,
) -> tuple[_AttachmentDemand, _AttachmentDemand]:
    return (
        _AttachmentDemand(
            identifier, identifier.endpoint_a, identifier.endpoint_b
        ),
        _AttachmentDemand(
            identifier, identifier.endpoint_b, identifier.endpoint_a
        ),
    )


def _node_to_data(value: NodeId) -> dict[str, object]:
    return {
        "asn": value.asn,
        "entity_kind": value.entity_kind,
        "entity_id": value.entity_id,
    }


def _node_from_data(value: Mapping[str, object]) -> NodeId:
    record = _record(
        value, {"asn", "entity_kind", "entity_id"}, "NodeId"
    )
    return NodeId(
        _integer(record["asn"], "asn"),
        _string(record["entity_kind"], "entity_kind"),
        _string(record["entity_id"], "entity_id"),
    )


def _optional_node_from_data(value: object, label: str) -> NodeId | None:
    return None if value is None else _node_from_data(_mapping(value, label))


def _validate_constraint_references(
    constraints: tuple[_OrderingConstraint, ...],
    *,
    resource_count: int,
    parameter_count: int,
    forwarding_count: int,
    zone_edge_count: int,
) -> None:
    counts = {
        "resource": resource_count,
        "parameter": parameter_count,
        "forwarding": forwarding_count,
        "zone-edge": zone_edge_count,
    }
    for constraint in constraints:
        for reference in (constraint.predecessor, constraint.successor):
            if reference.domain not in counts or not (
                0 <= reference.index < counts[reference.domain]
            ):
                raise ValueError(
                    "ordering constraint references an unknown compiled intent"
                )


def _validate_entry(
    value: BpfCompiledRealization,
    entry: Mapping[str, Any],
    *,
    index: int,
) -> None:
    expected = {
        "index": index,
        "source_plan_id": value.source_plan_id,
        "source_previous_plan_id": value.source_previous_plan_id,
        "physical_world_fingerprint": value.physical_world_fingerprint,
        "input_state_fingerprint": value.input_state_fingerprint,
        "output_state_fingerprint": value.output_state_fingerprint,
        "start_timestamp": value.start_timestamp.isoformat(),
        "end_timestamp": value.end_timestamp.isoformat(),
        "activation_count": len(value.activation_timestamps),
    }
    if any(entry[key] != item for key, item in expected.items()):
        raise ValueError(
            "persisted BPF realization lineage/interval evidence differs"
        )


def _validate_sequence_pair(
    previous: BpfCompiledRealization | None,
    value: BpfCompiledRealization,
) -> None:
    if previous is None:
        if (
            value.source_previous_plan_id is not None
            or value.input_state_fingerprint is not None
        ):
            raise ValueError(
                "realization handoff must begin with a bootstrap Plan"
            )
    elif (
        value.source_previous_plan_id != previous.source_plan_id
        or value.input_state_fingerprint != previous.output_state_fingerprint
        or value.observation_interval != previous.observation_interval
        or value.start_timestamp
        != previous.end_timestamp + value.observation_interval
        or previous.physical_world_fingerprint
        != value.physical_world_fingerprint
        or previous._physical_world_projection
        != value._physical_world_projection
    ):
        raise ValueError(
            "realization handoff sequence breaks Plan/state/PhysicalWorld lineage"
        )


def _entry_record(value: object) -> dict[str, Any]:
    record = _record(
        value,
        {
            "index",
            "path",
            "source_plan_id",
            "source_previous_plan_id",
            "physical_world_fingerprint",
            "input_state_fingerprint",
            "output_state_fingerprint",
            "start_timestamp",
            "end_timestamp",
            "activation_count",
            "compressed_bytes",
            "uncompressed_bytes",
            "sha256",
            "uncompressed_sha256",
        },
        "persisted BPF realization entry",
    )
    for name in (
        "index",
        "activation_count",
        "compressed_bytes",
        "uncompressed_bytes",
    ):
        record[name] = _integer(record[name], name)
        if record[name] < 0:
            raise ValueError(f"{name} must be non-negative")
    for name in (
        "path",
        "source_plan_id",
        "physical_world_fingerprint",
        "output_state_fingerprint",
        "start_timestamp",
        "end_timestamp",
        "sha256",
        "uncompressed_sha256",
    ):
        record[name] = _string(record[name], name)
    for name in ("source_previous_plan_id", "input_state_fingerprint"):
        record[name] = _optional_string(record[name], name)
    return record


def _sequence_sha256(entries: Sequence[Mapping[str, Any]]) -> str:
    payload = "\n".join(
        "|".join(
            str(entry.get(name))
            for name in (
                "index",
                "source_plan_id",
                "source_previous_plan_id",
                "physical_world_fingerprint",
                "input_state_fingerprint",
                "output_state_fingerprint",
                "start_timestamp",
                "end_timestamp",
                "activation_count",
                "sha256",
                "uncompressed_sha256",
            )
        )
        for entry in entries
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _safe_relative_path(value: object) -> Path:
    text = _string(value, "persisted BPF realization path")
    path = Path(text)
    if path.is_absolute() or len(path.parts) != 1 or path.name != text:
        raise ValueError(
            "persisted BPF realization path leaves its handoff root"
        )
    return path


def _record(
    value: object,
    keys: set[str],
    label: str,
) -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) != keys:
        raise ValueError(f"{label} fields differ from the canonical schema")
    return result


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return dict(value)


def _records(value: object, label: str) -> tuple[dict[str, Any], ...]:
    return tuple(_mapping(item, label) for item in _list(value, label))


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _timestamp(value: object, label: str):
    from datetime import datetime

    text = _string(value, label)
    try:
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO timestamp") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return result


def _timedelta_to_data(value: timedelta) -> dict[str, int]:
    if not isinstance(value, timedelta):
        raise TypeError("duration must be a timedelta")
    return {
        "days": value.days,
        "seconds": value.seconds,
        "microseconds": value.microseconds,
    }


def _timedelta_from_data(value: Mapping[str, object]) -> timedelta:
    record = _record(
        value,
        {"days", "seconds", "microseconds"},
        "observation_interval",
    )
    result = timedelta(
        days=_integer(record["days"], "days"),
        seconds=_integer(record["seconds"], "seconds"),
        microseconds=_integer(record["microseconds"], "microseconds"),
    )
    if _timedelta_to_data(result) != record:
        raise ValueError("observation_interval is not canonical")
    return result


def _canonical_json_bytes(
    value: object,
    *,
    trailing_newline: bool = False,
) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + ("\n" if trailing_newline else "")).encode("utf-8")


def _deterministic_gzip(content: bytes) -> bytes:
    destination = BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=6,
        fileobj=destination,
        mtime=0,
    ) as stream:
        stream.write(content)
    return destination.getvalue()


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    destination = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__: list[str] = []
