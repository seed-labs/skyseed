"""Capture and verify exact Tool-owned physical-link inspection evidence."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
from hashlib import sha256
from io import TextIOWrapper
import json
import os
from pathlib import Path
from typing import Any, Iterator

from skyseed_v2.simulator import PhysicalLinkObservation, PhysicalWorld
from skyseed_v2.shared._durable_evidence import _OrderedEvidenceWriter


_FORMAT_VERSION = 1
_ARTIFACT_TYPE = "physical-link-inspection-evidence"
_STREAM_NAME = "physical-link-observations.jsonl.gz"
_MANIFEST_NAME = "physical-link-observations-manifest.json"
_WORK_FIELDS = (
    "inter_satellite_pairs_requested",
    "inter_satellite_pairs_evaluated",
    "inter_satellite_pairs_feasible",
    "ground_station_pairs_requested",
    "ground_station_pairs_evaluated",
    "ground_station_pairs_feasible",
    "zone_service_pairs_requested",
    "zone_service_pairs_evaluated",
    "zone_service_pairs_feasible",
    "ground_station_pairs_not_returned",
    "inter_satellite_pairs_not_returned",
)


@dataclass(frozen=True, slots=True)
class PhysicalLinkInspectionIndex:
    """Describe one checksum-verified compact formal-observation stream."""

    path: Path
    nodes: tuple[dict[str, object], ...]
    zones: tuple[str, ...]
    zone_records: tuple[dict[str, object], ...]
    timestamps: tuple[str, ...]
    manifest: dict[str, object]


class _PhysicalLinkInspectionWriter:
    """Write exact already-produced candidates without owning their meaning."""

    def __init__(self, directory: Path, physical_world: PhysicalWorld) -> None:
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        root = Path(directory)
        if root.is_symlink():
            raise ValueError("inspection-evidence directory must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise FileExistsError(
                f"inspection-evidence directory is not empty: {root}"
            )
        self._root = root.resolve()
        self._path = self._root / _STREAM_NAME
        self._manifest_path = self._root / _MANIFEST_NAME
        self._nodes = _physical_nodes(physical_world)
        self._node_index = {
            str(value["node_id"]): index
            for index, value in enumerate(self._nodes)
        }
        try:
            zone_values = physical_world.get_zone_layout().zones
            self._zones = tuple(value.zone_id for value in zone_values)
            self._zone_records = tuple(
                {
                    "zone_id": value.zone_id,
                    "priority": value.priority,
                    "geometry": {
                        "south_latitude_deg": value.geometry.south_latitude_deg,
                        "north_latitude_deg": value.geometry.north_latitude_deg,
                        "west_longitude_deg": value.geometry.west_longitude_deg,
                        "east_longitude_deg": value.geometry.east_longitude_deg,
                        "representative_center": tuple(
                            value.geometry.representative_center
                        ),
                    },
                }
                for value in zone_values
            )
        except RuntimeError:
            self._zones = ()
            self._zone_records = ()
        self._zone_index = {
            value: index for index, value in enumerate(self._zones)
        }
        self._raw = self._path.open("wb")
        self._compressed = gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=self._raw,
            compresslevel=1,
            mtime=0,
        )
        self._stream = TextIOWrapper(self._compressed, encoding="utf-8")
        self._timestamps: list[str] = []
        self._candidate_totals = {
            "inter_satellite": 0,
            "ground_station": 0,
            "zone_service": 0,
        }
        self._finalized = False
        self._jobs = _OrderedEvidenceWriter(maximum_pending=2)
        self._write(
            {
                "record_type": "physical_link_inspection_header",
                "format_version": _FORMAT_VERSION,
                "artifact_type": _ARTIFACT_TYPE,
                "formal_observations_only": True,
                "nodes": self._nodes,
                "zones": self._zones,
                "zone_records": self._zone_records,
                "layouts": {
                    "inter_satellite": (
                        "endpoint_a_index",
                        "endpoint_b_index",
                        "distance_m",
                        "propagation_delay_us",
                        "same_plane_0_or_1",
                        "cyclic_plane_separation",
                    ),
                    "ground_station": (
                        "ground_station_index",
                        "satellite_index",
                        "distance_m",
                        "elevation_deg",
                        "propagation_delay_us",
                    ),
                    "zone_service": (
                        "zone_index",
                        "satellite_index",
                        "distance_m",
                        "elevation_deg",
                        "propagation_delay_us",
                    ),
                },
            }
        )

    @property
    def path(self) -> Path:
        return self._path

    def record(self, observation: PhysicalLinkObservation) -> None:
        if self._finalized:
            raise RuntimeError('inspection-evidence writer is finalized')
        self._jobs.submit(self._record, observation)

    def _record(self, observation: PhysicalLinkObservation) -> None:
        if self._finalized:
            raise RuntimeError("inspection-evidence writer is finalized")
        if not isinstance(observation, PhysicalLinkObservation):
            raise TypeError("observation must be a PhysicalLinkObservation")
        timestamp = observation.timestamp.isoformat()
        if self._timestamps and timestamp <= self._timestamps[-1]:
            raise ValueError("formal observation timestamps must be increasing")
        inter_satellite = [
            [
                self._node(value.endpoint_a),
                self._node(value.endpoint_b),
                value.distance_m,
                value.propagation_delay_us,
                int(value.same_plane),
                value.cyclic_plane_separation,
            ]
            for value in observation.inter_satellite_candidates
        ]
        ground_station = [
            [
                self._node(value.ground_station_id),
                self._node(value.satellite_id),
                value.distance_m,
                value.elevation_deg,
                value.propagation_delay_us,
            ]
            for value in observation.ground_station_access_candidates
        ]
        zone_service = [
            [
                self._zone(value.zone_id),
                self._node(value.satellite_id),
                value.distance_m,
                value.elevation_deg,
                value.propagation_delay_us,
            ]
            for value in observation.zone_service_candidates
        ]
        self._write(
            {
                "record_type": "physical_link_observation",
                "timestamp": timestamp,
                "inter_satellite": inter_satellite,
                "ground_station": ground_station,
                "zone_service": zone_service,
                "work_counts": {
                    name: getattr(observation.work_counts, name)
                    for name in _WORK_FIELDS
                },
            }
        )
        self._timestamps.append(timestamp)
        self._candidate_totals["inter_satellite"] += len(inter_satellite)
        self._candidate_totals["ground_station"] += len(ground_station)
        self._candidate_totals["zone_service"] += len(zone_service)

    def finalize(self) -> Path:
        self._jobs.finish()
        if self._finalized:
            return self._manifest_path
        self._stream.flush()
        self._stream.close()
        self._raw.flush()
        self._raw.close()
        self._finalized = True
        manifest = {
            "format_version": _FORMAT_VERSION,
            "artifact_type": _ARTIFACT_TYPE,
            "stream": _STREAM_NAME,
            "stream_size_bytes": self._path.stat().st_size,
            "stream_sha256": _sha256_file(self._path),
            "formal_observation_count": len(self._timestamps),
            "first_timestamp": (
                None if not self._timestamps else self._timestamps[0]
            ),
            "last_timestamp": (
                None if not self._timestamps else self._timestamps[-1]
            ),
            "timestamps": self._timestamps,
            "node_count": len(self._nodes),
            "zone_count": len(self._zones),
            "candidate_totals": self._candidate_totals,
        }
        temporary = self._manifest_path.with_name(
            f".{self._manifest_path.name}.tmp-{os.getpid()}"
        )
        try:
            temporary.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
        return self._manifest_path

    def close(self) -> None:
        try:
            self._jobs.finish()
        except Exception:
            pass  # record/finalize reports the optional Tool failure; close only releases handles.
        finally:
            self._close_stream()

    def _close_stream(self) -> None:
        if not self._finalized:
            self._stream.close()
            if not self._raw.closed:
                self._raw.close()
            self._finalized = True

    def _node(self, value) -> int:
        try:
            return self._node_index[value.value]
        except KeyError as error:
            raise ValueError("candidate node is foreign to PhysicalWorld") from error

    def _zone(self, value: str) -> int:
        try:
            return self._zone_index[value]
        except KeyError as error:
            raise ValueError("candidate Zone is foreign to PhysicalWorld") from error

    def _write(self, value: dict[str, object]) -> None:
        self._stream.write(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        )


def read_physical_link_inspection_index(
    path: Path,
) -> PhysicalLinkInspectionIndex:
    """Verify one sealed Tool stream and return its compact static index."""

    source = _verified_source(path)
    manifest = _read_manifest(source)
    records = iter_physical_link_inspection(source)
    try:
        header = next(records)
    except StopIteration as error:
        raise ValueError("physical-link inspection stream is empty") from error
    nodes = tuple(header["nodes"])
    zones = tuple(header["zones"])
    zone_records = tuple(header.get("zone_records", ()))
    if len(zone_records) != len(zones) or any(
        not isinstance(value, dict) or value.get("zone_id") != zone_id
        for value, zone_id in zip(zone_records, zones, strict=True)
    ):
        raise ValueError("physical-link inspection Zone records differ")
    count = sum(1 for _ in records)
    if count != manifest["formal_observation_count"]:
        raise ValueError("physical-link inspection record count differs")
    return PhysicalLinkInspectionIndex(
        path=source,
        nodes=nodes,
        zones=zones,
        zone_records=zone_records,
        timestamps=tuple(manifest["timestamps"]),
        manifest=manifest,
    )


def iter_physical_link_inspection(path: Path) -> Iterator[dict[str, Any]]:
    """Yield a verified header and exact compact formal observations."""

    source = _verified_source(path)
    manifest = _read_manifest(source)
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        header = _json_line(next(stream, ""), 1)
        if (
            header.get("record_type") != "physical_link_inspection_header"
            or header.get("format_version") != _FORMAT_VERSION
            or header.get("artifact_type") != _ARTIFACT_TYPE
            or header.get("formal_observations_only") is not True
        ):
            raise ValueError("physical-link inspection header is invalid")
        nodes = header.get("nodes")
        zones = header.get("zones")
        if not isinstance(nodes, list) or not isinstance(zones, list):
            raise ValueError("physical-link inspection index is malformed")
        yield header
        timestamps = tuple(manifest["timestamps"])
        count = 0
        for line_number, line in enumerate(stream, start=2):
            record = _json_line(line, line_number)
            _validate_record(record, len(nodes), len(zones))
            if count >= len(timestamps) or record["timestamp"] != timestamps[count]:
                raise ValueError("physical-link inspection timestamps differ")
            count += 1
            yield record
        if count != len(timestamps):
            raise ValueError("physical-link inspection timestamp count differs")


def _physical_nodes(physical_world: PhysicalWorld) -> tuple[dict[str, object], ...]:
    nodes = []
    for autonomous_system in physical_world.autonomous_systems:
        for shell in autonomous_system.shells:
            for orbit in shell.orbits:
                for satellite in orbit.satellites:
                    capability = satellite.inter_satellite_link_capability
                    nodes.append(
                        {
                            "node_id": satellite.node_id.value,
                            "kind": "satellite",
                            "asn": autonomous_system.asn,
                            "shell_id": shell.shell_id,
                            "orbit_id": orbit.orbit_id,
                            "orbit_index": satellite.orbit_index,
                            "ground_station_capacity": (
                                satellite.max_simultaneous_ground_station_accesses
                            ),
                            "inter_satellite_capacity": (
                                None
                                if capability is None
                                else capability.maximum_simultaneous_links
                            ),
                        }
                    )
        for station in autonomous_system.ground_stations:
            nodes.append(
                {
                    "node_id": station.node_id.value,
                    "kind": "ground-station",
                    "asn": autonomous_system.asn,
                    "station_type": station.station_type,
                    "operator": station.operator,
                    "ground_station_capacity": (
                        station.max_simultaneous_satellite_accesses
                    ),
                }
            )
    return tuple(sorted(nodes, key=lambda value: str(value["node_id"])))


def _verified_source(path: Path) -> Path:
    source = Path(path)
    if source.is_symlink():
        raise ValueError("physical-link inspection stream must not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as error:
        raise ValueError("physical-link inspection stream is unavailable") from error
    manifest = _read_manifest(resolved)
    if resolved.name != manifest.get("stream"):
        raise ValueError("physical-link inspection manifest names another stream")
    if resolved.stat().st_size != manifest.get("stream_size_bytes"):
        raise ValueError("physical-link inspection size differs")
    if _sha256_file(resolved) != manifest.get("stream_sha256"):
        raise ValueError("physical-link inspection checksum differs")
    return resolved


def _read_manifest(source: Path) -> dict[str, Any]:
    path = source.with_name(_MANIFEST_NAME)
    if path.is_symlink():
        raise ValueError("physical-link inspection manifest must not be a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("physical-link inspection manifest is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("format_version") != _FORMAT_VERSION
        or value.get("artifact_type") != _ARTIFACT_TYPE
        or not isinstance(value.get("timestamps"), list)
    ):
        raise ValueError("physical-link inspection manifest is malformed")
    return value


def _validate_record(
    value: dict[str, Any], node_count: int, zone_count: int
) -> None:
    if set(value) != {
        "record_type",
        "timestamp",
        "inter_satellite",
        "ground_station",
        "zone_service",
        "work_counts",
    } or value["record_type"] != "physical_link_observation":
        raise ValueError("physical-link inspection record is malformed")
    if not isinstance(value["timestamp"], str) or not value["timestamp"]:
        raise ValueError("physical-link inspection timestamp is invalid")
    for name, width, index_fields in (
        ("inter_satellite", 6, (0, 1)),
        ("ground_station", 5, (0, 1)),
        ("zone_service", 5, (1,)),
    ):
        rows = value[name]
        if not isinstance(rows, list) or any(
            not isinstance(row, list) or len(row) != width for row in rows
        ):
            raise ValueError(f"physical-link {name} rows are malformed")
        for row in rows:
            for field in index_fields:
                if (
                    isinstance(row[field], bool)
                    or not isinstance(row[field], int)
                    or not 0 <= row[field] < node_count
                ):
                    raise ValueError(f"physical-link {name} node index is invalid")
            if name == "zone_service" and (
                isinstance(row[0], bool)
                or not isinstance(row[0], int)
                or not 0 <= row[0] < zone_count
            ):
                raise ValueError("physical-link Zone index is invalid")
    work = value["work_counts"]
    if isinstance(work, dict):
        work = {"ground_station_pairs_not_returned": 0, "inter_satellite_pairs_not_returned": 0, **work}
    if not isinstance(work, dict) or set(work) != set(_WORK_FIELDS):
        raise ValueError("physical-link inspection work counts are malformed")
    if len(value["inter_satellite"]) != work["inter_satellite_pairs_feasible"]:
        raise ValueError("physical-link ISL candidate count differs")
    if len(value["ground_station"]) != work["ground_station_pairs_feasible"]:
        raise ValueError("physical-link GroundStation candidate count differs")
    if len(value["zone_service"]) != work["zone_service_pairs_feasible"]:
        raise ValueError("physical-link Zone candidate count differs")


def _json_line(line: str, line_number: int) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"invalid physical-link inspection JSON at line {line_number}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError("physical-link inspection records must be objects")
    return value


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "PhysicalLinkInspectionIndex",
    "_PhysicalLinkInspectionWriter",
    "iter_physical_link_inspection",
    "read_physical_link_inspection_index",
]
