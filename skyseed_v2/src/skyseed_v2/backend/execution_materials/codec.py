"""Read and decode sealed packed execution materials without Compiler or Runner."""

from __future__ import annotations

from copy import deepcopy
from skyseed_v2.backend.selection import _backend
from datetime import datetime
from hashlib import sha256
import json
import mmap
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import OperationalConnectionId, OperationalConnectionKind
from skyseed_v2.backend.execution_materials.actions import _PhysicalWorldProjection
from skyseed_v2.backend.execution_materials.mechanics import _CapacityAuditError
from skyseed_v2.backend.execution_materials.boundaries import (
    _ExecutionBoundary,
)


_ARTIFACT_TYPE = "skyseed-v2-runner-runtime-artifact"


_FORMAT_VERSION = 8


_ENCODING = "prepared-forwarding-v8"


_BINARY_NAME = "runner-runtime.bin"


_MANIFEST_NAME = "manifest.json"


_MAGIC = b"SKYRUN8\0"


_HEADER = struct.Struct("<8sHHIQQ32s")


_INDEX = struct.Struct("<QQII32s")


_MAX_BOUNDARIES = 0xFFFFFFFF  # count field in the binary header


_MAX_RECORD_BYTES = 0xFFFFFFFFFFFFFFFF  # length field in the binary index


_MAX_CONTAINER_ITEMS = 0xFFFFFFFF  # encoded sequence length


_MAX_DEPTH = 64


_DECODE_REUSE_LIMIT = 16_384


class _DecodedIdentities:
    """Immutable value reuse owned by one open finite artifact."""

    def __init__(self, nodes=()):
        self.nodes = {(node.asn, node.entity_kind, node.entity_id): node for node in nodes}
        self.connections = {}
        self.timestamps = {}
        self.texts = {}


