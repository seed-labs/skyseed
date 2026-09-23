"""Declared C/ELF ABI validation and runtime binding; no compilation or policy checks."""
from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import struct
import json
from functools import lru_cache

from skyseed.backend.linux.elf import _BpfObject

_CONFIG_MAP = '.rodata.skyseed'
_CONFIG = struct.Struct('<5I')
# Symbol -> runtime-owned identity. Managed tables are a private opt-in profile.
# One layout registry supplies compile-time expectations and runtime declarations.
# Capacity names are unresolved runtime reservation bindings, not forwarding policy.
_MAP_LAYOUTS = {
    "active_generation": ["active_gen", 2, 4, 4, 1],
    "generation_readers": ["gen_readers", 1, 4, 8, "resident_generation_capacity"],
    "traffic_events": ["traffic_events", 27, 0, 0, 1 << 20],
    "traffic_losses": ["traffic_losses", 2, 4, 8, "node_count"],
    "active_links": ["active_links", 1, 8, 4, "active_links_capacity"],
    "routes": ["routes", 1, 8, 4, "routes_capacity"],
    "ingress_bindings": ["ingress_bind", 1, 4, 4, "bindings_capacity"],
    "egress_bindings": ["egress_bind", 1, 4, 4, "bindings_capacity"],
    "generation_versions": ["gen_versions", 1, 4, 4, "resident_generation_capacity"],
}


def _map_layouts(managed_tables, value_size=4):
    private = {"active_links", "routes", "generation_versions"}
    result = {key: list(value) for key, value in _MAP_LAYOUTS.items()
              if managed_tables or key not in private}
    if managed_tables:
        result["routes"][3] = value_size
    return result

_DECLARED_MAPS = {value[0]: (identity, [*value[1:4], value[4] if type(value[4]) is int else 1, 0])
                  for identity, value in _MAP_LAYOUTS.items()}
_DEFAULT_MAPS = {name: entry for name, entry in _DECLARED_MAPS.items()
                 if name in {'gen_versions', 'active_links', 'routes'}}
_FRAMEWORK_MAPS = {name: entry for name, entry in _DECLARED_MAPS.items() if name not in _DEFAULT_MAPS}


def _inspect_object(obj):
    declaration = obj.declarations()
    if declaration['programs'] != {'skyseed_tc': ['tc', 3]}:
        raise ValueError('DataPlane requires the skyseed_tc TC entry point from SKYSEED_PROGRAM')
    if _CONFIG_MAP not in obj.maps:
        raise ValueError('DataPlane lacks the skyseed C contract declaration')
    if declaration['maps'][_CONFIG_MAP] != [2, 4, _CONFIG.size, 1, 1152]:
        raise ValueError('DataPlane configuration map layout differs')
    data = obj.initial_value(_CONFIG_MAP)
    if data is None or len(data) != _CONFIG.size:
        raise ValueError('DataPlane configuration layout differs')
    abi, managed, slot, telemetry, continuation = _CONFIG.unpack(data)
    if abi != 2 or managed not in (0, 1, 2) or (slot, telemetry, continuation) != (0, 1, 1):
        raise ValueError('unsupported DataPlane C contract declaration')
    data_definition = None
    if managed == 2:
        from .data import RouteData
        raw = obj.initial_value('.rodata.skydata')
        if raw is None or not raw.endswith(b'\0'):
            raise ValueError('generated data declaration is missing')
        data_definition = RouteData.from_data(json.loads(raw[:-1]))
    expected = _expected_maps(managed, data_definition.value_size if data_definition else 4)
    for name, (_, layout) in expected.items():
        if declaration['maps'].get(name) != layout:
            raise ValueError(f'DataPlane map layout differs: {name}')
    if not managed and set(_DEFAULT_MAPS) & obj.maps.keys():
        raise ValueError('default managed map names require the default table profile')
    for name, pointer in obj.maps.items():
        if obj.lib.bpf_map__pin_path(pointer):
            raise ValueError(f'object-owned map cannot request external automatic pinning: {name}')
    declaration['managed_tables'] = managed
    if data_definition is not None:
        declaration['data'] = data_definition.to_data()
    return declaration


def _program_bytes(prepared):
    if not isinstance(prepared, dict) or set(prepared) != {'elf', 'sha256', 'declaration', 'provenance'}:
        raise ValueError('invalid prepared ELF schema')
    content, declaration = _validated_elf(prepared['elf'], prepared['sha256'])
    if json.dumps(prepared['declaration'], sort_keys=True) != declaration:
        raise ValueError('prepared ELF declaration differs')
    return content


@lru_cache(maxsize=32)
def _validated_elf(encoded, digest):
    try:
        content = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError, binascii.Error) as error:
        raise ValueError('invalid prepared ELF bytes') from error
    if sha256(content).hexdigest() != digest:
        raise ValueError('prepared ELF digest differs')
    with _BpfObject(content) as obj:
        declaration = _inspect_object(obj)
    return content, json.dumps(declaration, sort_keys=True)


def _validate_program(prepared):
    _program_bytes(prepared)


def _load_object(prepared, *, bindings, capacities, node_slot, telemetry, continuation):
    obj = _BpfObject(_program_bytes(prepared))
    try:
        declaration = prepared['declaration']
        obj.configure(_CONFIG_MAP, _CONFIG.pack(2, int(declaration['managed_tables']), node_slot,
                                               int(telemetry), int(continuation)))
        expected = _expected_maps(declaration['managed_tables'], declaration['maps'].get('routes', [0, 0, 4])[2])
        for name, (identity, _) in expected.items():
            if (name in {'traffic_events', 'traffic_losses'} and not telemetry
                    or name == 'gen_readers' and not continuation):
                obj.disable(name)
            else:
                # Every object reuses the SAME shared publication/readers/ring FDs.
                # libbpf duplicates them; object.close never closes caller ownership.
                fd = bindings[identity]
                if type(fd) is not int or fd < 0:
                    raise ValueError(f'invalid DataPlane descriptor binding: {identity}')
                obj.reuse(name, fd, capacities[identity])
        fd = obj.load()
        return obj, fd
    except BaseException:
        obj.close()
        raise


def _expected_maps(managed, value_size=4):
    expected = {**_FRAMEWORK_MAPS, **(_DEFAULT_MAPS if managed else {})}
    if managed == 2:
        expected['routes'] = ('routes', [1, 8, value_size, 1, 0])
    return expected
