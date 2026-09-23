"""Linux BPF syscalls and bounded map-writing mechanisms."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import struct
from threading import local


_BPF_MAP_CREATE = 0


_BPF_MAP_LOOKUP_ELEM = 1


_BPF_MAP_UPDATE_ELEM = 2


_BPF_MAP_DELETE_ELEM = 3


_BPF_MAP_UPDATE_BATCH = 26


_BPF_OBJ_PIN = 6


_BPF_OBJ_GET = 7


_BPF_MAP_TYPE_HASH = 1


_BPF_MAP_TYPE_ARRAY = 2


_BPF_MAP_TYPE_RINGBUF = 27


_BPF_ANY = 0


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
    if isinstance(key, bytes):
        return ctypes.create_string_buffer(key)
    values = (key,) if isinstance(key, int) else key
    return ctypes.create_string_buffer(struct.pack(f"={len(values)}I", *values))


def _update_map(
    file_descriptor: int, key: int | tuple[int, int], value: int
) -> None:
    key_value = _key_buffer(key)
    map_value = ctypes.create_string_buffer(value) if isinstance(value, bytes) else ctypes.c_uint32(value)
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
        key if isinstance(key, bytes) else struct.pack(f"={1 if isinstance(key, int) else len(key)}I", *((key,) if isinstance(key, int) else key))
        for key, _ in entries)
    normalized_values = tuple(value if isinstance(value, bytes) else struct.pack("=I", value) for _, value in entries)
    if not normalized_keys[0] or not normalized_values[0] or any(len(key) != len(normalized_keys[0]) for key in normalized_keys) or any(len(value) != len(normalized_values[0]) for value in normalized_values):
        raise ValueError("BPF batch-update keys/values must have one fixed width")
    key_buffer = ctypes.create_string_buffer(b"".join(normalized_keys))
    value_buffer = ctypes.create_string_buffer(b"".join(normalized_values))
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