class _PackedBoundaryReader:
    """Decode the packed execution schema with shared immutable identities."""

    def __init__(self, data: bytes, identities: _DecodedIdentities):
        self.data = data
        self.cursor = 0
        self.identities = identities

    def header(self, tag: int, size: int) -> int:
        start = self.cursor
        if start + size > len(self.data):
            raise ValueError("Runner artifact packed value is truncated")
        if self.data[start] != tag:
            raise ValueError("Runner artifact packed field type differs")
        self.cursor = start + size
        return start

    def integer(self) -> int:
        return struct.unpack_from('<q', self.data, self.header(105, 9) + 1)[0]

    def text(self) -> str:
        start = self.header(115, 5)
        length = struct.unpack_from('<I', self.data, start + 1)[0]
        end = self.cursor + length
        if end > len(self.data):
            raise ValueError("Runner artifact packed value is truncated")
        raw = self.data[self.cursor:end]
        self.cursor = end
        texts = self.identities.texts
        value = texts.get(raw)
        if value is None:
            try:
                value = raw.decode('utf-8')
            except UnicodeDecodeError as error:
                raise ValueError("Runner artifact string is not UTF-8") from error
            if len(raw) <= 256 and len(texts) < _DECODE_REUSE_LIMIT:
                texts[raw] = value
        return value

    def count(self, tag=108, expected=None) -> int:
        start = self.header(tag, 5)
        count = struct.unpack_from('<I', self.data, start + 1)[0]
        if count > _MAX_CONTAINER_ITEMS:
            raise ValueError("Runner artifact packed container is too large")
        if expected is not None and count != expected:
            raise ValueError("Runner artifact packed sequence schema differs")
        return count

    def absent(self) -> bool:
        if self.cursor < len(self.data) and self.data[self.cursor] == 110:
            self.cursor += 1
            return True
        return False

    def value(self, depth=0):
        if depth > _MAX_DEPTH:
            raise ValueError("Runner artifact packed value is too deeply nested")
        if self.cursor == len(self.data):
            raise ValueError("Runner artifact packed value is truncated")
        tag = self.data[self.cursor]
        if tag in (110, 102, 116):
            self.cursor += 1
            return None if tag == 110 else tag == 116
        if tag == 105:
            return self.integer()
        if tag == 115:
            return self.text()
        if tag == 98:
            start = self.header(98, 5)
            length = struct.unpack_from('<I', self.data, start + 1)[0]
            end = self.cursor + length
            if end > len(self.data):
                raise ValueError("Runner artifact byte data is truncated")
            value = self.data[self.cursor:end]
            self.cursor = end
            return value
        if tag == 108:
            return [self.value(depth + 1) for _ in range(self.count())]
        if tag == 100:
            result = {}; prior = None
            for _ in range(self.count(100)):
                key = self.text()
                if prior is not None and key <= prior:
                    raise ValueError("Runner artifact dictionary keys are not canonical")
                result[key] = self.value(depth + 1); prior = key
            return result
        raise ValueError("Runner artifact packed value has an unknown type tag")

    def boundary(self, projection):
        record = self.value()
        if self.cursor != len(self.data):
            raise ValueError("Runner artifact packed value has trailing bytes")
        try:
            return _boundary_from_fields(record, projection, self.identities)
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError("Runner artifact executable boundary schema differs") from error


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
                self._native_boundaries = None
                self._native_link_networks = {}
                if self.forwarding_backend == "linux":
                    from skyseed_v2.backend.linux.materials import _validate_request
                    node_keys = frozenset(n.value for n in self.physical_world_projection.node_ids)
                    node_addresses = {n["key"]: n["node_address"] for n in self._snapshot["dataplane"]["nodes"]}
                    destinations = frozenset(node_addresses.values()) | frozenset(self._snapshot["dataplane"]["zone_addresses"])
                    boundaries = tuple(self.boundary(i) for i in range(len(self._indexes)))
                    for boundary in boundaries:
                        if set(boundary.executable) != {"request", "created", "removed"}:
                            raise ValueError("Linux material contains incompatible execution fields")
                        request = boundary.executable["request"]
                        self._native_link_networks.update(_validate_request(request, node_keys, node_addresses, destinations))
                        if request["boundary_timestamp"] != boundary.nominal_time.isoformat():
                            raise ValueError("Linux request timestamp differs from its sealed boundary")
                    self._native_boundaries = boundaries
            except BaseException:
                self._mmap.close()
                raise
        except BaseException:
            self._stream.close()
            raise

    @property
    def forwarding_backend(self):
        return self._snapshot["backend"]

    @property
    def retirements(self):
        return deepcopy(self._snapshot["retirements"])

    @property
    def dataplane(self):
        return deepcopy(self._snapshot["dataplane"])

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
    def manifest_sha256(self) -> str:
        return self._snapshot["manifest_sha256"]

    def delivery_contract(self) -> dict[str, Any] | None:
        """Return detached admission facts from the already loaded manifest."""
        return deepcopy(self._snapshot["delivery_contract"])

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

        return deepcopy(self._snapshot["runtime_capacity_demand"])

    def boundary(self, index: int):
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("Runner artifact boundary index must be an int")
        if self._native_boundaries is not None:
            return self._native_boundaries[index]
        try:
            offset, length, expected_operations, digest = self._indexes[index]
        except IndexError as error:
            raise IndexError("Runner artifact boundary index is out of range") from error
        packed = self._mmap[offset:offset + length]
        if sha256(packed).digest() != digest:
            raise ValueError("Runner artifact binary record hash differs")
        value = _PackedBoundaryReader(packed, self._identities).boundary(self.physical_world_projection)
        if value.executable is None:
            raise ValueError("Runner artifact omitted prepared DataPlane content")
        if value.operation_count != expected_operations:
            raise ValueError("Runner artifact operation count differs")
        return value

    def boundaries(self) -> Sequence[object]:
        """Expose an indexed view that decodes only the requested boundary."""

        return _RunnerBoundarySequence(self)

    def snapshot(self) -> dict[str, object]:
        return {
            "backend": self.forwarding_backend, "artifact_type": _ARTIFACT_TYPE, "format_version": _FORMAT_VERSION,
            "encoding": _ENCODING, "realization_count": self.realization_count,
            "boundary_count": self.boundary_count,
            "sequence_sha256": self.sequence_sha256, "mmap": True,
            "boundary_decode": "indexed-on-demand",
            "rich_compiled_realizations_reconstructed": False,
            "runtime_capacity_demand": self.runtime_capacity_demand,
            "delivery": self._snapshot["delivery"],
            "manifest_sha256": self._snapshot["manifest_sha256"],
        }

    def close(self) -> None:
        self._native_boundaries = None
        self._native_link_networks.clear()
        self._mmap.close()
        self._stream.close()
        self._identities.nodes.clear()
        self._identities.connections.clear()
        self._identities.timestamps.clear()
        self._identities.texts.clear()

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

    @property
    def native_link_networks(self):
        """Parsed subnets from this artifact's completed native schema validation."""
        return self._artifact._native_link_networks

    def __getitem__(self, index: int | slice) -> object:
        if isinstance(index, slice):
            return tuple(
                self._artifact.boundary(value)
                for value in range(*index.indices(len(self)))
            )
        return self._artifact.boundary(index)


def _open_runner_artifact(manifest_path: Path) -> _RunnerArtifact:
    return _RunnerArtifact(manifest_path)


