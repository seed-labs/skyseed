"""Runner-local traffic telemetry collector mechanism."""

from __future__ import annotations

import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import ctypes
import json
import os
import select
import sys
from typing import Any
from skyseed_v2.backend.bpf.runner.bpf import _lookup_u64_map, _open_pinned, _pin_root
from skyseed_v2.backend.bpf.runner.traffic_telemetry_abi import (
    _TELEMETRY_EVENT,
    _decode_telemetry_event,
)


def _write_collector_record(record: dict[str, Any]) -> None:
    print(
        json.dumps(record, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


def _collect_telemetry(request: dict[str, Any]) -> int:
    root = _pin_root(request["pin_root"])
    nodes = tuple(request["nodes"])
    event_fd = _open_pinned(root / "traffic_events")
    loss_fd = _open_pinned(root / "traffic_losses")
    libbpf = ctypes.CDLL("libbpf.so.1", use_errno=True)
    sample_callback_type = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    libbpf.ring_buffer__new.argtypes = (
        ctypes.c_int,
        sample_callback_type,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    libbpf.ring_buffer__new.restype = ctypes.c_void_p
    libbpf.ring_buffer__poll.argtypes = (ctypes.c_void_p, ctypes.c_int)
    libbpf.ring_buffer__poll.restype = ctypes.c_int
    libbpf.ring_buffer__consume.argtypes = (ctypes.c_void_p,)
    libbpf.ring_buffer__consume.restype = ctypes.c_int
    libbpf.ring_buffer__free.argtypes = (ctypes.c_void_p,)
    libbpf.ring_buffer__free.restype = None
    callback_error: list[BaseException] = []
    event_count = 0

    def sample(
        _context: ctypes.c_void_p,
        data: ctypes.c_void_p,
        size: int,
    ) -> int:
        nonlocal event_count
        try:
            record = _decode_telemetry_event(ctypes.string_at(data, size))
            _write_collector_record(record)
            event_count += 1
        except BaseException as error:
            callback_error.append(error)
            return -1
        return 0

    callback = sample_callback_type(sample)
    ring = libbpf.ring_buffer__new(event_fd, callback, None, None)
    if not ring:
        error_number = ctypes.get_errno()
        os.close(loss_fd)
        os.close(event_fd)
        raise OSError(error_number, os.strerror(error_number))
    _write_collector_record(
        {
            "record_type": "collector_start",
            "event_size_bytes": _TELEMETRY_EVENT.size,
            "node_count": len(nodes),
        }
    )
    try:
        stopping = False
        while not stopping:
            result = libbpf.ring_buffer__poll(ring, 100)
            if callback_error:
                raise callback_error[0]
            if result < 0:
                raise RuntimeError(
                    f"traffic telemetry ring poll failed: {result}"
                )
            readable, _, _ = select.select((sys.stdin,), (), (), 0)
            if readable:
                line = sys.stdin.readline()
                if not line:
                    stopping = True
                else:
                    command = json.loads(line)
                    if command != {"command": "stop"}:
                        raise ValueError(
                            "traffic telemetry collector received an invalid command"
                        )
                    stopping = True
        while True:
            consumed = libbpf.ring_buffer__consume(ring)
            if callback_error:
                raise callback_error[0]
            if consumed < 0:
                raise RuntimeError(
                    f"traffic telemetry final consume failed: {consumed}"
                )
            if consumed == 0:
                break
        total_losses = 0
        for node in nodes:
            lost = _lookup_u64_map(loss_fd, int(node["node_slot"]))
            if lost is None:
                raise RuntimeError("traffic telemetry loss counter is absent")
            total_losses += lost
            _write_collector_record(
                {
                    "record_type": "telemetry_loss",
                    "node_slot": int(node["node_slot"]),
                    "node_id": str(node["node_id"]),
                    "lost_events": lost,
                }
            )
        _write_collector_record(
            {
                "record_type": "collector_end",
                "packet_records": event_count,
                "reported_losses": total_losses,
            }
        )
        return 0
    finally:
        libbpf.ring_buffer__free(ring)
        os.close(loss_fd)
        os.close(event_fd)


def main() -> int:
    try:
        request_line = sys.stdin.readline()
        if not request_line:
            raise ValueError("traffic telemetry collector request is absent")
        request = json.loads(request_line)
        return _collect_telemetry(request)
    except BaseException as error:
        _write_collector_record(
            {
                "record_type": "collector_error",
                "error": str(error),
                "error_type": type(error).__name__,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
