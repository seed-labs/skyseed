"""Linux BPF syscalls and packet program generation."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import re
import struct
from threading import local
from skyseed_v2.backend.bpf.runner.traffic_telemetry_abi import (
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


_BPF_UPDATE_BATCH_SIZE = 1024


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


def _bpf_syscall_number() -> int:
    try:
        return {"x86_64": 321, "aarch64": 280}[os.uname().machine]
    except KeyError as error:
        raise RuntimeError("unsupported architecture for raw BPF syscall") from error


_BPF_CALLS = local()


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


def _bpf_call_counts() -> dict[str, int]:
    counts = getattr(_BPF_CALLS, "counts", {1: 0, 2: 0, 3: 0, 26: 0})
    return {"bpf_lookup_calls": counts[1], "bpf_update_calls": counts[2],
            "bpf_delete_calls": counts[3], "bpf_update_batch_calls": counts[26]}


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
    generation_versions_map: int,
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
    generation_versions_map: int,
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
