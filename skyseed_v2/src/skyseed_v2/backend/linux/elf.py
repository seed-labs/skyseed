"""libbpf object inspection, linking and loading; no handwritten ELF loader."""
from __future__ import annotations

import ctypes as C
from functools import lru_cache
import os


@lru_cache(maxsize=1)
def _libbpf():
    lib = C.CDLL("libbpf.so.1", use_errno=True)
    pointer, integer, string = C.c_void_p, C.c_int, C.c_char_p
    signatures = {
        "bpf_object__open_mem": (pointer, [pointer, C.c_size_t, pointer]),
        "bpf_object__close": (None, [pointer]),
        "bpf_object__load": (integer, [pointer]),
        "bpf_object__next_map": (pointer, [pointer, pointer]),
        "bpf_object__next_program": (pointer, [pointer, pointer]),
        "bpf_map__name": (string, [pointer]),
        "bpf_map__pin_path": (string, [pointer]),
        "bpf_map__initial_value": (pointer, [pointer, C.POINTER(C.c_size_t)]),
        "bpf_map__set_initial_value": (integer, [pointer, pointer, C.c_size_t]),
        "bpf_map__set_max_entries": (integer, [pointer, C.c_uint32]),
        "bpf_map__set_autocreate": (integer, [pointer, C.c_bool]),
        "bpf_map__reuse_fd": (integer, [pointer, integer]),
        "bpf_program__name": (string, [pointer]),
        "bpf_program__section_name": (string, [pointer]),
        "bpf_program__type": (integer, [pointer]),
        "bpf_program__fd": (integer, [pointer]),
        "bpf_linker__new": (pointer, [string, pointer]),
        "bpf_linker__add_file": (integer, [pointer, string, pointer]),
        "bpf_linker__finalize": (integer, [pointer]),
        "bpf_linker__free": (None, [pointer]),
    }
    for name in ("type", "key_size", "value_size", "max_entries", "map_flags"):
        signatures["bpf_map__" + name] = (C.c_uint32, [pointer])
    for name, (result, args) in signatures.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = result, args
    return lib


def _check(result, operation):
    if result < 0:
        raise OSError(-result, f"libbpf {operation}: {os.strerror(-result)}; see libbpf diagnostics")
    return result


class _OpenOptions(C.Structure):
    # libbpf's extensible opts accepts the prefix selected by sz.
    _fields_ = [("sz", C.c_size_t), ("object_name", C.c_char_p)]


class _BpfObject:
    """Own a libbpf object (including private maps) until explicit close."""
    def __init__(self, content: bytes):
        self.lib = _libbpf()
        self.buffer = C.create_string_buffer(content)
        options = _OpenOptions(C.sizeof(_OpenOptions), b"skyseed")
        self.pointer = self.lib.bpf_object__open_mem(self.buffer, len(content), C.byref(options))
        if not self.pointer:
            error = C.get_errno()
            raise ValueError(f"libbpf cannot open BPF ELF: {os.strerror(error)}; see libbpf diagnostics")
        try:
            self.maps = self._inventory("map")
            self.programs = self._inventory("program")
        except BaseException:
            self.close()
            raise

    def _inventory(self, kind):
        values, pointer = {}, None
        while pointer := getattr(self.lib, f"bpf_object__next_{kind}")(self.pointer, pointer):
            name = getattr(self.lib, f"bpf_{kind}__name")(pointer).decode()
            if name in values:
                raise ValueError(f"duplicate BPF {kind} name: {name}")
            values[name] = pointer
        return values

    def declarations(self):
        return {
            "maps": {name: [int(getattr(self.lib, "bpf_map__" + field)(pointer))
                             for field in ("type", "key_size", "value_size", "max_entries", "map_flags")]
                     for name, pointer in self.maps.items()},
            "programs": {name: [self.lib.bpf_program__section_name(pointer).decode(),
                                 self.lib.bpf_program__type(pointer)]
                         for name, pointer in self.programs.items()},
        }

    def initial_value(self, name):
        size = C.c_size_t()
        value = self.lib.bpf_map__initial_value(self.maps[name], C.byref(size))
        return C.string_at(value, size.value) if value else None

    def configure(self, name, content):
        data = C.create_string_buffer(content)
        _check(self.lib.bpf_map__set_initial_value(self.maps[name], data, len(content)), "set initial value")

    def reuse(self, name, fd, capacity):
        if type(capacity) is not int or not 1 <= capacity <= 0xffffffff:
            raise ValueError(f"BPF map capacity must fit its u32 ABI: {name}")
        pointer = self.maps[name]
        _check(self.lib.bpf_map__set_max_entries(pointer, capacity), f"capacity {name}")
        _check(self.lib.bpf_map__reuse_fd(pointer, fd), f"reuse {name}")

    def disable(self, name):
        _check(self.lib.bpf_map__set_autocreate(self.maps[name], False), f"disable {name}")

    def load(self):
        # Default libbpf logging retains normal verifier diagnostics on stderr.
        _check(self.lib.bpf_object__load(self.pointer), "load object")
        return _check(self.lib.bpf_program__fd(self.programs["skyseed_tc"]), "program descriptor")

    def close(self):
        if self.pointer:
            self.lib.bpf_object__close(self.pointer)
            self.pointer = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _link_objects(paths, destination):
    lib = _libbpf()
    linker = lib.bpf_linker__new(os.fsencode(destination), None)
    if not linker:
        raise OSError(C.get_errno(), "libbpf could not create BPF linker")
    try:
        for path in paths:
            _check(lib.bpf_linker__add_file(linker, os.fsencode(path), None), f"link {path}")
        _check(lib.bpf_linker__finalize(linker), "finalize link")
    finally:
        lib.bpf_linker__free(linker)
