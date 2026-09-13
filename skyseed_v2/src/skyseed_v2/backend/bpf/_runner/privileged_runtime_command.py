"""Serve one retained privileged kernel execution channel for BpfRunner."""

from __future__ import annotations

import ctypes
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import errno
from ipaddress import IPv4Address, IPv4Network, ip_interface, ip_network
import json
import os
from pathlib import Path
import re
import resource
import socket
import struct
import subprocess
import sys
import traceback
from threading import Barrier, Lock, local
from time import monotonic, monotonic_ns
from typing import Any


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skyseed_v2.backend.bpf._runner.kernel_netlink import _RouteSocket
from skyseed_v2.backend.bpf._runner import kernel_netlink as _netlink

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
from skyseed_v2.backend.bpf._runner.process_incarnation import (
    _capture_process_incarnations,
    _read_process_start_time_ticks,
)


_BPF_MAP_CREATE = 0
_BPF_MAP_LOOKUP_ELEM = 1
_BPF_MAP_UPDATE_ELEM = 2
_BPF_MAP_DELETE_ELEM = 3
_BPF_MAP_UPDATE_BATCH = 26
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
_BPF_UPDATE_BATCH_SIZE = 1024


class _InitializationFailure(RuntimeError):
    """Retain ownership when failed startup could not finish its own cleanup."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


class _PrePublicationActivationFailure(RuntimeError):
    """Carry cleanup ownership after a failed pre-publication mutation."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


