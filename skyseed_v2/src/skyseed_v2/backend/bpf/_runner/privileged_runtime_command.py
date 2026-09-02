"""Execute one privileged tc-BPF command for the private BPF Runner."""

from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor
import errno
from ipaddress import IPv4Address, IPv4Network, ip_interface, ip_network
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import traceback
from threading import Lock
from time import monotonic, monotonic_ns
from typing import Any


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skyseed_v2.backend.bpf._runner.traffic_telemetry_abi import (
    _TELEMETRY_DECISION_DROP,
    _TELEMETRY_DECISION_PASS,
    _TELEMETRY_DECISION_REDIRECT,
    _TELEMETRY_EVENT,
    _TELEMETRY_REASON_INACTIVE_INGRESS,
    _TELEMETRY_REASON_LOCAL,
    _TELEMETRY_REASON_NON_IPV4,
    _TELEMETRY_REASON_NO_ACTIVE_GENERATION,
    _TELEMETRY_REASON_NO_ROUTE,
    _TELEMETRY_REASON_REDIRECT,
    _TELEMETRY_REASON_TRUNCATED_IPV4,
    _TELEMETRY_REASON_TRUNCATED_L2,
    _telemetry_event_schema,
)


_BPF_MAP_CREATE = 0
_BPF_MAP_LOOKUP_ELEM = 1
_BPF_MAP_UPDATE_ELEM = 2
_BPF_MAP_DELETE_ELEM = 3
_BPF_MAP_DELETE_BATCH = 27
_BPF_PROG_LOAD = 5
_BPF_OBJ_PIN = 6
_BPF_OBJ_GET = 7
_BPF_MAP_TYPE_HASH = 1
_BPF_MAP_TYPE_ARRAY = 2
_BPF_MAP_TYPE_RINGBUF = 27
_BPF_PROG_TYPE_SCHED_CLS = 3
_BPF_ANY = 0
_BPF_PSEUDO_MAP_FD = 1
_BPF_FUNC_MAP_LOOKUP_ELEM = 1
_BPF_FUNC_KTIME_GET_NS = 5
_BPF_FUNC_REDIRECT = 23
_BPF_FUNC_RINGBUF_OUTPUT = 130
_BPF_JMP_JLT_IMMEDIATE = 0xA5
_TC_ACT_OK = 0
_TC_ACT_SHOT = 2
_PIN_PARENT = Path("/sys/fs/bpf/skyseed-v2")
_INTERFACE_NAME = re.compile(r"sv2[0-9a-f]{10}[ab]")
_TELEMETRY_RING_CAPACITY = 1 << 20
_METRICS_LOCK = Lock()
_ACTIVE_METRICS: dict[str, Any] | None = None


def _record_metric(name: str, amount: int = 1) -> None:
    with _METRICS_LOCK:
        if _ACTIVE_METRICS is not None:
            _ACTIVE_METRICS[name] = int(_ACTIVE_METRICS.get(name, 0)) + amount


def _record_batch_size(size: int) -> None:
    if size <= 0:
        return
    with _METRICS_LOCK:
        if _ACTIVE_METRICS is not None:
            _ACTIVE_METRICS.setdefault("batch_sizes", []).append(size)


def _record_worker_limit(workers: int) -> None:
    with _METRICS_LOCK:
        if _ACTIVE_METRICS is not None:
            _ACTIVE_METRICS["worker_limit"] = max(
                workers, int(_ACTIVE_METRICS.get("worker_limit", 1))
            )


def _run_observed_worker(operation, item):
    with _METRICS_LOCK:
        if _ACTIVE_METRICS is not None:
            active = int(_ACTIVE_METRICS.get("active_workers", 0)) + 1
            _ACTIVE_METRICS["active_workers"] = active
            _ACTIVE_METRICS["worker_high_water"] = max(
                active, int(_ACTIVE_METRICS.get("worker_high_water", 0))
            )
    try:
        return operation(item)
    finally:
        with _METRICS_LOCK:
            if _ACTIVE_METRICS is not None:
                _ACTIVE_METRICS["active_workers"] = max(
                    0, int(_ACTIVE_METRICS.get("active_workers", 1)) - 1
                )


def _worker_count(request: dict[str, Any], item_count: int) -> int:
    value = request.get("worker_count", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Runner privileged worker_count must be an int")
    if value <= 0 or value > 64:
        raise ValueError("Runner privileged worker_count must be between 1 and 64")
    return min(value, max(1, item_count))


def _parallel_map(request: dict[str, Any], items: list[Any], operation) -> list[Any]:
    """Run deterministic resource-local batches through one bounded executor."""

    if not items:
        return []
    workers = _worker_count(request, len(items))
    _record_worker_limit(workers)
    _record_metric("parallel_phase_count")
    _record_metric("parallel_task_count", len(items))
    if workers == 1:
        return [_run_observed_worker(operation, item) for item in items]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="skyseed-bpf") as pool:
        return list(
            pool.map(
                lambda item: _run_observed_worker(operation, item),
                items,
            )
        )


class _BpfMapCreate(ctypes.Structure):
    _fields_ = [
        ("map_type", ctypes.c_uint32),
        ("key_size", ctypes.c_uint32),
        ("value_size", ctypes.c_uint32),
        ("max_entries", ctypes.c_uint32),
        ("map_flags", ctypes.c_uint32),
        ("inner_map_fd", ctypes.c_uint32),
        ("numa_node", ctypes.c_uint32),
        ("map_name", ctypes.c_char * 16),
        ("map_ifindex", ctypes.c_uint32),
        ("btf_fd", ctypes.c_uint32),
        ("btf_key_type_id", ctypes.c_uint32),
        ("btf_value_type_id", ctypes.c_uint32),
        ("btf_vmlinux_value_type_id", ctypes.c_uint32),
        ("map_extra", ctypes.c_uint64),
    ]


