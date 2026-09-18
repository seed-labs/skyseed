"""Persist exact already-produced physical candidates for detached inspection."""
import gzip
from hashlib import sha256
from io import TextIOWrapper
import json
import os
from pathlib import Path
from skyseed_v2.simulator import PhysicalLinkQueryResult, PhysicalWorld
from skyseed_v2.shared._durable_evidence import _OrderedEvidenceWriter, _HashedOutput

_FORMAT_VERSION = 2
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
        self._path.write_bytes(b"")
        self._stream_seal = {"digest": sha256(), "bytes": 0}
        self._stream = None
        self._raw = None
        self._timestamp_path = self._root / "timestamps.jsonl"
        self._timestamp_stream = self._timestamp_path.open("w", encoding="utf-8")
        self._observation_count = 0
        self._first_timestamp = self._last_timestamp = None
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

    def record(self, observation: PhysicalLinkQueryResult) -> None:
        if self._finalized:
            raise RuntimeError('inspection-evidence writer is finalized')
        self._jobs.submit(self._record, observation)

    def _record(self, observation: PhysicalLinkQueryResult) -> None:
        if self._finalized:
            raise RuntimeError("inspection-evidence writer is finalized")
        if not isinstance(observation, PhysicalLinkQueryResult):
            raise TypeError("observation must be a PhysicalLinkQueryResult")
        timestamp = observation.timestamp.isoformat()
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            raise ValueError("formal observation timestamps must be increasing")
        inter_satellite = [
            [
                self._node(value.endpoint_a),
                self._node(value.endpoint_b),
                value.distance_m,
                value.propagation_delay_us,
                None if value.same_plane is None else int(value.same_plane),
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
                } if observation.work_counts is not None else None,
                "ground_station_search_complete": observation.ground_station_search_complete,
            }
        )
        self._timestamp_stream.write(json.dumps(timestamp) + "\n")
        if self._first_timestamp is None:
            self._first_timestamp = timestamp
        self._last_timestamp = timestamp
        self._observation_count += 1
        self._candidate_totals["inter_satellite"] += len(inter_satellite)
        self._candidate_totals["ground_station"] += len(ground_station)
        self._candidate_totals["zone_service"] += len(zone_service)

    def publish(self):
        self._jobs.flush()
        if self._stream is not None:
            self._stream.close()
            self._raw.close()
            self._stream = None
        self._timestamp_stream.flush()
        manifest = {
            "format_version": _FORMAT_VERSION,
            "artifact_type": _ARTIFACT_TYPE,
            "stream": _STREAM_NAME,
            "stream_size_bytes": self._stream_seal["bytes"],
            "stream_sha256": self._stream_seal["digest"].hexdigest(),
            "timestamp_index_bytes": self._timestamp_stream.tell(),
            "formal_observation_count": self._observation_count,
            "first_timestamp": self._first_timestamp,
            "last_timestamp": self._last_timestamp,
            "node_count": len(self._nodes),
            "zone_count": len(self._zones),
            "candidate_totals": dict(self._candidate_totals),
        }
        temporary = self._manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest) + "\n")
        os.replace(temporary, self._manifest_path)
        return manifest

    def finalize(self) -> Path:
        if not self._finalized:
            self.publish()
            self.close()
        return self._manifest_path

    def close(self) -> None:
        try:
            self._jobs.finish()
        finally:
            self._timestamp_stream.close()
            if self._stream is not None:
                self._stream.close()
            if self._raw is not None and not self._raw.closed:
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
        if self._stream is None:
            self._raw = _HashedOutput(self._path, self._stream_seal)
            self._stream = TextIOWrapper(gzip.GzipFile(filename="", mode="wb", fileobj=self._raw,
                compresslevel=1, mtime=0), encoding="utf-8")
        self._stream.write(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        )

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
