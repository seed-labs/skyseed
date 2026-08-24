"""Runner-private supervision and durable traffic-telemetry recording."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from threading import RLock, Thread
from typing import Any, Mapping, Protocol, Sequence, TextIO


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
        self._started = False
        self._stopped = False
        self._packet_records = 0
        self._identity_records = 0
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
                    "format_version": 1,
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

    def record_failure(self, error: BaseException) -> None:
        """Retain a non-authoritative telemetry failure without raising it."""

        self._fail(error)

    def write_generation_identity(
        self, records: Sequence[Mapping[str, Any]]
    ) -> None:
        for record in records:
            if self._failure is not None:
                return
            try:
                self._write({"record_type": "interface_identity", **dict(record)})
            except BaseException as error:
                self._fail(error)
                return
            with self._lock:
                self._identity_records += 1

    def write_simulation_clock_anchor(
        self,
        *,
        nominal_origin: str,
        monotonic_origin_seconds: float,
    ) -> None:
        """Persist the automatic-run origin in the packet timestamp domain."""

        if self._failure is not None:
            return
        if self._clock_anchor_written:
            self._fail(
                RuntimeError("traffic telemetry clock anchor is already written")
            )
            return
        try:
            self._write(
                {
                    "record_type": "simulation_clock_anchor",
                    "nominal_origin": nominal_origin,
                    "monotonic_origin_ns": round(
                        monotonic_origin_seconds * 1_000_000_000
                    ),
                    "clock_domain": "host-kernel-monotonic",
                    "nominal_pacing": "1:1",
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
        process = self._process
        if process is not None and process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write('{"command":"stop"}\n')
                process.stdin.flush()
            except BaseException as error:
                self._fail(error)
        if process is not None:
            try:
                self._collector_exit_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                self._fail(error)
                process.terminate()
                try:
                    self._collector_exit_code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    self._collector_exit_code = process.wait(timeout=2)
            if process.stdin is not None:
                process.stdin.close()
        if self._reader is not None:
            self._reader.join(timeout=2)
            if self._reader.is_alive():
                self._fail(RuntimeError("traffic telemetry reader did not stop"))
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
        line = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            if self._stream is None:
                raise RuntimeError("traffic telemetry durable stream is closed")
            self._stream.write(line + "\n")
            self._stream.flush()

    def _fail(self, error: BaseException) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = {
                    "type": type(error).__name__,
                    "message": str(error),
                }


__all__: list[str] = []