def _delivery_contract_from_manifest(path, manifest, manifest_sha256):
    delivery = manifest.get("delivery")
    if not isinstance(delivery, dict):
        raise ValueError("batch append requires explicit sealed delivery metadata")
    return {
        "path": path.resolve(), "manifest_sha256": manifest_sha256, "backend": manifest["backend"],
        "delivery": delivery, "timeline": manifest["timeline"],
        "world": sha256(json.dumps(
            [manifest["backend"], manifest["physical_world"], manifest["zone_prefixes"], manifest["dataplane"]],
            sort_keys=True,
        ).encode()).hexdigest(),
        "last_plan": manifest["lineage"][-1]["source_plan_id"],
        "last_state": manifest["lineage"][-1]["output_state_fingerprint"],
        "generation_count": manifest["runtime_capacity_demand"]["generation_count"],
    }


def _read_delivery_contract(path: Path) -> dict[str, Any]:
    """Read compact continuity facts without loading or scanning the binary."""
    if path.is_symlink():
        raise ValueError("batch manifest must not be a symlink")
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if (manifest.get("format_version") != _FORMAT_VERSION or manifest.get("encoding") != _ENCODING):
        raise ValueError("Runner artifact manifest/version is unsupported; regenerate materials")
    _backend(manifest.get("backend"))
    return _delivery_contract_from_manifest(path, manifest, sha256(raw).hexdigest())


