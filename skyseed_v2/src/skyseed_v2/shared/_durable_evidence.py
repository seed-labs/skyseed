"""Provide authority-free mechanical primitives for durable evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
import gzip
from io import TextIOWrapper
import json
from pathlib import Path
from typing import Any

from .node_id import NodeId


class _OrderedEvidenceWriter:
    """Bound finite serialization work and reuse invocation-owned streams.

    Callers supply immutable arguments to their own serializers. This helper
    owns only one I/O worker and file handles, never semantic state or seals.
    """

    def __init__(self, maximum_pending: int = 4):
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor
        if isinstance(maximum_pending, bool) or not isinstance(maximum_pending, int) or maximum_pending < 1:
            raise ValueError("maximum_pending must be a positive integer")
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='skyseed-evidence')
        self._pending = deque()
        self._streams = {}
        self._limit = maximum_pending
        self._closed = False
        self._failure = None
        self.peak_pending = 0
        self.wait_seconds = 0.0

    def submit(self, serialize, *args):
        from time import perf_counter
        if self._closed:
            raise RuntimeError('evidence writer is closed')
        if self._failure is not None:
            raise self._failure
        started = perf_counter()
        while self._pending and (self._pending[0].done() or len(self._pending) >= self._limit):
            self._result(self._pending.popleft())
        self.wait_seconds += perf_counter() - started
        self._pending.append(self._executor.submit(serialize, *args))
        self.peak_pending = max(self.peak_pending, len(self._pending))

    def append(self, path, record):
        # Called exclusively by the owned worker.
        stream = self._streams.get(path)
        if stream is None:
            stream = self._streams[path] = _open_jsonl(path, 'at')
        payload = json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n'
        stream.write(payload)
        return len(payload)  # ensure_ascii=True: encoded JSON byte count, before compression.

    def _result(self, future):
        try:
            return future.result()
        except BaseException as error:
            self._failure = error
            raise

    def finish(self):
        if self._closed:
            if self._failure is not None:
                raise self._failure
            return
        self._closed = True
        try:
            if self._failure is not None:
                raise self._failure
            while self._pending:
                self._result(self._pending.popleft())
        finally:
            self._executor.shutdown(wait=True)
            for stream in self._streams.values():
                stream.close()
            self._streams.clear()


def _reset_jsonl(path: Path, header: Mapping[str, object]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _open_jsonl(destination, "wt") as stream:
        stream.write(
            json.dumps(dict(header), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
    return destination


def _append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    with _open_jsonl(Path(path), "at") as stream:
        stream.write(
            json.dumps(dict(record), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
        stream.flush()


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(_iter_jsonl(path))


def _iter_jsonl(path: Path):
    """Yield conventional plain or gzip JSONL records one at a time."""

    with _open_jsonl(Path(path), "rt") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid durable evidence JSON at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError("durable evidence records must be JSON objects")
            yield value


def _open_jsonl(path: Path, mode: str):
    if path.suffix == ".gz":
        operation = mode.removesuffix("t")
        raw_mode = {
            "r": "rb",
            "w": "wb",
            "a": "ab",
        }[operation]
        raw = path.open(raw_mode)
        gzip_mode = "rb" if operation == "r" else "wb"
        compressed = gzip.GzipFile(
            filename="",
            mode=gzip_mode,
            fileobj=raw,
            compresslevel=1,
            **({} if operation == "r" else {"mtime": 0}),
        )
        return TextIOWrapper(compressed, encoding="utf-8")
    return path.open(mode.removesuffix("t"), encoding="utf-8")


def _semantic_value(value: object) -> object:
    """Detach one immutable semantic value into deterministic JSON data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, Enum):
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if isinstance(value, NodeId):
        return {
            "$type": "NodeId",
            "asn": value.asn,
            "entity_kind": value.entity_kind,
            "entity_id": value.entity_id,
        }
    if is_dataclass(value):
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                item.name: _semantic_value(getattr(value, item.name))
                for item in fields(value)
                if not item.name.startswith("_")
            },
        }
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {
                str(key): _semantic_value(item)
                for key, item in sorted(value.items(), key=lambda pair: pair[0])
            }
        return {
            "$type": "mapping",
            "items": [
                {"key": _semantic_value(key), "value": _semantic_value(item)}
                for key, item in sorted(
                    value.items(), key=lambda pair: repr(pair[0])
                )
            ],
        }
    if isinstance(value, (tuple, list)):
        return [_semantic_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_semantic_value(item) for item in sorted(value, key=repr)]
    raise TypeError(
        f"unsupported durable semantic value: {type(value).__module__}."
        f"{type(value).__qualname__}"
    )
