"""Provide authority-free mechanical primitives for durable evidence."""

from __future__ import annotations

from collections.abc import Mapping
import gzip
from io import TextIOWrapper
from hashlib import sha256
import json
from pathlib import Path
from typing import Any



class _HashedOutput:
    """Hash the exact bytes emitted by a stream, including gzip framing."""
    def __init__(self, path, summary):
        self.raw = path.open("ab")
        self.summary = summary
    def write(self, data):
        count = self.raw.write(data)
        self.summary["digest"].update(data[:count])
        self.summary["bytes"] += count
        return count
    def flush(self): self.raw.flush()
    def close(self): self.raw.close()
    def tell(self): return self.raw.tell()
    @property
    def closed(self): return self.raw.closed


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
        self._summaries = {}
        self._raw_streams = {}
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

    def reset(self, path, header):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        self._summaries[path] = {"digest": sha256(), "bytes": 0, "record_count": -1}
        self.append(path, header)
        return path

    def append(self, path, record):
        # Called exclusively by the owned worker after initial headers.
        path = Path(path)
        stream = self._streams.get(path)
        if stream is None:
            if path in self._summaries:
                raw = _HashedOutput(path, self._summaries[path])
                self._raw_streams[path] = raw
                if path.suffix == ".gz":
                    stream = TextIOWrapper(gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=1, mtime=0), encoding="utf-8")
                else:
                    stream = raw
            else:
                stream = _open_jsonl(path, 'at')
            self._streams[path] = stream
        payload = json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n'
        stream.write(payload.encode() if isinstance(stream, _HashedOutput) else payload)
        if path in self._summaries:
            self._summaries[path]["record_count"] += 1
        return len(payload)

    def sealed_summary(self, path):
        if self._streams or self._pending:
            raise RuntimeError("evidence must drain before summarizing")
        value = self._summaries[Path(path)]
        return {"sha256": value["digest"].hexdigest(), "bytes": value["bytes"], "record_count": value["record_count"]}

    def _close_streams(self):
        failure = None
        for stream in (*self._streams.values(), *self._raw_streams.values()):
            try:
                stream.close()
            except BaseException as error:
                if failure is None:
                    failure = error
                elif error is not failure:
                    failure.add_note(f"another evidence stream close failed: {error}")
        self._streams.clear()
        self._raw_streams.clear()
        if failure is not None:
            raise failure

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
        failure = self._failure
        while self._pending:
            future = self._pending[0]
            try:
                future.result()
            except BaseException as error:
                if failure is None:
                    failure = error
                elif error is not failure:
                    failure.add_note(f"accepted evidence work also failed: {error}")
            # An executor interrupt does not establish worker completion.
            if future.done():
                self._pending.popleft()
        for close in (lambda: self._executor.shutdown(wait=True), self._close_streams):
            try:
                close()
            except BaseException as error:
                if failure is None:
                    failure = error
                elif error is not failure:
                    failure.add_note(f"evidence cleanup also failed: {error}")
        self._failure = failure
        if failure is not None:
            raise failure

    def flush(self):
        """Drain and close current stream members without ending the worker."""
        while self._pending:
            self._result(self._pending.popleft())
        self._close_streams()


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