class _PostPublicationActivationFailure(RuntimeError):
    """Report failure after BPF forwarding publication without pseudo-rollback."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def _worker_count(request: dict[str, Any], item_count: int) -> int:
    value = request.get("worker_count", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Runner privileged worker_count must be an int")
    if value <= 0 or value > 64:
        raise ValueError("Runner privileged worker_count must be between 1 and 64")
    return min(value, max(1, item_count))


def _parallel_map(request: dict[str, Any], items: list[Any], operation) -> list[Any]:
    """Bound independent startup namespace work; formal Main never calls this."""
    if not items:
        return []
    workers = _worker_count(request, len(items))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="skyseed-bpf-prepare") as pool:
        return list(pool.map(operation, items))


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


_BPF_CALLS = local()
_JOINED_NETLINK_CALLS = 0


def _bpf(command: int, attributes: ctypes.Structure) -> int:
    if command in (1, 2, 3, 26):
        counts = getattr(_BPF_CALLS, "counts", None)
        if counts is None:
            counts = _BPF_CALLS.counts = {1: 0, 2: 0, 3: 0, 26: 0}
        counts[command] += 1
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


def _kernel_call_counts() -> dict[str, int]:
    counts = getattr(_BPF_CALLS, "counts", {1: 0, 2: 0, 3: 0, 26: 0})
    return {"netlink_request_calls": _netlink._request_count() + _JOINED_NETLINK_CALLS,
            "bpf_lookup_calls": counts[1], "bpf_update_calls": counts[2],
            "bpf_delete_calls": counts[3], "bpf_update_batch_calls": counts[26]}


def _timed_call(timings: dict[str, list[int]] | None, category: str, operation, *args, **kwargs):
    """Accumulate attempted call wall time, including a failing call; no per-call records."""
    if timings is None:
        return operation(*args, **kwargs)
    started = monotonic_ns()
    try:
        return operation(*args, **kwargs)
    finally:
        elapsed = monotonic_ns() - started
        value = timings.setdefault(category, [0, 0])
        value[0] += elapsed
        value[1] += 1


def _timing_records(timings: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    return {name: {"duration_seconds": value[0] / 1e9, "operation_count": value[1]}
            for name, value in timings.items()}


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


def _update_map_batch(
    file_descriptor: int,
    entries: tuple[tuple[int | tuple[int, int], int], ...],
) -> int:
    """Update one exact bounded key/value set with one batch syscall."""

    if not entries:
        return 0
    if len(entries) > _BPF_UPDATE_BATCH_SIZE:
        raise ValueError("BPF batch update exceeds the bounded chunk size")
    normalized_keys = tuple(
        (key,) if isinstance(key, int) else tuple(key) for key, _ in entries
    )
    key_width = len(normalized_keys[0])
    if key_width not in {1, 2} or any(
        len(key) != key_width for key in normalized_keys
    ):
        raise ValueError("BPF batch-update keys must have one fixed width")
    key_buffer = ctypes.create_string_buffer(
        b"".join(
            struct.pack(f"={key_width}I", *key) for key in normalized_keys
        )
    )
    value_buffer = ctypes.create_string_buffer(
        b"".join(struct.pack("=I", int(value)) for _, value in entries)
    )
    attributes = _BpfMapBatch(
        keys=ctypes.addressof(key_buffer),
        values=ctypes.addressof(value_buffer),
        count=len(entries),
        map_fd=file_descriptor,
        elem_flags=_BPF_ANY,
    )
    _bpf(_BPF_MAP_UPDATE_BATCH, attributes)
    updated = int(attributes.count)
    if updated != len(entries):
        raise RuntimeError(
            "BPF batch-update did not process every requested entry: "
            f"requested={len(entries)}, updated={updated}"
        )
    return updated


def _update_map_entries(
    file_descriptor: int,
    entries: tuple[tuple[int | tuple[int, int], int], ...],
    *,
    batch_supported: bool,
) -> int:
    """Apply deterministic chunks or the capability-tested baseline path."""

    if not batch_supported:
        for key, value in entries:
            _update_map(file_descriptor, key, value)
        return len(entries)
    updated = 0
    for offset in range(0, len(entries), _BPF_UPDATE_BATCH_SIZE):
        updated += _update_map_batch(
            file_descriptor,
            entries[offset : offset + _BPF_UPDATE_BATCH_SIZE],
        )
    return updated


def _probe_batch_update_support() -> bool:
    """Test the update-batch command on one disposable unpinned map."""

    file_descriptor = _create_map(
        "skyseed_batch", key_size=8, max_entries=1
    )
    try:
        try:
            _update_map_batch(file_descriptor, (((0, 0), 1),))
        except OSError as error:
            if error.errno in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
                return False
            raise
        return True
    finally:
        os.close(file_descriptor)


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


def _update_u64_map(file_descriptor: int, key: int, value: int) -> None:
    key_buffer, value_buffer = _key_buffer(key), ctypes.c_uint64(value)
    _bpf(_BPF_MAP_UPDATE_ELEM, _BpfMapElement(
        map_fd=file_descriptor, key=ctypes.addressof(key_buffer),
        value=ctypes.addressof(value_buffer), flags=_BPF_ANY))


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
    ingress_binding_map: int,
    egress_binding_map: int,
    *,
    telemetry_ring_map: int | None = None,
    telemetry_loss_map: int | None = None,
    node_slot: int | None = None,
    generation_versions_map: int | None = None,
    generation_readers_map: int | None = None,
) -> bytes:
    telemetry_values = (telemetry_ring_map, telemetry_loss_map, node_slot)
    telemetry_enabled = all(value is not None for value in telemetry_values)
    if telemetry_enabled != any(value is not None for value in telemetry_values):
        raise ValueError("tc telemetry maps and node_slot must be supplied together")
    program = _Assembler()
    program.emit(0xBF, destination=6, source=1)  # r6 = skb
    if generation_readers_map is not None:
        program.emit(0x7A, destination=10, offset=-88, immediate=0)
        program.emit(0x7A, destination=10, offset=-120, immediate=0)
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

    program.label("acquire_generation")
    program.emit(0xB7, destination=4, immediate=0)
    program.emit(0x63, destination=10, source=4, offset=-52)
    program.load_map(1, active_generation_map)
    program.emit(0xBF, destination=2, source=10)
    program.emit(0x07, destination=2, immediate=-52)
    program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
    program.jump(0x15, destination=0, immediate=0, label="no_active_generation")
    program.emit(0x61, destination=9, source=0, offset=0)
    if generation_readers_map is not None:
        # Pin a reader before using generation tables, then re-read publication.
        # A reader arriving after retirement observes a missing counter or a
        # changed publication and never touches the retired tables.
        program.emit(0x7B, destination=10, source=0, offset=-104)
        program.emit(0x63, destination=10, source=9, offset=-112)
        program.load_map(1, generation_readers_map)
        program.emit(0xBF, destination=2, source=10)
        program.emit(0x07, destination=2, immediate=-112)
        program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
        program.jump(0x15, destination=0, immediate=0, label="retry_generation")
        program.emit(0x7B, destination=10, source=0, offset=-88)
        program.emit(0xB7, destination=1, immediate=1)
        program.emit(0xDB, destination=0, source=1)
        program.emit(0x79, destination=4, source=10, offset=-104)
        program.emit(0x61, destination=4, source=4)
        program.jump(0x5D, destination=4, source=9, label="retry_generation")
    if telemetry_enabled:
        program.emit(0x63, destination=10, source=9, offset=-36)
    if generation_versions_map is not None:
        program.emit(0x63, destination=10, source=9, offset=-112)
        program.load_map(1, generation_versions_map)
        program.emit(0xBF, destination=2, source=10)
        program.emit(0x07, destination=2, immediate=-112)
        program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
        program.jump(0x15, destination=0, immediate=0, label="no_active_generation")
        program.emit(0x61, destination=9, source=0)

    program.emit(0x61, destination=4, source=6, offset=40)  # skb->ifindex
    program.emit(0x63, destination=10, source=4, offset=-76)
    program.load_map(1, ingress_binding_map)
    program.emit(0xBF, destination=2, source=10)
    program.emit(0x07, destination=2, immediate=-76)
    program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
    program.jump(0x15, destination=0, immediate=0, label="inactive_ingress")
    program.emit(0x61, destination=4, source=0, offset=0)
    program.jump(0x15, destination=4, immediate=0, label="inactive_ingress")
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
    program.emit(0x63, destination=10, source=1, offset=-80)
    program.load_map(1, egress_binding_map)
    program.emit(0xBF, destination=2, source=10)
    program.emit(0x07, destination=2, immediate=-80)
    program.emit(0x85, immediate=_BPF_FUNC_MAP_LOOKUP_ELEM)
    program.jump(0x15, destination=0, immediate=0, label="no_route")
    program.emit(0x61, destination=1, source=0, offset=0)
    program.jump(0x15, destination=1, immediate=0, label="no_route")
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

    def release_reader(label: str) -> None:
        if generation_readers_map is None:
            return
        program.emit(0x79, destination=4, source=10, offset=-88)
        program.jump(0x15, destination=4, immediate=0, label=label)
        program.emit(0xB7, destination=5, immediate=-1)
        program.emit(0xDB, destination=4, source=5)
        program.emit(0x7A, destination=10, offset=-88, immediate=0)
        program.label(label)

    if generation_readers_map is not None:
        program.label("retry_generation")
        release_reader("retry_released")
        program.emit(0x79, destination=4, source=10, offset=-120)
        program.emit(0x07, destination=4, immediate=1)
        program.emit(0x7B, destination=10, source=4, offset=-120)
        program.jump(_BPF_JMP_JLT_IMMEDIATE, destination=4, immediate=3, label="acquire_generation")
        program.jump(0x05, destination=0, label="no_active_generation")

    program.label("redirect")
    release_reader("redirect_released")
    if telemetry_enabled:
        program.emit(0x61, destination=1, source=10, offset=-28)
    program.emit(0xB7, destination=2, immediate=0)
    program.emit(0x85, immediate=_BPF_FUNC_REDIRECT)
    program.emit(0x95)

    program.label("drop")
    release_reader("drop_released")
    program.emit(0xB7, destination=0, immediate=_TC_ACT_SHOT)
    program.emit(0x95)
    program.label("pass")
    release_reader("pass_released")
    program.emit(0xB7, destination=0, immediate=_TC_ACT_OK)
    program.emit(0x95)
    return program.finish()


def _load_program(
    active_generation_map: int,
    active_map: int,
    route_map: int,
    ingress_binding_map: int,
    egress_binding_map: int,
    *,
    telemetry_ring_map: int | None = None,
    telemetry_loss_map: int | None = None,
    node_slot: int | None = None,
    generation_versions_map: int | None = None,
    generation_readers_map: int | None = None,
) -> int:
    instructions = _tc_program(
        active_generation_map,
        active_map,
        route_map,
        ingress_binding_map,
        egress_binding_map,
        telemetry_ring_map=telemetry_ring_map,
        telemetry_loss_map=telemetry_loss_map,
        node_slot=node_slot,
        generation_versions_map=generation_versions_map,
        generation_readers_map=generation_readers_map,
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
    expected_start_time = int(node["process_start_time_ticks"])
    actual_start_time = _read_process_start_time_ticks(pid)
    if actual_start_time != expected_start_time:
        raise RuntimeError(
            f"node process incarnation is stale: pid={pid}, "
            f"expected={expected_start_time}, actual={actual_start_time}"
        )
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
    return _LIVE_NODES[node["key"]]["netlink"].ipv4_address_facts()


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
    address_networks = _namespace_ipv4_address_networks(node)
    return address_networks, _LIVE_NODES[node["key"]]["netlink"].ipv4_route_networks()


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
                    f"{source} during Preparation: node={node['key']}, "
                    f"selected={selected_range}, observed={collision}"
                )
        return len(address_networks), len(route_networks)

    observed = _parallel_map(
        request, list(nodes), audit_node
    )
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


_LIVE_NODES: dict[str, dict[str, Any]] = {}
_GENERATION_FD = -1
_GENERATION_READERS_FD = -1
_GENERATIONS: dict[int, dict[str, int]] = {}
_VERSIONS: dict[tuple[str, int], dict[str, Any]] = {}
_READY_GENERATIONS: set[int] = set()
_OWNED_PIN_ROOT: Path | None = None
_OWNED_NODE_ADDRESSES: set[str] = set()
_INITIALIZATION_REQUESTED = False
_FOUNDATION_OUTCOME = "no-mutation"


_RETIREMENT_POOL: ThreadPoolExecutor | None = None
_RETIREMENT_WORKERS = 0
_OWNED_RETIREMENTS: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}


def _start_retirement_workers(count: int) -> None:
    global _RETIREMENT_POOL, _RETIREMENT_WORKERS
    if _RETIREMENT_POOL is not None:
        raise RuntimeError("retirement workers already initialized")
    _RETIREMENT_WORKERS = count
    _RETIREMENT_POOL = ThreadPoolExecutor(max_workers=count, thread_name_prefix="skyseed-bpf-retire")
    ready = Barrier(count + 1)
    futures = [_RETIREMENT_POOL.submit(ready.wait) for _ in range(count)]
    ready.wait()
    for future in futures:
        future.result()


def _forget_connection(connection):
    key = (connection["retirement_node"], connection["retirement_group"])
    owned = _OWNED_RETIREMENTS[key]
    owned.pop(connection["connection"])
    if not owned:
        del _OWNED_RETIREMENTS[key]
    for side in ("a", "b"):
        _LIVE_NODES[connection[f"node_{side}"]]["indexes"].pop(connection[f"interface_{side}"], None)


def _retire_batches(batches, *, cleanup=False):
    """Join bounded namespace jobs; each retained socket has one ACK reader."""
    global _JOINED_NETLINK_CALLS
    started = monotonic_ns()
    by_node = {}
    for batch in batches:
        key = (batch["key"], batch["group"])
        owned = _OWNED_RETIREMENTS.get(key, {})
        if {value["connection"] for value in batch["connections"]} != owned.keys():
            raise RuntimeError("retirement batch differs from the exact owned cohort")
        by_node.setdefault(batch["key"], []).append(batch)
    result = {"retired_pairs": 0, "deletion_requests": 0, "grouped_batches": 0,
              "task_duration_seconds_sum": 0.0, "errors": [],
              "workers": min(_RETIREMENT_WORKERS, len(by_node))}

    def retire_node(item):
        key, values = item
        task_started = monotonic_ns()
        calls = _netlink._request_count()
        confirmed = []
        errors = []
        attempted = 0
        for batch in values:
            attempted += 1
            try:
                _LIVE_NODES[key]["netlink"].delete_group(batch["group"])
            except OSError as error:
                if not cleanup or error.errno not in (errno.ENODEV, errno.ENOENT):
                    errors.append(f"{key} group {batch['group']}: {error}")
                    continue
            except Exception as error:
                errors.append(f"{key} group {batch['group']}: {error}")
                continue
            confirmed.append(batch)
        return confirmed, errors, attempted, _netlink._request_count() - calls, monotonic_ns() - task_started

    pending = set()
    items = iter(by_node.items())
    if by_node and _RETIREMENT_POOL is None:
        raise RuntimeError("retirement workers were not initialized in Preparation")
    for _ in range(min(_RETIREMENT_WORKERS, len(by_node))):
        pending.add(_RETIREMENT_POOL.submit(retire_node, next(items)))
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            confirmed, errors, attempted, calls, duration = future.result()
            _JOINED_NETLINK_CALLS += calls
            result["deletion_requests"] += attempted
            result["grouped_batches"] += len(confirmed)
            result["task_duration_seconds_sum"] += duration / 1e9
            result["errors"].extend(errors)
            for batch in confirmed:
                owned = _OWNED_RETIREMENTS[(batch["key"], batch["group"])]
                for connection in tuple(owned.values()):
                    _forget_connection(connection)
                    result["retired_pairs"] += 1
            item = next(items, None)
            if item is not None:
                pending.add(_RETIREMENT_POOL.submit(retire_node, item))
    result["wall_seconds"] = (monotonic_ns() - started) / 1e9
    return result


def _setns(fd: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setns(ctypes.c_int(fd), ctypes.c_int(0x40000000)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _retain_namespaces(nodes: tuple[dict[str, Any], ...]) -> None:
    if _LIVE_NODES:
        raise RuntimeError("namespace foundation is already retained")
    original = os.open("/proc/self/ns/net", os.O_RDONLY | os.O_CLOEXEC)
    try:
        for node in nodes:
            _assert_netns(node)
            fd = os.open(f"/proc/{int(node['pid'])}/ns/net", os.O_RDONLY | os.O_CLOEXEC)
            try:
                if os.fstat(fd).st_ino != int(node["netns_inode"]):
                    raise RuntimeError("namespace changed during Preparation")
                _setns(fd)
                channel = _RouteSocket()
                _LIVE_NODES[node["key"]] = {
                    **node, "namespace_fd": fd, "netlink": channel,
                    "indexes": {}, "maps": {},
                }
            except BaseException:
                os.close(fd)
                raise
            finally:
                _setns(original)
    except BaseException:
        _close_foundation()
        raise
    finally:
        os.close(original)


def _close_foundation(timings=None) -> None:
    global _GENERATION_FD, _GENERATION_READERS_FD, _RETIREMENT_POOL, _RETIREMENT_WORKERS
    if _RETIREMENT_POOL is not None:
        _timed_call(timings, "retirement_worker_shutdown", _RETIREMENT_POOL.shutdown, wait=True)
        _RETIREMENT_POOL = None
        _RETIREMENT_WORKERS = 0
    for node in _LIVE_NODES.values():
        for fd in node["maps"].values():
            _timed_call(timings, "bpf_descriptor_close", os.close, fd)
        _timed_call(timings, "retained_socket_close", node["netlink"].close)
        _timed_call(timings, "retained_namespace_fd_close", os.close, node["namespace_fd"])
    _LIVE_NODES.clear()
    _GENERATIONS.clear()
    _VERSIONS.clear()
    _READY_GENERATIONS.clear()
    if _GENERATION_READERS_FD >= 0:
        _timed_call(timings, "bpf_descriptor_close", os.close, _GENERATION_READERS_FD)
        _GENERATION_READERS_FD = -1
    if _GENERATION_FD >= 0:
        _timed_call(timings, "bpf_descriptor_close", os.close, _GENERATION_FD)
        _GENERATION_FD = -1


def _reserve_foundation_descriptors(node_count: int, worker_count: int) -> None:
    # Per node: namespace FD, route socket, and six retained BPF objects.
    # Concurrent loaders use at most six transient FDs each. Leave room for
    # stdio, shared maps, namespace switching, and ordinary file reads.
    required = 8 * node_count + 6 * worker_count + 64
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and required > hard:
        raise RuntimeError(
            f"Runner foundation needs {required} file descriptors; hard limit is {hard}"
        )
    if soft != resource.RLIM_INFINITY and soft < required:
        resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))


def _initialize(request: dict[str, Any]) -> dict[str, Any]:
    global _GENERATION_FD, _GENERATION_READERS_FD, _OWNED_PIN_ROOT, _INITIALIZATION_REQUESTED, _FOUNDATION_OUTCOME
    if _INITIALIZATION_REQUESTED:
        raise RuntimeError("Runner foundation initialization is one-shot")
    _INITIALIZATION_REQUESTED = True
    root = _pin_root(request["pin_root"])
    nodes = tuple(request["nodes"])
    try:
        if root.exists():
            raise RuntimeError(f"Runner pin root already exists: {root}")
        _reserve_foundation_descriptors(len(nodes), _worker_count(request, len(nodes)))
        _retain_namespaces(nodes)
        group_end = request["retirement_group_end"]
        if type(group_end) is not int or not _netlink._RETIREMENT_GROUP_BASE < group_end <= 0xFFFFFFFF:
            raise ValueError("invalid finite retirement group range")
        for live in _LIVE_NODES.values():
            live["loopback"] = live["netlink"].initial_links(group_end)
        _start_retirement_workers(_worker_count(request, len(nodes)))
        batch_update_supported = _probe_batch_update_support()
        collision_audit_started = monotonic()
        collision_audit = _audit_node_addresses(request)
        collision_audit_seconds = monotonic() - collision_audit_started
        root.mkdir(parents=True)
    except BaseException as error:
        _close_foundation()
        raise _InitializationFailure(str(error), evidence={
            "initialization_outcome": "no-mutation",
        }) from error
    _OWNED_PIN_ROOT = root
    _FOUNDATION_OUTCOME = "partial"
    installed_node_keys = _OWNED_NODE_ADDRESSES
    installed_node_keys_lock = Lock()
    active_generation_fd = -1
    generation_readers_fd = -1
    telemetry_ring_fd = -1
    telemetry_loss_fd = -1
    telemetry_enabled = request.get("telemetry_enabled") is True
    try:
        shared_scaffolding_started = monotonic()
        active_generation_fd = _create_map(
            "active_gen", key_size=4, max_entries=1, map_type=_BPF_MAP_TYPE_ARRAY
        )
        _pin_object(active_generation_fd, root / "active_generation")
        _update_map(
            active_generation_fd, 0, int(request["inactive_generation"])
        )
        version_capacity = request.get("resident_generation_capacity")
        if request.get("continuation_enabled") is True:
            generation_readers_fd = _create_map(
                "gen_readers", key_size=4, value_size=8, max_entries=int(version_capacity))
            _pin_object(generation_readers_fd, root / "generation_readers")
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
            live = _LIVE_NODES[node["key"]]
            channel = live["netlink"]
            channel.set_link(live["loopback"], up=True)
            with installed_node_keys_lock:
                installed_node_keys.add(node["key"])
            # The collision audit established absence. Retain ownership before
            # sending the mutation, including a lost or failed ACK.
            channel.address(live["loopback"], node["node_address"])
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
            ingress_fd = _create_map("ingress_bind", key_size=4, max_entries=int(node["bindings_capacity"]))
            egress_fd = _create_map("egress_bind", key_size=4, max_entries=int(node["bindings_capacity"]))
            program_fd = -1
            versions_fd = -1
            try:
                if version_capacity is not None:
                    versions_fd = _create_map("gen_versions", key_size=4, max_entries=int(version_capacity))
                    _pin_object(versions_fd, node_root / "generation_versions")
                _pin_object(active_fd, active_path)
                _pin_object(route_fd, route_path)
                _pin_object(ingress_fd, node_root / "ingress_bindings")
                _pin_object(egress_fd, node_root / "egress_bindings")
                if telemetry_enabled:
                    program_fd = _load_program(
                        active_generation_fd,
                        active_fd,
                        route_fd,
                        ingress_fd,
                        egress_fd,
                        telemetry_ring_map=telemetry_ring_fd,
                        telemetry_loss_map=telemetry_loss_fd,
                        node_slot=int(node["node_slot"]),
                        generation_versions_map=None if versions_fd < 0 else versions_fd,
                        generation_readers_map=None if generation_readers_fd < 0 else generation_readers_fd,
                    )
                else:
                    program_fd = _load_program(
                        active_generation_fd,
                        active_fd,
                        route_fd,
                        ingress_fd,
                        egress_fd,
                        generation_versions_map=None if versions_fd < 0 else versions_fd,
                        generation_readers_map=None if generation_readers_fd < 0 else generation_readers_fd,
                    )
                _pin_object(program_fd, program_path)
            finally:
                for file_descriptor in (program_fd, route_fd, active_fd, ingress_fd, egress_fd, versions_fd):
                    if file_descriptor >= 0:
                        os.close(file_descriptor)
            return {
                "key": node["key"],
                "active_links": int(node["active_links_capacity"]),
                "routes": int(node["routes_capacity"]),
            }

        node_foundation_started = monotonic()
        map_capacities = _parallel_map(
            request,
            list(nodes),
            initialize_node,
        )
        node_foundation_seconds = monotonic() - node_foundation_started
        _GENERATION_FD = _open_pinned(root / "active_generation")
        if generation_readers_fd >= 0:
            _GENERATION_READERS_FD = _open_pinned(root / "generation_readers")
        for node in _LIVE_NODES.values():
            for name in ("active_links", "routes", "ingress_bindings", "egress_bindings", "tc_ingress"):
                node["maps"][name] = _open_pinned(root / node["pin_component"] / name)
            if version_capacity is not None:
                node["maps"]["generation_versions"] = _open_pinned(root / node["pin_component"] / "generation_versions")
        return {
            "pin_root": str(root),
            "node_count": len(nodes),
            "active_generation": int(request["inactive_generation"]),
            "shared_active_generation_map": True,
            "bpf_batch_update_supported": batch_update_supported,
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
    except BaseException as error:
        try:
            cleanup_result = _cleanup(
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
        except BaseException as cleanup_error:
            cleanup_result = {"errors": [str(cleanup_error)]}
        raise _InitializationFailure(str(error), evidence={
            "initialization_outcome": "partial" if cleanup_result.get("errors") else "cleaned",
            "installed_node_addresses": sorted(installed_node_keys),
            "cleanup_errors": cleanup_result.get("errors", []),
        }) from error
    finally:
        for file_descriptor in (
            telemetry_loss_fd,
            telemetry_ring_fd,
            active_generation_fd,
            generation_readers_fd,
        ):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _capture_incarnations(request: dict[str, Any]) -> dict[str, Any]:
    """Capture one complete PID set inside the privileged host-PID context."""

    raw_pids = request.get("pids")
    if not isinstance(raw_pids, list) or not raw_pids:
        raise ValueError("Runner privileged incarnation request needs a PID list")
    pids = tuple(raw_pids)
    values = _capture_process_incarnations(pids)
    if len(values) != len(pids):
        raise RuntimeError("Runner privileged incarnation capture is incomplete")
    return {
        "incarnations": values,
        "requested_pid_count": len(pids),
        "captured_pid_count": len(values),
        "capture_mode": "single-privileged-batch",
    }


def _prewrite_generation(request: dict[str, Any]) -> dict[str, Any]:
    generation = int(request["generation"])
    active_generation = _lookup_map(_GENERATION_FD, 0)
    if active_generation != 0xFFFFFFFF and generation <= active_generation:
        raise RuntimeError("prewrite must target a strictly future generation")
    records = request["generation_nodes"]
    shared = bool(records) and "version" in records[0]
    if shared and (len(records) > 64 or sum(
        len(record["active_ports"]) + len(record["routes"]) for record in records
    ) > 2048):
        raise ValueError("prewrite work unit exceeds the bounded channel budget")
    if shared and generation not in _GENERATIONS:
        _GENERATIONS[generation] = {}
        if _GENERATION_READERS_FD >= 0:
            _update_u64_map(_GENERATION_READERS_FD, generation, 0)
    active_count = route_count = reused = new_versions = 0
    for record in records:
        node_key = record["key"]
        maps = _LIVE_NODES[node_key]["maps"]
        version = int(record.get("version", generation))
        value = None
        if shared:
            identity = node_key, version
            value = _VERSIONS.get(identity)
            if node_key not in _GENERATIONS[generation]:
                if value is None:
                    if version != generation:
                        raise RuntimeError("shared version is not resident")
                    value = _VERSIONS[identity] = {"active": [], "routes": [], "references": 0, "complete": False}
                    new_versions += 1
                elif version != generation:
                    if not value["complete"] or record["active_ports"] or record["routes"]:
                        raise RuntimeError("shared version is incomplete or being overwritten")
                    reused += 1
                value["references"] += 1
                _GENERATIONS[generation][node_key] = version
                _update_map(maps["generation_versions"], generation, version)
            elif _GENERATIONS[generation][node_key] != version or value["complete"]:
                raise RuntimeError("generation node version cannot be replaced")
        active = [((version, int(port)), 1) for port in record["active_ports"]]
        routes = [((version, _ip_key(route["destination"])), int(route["logical_port_id"]))
                  for route in record["routes"]]
        active_count += _update_map_entries(maps["active_links"], active,
                            batch_supported=bool(request["bpf_batch_update_supported"]))
        route_count += _update_map_entries(maps["routes"], routes,
                            batch_supported=bool(request["bpf_batch_update_supported"]))
        if value is not None and version == generation:
            value["active"].extend(key for key, _ in active)
            value["routes"].extend(key for key, _ in routes)
            value["complete"] = record.get("complete", True)
    if shared and request.get("seal"):
        nodes = _GENERATIONS[generation]
        if nodes.keys() != _LIVE_NODES.keys() or any(
            not _VERSIONS[key, version]["complete"] for key, version in nodes.items()
        ):
            raise RuntimeError("generation prewrite is incomplete")
        _READY_GENERATIONS.add(generation)
    return {"preloaded_active_links": active_count, "preloaded_routes": route_count,
            "node_versions_written": new_versions, "node_versions_reused": reused}


def _reclaim_generations(request: dict[str, Any]) -> dict[str, Any]:
    """Retire a bounded number of entries after publication and reader quiescence."""
    if _GENERATION_READERS_FD < 0:
        raise RuntimeError("generation reclaim requires instrumented in-flight readers")
    budget = request.get("maximum_entries", 1024)
    if type(budget) is not int or not 1 <= budget <= 2048:
        raise ValueError("invalid reclaim work-unit budget")
    active = _lookup_map(_GENERATION_FD, 0)
    if active is None or active == 0xFFFFFFFF:
        return {"reclaimed_generations": [], "deleted_entries": 0, "reader_wait": False}
    cutoff = min(int(request["before_generation"]), active)
    deleted = units = 0
    reclaimed = []
    reader_wait = False
    for generation in sorted(g for g in _GENERATIONS if g < cutoff):
        if _lookup_u64_map(_GENERATION_READERS_FD, generation) != 0:
            reader_wait = True
            continue
        nodes = _GENERATIONS[generation]
        while nodes and units < budget:
            key = next(iter(nodes))
            version = nodes[key]
            value = _VERSIONS[key, version]
            maps = _LIVE_NODES[key]["maps"]
            if value["references"] == 1:
                for field, map_name in (("active", "active_links"), ("routes", "routes")):
                    entries = value[field]
                    while entries and units < budget:
                        deleted += int(_delete_map(maps[map_name], entries[-1]))
                        entries.pop()
                        units += 1
                if value["active"] or value["routes"] or units >= budget:
                    break
                del _VERSIONS[key, version]
            else:
                value["references"] -= 1
            deleted += int(_delete_map(maps["generation_versions"], generation))
            del nodes[key]
            units += 1
        if not nodes:
            _delete_map(_GENERATION_READERS_FD, generation)
            del _GENERATIONS[generation]
            _READY_GENERATIONS.discard(generation)
            reclaimed.append(generation)
        if units >= budget:
            break
    return {"reclaimed_generations": reclaimed, "deleted_entries": deleted,
            "work_units": units, "reader_wait": reader_wait,
            "active_generation": active, "resident_generations": len(_GENERATIONS),
            "resident_node_versions": len(_VERSIONS)}


def _activate_boundary(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one prepared delta through retained namespace sockets and BPF FDs."""
    activation_started_ns = monotonic_ns()
    generation = int(request["generation"])
    expected = int(request["expected_active_generation"])
    changed = request["generation_changed"]
    if _GENERATIONS and changed and generation not in _READY_GENERATIONS:
        raise RuntimeError("Main cannot publish an unready generation")
    connections = request["connections"]
    batches = request["retirement_batches"]
    removed = [connection for batch in batches for connection in batch["connections"]]
    operations = {node["key"]: node for node in request["operation_nodes"]}
    new_endpoints: dict[str, list[tuple[str, int, dict[str, Any]]]] = {}
    old_endpoints: dict[str, list[tuple[str, int]]] = {}
    for connection in connections:
        for side in ("a", "b"):
            new_endpoints.setdefault(connection[f"node_{side}"], []).append((
                connection[f"interface_{side}"], connection[f"logical_port_{side}"],
                connection[f"parameters_{side}"],
            ))
    for connection in removed:
        for side in ("a", "b"):
            old_endpoints.setdefault(connection[f"node_{side}"], []).append((
                connection[f"interface_{side}"], connection[f"logical_port_{side}"],
            ))
    created = []
    deleted = 0
    identities = []
    published = False
    mutation_started = False
    complete = False
    phase = "pre-publication"
    timings = {}
    switch = {}
    anchor = monotonic_ns()
    detail: dict[str, list[int]] = {}
    retirement = None

    def evidence() -> dict[str, Any]:
        return {
            "boundary_timestamp": request["boundary_timestamp"],
            "last_completed_active_frontier": request.get("last_completed_active_frontier"),
            "previous_generation": expected, "active_generation": generation if published else expected,
            "generation_publication_occurred": published, "generation_publications": int(published),
            "generation_reused": not changed, "target_transition_complete": complete,
            "live_mutation_started": mutation_started,
            "created_connections": len(created), "deleted_connections": deleted,
            "interface_identities": identities,
            "withdrawn_logical_ports": [port for values in old_endpoints.values() for _, port in values],
            "activation_anchor_monotonic_ns": anchor,
            "activation_phases": timings,
            "detailed_timing_version": 2,
            "retirement": retirement,
            "standalone_mac_requests": 0, "standalone_down_requests": 0,
            "operation_timings": _timing_records(detail),
            "helper_activation_start_monotonic_ns": activation_started_ns,
            "helper_activation_evidence_monotonic_ns": monotonic_ns(),
            **switch,
        }

    def index(node: dict[str, Any], name: str) -> int:
        # Only new-interface creation populates this retained binding ledger.
        # A missing/stale retained resource fails the corresponding operation.
        return node["indexes"][name]

    def routes(node: dict[str, Any], operation: dict[str, Any], *, withdraw: bool) -> None:
        prefix = "post" if withdraw else "pre"
        channel = node["netlink"]
        values = operation.get("route_withdraw" if withdraw else "route_replace", ())
        for value in values:
            destination, name = value[:2]
            if not withdraw or value[2]:
                _timed_call(detail, prefix + ("_route_deletion" if withdraw else "_route_replacement"), channel.route, index(node, name), destination, delete=withdraw)
            _timed_call(detail, prefix + ("_neighbour_deletion" if withdraw else "_neighbour_replacement"), channel.neighbour, index(node, name), destination, request["common_mac"], delete=withdraw)

    try:
        if _lookup_map(_GENERATION_FD, 0) != expected:
            raise RuntimeError("active generation predecessor differs")
        started = monotonic()
        for connection in connections:
            node = _LIVE_NODES[connection["node_a"]]
            peer = _LIVE_NODES[connection["node_b"]]
            mutation_started = True
            owner = connection["retirement_node"]
            group = connection["retirement_group"]
            _OWNED_RETIREMENTS.setdefault((owner, group), {})[connection["connection"]] = connection
            _timed_call(detail, "pre_veth_creation", node["netlink"].create_veth,
                        connection["interface_a"], connection["interface_b"], peer["namespace_fd"],
                        mac=request["common_mac"], group=group if owner == connection["node_a"] else 0,
                        peer_group=group if owner == connection["node_b"] else 0)
            created.append(connection)
        # Affected namespaces only. A socket is already bound to each namespace;
        # profiling does not introduce namespace entries or process barriers.
        for key in sorted(operations.keys() | new_endpoints.keys()):
            node = _LIVE_NODES[key]
            channel = node["netlink"]
            maps = node["maps"]
            operation = operations.get(key, {})
            for name, port, parameters in new_endpoints.get(key, ()):
                mutation_started = True
                ifindex = _timed_call(detail, "pre_ifindex_resolution", channel.link_index, name)
                node["indexes"][name] = ifindex
                _timed_call(detail, "pre_qdisc_creation", channel.netem, ifindex, parameters)
                _timed_call(detail, "pre_bpf_attachment", channel.attach_bpf, ifindex, maps["tc_ingress"])
                _timed_call(detail, "pre_ingress_binding_update", _update_map, maps["ingress_bindings"], ifindex, port)
                _timed_call(detail, "pre_egress_binding_update", _update_map, maps["egress_bindings"], port, ifindex)
                identities.append({"key": key, "interface": name, "ifindex": ifindex, "logical_port_id": port})
                _timed_call(detail, "pre_link_up", channel.set_link, ifindex, up=True)
            for value in operation.get("qdisc", ()):
                mutation_started = True
                _timed_call(detail, "pre_existing_qdisc_update", channel.netem, index(node, value["interface"]), value)
            for address in operation.get("zone_address_add", ()):
                mutation_started = True
                _timed_call(detail, "pre_zone_address_addition", channel.address, node["loopback"], address)
            if operation.get("route_replace"):
                mutation_started = True
            routes(node, operation, withdraw=False)
        timings["pre_publication"] = {"duration_seconds": monotonic() - started}
        phase = "active-generation-publication"
        if changed:
            switch["generation_switch_start_monotonic_ns"] = monotonic_ns()
            try:
                _update_map(_GENERATION_FD, 0, generation)
            except BaseException:
                detail["active_generation_update"] = [monotonic_ns() - switch["generation_switch_start_monotonic_ns"], 1]
                raise
            published = True
            switch["generation_switch_end_monotonic_ns"] = monotonic_ns()
            timings["active_generation_publication"] = {"duration_seconds": (
                switch["generation_switch_end_monotonic_ns"]
                - switch["generation_switch_start_monotonic_ns"]
            ) / 1e9}
            detail["active_generation_update"] = [switch["generation_switch_end_monotonic_ns"] - switch["generation_switch_start_monotonic_ns"], 1]
        phase = "post-publication"
        started = monotonic()
        for key in sorted(operations.keys() | old_endpoints.keys()):
            node = _LIVE_NODES[key]
            operation = operations.get(key, {})
            routes(node, operation, withdraw=True)
            for address in operation.get("zone_address_delete", ()):
                _timed_call(detail, "post_zone_address_removal", node["netlink"].address, node["loopback"], address, delete=True)
            for name, port in old_endpoints.get(key, ()):
                ifindex = index(node, name)
                if not _timed_call(detail, "post_ingress_binding_deletion", _delete_map, node["maps"]["ingress_bindings"], ifindex):
                    raise RuntimeError("withdrawn ingress binding was absent")
                if not _timed_call(detail, "post_egress_binding_deletion", _delete_map, node["maps"]["egress_bindings"], port):
                    raise RuntimeError("withdrawn egress binding was absent")
        timings["withdrawal"] = {"duration_seconds": monotonic() - started}
        retirement = _retire_batches(batches)
        timings["physical_deletion"] = {"duration_seconds": retirement["wall_seconds"]}
        deleted = retirement["retired_pairs"]
        detail["post_veth_deletion"] = [round(retirement["wall_seconds"] * 1e9), deleted]
        if retirement["errors"]:
            raise RuntimeError("physical retirement incomplete: " + "; ".join(retirement["errors"]))
        timings["post_publication"] = {"duration_seconds": monotonic() - started}
        complete = True
        return evidence()
    except BaseException as error:
        rollback_errors = []
        if not published:
            # Roll back newly allocated physical/binding resources through the
            # same sockets and syscalls, retaining all uncertain cleanup ownership.
            for connection in reversed(created):
                try:
                    for side in ("a", "b"):
                        node = _LIVE_NODES[connection[f"node_{side}"]]
                        name = connection[f"interface_{side}"]
                        ifindex = node["indexes"].get(name)
                        if ifindex is not None:
                            _delete_map(node["maps"]["ingress_bindings"], ifindex)
                        _delete_map(node["maps"]["egress_bindings"], connection[f"logical_port_{side}"])
                    node = _LIVE_NODES[connection["node_a"]]
                    name = connection["interface_a"]
                    ifindex = node["indexes"].get(name)
                    if ifindex is None:
                        ifindex = node["netlink"].link_index(name)
                    node["netlink"].delete_link(ifindex)
                    _forget_connection(connection)
                except BaseException as rollback_error:
                    rollback_errors.append(str(rollback_error))
        failure_evidence = {
            **evidence(), "failure_phase": phase, "failure_type": type(error).__name__,
            "failure_message": str(error), "live_state_may_be_partial": mutation_started,
            "cleanup_connections": list(connections) + list(removed),
            "cleanup_ownership_retained": True,
            "candidate_rollback_attempted": bool(created) and not published,
            "candidate_rollback_complete": not rollback_errors if created and not published else None,
            "rollback_errors": rollback_errors,
        }
        failure_type = _PostPublicationActivationFailure if published else _PrePublicationActivationFailure
        raise failure_type(str(error), evidence=failure_evidence) from error