def _read_runner_artifact_manifest_snapshot(manifest_path: Path) -> dict[str, Any]:
    raw = Path(manifest_path)
    if raw.is_symlink():
        raise ValueError("Runner artifact manifest must not be a symlink")
    path = raw.resolve()
    try:
        manifest_bytes = path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Runner artifact manifest is unavailable or malformed") from error
    expected = {
        "artifact_type", "format_version", "encoding", "terminal",
        "semantic_validation", "binary", "realization_count", "sequence_sha256",
        "physical_world", "zone_prefixes", "runtime_capacity_demand", "logical_ports",
        "timeline", "lineage", "dataplane", "retirements", "backend",
    }
    if not isinstance(manifest, dict) or set(manifest) not in (expected, expected | {"delivery"}):
        raise ValueError("Runner artifact manifest schema differs")
    if manifest["artifact_type"] != _ARTIFACT_TYPE or manifest["format_version"] != _FORMAT_VERSION or manifest["encoding"] != _ENCODING or manifest["terminal"] is not True or manifest["semantic_validation"] != "complete-before-seal":
        raise ValueError("Runner artifact manifest/version/seal is unsupported")
    backend = _backend(manifest["backend"])
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
    if not isinstance(manifest["timeline"], dict) or manifest["timeline"].get("boundary_count") != boundary_count:
        raise ValueError("Runner artifact Timeline boundary count differs")
    lineage = manifest["lineage"]
    if not isinstance(lineage, list) or len(lineage) != manifest["realization_count"]:
        raise ValueError("Runner artifact lineage count differs")
    if _sequence_sha256(lineage) != manifest["sequence_sha256"]:
        raise ValueError("Runner artifact lineage hash differs")
    delivery = manifest.get("delivery")
    if delivery is not None:
        fields = {"index", "previous_manifest_sha256", "final", "prior_generation",
                  "prior_end", "prior_plan_id", "prior_state_fingerprint", "observation_interval_seconds"}
        if (not isinstance(delivery, dict) or set(delivery) != fields
            or type(delivery["index"]) is not int or delivery["index"] < 0
            or type(delivery["final"]) is not bool
            or type(delivery["observation_interval_seconds"]) not in (float, int)
            or not 0 < delivery["observation_interval_seconds"] < float("inf")):
            raise ValueError("Runner artifact delivery metadata differs")
        prior_fields = ("previous_manifest_sha256", "prior_generation", "prior_end",
                        "prior_plan_id", "prior_state_fingerprint")
        if delivery["index"] == 0:
            if any(delivery[key] is not None for key in prior_fields):
                raise ValueError("initial batch must not have a predecessor")
        elif (any(delivery[key] is None for key in prior_fields)
              or type(delivery["prior_generation"]) is not int
              or delivery["prior_generation"] < 0
              or (backend == "bpf" and delivery["prior_generation"] >= 0xFFFFFFFF)
              or lineage[0]["source_previous_plan_id"] != delivery["prior_plan_id"]
              or lineage[0]["input_state_fingerprint"] != delivery["prior_state_fingerprint"]):
            raise ValueError("continued artifact predecessor metadata differs")
    physical = manifest["physical_world"]
    if not isinstance(physical, dict) or set(physical) != {"nodes", "zones", "fingerprint", "network_scopes"}:
        raise ValueError("Runner artifact PhysicalWorld metadata differs")
    projection = _PhysicalWorldProjection(
        tuple(_node_from_data(value) for value in physical["nodes"]),
        tuple(str(value) for value in physical["zones"]),
        tuple(physical["network_scopes"]),
    )
    if backend == "bpf":
        from skyseed_v2.backend.bpf.dataplane.materials import _decode_foundation, _validate_retirements
        dataplane = _decode_foundation(manifest["dataplane"], tuple(node.value for node in projection.node_ids))
        retirements = _validate_retirements(manifest["retirements"], frozenset(node.value for node in projection.node_ids))
    else:
        from skyseed_v2.backend.linux.materials import _validate_foundation
        dataplane = _validate_foundation(manifest["dataplane"], tuple(node.value for node in projection.node_ids))
        retirements = manifest["retirements"]
        if retirements != {} or manifest["logical_ports"] != []:
            raise ValueError("Linux materials cannot contain BPF retirement or logical-port bindings")
    logical_ports = {}
    node_ids = frozenset(projection.node_ids)
    allocated = set()
    for record in _list(manifest["logical_ports"], "logical_ports"):
        if not isinstance(record, list) or len(record) != 3:
            raise ValueError("Runner artifact logical port schema differs")
        connection = _connection_from_data(record[0])
        ports = tuple(record[1:])
        if (connection in logical_ports
            or connection.endpoint_a not in node_ids
            or connection.endpoint_b not in node_ids
            or any(type(port) is not int or not 0 < port <= 0xFFFFFFFC
                   or port in allocated for port in ports)
            or ports[0] == ports[1]):
            raise ValueError("Runner artifact logical port identity is invalid")
        logical_ports[connection] = ports
        allocated.update(ports)
    zone_prefixes = tuple((tuple(value[0]), str(value[1])) for value in manifest["zone_prefixes"] if isinstance(value, list) and len(value) == 2 and isinstance(value[0], list) and len(value[0]) == 2)
    if len(zone_prefixes) != len(manifest["zone_prefixes"]):
        raise ValueError("Runner artifact Zone prefix metadata differs")
    from .mechanics import _allocate_zone_prefixes
    if zone_prefixes != tuple(sorted(_allocate_zone_prefixes(projection.service_keys).items())):
        raise ValueError("Runner artifact scoped Zone destinations differ")
    if backend == "bpf":
        from skyseed_v2.backend.bpf.dataplane.materials import _validate_runtime_capacity_demand
        try:
            capacity_demand = _validate_runtime_capacity_demand(
                manifest["runtime_capacity_demand"],
                expected_node_keys=tuple(node.value for node in projection.node_ids),
                expected_boundary_count=boundary_count)
        except _CapacityAuditError as error:
            raise ValueError("Runner artifact runtime capacity demand differs") from error
    else:
        from skyseed_v2.backend.linux.materials import _validate_demand
        capacity_demand = _validate_demand(manifest["runtime_capacity_demand"], boundary_count)
    manifest_sha256 = sha256(manifest_bytes).hexdigest()
    return {
        "backend": backend, "format_version": _FORMAT_VERSION, "encoding": _ENCODING,
        "realization_count": manifest["realization_count"],
        "boundary_count": boundary_count, "operation_count": binary["operation_count"],
        "sequence_sha256": manifest["sequence_sha256"],
        "binary_path": _BINARY_NAME, "binary_sha256": binary["sha256"],
        "binary_body_sha256": binary_body_sha256,
        "physical_world_fingerprint": physical["fingerprint"],
        "physical_world_projection": projection, "zone_prefixes": zone_prefixes, "dataplane": dataplane, "retirements": retirements,
        "runtime_capacity_demand": capacity_demand, "logical_ports": logical_ports,
        "timeline": manifest["timeline"], "lineage": tuple(dict(value) for value in lineage),
        "delivery": delivery,
        "manifest_sha256": manifest_sha256,
        "delivery_contract": None if delivery is None else _delivery_contract_from_manifest(
            path, manifest, manifest_sha256,
        ),
    }


def _boundary_to_data(boundary: object) -> dict[str, object]:
    return {
        "executable": None if boundary.executable is None else {
            **boundary.executable,
            "created": [_connection_to_data(v) for v in boundary.executable["created"]],
            "removed": [_connection_to_data(v) for v in boundary.executable["removed"]],
        },
        "time": boundary.nominal_time.isoformat(),
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


def _boundary_from_fields(record, physical_world_projection, identities):
    if not isinstance(record.get("executable"), dict):
        raise ValueError("Runner artifact omitted prepared DataPlane content")
    return _ExecutionBoundary(
        nominal_time=_timestamp(record["time"], identities),
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
        executable=None if record.get("executable") is None else {
            **record["executable"],
            "created": tuple(_connection_from_data(v, identities) for v in record["executable"]["created"]),
            "removed": tuple(_connection_from_data(v, identities) for v in record["executable"]["removed"]),
        },
    )


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
        elif isinstance(item, bytes):
            output.extend(b"b" + struct.pack("<I", len(item)) + item)
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
