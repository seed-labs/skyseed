"""Small libbpf binding: ELF loading and map operations, with explicit FD owners."""
import ctypes as C
import errno
from functools import lru_cache
import os
import struct


@lru_cache(maxsize=1)
def library():
    lib = C.CDLL("libbpf.so.1", use_errno=True)
    pointer, integer, string = C.c_void_p, C.c_int, C.c_char_p
    signatures = {
        "bpf_object__open_mem": (pointer, [pointer, C.c_size_t, pointer]),
        "bpf_object__close": (None, [pointer]),
        "bpf_object__load": (integer, [pointer]),
        "bpf_object__next_map": (pointer, [pointer, pointer]),
        "bpf_object__next_program": (pointer, [pointer, pointer]),
        "bpf_map__name": (string, [pointer]),
        "bpf_map__fd": (integer, [pointer]),
        "bpf_map__reuse_fd": (integer, [pointer, integer]),
        "bpf_map__set_max_entries": (integer, [pointer, C.c_uint32]),
        "bpf_program__name": (string, [pointer]),
        "bpf_program__fd": (integer, [pointer]),
        "bpf_map_create": (integer, [integer, string, C.c_uint32, C.c_uint32, C.c_uint32, pointer]),
        "bpf_map_update_elem": (integer, [integer, pointer, pointer, C.c_uint64]),
        "bpf_map_lookup_elem": (integer, [integer, pointer, pointer]),
        "bpf_map_delete_elem": (integer, [integer, pointer]),
        "bpf_map_update_batch": (integer, [integer, pointer, pointer, pointer, pointer]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = result, arguments
    return lib


def check(result, operation):
    if result < 0:
        code = -result if result != -1 else C.get_errno()
        raise OSError(code, f"{operation}: {os.strerror(code)}; see libbpf stderr for verifier details")
    return result


class _MapOptions(C.Structure):
    _fields_ = [("sz", C.c_size_t), ("btf_fd", C.c_uint32), ("btf_key_type_id", C.c_uint32),
                ("btf_value_type_id", C.c_uint32), ("btf_vmlinux_value_type_id", C.c_uint32),
                ("inner_map_fd", C.c_uint32), ("map_flags", C.c_uint32),
                ("map_extra", C.c_uint64)]


def create_map(name, map_type, key_size, value_size, capacity, flags=0):
    options = _MapOptions(sz=C.sizeof(_MapOptions), map_flags=flags)
    return check(library().bpf_map_create(map_type, name.encode(), key_size, value_size,
                                          capacity, C.byref(options)), f"create map {name}")


def _bytes(value):
    return struct.pack("=I", value) if isinstance(value, int) else value


def update(fd, key, value):
    key_buffer = C.create_string_buffer(_bytes(key))
    value_buffer = C.create_string_buffer(_bytes(value))
    check(library().bpf_map_update_elem(fd, key_buffer, value_buffer, 0), "update map")


def lookup(fd, key, size=4):
    key_buffer = C.create_string_buffer(_bytes(key))
    value = C.create_string_buffer(size)
    result = library().bpf_map_lookup_elem(fd, key_buffer, value)
    if result < 0 and C.get_errno() == errno.ENOENT:
        return None
    check(result, "lookup map")
    return value.raw


def delete(fd, key):
    key_buffer = C.create_string_buffer(_bytes(key))
    result = library().bpf_map_delete_elem(fd, key_buffer)
    if result < 0 and C.get_errno() == errno.ENOENT:
        return
    check(result, "delete map entry")


def write_entries(fd, entries, *, batch=True):
    """One caller-bounded unit. Probe batch support on this real map type."""
    if not entries:
        return batch
    if batch:
        keys = C.create_string_buffer(b"".join(_bytes(key) for key, _ in entries))
        values = C.create_string_buffer(b"".join(_bytes(value) for _, value in entries))
        count = C.c_uint32(len(entries))
        result = library().bpf_map_update_batch(fd, keys, values, C.byref(count), None)
        if result == 0:
            if count.value != len(entries):
                raise RuntimeError("incomplete BPF batch update")
            return True
        if C.get_errno() not in (errno.EINVAL, errno.EOPNOTSUPP, errno.ENOSYS):
            check(result, "batch update")
        batch = False
    for key, value in entries:
        update(fd, key, value)
    return batch


class BpfObject:
    def __init__(self, content, shared=None):
        self.lib = library()
        buffer = C.create_string_buffer(content)
        self.pointer = self.lib.bpf_object__open_mem(buffer, len(content), None)
        if not self.pointer:
            raise OSError(C.get_errno(), "cannot open label ELF")
        try:
            declarations = self._inventory("map")
            for name, fd in (shared or {}).items():
                check(self.lib.bpf_map__reuse_fd(declarations[name], fd), f"reuse {name}")
            check(self.lib.bpf_object__load(self.pointer), "load label ELF")
            self.maps = {name: check(self.lib.bpf_map__fd(pointer), name)
                         for name, pointer in declarations.items()}
            self.programs = {name: check(self.lib.bpf_program__fd(pointer), name)
                             for name, pointer in self._inventory("program").items()}
        except BaseException:
            self.close()
            raise

    def _inventory(self, kind):
        result, pointer = {}, None
        while True:
            pointer = getattr(self.lib, f"bpf_object__next_{kind}")(self.pointer, pointer)
            if not pointer:
                return result
            name = getattr(self.lib, f"bpf_{kind}__name")(pointer).decode()
            result[name] = pointer

    def close(self):
        if self.pointer:
            self.lib.bpf_object__close(self.pointer)
            self.pointer = None