def _cleanup(request: dict[str, Any], *, tolerate_errors: bool = False) -> dict[str, Any]:
    global _OWNED_PIN_ROOT, _FOUNDATION_OUTCOME
    detail: dict[str, list[int]] = {}
    phases = {}
    root = _pin_root(request["pin_root"])
    if _OWNED_PIN_ROOT is None:
        # A rejected startup must never remove a conflicting pre-existing root.
        _close_foundation(detail)
        return {"deleted_connections": 0, "pin_root_removed": not root.exists(),
                "cleanup_confirmed": True, "initialization_outcome": _FOUNDATION_OUTCOME, "errors": [],
                "detailed_timing_version": 1, "cleanup_operations": _timing_records(detail)}
    if _OWNED_PIN_ROOT != root:
        raise RuntimeError("cleanup pin root differs from the owned foundation")
    errors = []
    deleted = 0
    network_started = monotonic_ns()
    retirement = _retire_batches([
        {"key": key, "group": group, "connections": tuple(owned.values())}
        for (key, group), owned in _OWNED_RETIREMENTS.items()
    ], cleanup=True)
    deleted = retirement["retired_pairs"]
    errors.extend(retirement["errors"])
    detail["remaining_veth_deletion"] = [round(retirement["wall_seconds"] * 1e9), deleted]
    network_ended = monotonic_ns()
    phases["remaining_network_seconds"] = (network_ended - network_started) / 1e9
    for record in request.get("nodes", ()):
        node = _LIVE_NODES.get(record["key"])
        if node is None or "loopback" not in node:
            continue
        addresses = set(record.get("zone_addresses", ()))
        if record["key"] in _OWNED_NODE_ADDRESSES:
            addresses.add(record["node_address"])
        for address in sorted(addresses):
            try:
                _timed_call(detail, "address_removal", node["netlink"].address, node["loopback"], address, delete=True)
            except OSError as error:
                if error.errno not in (errno.EADDRNOTAVAIL, errno.ENOENT):
                    errors.append(str(error))
    addresses_ended = monotonic_ns()
    phases["address_removal_seconds"] = (addresses_ended - network_ended) / 1e9
    if not errors:
        if root.exists():
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    _timed_call(detail, "pin_directory_removal", path.rmdir) if path.is_dir() else _timed_call(detail, "bpf_unpin", path.unlink)
                except OSError as error:
                    errors.append(str(error))
            if not errors:
                _timed_call(detail, "pin_directory_removal", root.rmdir)
        unpin_ended = monotonic_ns()
        phases["bpf_unpin_seconds"] = (unpin_ended - addresses_ended) / 1e9
        if not errors:
            _close_foundation(detail)
            phases["retained_descriptor_close_seconds"] = (monotonic_ns() - unpin_ended) / 1e9
            _OWNED_PIN_ROOT = None
            _FOUNDATION_OUTCOME = "cleaned"
            _OWNED_NODE_ADDRESSES.clear()
    if errors and not tolerate_errors:
        error = RuntimeError("Runner cleanup left residue: " + "; ".join(errors))
        error.evidence = {"retirement": retirement, "cleanup_phases": phases, "cleanup_operations": _timing_records(detail)}
        raise error
    return {"deleted_connections": deleted, "pin_root_removed": not root.exists(),
            "cleanup_confirmed": not errors, "initialization_outcome": "partial" if errors else "cleaned",
            "errors": errors, "detailed_timing_version": 2,
            "retirement": retirement, "cleanup_phases": phases, "cleanup_operations": _timing_records(detail)}


