"""Runner-local traffic telemetry mechanism."""

from __future__ import annotations

from copy import deepcopy
import json
from queue import SimpleQueue
from pathlib import Path
import subprocess
from threading import RLock, Thread
from time import perf_counter
from typing import Any, Mapping, Protocol, Sequence, TextIO


_GENERATION_IDENTITY_CHUNK_SIZE = 512


class _CollectorHelper(Protocol):
    def start_telemetry_collector(
        self, request: Mapping[str, Any]
    ) -> subprocess.Popen[str]: ...


class _TrafficTelemetryRecorder:
    """Stream one helper collector into a user-owned durable JSONL prefix."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._path = self._directory / "traffic-telemetry.jsonl"
        self._lock = RLock()
        self._stream: TextIO | None = None
        self._process: subprocess.Popen[str] | None = None
        self._reader: Thread | None = None
        self._identity_writer: Thread | None = None
        self._identity_queue = SimpleQueue()
        self._live_ports: dict[int, int] = {}
        self._started = False
        self._stopped = False
        self._packet_records = 0
        self._identity_records = 0
        self._identity_observations = 0
        self._identity_generation_records = 0
        self._identity_chunks = 0
        self._identity_stream_writes = 0
        self._reported_losses = 0
        self._collector_exit_code: int | None = None
        self._collector_end_seen = False
        self._clock_anchor_written = False
        self._failure: dict[str, str] | None = None

    def start(
        self,
        *,
        helper: _CollectorHelper,
        foundation: Mapping[str, Any],
    ) -> None:
        if self._started:
            raise RuntimeError("traffic telemetry recorder is already started")
        self._started = True
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            self._stream = self._path.open("w", encoding="utf-8", buffering=1)
            self._write(
                {
                    "record_type": "telemetry_header",
                    "format_version": 2,
                    "event_schema": foundation["event_schema"],
                    "foundation_token": foundation["foundation_token"],
                    "pin_root": foundation["pin_root"],
                    "ring_capacity_bytes": foundation["ring_capacity_bytes"],
                    "source_observation": {
                        "hook": "ingress-only",
                        "source_outbound_observed": False,
                        "first_link": "inferred-from-first-observed-ingress",
                    },
                }
            )
            for node in foundation["nodes"]:
                self._write({"record_type": "node_identity", **dict(node)})
                self._identity_records += 1
            process = helper.start_telemetry_collector(
                foundation["collector_request"]
            )
        except BaseException as error:
            self._fail(error)
            return
        self._process = process
        self._reader = Thread(
            target=self._read_collector,
            name="skyseed-traffic-telemetry",
            daemon=True,
        )
        self._reader.start()
        self._identity_writer = Thread(target=self._write_queued_identities,
                                       name="skyseed-telemetry-identities", daemon=True)
        self._identity_writer.start()

    def record_failure(self, error: BaseException) -> None:
        """Retain a non-authoritative telemetry failure without raising it."""

        self._fail(error)

    def enqueue_generation_identity(self, templates, installed, withdrawn, boundary) -> None:
        """Enqueue a prepared template reference and current binding delta."""
        if self._failure is None:
            self._identity_queue.put((templates, installed, withdrawn, boundary))

    def _write_queued_identities(self) -> None:
        try:
            while True:
                item = self._identity_queue.get()
                if item is None:
                    return
                if isinstance(item, Mapping):
                    self._write(item)
                    continue
                templates, installed, withdrawn, boundary = item
                for port in withdrawn:
                    self._live_ports.pop(port, None)
                for value in installed:
                    self._live_ports[int(value["logical_port_id"])] = int(value["ifindex"])
                # Keep only changed endpoint facts in the stream. The closing
                # generation record commits the full retained membership and its
                # exact boundary; no identity is discarded at a batch seam.
                if not boundary.get("generation_publication_occurred"):
                    self._write({"record_type": "boundary_activation", **boundary})
                    continue
                current = {}
                updates = []
                previous = getattr(self, "_written_interfaces", {})
                for record in templates:
                    port = record["logical_port_id"]
                    value = {k: v for k, v in record.items() if k not in {"generation", "boundary_timestamp"}}
                    value["ifindex"] = self._live_ports[port]
                    current[port] = value
                    if previous.get(port) != value:
                        before = previous.get(port, {})
                        updates.append({"record_type": "interface_update", "logical_port_id": port,
                            **{k: v for k, v in value.items() if k not in before or before[k] != v}})
                    if len(updates) == _GENERATION_IDENTITY_CHUNK_SIZE:
                        self._write_many(updates)
                        self._identity_records += len(updates)
                        self._identity_stream_writes += 1
                        self._identity_chunks += 1
                        updates.clear()
                if updates:
                    self._write_many(updates)
                    self._identity_records += len(updates)
                    self._identity_stream_writes += 1
                    self._identity_chunks += 1
                if boundary.get("generation_publication_occurred"):
                    self._write({"record_type": "interface_generation",
                        "generation": boundary["active_generation"],
                        "boundary_timestamp": boundary["boundary_timestamp"],
                        "withdrawn": sorted(previous.keys()-current.keys())})
                    self._identity_stream_writes += 1
                    self._identity_generation_records += 1
                self._identity_observations += len(templates)
                self._written_interfaces = current
                self._write({"record_type": "boundary_activation", **boundary})
        except BaseException as error:
            self._fail(error)


    def write_simulation_clock_anchor(
        self,
        *,
        nominal_origin: str,
        monotonic_origin_seconds: float,
        revision: int = 0,
        held_since: float | None = None,
        active_frontier: str | None = None,
        action: str = "start",
        effective_monotonic_ns: int | None = None,
    ) -> None:
        """Enqueue the automatic-run clock anchor without waiting for the writer."""

        if self._failure is not None:
            return
        if self._clock_anchor_written and action == "start":
            self._fail(
                RuntimeError("traffic telemetry clock anchor is already written")
            )
            return
        try:
            self._identity_queue.put(
                {
                    "record_type": "simulation_clock_anchor",
                    "nominal_origin": nominal_origin,
                    "monotonic_origin_ns": round(
                        monotonic_origin_seconds * 1_000_000_000
                    ),
                    "clock_domain": "host-kernel-monotonic",
                    "nominal_pacing": "1:1",
                    "revision": revision, "held_since": held_since,
                    "active_frontier": active_frontier, "action": action,
                    "effective_monotonic_ns": (round(monotonic_origin_seconds * 1e9)
                        if effective_monotonic_ns is None else effective_monotonic_ns),
                }
            )
        except BaseException as error:
            self._fail(error)
            return
        self._clock_anchor_written = True

    def stop_and_drain(self) -> dict[str, Any]:
        if self._stopped:
            return self.snapshot()
        self._stopped = True
        if self._identity_writer is not None:
            self._identity_queue.put(None)
            self._identity_writer.join()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write('{"command":"stop"}\n')
                process.stdin.flush()
            except BaseException as error:
                self._fail(error)
        if process is not None:
            self._collector_exit_code = process.wait()
            if process.stdin is not None:
                process.stdin.close()
        if self._reader is not None:
            self._reader.join()
        if process is not None and not self._collector_end_seen:
            self._fail(
                RuntimeError(
                    "traffic telemetry collector omitted final-drain evidence"
                )
            )
        if process is not None and process.stderr is not None:
            stderr = process.stderr.read().strip()
            if stderr and self._failure is None:
                self._fail(RuntimeError(f"traffic telemetry collector stderr: {stderr}"))
        if self._collector_exit_code not in (None, 0) and self._failure is None:
            self._fail(
                RuntimeError(
                    "traffic telemetry collector exited with "
                    f"{self._collector_exit_code}"
                )
            )
        try:
            self._write(
                {
                    "record_type": "recorder_end",
                    "collector_exit_code": self._collector_exit_code,
                    "complete": self._failure is None,
                    "failure": self._failure,
                    "packet_records": self._packet_records,
                    "identity_records": self._identity_records,
                    "identity_observations": self._identity_observations,
                    "identity_generation_records": self._identity_generation_records,
                    "identity_chunks": self._identity_chunks,
                    "identity_stream_writes": self._identity_stream_writes,
                    "identity_chunk_size": _GENERATION_IDENTITY_CHUNK_SIZE,
                    "reported_losses": self._reported_losses,
                }
            )
        except BaseException as error:
            self._fail(error)
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.flush()
                finally:
                    self._stream.close()
                    self._stream = None
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(
                {
                    "enabled": True,
                    "path": str(self._path),
                    "started": self._started,
                    "stopped": self._stopped,
                    "complete": self._stopped and self._failure is None,
                    "packet_records": self._packet_records,
                    "identity_records": self._identity_records,
                    "identity_observations": self._identity_observations,
                    "identity_generation_records": self._identity_generation_records,
                    "identity_chunks": self._identity_chunks,
                    "identity_stream_writes": self._identity_stream_writes,
                    "identity_chunk_size": _GENERATION_IDENTITY_CHUNK_SIZE,
                    "reported_losses": self._reported_losses,
                    "collector_exit_code": self._collector_exit_code,
                    "failure": self._failure,
                }
            )

    def _read_collector(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                value = json.loads(line)
                if not isinstance(value, dict) or not isinstance(
                    value.get("record_type"), str
                ):
                    raise RuntimeError(
                        "traffic telemetry collector emitted an invalid record"
                    )
                self._write(value)
                if value["record_type"] == "packet":
                    with self._lock:
                        self._packet_records += 1
                elif value["record_type"] == "telemetry_loss":
                    with self._lock:
                        self._reported_losses += int(value.get("lost_events", 0))
                elif value["record_type"] == "collector_end":
                    with self._lock:
                        self._collector_end_seen = True
                elif value["record_type"] == "collector_error":
                    raise RuntimeError(str(value.get("error", "collector error")))
        except BaseException as error:
            self._fail(error)

    def _write(self, value: Mapping[str, Any]) -> None:
        line = self._serialize(value)
        with self._lock:
            if self._stream is None:
                raise RuntimeError("traffic telemetry durable stream is closed")
            self._stream.write(line)
            self._stream.flush()

    def _write_many(
        self, values: Sequence[Mapping[str, Any]]
    ) -> dict[str, float | int]:
        if not values:
            return {
                "serialization_seconds": 0.0,
                "write_flush_seconds": 0.0,
                "serialized_bytes": 0,
            }
        serialization_started = perf_counter()
        payload = "".join(self._serialize(value) for value in values)
        serialization_seconds = perf_counter() - serialization_started
        write_started = perf_counter()
        with self._lock:
            if self._stream is None:
                raise RuntimeError("traffic telemetry durable stream is closed")
            self._stream.write(payload)
            self._stream.flush()
        return {
            "serialization_seconds": serialization_seconds,
            "write_flush_seconds": perf_counter() - write_started,
            "serialized_bytes": len(payload.encode("utf-8")),
        }

    @staticmethod
    def _serialize(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"

    def _fail(self, error: BaseException) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
