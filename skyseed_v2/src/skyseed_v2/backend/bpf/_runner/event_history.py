"""Bounded nonblocking durable event recording for the private BPF Runner."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any, Mapping

from skyseed_v2.shared._durable_evidence import _reset_jsonl


_DEFAULT_QUEUE_CAPACITY = 4096
_WRITE_FLUSH_INTERVAL_SECONDS = 0.1


class _NonBlockingEventHistory:
    """Move JSON materialization and I/O off Runner lifecycle threads."""

    def __init__(self, path: Path, *, capacity: int = _DEFAULT_QUEUE_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("Runner event-history capacity must be positive")
        self.path = _reset_jsonl(
            path,
            {
                "record_type": "history_header",
                "format_version": 2,
                "owner": "BpfRunner",
                "capture": "bounded-nonblocking",
            },
        )
        self._queue: Queue[Mapping[str, Any]] = Queue(maxsize=capacity)
        self._capacity = capacity
        self._lock = RLock()
        self._stop = Event()
        self._closed = False
        self._enqueued = 0
        self._written = 0
        self._dropped = 0
        self._failure: dict[str, str] | None = None
        self._thread = Thread(
            target=self._write_loop,
            name="skyseed-runner-evidence",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, record: Mapping[str, Any]) -> bool:
        with self._lock:
            if self._closed or self._failure is not None:
                self._dropped += 1
                return False
        try:
            self._queue.put_nowait(record)
        except Full:
            with self._lock:
                self._dropped += 1
            return False
        with self._lock:
            self._enqueued += 1
        return True

    def close(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        if timeout_seconds < 0:
            raise ValueError("Runner event-history close timeout must be non-negative")
        with self._lock:
            if self._closed:
                return self.snapshot()
            self._closed = True
        self._stop.set()
        self._thread.join(timeout=timeout_seconds)
        if self._thread.is_alive():
            self._fail(RuntimeError("Runner event-history writer did not stop in time"))
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "path": str(self.path),
                "queue_capacity": self._capacity,
                "queue_depth": self._queue.qsize(),
                "enqueued_count": self._enqueued,
                "written_count": self._written,
                "dropped_count": self._dropped,
                "overflowed": self._dropped > 0,
                "complete": (
                    self._closed
                    and not self._thread.is_alive()
                    and self._failure is None
                    and self._dropped == 0
                    and self._written == self._enqueued
                ),
                "closed": self._closed,
                "failure": None if self._failure is None else dict(self._failure),
            }

    def _write_loop(self) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                last_flush = monotonic()
                while not self._stop.is_set() or not self._queue.empty():
                    try:
                        record = self._queue.get(timeout=0.05)
                    except Empty:
                        if monotonic() - last_flush >= _WRITE_FLUSH_INTERVAL_SECONDS:
                            stream.flush()
                            last_flush = monotonic()
                        continue
                    stream.write(
                        json.dumps(
                            dict(record),
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    with self._lock:
                        self._written += 1
                    self._queue.task_done()
                    if monotonic() - last_flush >= _WRITE_FLUSH_INTERVAL_SECONDS:
                        stream.flush()
                        last_flush = monotonic()
                stream.flush()
        except BaseException as error:
            self._fail(error)

    def _fail(self, error: BaseException) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = {
                    "type": type(error).__name__,
                    "message": str(error),
                }


__all__: list[str] = []