def _dispatch_action(action: str, request: dict[str, Any]) -> dict[str, Any]:
    actions = {
        "initialize": _initialize,
        "prewrite_generation": _prewrite_generation,
        "reclaim_generations": _reclaim_generations,
        "activate_boundary": _activate_boundary,
        "cleanup": _cleanup,
    }
    if action not in actions:
        raise ValueError("unknown private Runner command action")
    return actions[action](request)


def _respond(action: str, request: dict[str, Any], *, request_timing=None) -> dict[str, Any]:
    # Main/Cleanup add joined worker counters to the dispatch thread counters.
    # Startup worker counts remain unavailable. These are attempted calls.
    counters = None if action == "initialize" else _kernel_call_counts()
    started = monotonic_ns()
    try:
        result = _dispatch_action(action, request)
        ended = monotonic_ns()
        response = {"status": "ok", "result": result}
    except BaseException as error:
        ended = monotonic_ns()
        response = {"status": "error", "error": str(error), "error_type": type(error).__name__,
                "evidence": getattr(error, "evidence", None), "traceback": traceback.format_exc()}
    response["helper_timing"] = {
        **(request_timing or {}),
        "dispatch_start_monotonic_ns": started, "dispatch_end_monotonic_ns": ended,
        "dispatch_seconds": (ended - started) / 1e9,
        "kernel_calls": None if counters is None else {
            key: value - counters[key] for key, value in _kernel_call_counts().items()
        },
    }
    return response


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    if sys.argv[1] == "capture_incarnations":
        try:
            result = _capture_incarnations(json.load(sys.stdin))
            print(json.dumps({"status": "ok", "result": result}), flush=True)
            return 0
        except BaseException as error:
            print(json.dumps({"status": "error", "error": str(error)}), flush=True)
            return 1
    if sys.argv[1] != "serve":
        return 2
    try:
        for line in sys.stdin:
            received = monotonic_ns()
            envelope = json.loads(line)
            decoded = monotonic_ns()
            response = _respond(envelope["action"], envelope["request"], request_timing={
                "line_received_monotonic_ns": received,
                "request_decoded_monotonic_ns": decoded,
                "request_decode_seconds": (decoded - received) / 1e9,
            })
            print(json.dumps(response, separators=(",", ":")), flush=True)
    finally:
        _close_foundation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