class _BpfMapElement(ctypes.Structure):
    _fields_ = [
        ("map_fd", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
        ("key", ctypes.c_uint64),
        ("value", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
    ]


class _BpfMapBatch(ctypes.Structure):
    _fields_ = [
        ("in_batch", ctypes.c_uint64),
        ("out_batch", ctypes.c_uint64),
        ("keys", ctypes.c_uint64),
        ("values", ctypes.c_uint64),
        ("count", ctypes.c_uint32),
        ("map_fd", ctypes.c_uint32),
        ("elem_flags", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
    ]


class _BpfObjectPath(ctypes.Structure):
    _fields_ = [
        ("pathname", ctypes.c_uint64),
        ("bpf_fd", ctypes.c_uint32),
        ("file_flags", ctypes.c_uint32),
        ("path_fd", ctypes.c_uint32),
    ]


class _BpfProgramLoad(ctypes.Structure):
    _fields_ = [
        ("prog_type", ctypes.c_uint32),
        ("insn_cnt", ctypes.c_uint32),
        ("insns", ctypes.c_uint64),
        ("license", ctypes.c_uint64),
        ("log_level", ctypes.c_uint32),
        ("log_size", ctypes.c_uint32),
        ("log_buf", ctypes.c_uint64),
        ("kern_version", ctypes.c_uint32),
        ("prog_flags", ctypes.c_uint32),
        ("prog_name", ctypes.c_char * 16),
        ("prog_ifindex", ctypes.c_uint32),
        ("expected_attach_type", ctypes.c_uint32),
    ]


_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.syscall.restype = ctypes.c_long


def _bpf_syscall_number() -> int:
    try:
        return {"x86_64": 321, "aarch64": 280}[os.uname().machine]
    except KeyError as error:
        raise RuntimeError("unsupported architecture for raw BPF syscall") from error


def _bpf(command: int, attributes: ctypes.Structure) -> int:
    _record_metric("bpf_syscalls")
    if command == _BPF_MAP_UPDATE_ELEM:
        _record_metric("bpf_map_update_syscalls")
    elif command in {_BPF_MAP_DELETE_ELEM, _BPF_MAP_DELETE_BATCH}:
        _record_metric("bpf_map_delete_syscalls")
        if command == _BPF_MAP_DELETE_BATCH:
            _record_metric("bpf_map_delete_batch_syscalls")
    result = _LIBC.syscall(
        _bpf_syscall_number(),
        command,
        ctypes.byref(attributes),
        ctypes.sizeof(attributes),
    )
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return int(result)


def _create_map(
    name: str,
    *,
    key_size: int,
    max_entries: int,
    map_type: int = _BPF_MAP_TYPE_HASH,
    value_size: int = 4,
) -> int:
    attributes = _BpfMapCreate(
        map_type=map_type,
        key_size=key_size,
        value_size=value_size,
        max_entries=max_entries,
        map_name=name.encode("ascii"),
    )
    return _bpf(_BPF_MAP_CREATE, attributes)


def _pin_object(file_descriptor: int, path: Path) -> None:
    encoded = ctypes.create_string_buffer(os.fsencode(path) + b"\0")
    attributes = _BpfObjectPath(
        pathname=ctypes.addressof(encoded),
        bpf_fd=file_descriptor,
    )
    _bpf(_BPF_OBJ_PIN, attributes)


def _open_pinned(path: Path) -> int:
    encoded = ctypes.create_string_buffer(os.fsencode(path) + b"\0")
    attributes = _BpfObjectPath(pathname=ctypes.addressof(encoded))
    return _bpf(_BPF_OBJ_GET, attributes)


def _key_buffer(key: int | tuple[int, int]) -> ctypes.Array[ctypes.c_char]:
    values = (key,) if isinstance(key, int) else key
    return ctypes.create_string_buffer(struct.pack(f"={len(values)}I", *values))


def _update_map(
    file_descriptor: int, key: int | tuple[int, int], value: int
) -> None:
    key_value = _key_buffer(key)
    map_value = ctypes.c_uint32(value)
    attributes = _BpfMapElement(
        map_fd=file_descriptor,
        key=ctypes.addressof(key_value),
        value=ctypes.addressof(map_value),
        flags=_BPF_ANY,
    )
    _bpf(_BPF_MAP_UPDATE_ELEM, attributes)


def _lookup_map(
    file_descriptor: int, key: int | tuple[int, int]
) -> int | None:
    key_value = _key_buffer(key)
    map_value = ctypes.c_uint32()
    attributes = _BpfMapElement(
        map_fd=file_descriptor,
        key=ctypes.addressof(key_value),
        value=ctypes.addressof(map_value),
    )
    try:
        _bpf(_BPF_MAP_LOOKUP_ELEM, attributes)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return None
        raise
    return int(map_value.value)


def _lookup_u64_map(file_descriptor: int, key: int) -> int | None:
    key_value = _key_buffer(key)
    map_value = ctypes.c_uint64()
    attributes = _BpfMapElement(
        map_fd=file_descriptor,
        key=ctypes.addressof(key_value),
        value=ctypes.addressof(map_value),
    )
    try:
        _bpf(_BPF_MAP_LOOKUP_ELEM, attributes)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return None
        raise
    return int(map_value.value)


def _delete_map(file_descriptor: int, key: int | tuple[int, int]) -> bool:
    key_value = _key_buffer(key)
    attributes = _BpfMapElement(
        map_fd=file_descriptor,
        key=ctypes.addressof(key_value),
    )
    try:
        _bpf(_BPF_MAP_DELETE_ELEM, attributes)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return False
        raise
    return True


def _delete_map_batch(
    file_descriptor: int,
    keys: tuple[int | tuple[int, int], ...],
) -> int:
    """Delete one exact key set with one fail-closed kernel batch syscall."""

    if not keys:
        return 0
    normalized = tuple(
        (key,) if isinstance(key, int) else tuple(key)
        for key in keys
    )
    key_width = len(normalized[0])
    if key_width not in {1, 2} or any(
        len(key) != key_width for key in normalized
    ):
        raise ValueError("BPF batch-delete keys must have one fixed width")
    packed = b"".join(
        struct.pack(f"={key_width}I", *key) for key in normalized
    )
    key_buffer = ctypes.create_string_buffer(packed)
    attributes = _BpfMapBatch(
        keys=ctypes.addressof(key_buffer),
        count=len(normalized),
        map_fd=file_descriptor,
    )
    _bpf(_BPF_MAP_DELETE_BATCH, attributes)
    deleted = int(attributes.count)
    if deleted != len(normalized):
        raise RuntimeError(
            "BPF batch-delete did not process every requested key: "
            f"requested={len(normalized)}, deleted={deleted}"
        )
    _record_metric("bpf_map_delete_operations", deleted)
    return deleted


def _instruction(
    code: int,
    destination: int = 0,
    source: int = 0,
    offset: int = 0,
    immediate: int = 0,
) -> bytes:
    return struct.pack(
        "=BBhi",
        code,
        destination | (source << 4),
        offset,
        immediate,
    )


class _Assembler:
    def __init__(self) -> None:
        self.instructions: list[bytes] = []
        self.labels: dict[str, int] = {}
        self.jumps: list[tuple[int, str, int, int, int, int]] = []

    def emit(
        self,
        code: int,
        destination: int = 0,
        source: int = 0,
        offset: int = 0,
        immediate: int = 0,
    ) -> None:
        self.instructions.append(
            _instruction(code, destination, source, offset, immediate)
        )

    def load_map(self, destination: int, file_descriptor: int) -> None:
        self.emit(
            0x18,
            destination=destination,
            source=_BPF_PSEUDO_MAP_FD,
            immediate=file_descriptor,
        )
        self.emit(0x00)

    def jump(
        self,
        code: int,
        *,
        destination: int,
        source: int = 0,
        immediate: int = 0,
        label: str,
    ) -> None:
        index = len(self.instructions)
        self.instructions.append(b"")
        self.jumps.append(
            (index, label, code, destination, source, immediate)
        )

    def label(self, name: str) -> None:
        if name in self.labels:
            raise RuntimeError(f"duplicate BPF label: {name}")
        self.labels[name] = len(self.instructions)

    def finish(self) -> bytes:
        for index, label, code, destination, source, immediate in self.jumps:
            try:
                target = self.labels[label]
            except KeyError as error:
                raise RuntimeError(f"unknown BPF label: {label}") from error
            self.instructions[index] = _instruction(
                code,
                destination,
                source,
                target - index - 1,
                immediate,
            )
        return b"".join(self.instructions)


def _tc_program(
    active_generation_map: int,
    active_map: int,
    route_map: int,
    *,
    telemetry_ring_map: int | None = None,
    telemetry_loss_map: int | None = None,
    node_slot: int | None = None,
) -> bytes:
    telemetry_values = (telemetry_ring_map, telemetry_loss_map, node_slot)
    telemetry_enabled = all(value is not None for value in telemetry_values)
    if telemetry_enabled != any(value is not None for value in telemetry_values):
        raise ValueError("tc telemetry maps and node_slot must be supplied together")
    program = _Assembler()
    program.emit(0xBF, destination=6, source=1)  # r6 = skb
    program.emit(0x61, destination=7, source=6, offset=76)  # data
    program.emit(0x61, destination=8, source=6, offset=80)  # data_end

    if telemetry_enabled:
        assert node_slot is not None
        for offset in (-48, -40, -32, -24, -16, -8):
            program.emit(0x7A, destination=10, offset=offset, immediate=0)
        program.emit(0x85, immediate=_BPF_FUNC_KTIME_GET_NS)
        program.emit(0x7B, destination=10, source=0, offset=-48)
        program.emit(0x62, destination=10, offset=-40, immediate=node_slot)
        program.emit(0x62, destination=10, offset=-36, immediate=-1)
        program.emit(0x61, destination=4, source=6, offset=40)  # skb->ifindex
        program.emit(0x63, destination=10, source=4, offset=-32)
        program.emit(0x61, destination=4, source=6, offset=0)  # skb->len
        program.emit(0x63, destination=10, source=4, offset=-16)

    def terminal(decision: int, reason: int, fallback: str) -> None:
        if telemetry_enabled:
            program.emit(0x72, destination=10, offset=-12, immediate=decision)
            program.emit(0x72, destination=10, offset=-11, immediate=reason)
            program.jump(0x05, destination=0, label="emit_telemetry")
        else:
            program.jump(0x05, destination=0, label=fallback)

    program.emit(0xBF, destination=4, source=7)
    program.emit(0x07, destination=4, immediate=14)
    program.jump(0x2D, destination=4, source=8, label="truncated_l2")
    program.emit(0x69, destination=4, source=7, offset=12)
    program.jump(
        0x55,
        destination=4,
        immediate=0x0008,
        label="non_ipv4",
    )
    program.emit(0xBF, destination=4, source=7)
    program.emit(0x07, destination=4, immediate=34)
    program.jump(0x2D, destination=4, source=8, label="truncated_ipv4")

    if telemetry_enabled:
        program.emit(0x61, destination=4, source=7, offset=26)  # IPv4 saddr
        program.emit(0x63, destination=10, source=4, offset=-24)
        program.emit(0x61, destination=4, source=7, offset=30)  # IPv4 daddr
        program.emit(0x63, destination=10, source=4, offset=-20)
        program.emit(0x71, destination=4, source=7, offset=23)  # protocol
        program.emit(0x73, destination=10, source=4, offset=-10)
        program.jump(0x55, destination=4, immediate=1, label="after_icmp")
        program.emit(0x71, destination=4, source=7, offset=14)  # version/IHL
        program.emit(0x57, destination=4, immediate=0x0F)
        # An IPv4 IHL below five is malformed. Leave ICMP metadata invalid
        # without changing the forwarding decision computed below.
        program.jump(
            _BPF_JMP_JLT_IMMEDIATE,
            destination=4,
            immediate=5,
            label="after_icmp",
        )
        program.emit(0x67, destination=4, immediate=2)
        program.emit(0xBF, destination=5, source=7)
        program.emit(0x07, destination=5, immediate=14)
        program.emit(0x0F, destination=5, source=4)
        program.emit(0xBF, destination=4, source=5)
        program.emit(0x07, destination=4, immediate=8)
        program.jump(0x2D, destination=4, source=8, label="after_icmp")
        program.emit(0x71, destination=4, source=5, offset=0)
        program.emit(0x73, destination=10, source=4, offset=-8)
        program.emit(0x71, destination=4, source=5, offset=1)
        program.emit(0x73, destination=10, source=4, offset=-7)
        program.emit(0x69, destination=4, source=5, offset=4)
        program.emit(0x6B, destination=10, source=4, offset=-6)
        program.emit(0x69, destination=4, source=5, offset=6)
        program.emit(0x6B, destination=10, source=4, offset=-4)
        program.emit(0x72, destination=10, offset=-9, immediate=1)
        program.label("after_icmp")

    program.emit(0xB7, destination=4, immediate=0)
    program.emit(0x63, destination=10, source=4, offset=-52)
    program.load_map(1, active_generation_map)
    program.emit(0xBF, destination=2, source=10)
    program.emit(0x07, destination=2, immediate=-52)
    program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
    program.jump(0x15, destination=0, immediate=0, label="no_active_generation")
    program.emit(0x61, destination=9, source=0, offset=0)
    if telemetry_enabled:
        program.emit(0x63, destination=10, source=9, offset=-36)

    program.emit(0x61, destination=4, source=6, offset=40)  # skb->ifindex
    program.emit(0x63, destination=10, source=9, offset=-64)
    program.emit(0x63, destination=10, source=4, offset=-60)
    program.load_map(1, active_map)
    program.emit(0xBF, destination=2, source=10)
    program.emit(0x07, destination=2, immediate=-64)
    program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
    program.jump(0x15, destination=0, immediate=0, label="inactive_ingress")

    program.emit(0x61, destination=4, source=7, offset=30)  # IPv4 daddr
    program.emit(0x63, destination=10, source=9, offset=-72)
    program.emit(0x63, destination=10, source=4, offset=-68)
    program.load_map(1, route_map)
    program.emit(0xBF, destination=2, source=10)
    program.emit(0x07, destination=2, immediate=-72)
    program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
    program.jump(0x15, destination=0, immediate=0, label="no_route")
    program.emit(0x61, destination=1, source=0, offset=0)
    program.jump(0x15, destination=1, immediate=0, label="local_route")
    if telemetry_enabled:
        program.emit(0x63, destination=10, source=1, offset=-28)
        terminal(
            _TELEMETRY_DECISION_REDIRECT,
            _TELEMETRY_REASON_REDIRECT,
            "redirect",
        )
    else:
        program.jump(0x05, destination=0, label="redirect")

    program.label("truncated_l2")
    terminal(
        _TELEMETRY_DECISION_DROP,
        _TELEMETRY_REASON_TRUNCATED_L2,
        "drop",
    )
    program.label("non_ipv4")
    terminal(
        _TELEMETRY_DECISION_PASS,
        _TELEMETRY_REASON_NON_IPV4,
        "pass",
    )
    program.label("truncated_ipv4")
    terminal(
        _TELEMETRY_DECISION_DROP,
        _TELEMETRY_REASON_TRUNCATED_IPV4,
        "drop",
    )
    program.label("no_active_generation")
    terminal(
        _TELEMETRY_DECISION_DROP,
        _TELEMETRY_REASON_NO_ACTIVE_GENERATION,
        "drop",
    )
    program.label("inactive_ingress")
    terminal(
        _TELEMETRY_DECISION_DROP,
        _TELEMETRY_REASON_INACTIVE_INGRESS,
        "drop",
    )
    program.label("no_route")
    terminal(
        _TELEMETRY_DECISION_DROP,
        _TELEMETRY_REASON_NO_ROUTE,
        "drop",
    )
    program.label("local_route")
    terminal(
        _TELEMETRY_DECISION_PASS,
        _TELEMETRY_REASON_LOCAL,
        "pass",
    )

    if telemetry_enabled:
        assert telemetry_ring_map is not None
        assert telemetry_loss_map is not None
        program.label("emit_telemetry")
        program.load_map(1, telemetry_ring_map)
        program.emit(0xBF, destination=2, source=10)
        program.emit(0x07, destination=2, immediate=-48)
        program.emit(0xB7, destination=3, immediate=_TELEMETRY_EVENT.size)
        program.emit(0xB7, destination=4, immediate=0)
        program.emit(0x85, immediate=_BPF_FUNC_RINGBUF_OUTPUT)
        program.jump(0x75, destination=0, immediate=0, label="telemetry_emitted")
        program.load_map(1, telemetry_loss_map)
        program.emit(0xBF, destination=2, source=10)
        program.emit(0x07, destination=2, immediate=-40)
        program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
        program.jump(0x15, destination=0, immediate=0, label="telemetry_emitted")
        program.emit(0xB7, destination=1, immediate=1)
        program.emit(0xDB, destination=0, source=1, offset=0)
        program.label("telemetry_emitted")
        program.emit(0x71, destination=1, source=10, offset=-12)
        program.jump(
            0x15,
            destination=1,
            immediate=_TELEMETRY_DECISION_REDIRECT,
            label="redirect",
        )
        program.jump(
            0x15,
            destination=1,
            immediate=_TELEMETRY_DECISION_DROP,
            label="drop",
        )
        program.jump(0x05, destination=0, label="pass")

    program.label("redirect")
    if telemetry_enabled:
        program.emit(0x61, destination=1, source=10, offset=-28)
    program.emit(0xB7, destination=2, immediate=0)
    program.emit(0x85, immediate=_BPF_FUNC_REDIRECT)
    program.emit(0x95)

    program.label("drop")
    program.emit(0xB7, destination=0, immediate=_TC_ACT_SHOT)
    program.emit(0x95)
    program.label("pass")
    program.emit(0xB7, destination=0, immediate=_TC_ACT_OK)
    program.emit(0x95)
    return program.finish()


def _load_program(
    active_generation_map: int,
    active_map: int,
    route_map: int,
    *,
    telemetry_ring_map: int | None = None,
    telemetry_loss_map: int | None = None,
    node_slot: int | None = None,
) -> int:
    instructions = _tc_program(
        active_generation_map,
        active_map,
        route_map,
        telemetry_ring_map=telemetry_ring_map,
        telemetry_loss_map=telemetry_loss_map,
        node_slot=node_slot,
    )
    instruction_buffer = ctypes.create_string_buffer(instructions)
    license_buffer = ctypes.create_string_buffer(b"GPL\0")
    log_buffer = ctypes.create_string_buffer(1 << 20)
    attributes = _BpfProgramLoad(
        prog_type=_BPF_PROG_TYPE_SCHED_CLS,
        insn_cnt=len(instructions) // 8,
        insns=ctypes.addressof(instruction_buffer),
        license=ctypes.addressof(license_buffer),
        log_level=1,
        log_size=len(log_buffer),
        log_buf=ctypes.addressof(log_buffer),
        prog_name=b"skyseed_tc",
    )
    try:
        return _bpf(_BPF_PROG_LOAD, attributes)
    except OSError as error:
        verifier = log_buffer.value.decode("utf-8", errors="replace")
        raise RuntimeError(f"BPF program load failed: {error}; {verifier}") from error


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    tolerate_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    _record_metric("external_processes")
    if command and command[0] == "nsenter":
        _record_metric("namespace_processes")
    if input_text is not None and "-batch" in command:
        _record_batch_size(sum(bool(line.strip()) for line in input_text.splitlines()))
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 and not tolerate_failure:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
    return result


def _assert_interface_name(value: str) -> str:
    if not isinstance(value, str) or _INTERFACE_NAME.fullmatch(value) is None:
        raise ValueError(f"invalid Runner-owned interface name: {value!r}")
    return value


def _pin_root(value: str) -> Path:
    path = Path(value)
    try:
        relative = path.relative_to(_PIN_PARENT)
    except ValueError as error:
        raise ValueError("pin root is outside the Runner-owned parent") from error
    if len(relative.parts) != 1 or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{5,63}", relative.name
    ):
        raise ValueError("pin root has an invalid deployment token")
    return path


def _assert_netns(node: dict[str, Any]) -> None:
    pid = int(node["pid"])
    expected = int(node["netns_inode"])
    try:
        actual = os.stat(f"/proc/{pid}/ns/net").st_ino
    except OSError as error:
        raise RuntimeError(f"node PID/netns is unavailable: {pid}") from error
    if actual != expected:
        raise RuntimeError(
            f"node PID/netns evidence is stale: pid={pid}, "
            f"expected={expected}, actual={actual}"
        )


def _in_netns(
    pid: int,
    command: list[str],
    *,
    input_text: str | None = None,
    tolerate_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["nsenter", "-t", str(pid), "-n", *command],
        input_text=input_text,
        tolerate_failure=tolerate_failure,
    )


def _interface_index(pid: int, interface_name: str) -> int:
    result = _in_netns(
        pid,
        ["ip", "-j", "link", "show", "dev", interface_name],
    )
    values = json.loads(result.stdout)
    if len(values) != 1 or not isinstance(values[0].get("ifindex"), int):
        raise RuntimeError(f"cannot resolve interface index: {interface_name}")
    return int(values[0]["ifindex"])


def _interface_indexes(pid: int, interface_names: set[str]) -> dict[str, int]:
    """Resolve any number of Runner interfaces in one namespace query."""

    expected = {_assert_interface_name(value) for value in interface_names}
    if not expected:
        return {}
    _record_metric("interface_identity_queries")
    values = _json_list(
        _in_netns(pid, ["ip", "-j", "link", "show"]),
        context="Runner interface identity query",
    )
    resolved = {
        value["ifname"]: int(value["ifindex"])
        for value in values
        if isinstance(value, dict)
        and value.get("ifname") in expected
        and isinstance(value.get("ifindex"), int)
    }
    if set(resolved) != expected or any(value <= 0 for value in resolved.values()):
        missing = sorted(expected - set(resolved))
        raise RuntimeError(
            "cannot resolve stable Runner interface identities: "
            f"missing={missing}"
        )
    return resolved


def _ip_key(address: str) -> int:
    packed = socket.inet_aton(address)
    return struct.unpack("=I", packed)[0]


def _loss_percent(packet_loss_ppm: int) -> str:
    return f"{packet_loss_ppm / 10_000:.6f}%"


def _is_zone_address(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("198.18.", "198.19."))


def _json_list(result: subprocess.CompletedProcess[str], *, context: str) -> list:
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{context} returned invalid JSON") from error
    if not isinstance(values, list):
        raise RuntimeError(f"{context} did not return a list")
    return values


def _namespace_ipv4_address_facts(
    node: dict[str, Any],
) -> tuple[tuple[IPv4Address, int], ...]:
    pid = int(node["pid"])
    address_values = _json_list(
        _in_netns(pid, ["ip", "-j", "-4", "address", "show"]),
        context="Runner IPv4 address audit",
    )
    address_facts = []
    for interface in address_values:
        if not isinstance(interface, dict):
            raise RuntimeError("Runner IPv4 address audit returned an invalid interface")
        for address in interface.get("addr_info", ()):
            if not isinstance(address, dict) or address.get("family") != "inet":
                continue
            local = address.get("local")
            prefix_length = address.get("prefixlen")
            if not isinstance(local, str) or not isinstance(prefix_length, int):
                raise RuntimeError("Runner IPv4 address audit returned an invalid prefix")
            address_facts.append((IPv4Address(local), prefix_length))

    return tuple(address_facts)


def _namespace_ipv4_local_addresses(
    node: dict[str, Any],
) -> frozenset[IPv4Address]:
    return frozenset(
        address for address, _ in _namespace_ipv4_address_facts(node)
    )


def _namespace_ipv4_address_networks(
    node: dict[str, Any],
) -> tuple[IPv4Network, ...]:
    address_networks = tuple(
        ip_network(f"{address}/{prefix_length}", strict=False)
        for address, prefix_length in _namespace_ipv4_address_facts(node)
    )

    return address_networks


def _namespace_ipv4_networks(
    node: dict[str, Any],
) -> tuple[tuple[IPv4Network, ...], tuple[IPv4Network, ...]]:
    pid = int(node["pid"])
    address_networks = _namespace_ipv4_address_networks(node)
    route_values = _json_list(
        _in_netns(pid, ["ip", "-j", "-4", "route", "show", "table", "all"]),
        context="Runner IPv4 route audit",
    )
    route_networks = []
    for route in route_values:
        if not isinstance(route, dict):
            raise RuntimeError("Runner IPv4 route audit returned an invalid route")
        destination = route.get("dst")
        if destination in (None, "default"):
            continue
        if not isinstance(destination, str):
            raise RuntimeError("Runner IPv4 route audit returned an invalid destination")
        route_networks.append(ip_network(destination, strict=False))
    return address_networks, tuple(route_networks)


def _audit_node_addresses(request: dict[str, Any]) -> dict[str, Any]:
    try:
        selected_range = ip_network(request["node_address_range"], strict=True)
        if not isinstance(selected_range, IPv4Network):
            raise ValueError("node address range must be IPv4")
        zone_networks = tuple(
            ip_interface(prefix).network for prefix in request["zone_prefixes"]
        )
        if any(not isinstance(network, IPv4Network) for network in zone_networks):
            raise ValueError("Zone prefix must be IPv4")
        nodes = tuple(request["nodes"])
        node_addresses = tuple(
            IPv4Address(node["node_address"]) for node in nodes
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Runner node address audit request is invalid") from error
    if len(set(node_addresses)) != len(nodes):
        raise RuntimeError("Runner node address audit found duplicate allocation")
    if any(address not in selected_range for address in node_addresses):
        raise RuntimeError("Runner node address lies outside selected range")
    if any(selected_range.overlaps(zone_network) for zone_network in zone_networks):
        raise RuntimeError("Runner node address collides with compiled Zone identity")

    def audit_node(node: dict[str, Any]) -> tuple[int, int]:
        _assert_netns(node)
        address_networks, route_networks = _namespace_ipv4_networks(node)
        for source, networks in (
            ("address", address_networks),
            ("route", route_networks),
        ):
            collision = next(
                (network for network in networks if selected_range.overlaps(network)),
                None,
            )
            if collision is not None:
                raise RuntimeError(
                    "Runner node address range collides with live namespace "
                    f"{source} before READY: node={node['key']}, "
                    f"selected={selected_range}, observed={collision}"
                )
        return len(address_networks), len(route_networks)

    observed = _parallel_map(request, list(nodes), audit_node)
    observed_addresses = sum(value[0] for value in observed)
    observed_routes = sum(value[1] for value in observed)
    return {
        "result": "passed",
        "selected_node_address_range": str(selected_range),
        "allocated_node_addresses": len(node_addresses),
        "compiled_zone_prefixes": len(zone_networks),
        "audited_node_namespaces": len(nodes),
        "observed_ipv4_address_prefixes": observed_addresses,
        "observed_non_default_ipv4_route_prefixes": observed_routes,
        "audited_sources": [
            "compiler-zone-prefixes",
            "live-node-netns-ipv4-addresses",
            "live-node-netns-non-default-ipv4-routes",
        ],
    }


def _initialize(request: dict[str, Any]) -> dict[str, Any]:
    root = _pin_root(request["pin_root"])
    if root.exists():
        raise RuntimeError(f"Runner pin root already exists: {root}")
    nodes = tuple(request["nodes"])
    for node in nodes:
        _assert_netns(node)
    collision_audit_started = monotonic()
    collision_audit = _audit_node_addresses(request)
    collision_audit_seconds = monotonic() - collision_audit_started
    installed_node_keys: set[str] = set()
    installed_node_keys_lock = Lock()
    root.mkdir(parents=True)
    active_generation_fd = -1
    telemetry_ring_fd = -1
    telemetry_loss_fd = -1
    telemetry_enabled = request.get("telemetry_enabled") is True
    try:
        shared_scaffolding_started = monotonic()
        active_generation_fd = _create_map(
            "active_gen", key_size=4, max_entries=1
        )
        _pin_object(active_generation_fd, root / "active_generation")
        _update_map(
            active_generation_fd, 0, int(request["inactive_generation"])
        )
        if telemetry_enabled:
            telemetry_ring_fd = _create_map(
                "traffic_events",
                key_size=0,
                value_size=0,
                max_entries=_TELEMETRY_RING_CAPACITY,
                map_type=_BPF_MAP_TYPE_RINGBUF,
            )
            telemetry_loss_fd = _create_map(
                "traffic_losses",
                key_size=4,
                value_size=8,
                max_entries=len(nodes),
                map_type=_BPF_MAP_TYPE_ARRAY,
            )
            _pin_object(telemetry_ring_fd, root / "traffic_events")
            _pin_object(telemetry_loss_fd, root / "traffic_losses")
        shared_scaffolding_seconds = monotonic() - shared_scaffolding_started
        def initialize_node(node: dict[str, Any]) -> dict[str, Any]:
            _in_netns(
                int(node["pid"]),
                ["ip", "-batch", "-"],
                input_text=(
                    "link set dev lo up\n"
                    f"address add {node['node_address']}/32 dev lo\n"
                ),
            )
            with installed_node_keys_lock:
                installed_node_keys.add(node["key"])
            node_root = root / node["pin_component"]
            node_root.mkdir()
            active_path = node_root / "active_links"
            route_path = node_root / "routes"
            program_path = node_root / "tc_ingress"
            active_fd = _create_map(
                "active_links",
                key_size=8,
                max_entries=int(node["active_links_capacity"]),
            )
            route_fd = _create_map(
                "routes",
                key_size=8,
                max_entries=int(node["routes_capacity"]),
            )
            program_fd = -1
            try:
                _pin_object(active_fd, active_path)
                _pin_object(route_fd, route_path)
                if telemetry_enabled:
                    program_fd = _load_program(
                        active_generation_fd,
                        active_fd,
                        route_fd,
                        telemetry_ring_map=telemetry_ring_fd,
                        telemetry_loss_map=telemetry_loss_fd,
                        node_slot=int(node["node_slot"]),
                    )
                else:
                    program_fd = _load_program(
                        active_generation_fd,
                        active_fd,
                        route_fd,
                    )
                _pin_object(program_fd, program_path)
            finally:
                for file_descriptor in (program_fd, route_fd, active_fd):
                    if file_descriptor >= 0:
                        os.close(file_descriptor)
            return {
                "key": node["key"],
                "active_links": int(node["active_links_capacity"]),
                "routes": int(node["routes_capacity"]),
            }

        node_foundation_started = monotonic()
        map_capacities = _parallel_map(request, list(nodes), initialize_node)
        node_foundation_seconds = monotonic() - node_foundation_started
        return {
            "pin_root": str(root),
            "node_count": len(nodes),
            "active_generation": int(request["inactive_generation"]),
            "shared_active_generation_map": True,
            "traffic_telemetry": {
                "enabled": telemetry_enabled,
                "shared_ring_buffer": telemetry_enabled,
                "ring_capacity_bytes": (
                    _TELEMETRY_RING_CAPACITY if telemetry_enabled else None
                ),
                "event_schema": (
                    _telemetry_event_schema() if telemetry_enabled else None
                ),
                "loss_counter_entries": len(nodes) if telemetry_enabled else 0,
            },
            "collision_audit": collision_audit,
            "installed_node_addresses": sorted(installed_node_keys),
            "map_capacities": map_capacities,
            "preparation_phases": {
                "collision_audit_seconds": collision_audit_seconds,
                "shared_bpf_map_pin_scaffolding_seconds": (
                    shared_scaffolding_seconds
                ),
                "node_address_map_program_scaffolding_seconds": (
                    node_foundation_seconds
                ),
            },
        }
    except BaseException:
        _cleanup(
            {
                **request,
                "nodes": [
                    {
                        **node,
                        "node_address_owned": node["key"] in installed_node_keys,
                    }
                    for node in nodes
                ],
                "connections": (),
            },
            tolerate_errors=True,
        )
        raise
    finally:
        for file_descriptor in (
            telemetry_loss_fd,
            telemetry_ring_fd,
            active_generation_fd,
        ):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _prepare_boundary(request: dict[str, Any]) -> dict[str, Any]:
    root = _pin_root(request["pin_root"])
    if not root.is_dir():
        raise RuntimeError("Runner dataplane foundation is absent")
    nodes = {node["key"]: node for node in request["nodes"]}
    for node in nodes.values():
        _assert_netns(node)
    created: list[dict[str, Any]] = []
    try:
        physical_link_started = monotonic()
        connections = list(request["connections"])
        created.extend(connections)
        if connections:
            worker_count = _worker_count(request, len(connections))
            chunk_size = (len(connections) + worker_count - 1) // worker_count
            connection_chunks = [
                connections[index : index + chunk_size]
                for index in range(0, len(connections), chunk_size)
            ]

            def create_connection_chunk(
                chunk: list[dict[str, Any]],
            ) -> int:
                commands = []
                for connection in chunk:
                    interface_a = _assert_interface_name(
                        connection["interface_a"]
                    )
                    interface_b = _assert_interface_name(
                        connection["interface_b"]
                    )
                    commands.append(
                        f"link add {interface_a} netns "
                        f"{nodes[connection['node_a']]['pid']} type veth "
                        f"peer name {interface_b} netns "
                        f"{nodes[connection['node_b']]['pid']}"
                    )
                _run(
                    ["ip", "-batch", "-"],
                    input_text="\n".join(commands) + "\n",
                )
                return len(chunk)

            if sum(
                _parallel_map(
                    request,
                    connection_chunks,
                    create_connection_chunk,
                )
            ) != len(connections):
                raise RuntimeError(
                    "Runner physical resource chunk join is incomplete"
                )
        physical_link_seconds = monotonic() - physical_link_started
        interfaces_by_node: dict[str, list[tuple[str, dict[str, Any]]]] = {
            key: [] for key in nodes
        }
        for connection in request["connections"]:
            interfaces_by_node[connection["node_a"]].append(
                (connection["interface_a"], connection["parameters_a"])
            )
            interfaces_by_node[connection["node_b"]].append(
                (connection["interface_b"], connection["parameters_b"])
            )
        def prepare_node(item: tuple[str, list[tuple[str, dict[str, Any]]]]) -> None:
            key, interfaces = item
            node = nodes[key]
            ip_commands = [
                f"link set dev {_assert_interface_name(name)} address {request['common_mac']}"
                for name, _ in interfaces
            ]
            _in_netns(
                int(node["pid"]), ["ip", "-batch", "-"],
                input_text="\n".join(ip_commands) + "\n",
            )
            tc_commands = []
            program_path = root / node["pin_component"] / "tc_ingress"
            for name, parameters in interfaces:
                name = _assert_interface_name(name)
                tc_commands.extend(
                    (
                        f"qdisc replace dev {name} root handle 1: netem limit 1000 "
                        f"delay {parameters['delay_us']}us "
                        f"loss {_loss_percent(parameters['packet_loss_ppm'])} "
                        f"rate {parameters['bandwidth_bps']}bit",
                        f"qdisc replace dev {name} clsact",
                        f"filter replace dev {name} ingress protocol ip priority 1 "
                        f"bpf direct-action pinned {program_path}",
                    )
                )
            _in_netns(
                int(node["pid"]), ["tc", "-batch", "-"],
                input_text="\n".join(tc_commands) + "\n",
            )

        setup_items = [
            (key, interfaces)
            for key, interfaces in sorted(interfaces_by_node.items())
            if interfaces
        ]
        interface_qdisc_started = monotonic()
        _parallel_map(request, setup_items, prepare_node)
        interface_qdisc_seconds = monotonic() - interface_qdisc_started

        generation = int(request["generation"])
        def preload_node(generation_node: dict[str, Any]) -> dict[str, Any]:
            node = nodes[generation_node["key"]]
            cached_indexes: dict[str, int] = {}
            for identity in generation_node.get("interface_identities", ()):
                name = _assert_interface_name(identity["interface"])
                ifindex = int(identity["ifindex"])
                if ifindex <= 0 or name in cached_indexes:
                    raise RuntimeError("cached Runner interface identity is invalid")
                cached_indexes[name] = ifindex
            required_names = {
                _assert_interface_name(name)
                for name in generation_node["active_interfaces"]
            }
            required_names.update(
                _assert_interface_name(route["interface"])
                for route in generation_node["routes"]
                if route["interface"] is not None
            )
            missing_names = required_names - set(cached_indexes)
            if missing_names:
                cached_indexes.update(
                    _interface_indexes(int(node["pid"]), missing_names)
                )
            if len(set(cached_indexes.values())) != len(cached_indexes):
                raise RuntimeError("Runner interface identities are not unique in one namespace")
            active_fd = _open_pinned(root / node["pin_component"] / "active_links")
            route_fd = _open_pinned(root / node["pin_component"] / "routes")
            try:
                indexes = [
                    cached_indexes[_assert_interface_name(name)]
                    for name in generation_node["active_interfaces"]
                ]
                for index in indexes:
                    _update_map(active_fd, (generation, index), 1)
                route_keys = []
                for route in generation_node["routes"]:
                    destination = _ip_key(route["destination"])
                    interface = route["interface"]
                    interface_index = (
                        0
                        if interface is None
                        else cached_indexes[_assert_interface_name(interface)]
                    )
                    _update_map(route_fd, (generation, destination), interface_index)
                    route_keys.append(destination)
            finally:
                os.close(route_fd)
                os.close(active_fd)
            return {
                "key": generation_node["key"],
                "active_interface_indexes": indexes,
                "route_keys": route_keys,
                "interface_identities": [
                    {"interface": name, "ifindex": cached_indexes[name]}
                    for name in sorted(required_names)
                ],
                "new_interface_identity_count": len(missing_names),
            }

        generation_preload_started = monotonic()
        generation_keys = _parallel_map(
            request,
            list(request["generation_nodes"]),
            preload_node,
        )
        generation_preload_seconds = monotonic() - generation_preload_started
        return {
            "pin_root": str(root),
            "boundary_timestamp": request["boundary_timestamp"],
            "generation": generation,
            "generation_keys": generation_keys,
            "prepared_connections": len(created),
            "prepared_interfaces": len(created) * 2,
            "prepared_links_admin_down": len(created) * 2,
            "preloaded_active_links": sum(
                len(value["active_interface_indexes"])
                for value in generation_keys
            ),
            "preloaded_routes": sum(len(value["route_keys"]) for value in generation_keys),
            "stable_interface_identity_count": sum(
                len(value["interface_identities"]) for value in generation_keys
            ),
            "new_interface_identity_count": sum(
                value["new_interface_identity_count"] for value in generation_keys
            ),
            "preparation_phases": {
                "physical_veth_creation_seconds": physical_link_seconds,
                "interface_qdisc_bpf_setup_seconds": interface_qdisc_seconds,
                "stable_identity_and_generation_preload_seconds": (
                    generation_preload_seconds
                ),
            },
        }
    except BaseException:
        for connection in reversed(created):
            node = nodes[connection["node_a"]]
            _in_netns(
                int(node["pid"]),
                ["ip", "link", "del", connection["interface_a"]],
                tolerate_failure=True,
            )
        raise


def _activate_boundary(request: dict[str, Any]) -> dict[str, Any]:
    root = _pin_root(request["pin_root"])
    generation_fd = _open_pinned(root / "active_generation")
    try:
        actual = _lookup_map(generation_fd, 0)
        expected = int(request["expected_active_generation"])
        if actual != expected:
            raise RuntimeError(
                f"active generation evidence is stale: expected={expected}, actual={actual}"
            )
        for node in request["nodes"]:
            _assert_netns(node)
            identities = {
                _assert_interface_name(value["interface"]): int(value["ifindex"])
                for value in node.get("interface_identities", ())
            }
            for field in ("link_up", "link_down"):
                for interface in node[field]:
                    name = _assert_interface_name(interface)
                    if identities.get(name, 0) <= 0:
                        raise RuntimeError(
                            "Main activation lacks stable interface identity: " + name
                        )

        def link_up_node(node: dict[str, Any]) -> int:
            commands = [
                f"link set dev {_assert_interface_name(name)} up"
                for name in node["link_up"]
            ]
            if commands:
                _in_netns(
                    int(node["pid"]),
                    ["ip", "-batch", "-"],
                    input_text="\n".join(commands) + "\n",
                )
            return len(commands)

        link_updates = sum(
            _parallel_map(request, list(request["nodes"]), link_up_node)
        )

        def qdisc_node(node: dict[str, Any]) -> int:
            commands = []
            for value in node["qdisc"]:
                name = _assert_interface_name(value["interface"])
                commands.append(
                    f"qdisc replace dev {name} root handle 1: netem limit 1000 "
                    f"delay {value['delay_us']}us loss {_loss_percent(value['packet_loss_ppm'])} "
                    f"rate {value['bandwidth_bps']}bit"
                )
            if commands:
                _in_netns(
                    int(node["pid"]),
                    ["tc", "-batch", "-"],
                    input_text="\n".join(commands) + "\n",
                )
            return len(commands)

        qdisc_updates = sum(
            _parallel_map(request, list(request["nodes"]), qdisc_node)
        )

        def zone_readiness_node(node: dict[str, Any]) -> tuple[int, int]:
            commands = [
                f"address replace {value}/32 dev lo"
                for value in node["zone_address_add"]
            ]
            if commands:
                _in_netns(
                    int(node["pid"]),
                    ["ip", "-force", "-batch", "-"],
                    input_text="\n".join(commands) + "\n",
                )
            return len(commands), int(bool(commands))

        zone_readiness_results = _parallel_map(
            request,
            list(request["nodes"]),
            zone_readiness_node,
        )
        zone_readiness_updates = sum(
            value[0] for value in zone_readiness_results
        )

        def route_node(node: dict[str, Any]) -> tuple[int, int]:
            commands = []
            access_updates = 0
            for route in node["route_delete"]:
                destination = route["destination"]
                interface = _assert_interface_name(route["interface"])
                commands.extend(
                    (
                        f"route del {destination}/32",
                        f"neigh del {destination} dev {interface}",
                    )
                )
            for route in node["route_replace"]:
                name = _assert_interface_name(route["interface"])
                destination = route["destination"]
                commands.extend(
                    (
                        f"route replace {destination}/32 dev {name} scope link",
                        f"neigh replace {destination} lladdr "
                        f"{request['common_mac']} nud permanent dev {name}",
                    )
                )
            for route in node.get("access_peer_route_delete", ()):
                destination = route["destination"]
                interface = _assert_interface_name(route["interface"])
                commands.extend(
                    (
                        f"route del {destination}/32",
                        f"neigh del {destination} dev {interface}",
                    )
                )
                access_updates += 2
            for route in node.get("access_peer_route_replace", ()):
                name = _assert_interface_name(route["interface"])
                destination = route["destination"]
                commands.extend(
                    (
                        f"route replace {destination}/32 dev {name} scope link",
                        f"neigh replace {destination} lladdr "
                        f"{request['common_mac']} nud permanent dev {name}",
                    )
                )
                access_updates += 2
            if commands:
                _in_netns(
                    int(node["pid"]),
                    ["ip", "-force", "-batch", "-"],
                    input_text="\n".join(commands) + "\n",
                )
            return len(commands), access_updates

        route_results = _parallel_map(
            request, list(request["nodes"]), route_node
        )
        route_updates = sum(value[0] for value in route_results)
        access_peer_route_updates = sum(value[1] for value in route_results)

        generation_switch_start_monotonic_ns = monotonic_ns()
        generation_changed = bool(request.get("generation_changed", True))
        if generation_changed:
            _update_map(generation_fd, 0, int(request["generation"]))
        generation_switch_end_monotonic_ns = monotonic_ns()
        activation_switch_seconds = (
            generation_switch_end_monotonic_ns
            - generation_switch_start_monotonic_ns
        ) / 1_000_000_000

        def post_switch_node(node: dict[str, Any]) -> tuple[int, int, int]:
            commands = [
                f"address del {value}/32 dev lo"
                for value in node["zone_address_delete"]
            ]
            zone_count = len(commands)
            commands.extend(
                f"link set dev {_assert_interface_name(name)} down"
                for name in node["link_down"]
            )
            if commands:
                _in_netns(
                    int(node["pid"]),
                    ["ip", "-force", "-batch", "-"],
                    input_text="\n".join(commands) + "\n",
                )
            return zone_count, len(commands) - zone_count, int(bool(commands))

        post_switch_results = _parallel_map(
            request,
            list(request["nodes"]),
            post_switch_node,
        )
        zone_updates = zone_readiness_updates + sum(
            value[0] for value in post_switch_results
        )
        link_updates += sum(value[1] for value in post_switch_results)
        namespace_process_families = {
            "link_up": sum(bool(node["link_up"]) for node in request["nodes"]),
            "qdisc": sum(bool(node["qdisc"]) for node in request["nodes"]),
            "zone_readiness": sum(
                value[1] for value in zone_readiness_results
            ),
            "route_neighbour": sum(
                bool(
                    node["route_delete"]
                    or node["route_replace"]
                    or node.get("access_peer_route_delete")
                    or node.get("access_peer_route_replace")
                )
                for node in request["nodes"]
            ),
            "post_switch": sum(value[2] for value in post_switch_results),
        }
        separate_post_switch_processes = sum(
            bool(node["zone_address_delete"])
            + bool(node["link_down"])
            for node in request["nodes"]
        )
        return {
            "pin_root": str(root),
            "boundary_timestamp": request["boundary_timestamp"],
            "previous_generation": expected,
            "active_generation": int(request["generation"]),
            "generation_publications": int(generation_changed),
            "generation_reused": not generation_changed,
            "folded_forwarding_constraints": int(
                request.get("folded_forwarding_constraints", 0)
            ),
            "link_state_updates": link_updates,
            "qdisc_updates": qdisc_updates,
            "route_neighbour_commands": route_updates,
            "access_peer_route_neighbour_commands": access_peer_route_updates,
            "zone_address_updates": zone_updates,
            "namespace_process_families": namespace_process_families,
            "post_switch_namespace_processes_avoided": (
                separate_post_switch_processes
                - namespace_process_families["post_switch"]
            ),
            "activation_switch_seconds": activation_switch_seconds,
            "generation_switch_start_monotonic_ns": (
                generation_switch_start_monotonic_ns
            ),
            "generation_switch_end_monotonic_ns": (
                generation_switch_end_monotonic_ns
            ),
            "grouping": request.get("grouping", {}),
        }
    finally:
        os.close(generation_fd)


def _validate(request: dict[str, Any]) -> dict[str, Any]:
    for node in request["nodes"]:
        _assert_netns(node)
    return {"validated_nodes": len(request["nodes"])}


def _reclaim_generation(request: dict[str, Any]) -> dict[str, Any]:
    root = _pin_root(request["pin_root"])
    nodes = {node["key"]: node for node in request["nodes"]}
    generation = int(request["generation"])
    def reclaim_node(keys: dict[str, Any]) -> int:
        node = nodes[keys["key"]]
        _assert_netns(node)
        active_fd = _open_pinned(root / node["pin_component"] / "active_links")
        route_fd = _open_pinned(root / node["pin_component"] / "routes")
        try:
            deleted = _delete_map_batch(
                active_fd,
                tuple(
                    (generation, int(value))
                    for value in keys["active_interface_indexes"]
                ),
            )
            deleted += _delete_map_batch(
                route_fd,
                tuple(
                    (generation, int(value))
                    for value in keys["route_keys"]
                ),
            )
        finally:
            os.close(route_fd)
            os.close(active_fd)
        return deleted

    deleted = sum(
        _parallel_map(request, list(request["generation_keys"]), reclaim_node)
    )
    return {"generation": generation, "deleted_entries": deleted}


def _reclaim_resources(request: dict[str, Any]) -> dict[str, Any]:
    nodes = {node["key"]: node for node in request["nodes"]}
    by_node: dict[str, list[dict[str, Any]]] = {}
    for connection in request["connections"]:
        by_node.setdefault(connection["node_a"], []).append(connection)

    def reclaim_node(item: tuple[str, list[dict[str, Any]]]) -> int:
        key, connections = item
        node = nodes[key]
        _assert_netns(node)
        commands = [
            f"link del {_assert_interface_name(connection['interface_a'])}"
            for connection in connections
        ]
        if commands:
            _in_netns(
                int(node["pid"]),
                ["ip", "-force", "-batch", "-"],
                input_text="\n".join(commands) + "\n",
            )
        return len(commands)

    deleted = sum(
        _parallel_map(request, sorted(by_node.items()), reclaim_node)
    )
    return {"deleted_connections": deleted}


def _cleanup(
    request: dict[str, Any], *, tolerate_errors: bool = False
) -> dict[str, Any]:
    root = _pin_root(request["pin_root"])
    errors: list[str] = []
    nodes = {node["key"]: node for node in request.get("nodes", ())}
    interfaces_by_node: dict[str, list[str]] = {key: [] for key in nodes}
    for connection in request.get("connections", ()):
        interfaces_by_node[connection["node_a"]].append(
            _assert_interface_name(connection["interface_a"])
        )
        interfaces_by_node[connection["node_b"]].append(
            _assert_interface_name(connection["interface_b"])
        )

    def detach_node(item: tuple[str, dict[str, Any]]) -> list[str]:
        key, node = item
        node_errors: list[str] = []
        try:
            _assert_netns(node)
        except RuntimeError as error:
            return [str(error)]
        tc_commands = []
        for interface_name in interfaces_by_node[key]:
            tc_commands.extend(
                (
                    f"filter del dev {interface_name} ingress",
                    f"qdisc del dev {interface_name} clsact",
                    f"qdisc del dev {interface_name} root",
                )
            )
        if tc_commands:
            result = _in_netns(
                int(node["pid"]),
                ["tc", "-force", "-batch", "-"],
                input_text="\n".join(tc_commands) + "\n",
                tolerate_failure=True,
            )
            if result.returncode != 0 and result.stderr:
                node_errors.append(result.stderr.strip())
        return node_errors

    node_items = sorted(nodes.items())
    for node_errors in _parallel_map(request, node_items, detach_node):
        errors.extend(node_errors)

    connections_by_node: dict[str, list[dict[str, Any]]] = {}
    for connection in request.get("connections", ()):
        connections_by_node.setdefault(connection["node_a"], []).append(connection)

    def delete_connections(
        item: tuple[str, list[dict[str, Any]]],
    ) -> tuple[int, list[str]]:
        key, connections = item
        node = nodes[key]
        try:
            _assert_netns(node)
        except RuntimeError as error:
            return 0, [str(error)]
        commands = [
            f"link del {_assert_interface_name(connection['interface_a'])}"
            for connection in connections
        ]
        result = _in_netns(
            int(node["pid"]),
            ["ip", "-force", "-batch", "-"],
            input_text="\n".join(commands) + "\n",
            tolerate_failure=True,
        )
        if result.returncode != 0:
            return 0, [result.stderr.strip()] if result.stderr else []
        return len(commands), []

    deleted_connections = 0
    for deleted, node_errors in _parallel_map(
        request, sorted(connections_by_node.items()), delete_connections
    ):
        deleted_connections += deleted
        errors.extend(node_errors)

    def clean_node_addresses(
        item: tuple[str, dict[str, Any]],
    ) -> tuple[int, list[str]]:
        _, node = item
        node_errors: list[str] = []
        try:
            _assert_netns(node)
            current_addresses = _namespace_ipv4_local_addresses(node)
        except RuntimeError as error:
            return 0, [str(error)]
        node_address = IPv4Address(node["node_address"])
        zone_addresses = tuple(
            IPv4Address(value) for value in node.get("zone_addresses", ())
        )
        address_commands = []
        node_address_present = (
            node.get("node_address_owned") is True
            and node_address in current_addresses
        )
        if node_address_present:
            address_commands.append(
                f"address del {node['node_address']}/32 dev lo"
            )
        address_commands.extend(
            f"address del {address}/32 dev lo"
            for address in zone_addresses
            if address in current_addresses
        )
        if address_commands:
            result = _in_netns(
                int(node["pid"]),
                ["ip", "-force", "-batch", "-"],
                input_text="\n".join(address_commands) + "\n",
                tolerate_failure=True,
            )
            if result.returncode != 0 and result.stderr:
                node_errors.append(result.stderr.strip())
        deleted = 0
        try:
            remaining = _namespace_ipv4_local_addresses(node)
            if (
                node.get("node_address_owned") is True
                and node_address in remaining
            ):
                node_errors.append(
                    "Runner-owned node address remains after cleanup: "
                    f"node={node['key']}, address={node_address}"
                )
            elif node_address_present:
                deleted = 1
            for address in zone_addresses:
                if address in remaining:
                    node_errors.append(
                        "Runner-owned Zone address remains after cleanup: "
                        f"node={node['key']}, address={address}"
                    )
        except RuntimeError as error:
            node_errors.append(str(error))
        return deleted, node_errors

    deleted_node_addresses = 0
    for deleted, node_errors in _parallel_map(
        request, node_items, clean_node_addresses
    ):
        deleted_node_addresses += deleted
        errors.extend(node_errors)

    if root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            except OSError as error:
                errors.append(f"cannot remove {path}: {error}")
        try:
            root.rmdir()
        except OSError as error:
            errors.append(f"cannot remove {root}: {error}")
    try:
        _PIN_PARENT.rmdir()
    except FileNotFoundError:
        pass
    except OSError as error:
        if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            errors.append(f"cannot remove {_PIN_PARENT}: {error}")

    if errors and not tolerate_errors:
        raise RuntimeError("Runner cleanup left residue: " + "; ".join(errors))
    return {
        "deleted_connections": deleted_connections,
        "deleted_node_addresses": deleted_node_addresses,
        "pin_root_removed": not root.exists(),
        "errors": errors,
    }


def main() -> int:
    global _ACTIVE_METRICS
    if len(sys.argv) != 2 or sys.argv[1] not in {
        "audit_node_addresses",
        "initialize",
        "prepare_boundary",
        "activate_boundary",
        "reclaim_generation",
        "reclaim_resources",
        "validate",
        "cleanup",
    }:
        print(
            json.dumps(
                {
                    "error": (
                        "unknown private Runner command action"
                    )
                }
            )
        )
        return 2
    try:
        with _METRICS_LOCK:
            _ACTIVE_METRICS = {
                "external_processes": 0,
                "namespace_processes": 0,
                "interface_identity_queries": 0,
                "bpf_syscalls": 0,
                "bpf_map_update_syscalls": 0,
                "bpf_map_delete_syscalls": 0,
                "bpf_map_delete_batch_syscalls": 0,
                "bpf_map_delete_operations": 0,
                "worker_limit": 1,
                "worker_high_water": 0,
                "active_workers": 0,
                "parallel_phase_count": 0,
                "parallel_task_count": 0,
                "batch_sizes": [],
            }
        request = json.load(sys.stdin)
        if sys.argv[1] == "audit_node_addresses":
            result = _audit_node_addresses(request)
        elif sys.argv[1] == "initialize":
            result = _initialize(request)
        elif sys.argv[1] == "prepare_boundary":
            result = _prepare_boundary(request)
        elif sys.argv[1] == "activate_boundary":
            result = _activate_boundary(request)
        elif sys.argv[1] == "reclaim_generation":
            result = _reclaim_generation(request)
        elif sys.argv[1] == "reclaim_resources":
            result = _reclaim_resources(request)
        elif sys.argv[1] == "validate":
            result = _validate(request)
        else:
            result = _cleanup(request)
        with _METRICS_LOCK:
            metrics = dict(_ACTIVE_METRICS or {})
            _ACTIVE_METRICS = None
        metrics.pop("active_workers", None)
        batch_sizes = tuple(int(value) for value in metrics.pop("batch_sizes", ()))
        metrics["batch_count"] = len(batch_sizes)
        metrics["batch_operation_count"] = sum(batch_sizes)
        metrics["batch_size_min"] = min(batch_sizes, default=0)
        metrics["batch_size_max"] = max(batch_sizes, default=0)
        metrics["batch_size_distribution"] = {
            label: sum(lower <= value <= upper for value in batch_sizes)
            for label, lower, upper in (
                ("1", 1, 1),
                ("2-4", 2, 4),
                ("5-16", 5, 16),
                ("17-64", 17, 64),
                ("65-256", 65, 256),
                ("257+", 257, 2**63 - 1),
            )
        }
        result = {**result, "execution_metrics": metrics}
    except BaseException as error:
        with _METRICS_LOCK:
            metrics = dict(_ACTIVE_METRICS or {})
            _ACTIVE_METRICS = None
        metrics.pop("active_workers", None)
        print(
            json.dumps(
                {
                    "error": str(error),
                    "error_type": type(error).__name__,
                    "traceback": traceback.format_exc(),
                    "execution_metrics": metrics,
                }
            )
        )
        return 1
    print(json.dumps({"status": "ok", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
