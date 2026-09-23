"""Explicit planned-data definitions: one source for C layout and value encoding."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
from functools import cached_property

from skyseed.simulator.planner.values import (
    LocalRouteOutcome, LogicalExit, PolicyRecord, RouteDisposition,
)
from skyseed.shared import NodeId

# C ABI natural alignment is made explicit in both generated asserts and encoding.
_SCALARS = {
    'u8': ('__u8', 'B', 1), 'u16': ('__u16', 'H', 2),
    'u32': ('__u32', 'I', 4), 'u64': ('__u64', 'Q', 8),
    'i32': ('__s32', 'i', 4), 'i64': ('__s64', 'q', 8),
    'bool': ('__u8', 'B', 1), 'exit': ('__u32', 'I', 4),
    'node': ('__u32', 'I', 4),
}
_C_RESERVED = set('auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn _Static_assert _Thread_local'.split())


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', value) or value in _C_RESERVED or value.startswith('sky_'):
        raise ValueError(f'invalid or reserved C data name: {value!r}')
    return value


@dataclass(frozen=True)
class Scalar:
    kind: str

    def __post_init__(self):
        if self.kind not in _SCALARS:
            raise ValueError(f'unsupported scalar: {self.kind}')


U8, U16, U32, U64, I32, I64, BOOL, EXIT, NODE = (Scalar(k) for k in _SCALARS)


@dataclass(frozen=True, init=False)
class Record:
    """Ordered named fields. Supported leaves: fixed integers, BOOL, EXIT, NODE."""
    fields: tuple

    def __init__(self, fields=(), **named):
        pairs = tuple(fields.items()) if isinstance(fields, Mapping) else tuple(fields)
        values = dict(pairs)
        if len(values) != len(pairs) or values.keys() & named.keys():
            raise ValueError('duplicate record field')
        values.update(named)
        if any(not isinstance(v, (Scalar, Record)) for v in values.values()):
            raise TypeError('record fields must be Scalar or Record definitions')
        object.__setattr__(self, 'fields', tuple((_name(k), v) for k, v in values.items()))


    @cached_property
    def _field_names(self):
        return frozenset(name for name, _ in self.fields)

    @cached_property
    def _layout(self):
        # Derived facts belong to this immutable definition, not encoded values.
        offset, alignment, fields = 0, 1, []
        for name, definition in self.fields:
            size, align = (_size(definition), _alignment(definition))
            offset = (offset + align - 1) // align * align
            fields.append((name, definition, offset))
            offset += size
            alignment = max(alignment, align)
        return max(1, (offset + alignment - 1) // alignment * alignment), alignment, tuple(fields)


def _size(value):
    return value._layout[0] if isinstance(value, Record) else _SCALARS[value.kind][2]


def _alignment(value):
    return value._layout[1] if isinstance(value, Record) else _SCALARS[value.kind][2]


def _spec(value):
    return [[k, _spec(v)] for k, v in value.fields] if isinstance(value, Record) else value.kind


def _from_spec(value):
    return Scalar(value) if isinstance(value, str) else Record((k, _from_spec(v)) for k, v in value)


def _encode_record(definition, record, *, bind_exit, bind_node):
    if not isinstance(record, PolicyRecord):
        record = PolicyRecord(record)
    fields = dict(record.fields)
    if fields.keys() != definition._field_names:
        raise ValueError(f'policy fields differ: expected {tuple(name for name, _ in definition.fields)}, got {tuple(fields)}')
    size, _, layout = definition._layout
    data = bytearray(size)
    for name, kind, offset in layout:
        value = fields[name]
        if isinstance(kind, Record):
            encoded = _encode_record(kind, value, bind_exit=bind_exit, bind_node=bind_node)
        else:
            if kind.kind == 'exit':
                if not isinstance(value, LogicalExit):
                    raise TypeError(f'{name} requires LogicalExit')
                value = bind_exit(value)
            elif kind.kind == 'node':
                if not isinstance(value, NodeId):
                    raise TypeError(f'{name} requires NodeId')
                value = bind_node(value)
            elif kind.kind == 'bool':
                if type(value) is not bool:
                    raise TypeError(f'{name} requires bool')
            elif type(value) is not int:
                raise TypeError(f'{name} requires an integer')
            try:
                encoded = struct.pack('<' + _SCALARS[kind.kind][1], value)
            except struct.error as error:
                raise ValueError(f'{name} is outside {kind.kind} representation') from error
        data[offset:offset + len(encoded)] = encoded
    return bytes(data)


@dataclass(frozen=True)
class RouteData:
    """Fixed route record and bounded local candidate collection.

    Names and capacity are declarations, never inferred from Plan values. The
    key is the framework's IPv4 packet target; EXIT and NODE are logical u32
    references. Fields, including priority/weight, have no implicit policy.
    """
    name: str
    rule: Record = Record()
    candidate: Record = Record()
    max_candidates: int = 1
    value_field: str | None = None

    def __post_init__(self):
        _name(self.name)
        if self.value_field is not None:
            _name(self.value_field)
        if not isinstance(self.rule, Record) or not isinstance(self.candidate, Record):
            raise TypeError('rule and candidate must be Record definitions')
        if type(self.max_candidates) is not int or not 1 <= self.max_candidates <= 0xffffffff:
            raise ValueError('max_candidates must be a positive u32')
        if self.value_size > 0xffffffff:
            raise ValueError('map value size exceeds the BPF u32 declaration')

    @cached_property
    def _candidate_record(self):
        return Record(exit=EXIT, data=self.candidate)

    @cached_property
    def _prefix(self):
        return Record(disposition=U32, count=U32, data=self.rule)

    @cached_property
    def _candidate_offset(self):
        align = _alignment(self._candidate_record)
        return (_size(self._prefix) + align - 1) // align * align

    @cached_property
    def value_size(self):
        alignment = max(_alignment(self._prefix), _alignment(self._candidate_record))
        size = self._candidate_offset + self.max_candidates * _size(self._candidate_record)
        return (size + alignment - 1) // alignment * alignment

    def to_data(self):
        return {'name': self.name, 'rule': _spec(self.rule), 'candidate': _spec(self.candidate),
                'max_candidates': self.max_candidates, 'value_field': self.value_field}

    @classmethod
    def from_data(cls, value):
        if set(value) != {'name', 'rule', 'candidate', 'max_candidates', 'value_field'}:
            raise ValueError('invalid route data declaration')
        return cls(value['name'], _from_spec(value['rule']), _from_spec(value['candidate']), value['max_candidates'], value['value_field'])

    def encode(self, outcome, *, bind_exit, bind_node):
        """Encode declared logical values; bindings are supplied by DataPlane."""
        if not isinstance(outcome, LocalRouteOutcome):
            raise TypeError('route encoding requires LocalRouteOutcome')
        if len(outcome.candidates) > self.max_candidates:
            raise ValueError(f'{self.name}: candidate count exceeds declared capacity {self.max_candidates}')
        def selected(record):
            return record if self.value_field is None else record[self.value_field]
        disposition = {RouteDisposition.FORWARD: 1, RouteDisposition.DROP: 2, RouteDisposition.PASS: 3}[outcome.disposition]
        data = bytearray(self.value_size)
        prefix = _encode_record(self._prefix, PolicyRecord(disposition=disposition, count=len(outcome.candidates), data=selected(outcome.data)), bind_exit=bind_exit, bind_node=bind_node)
        data[:len(prefix)] = prefix
        for i, c in enumerate(outcome.candidates):
            encoded = _encode_record(self._candidate_record, PolicyRecord(exit=c.exit, data=selected(c.data)), bind_exit=bind_exit, bind_node=bind_node)
            offset = self._candidate_offset + i * len(encoded)
            data[offset:offset + len(encoded)] = encoded
        return bytes(data)

    def generate_headers(self, directory):
        """Prepare stable author-visible declarations, without a Plan or C source."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{self.name}.h'
        content = self._header()
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
        return path

    def _header(self):
        name = self.name
        lines = [f'/* Generated by RouteData: edit the Python definition, not this file. */',
                 f'#ifndef SKY_GENERATED_{name.upper()}_H', f'#define SKY_GENERATED_{name.upper()}_H',
                 '#define SKY_MANAGED_TABLES 2', '#include "skyseed.h"', '#include <stddef.h>']
        def declaration(tag, definition):
            for field, child in definition.fields:
                if isinstance(child, Record):
                    declaration(f'{tag}_{len(field)}_{field}', child)
            lines.append(f'struct {tag} {{')
            for field, child in definition.fields:
                ctype = f'struct {tag}_{len(field)}_{field}' if isinstance(child, Record) else _SCALARS[child.kind][0]
                lines.append(f'    {ctype} {field};')
            if not definition.fields:
                lines.append('    __u8 reserved;')
            lines.append('};')
            lines.append(f'_Static_assert(sizeof(struct {tag}) == {_size(definition)}, "{tag} size");')
            for field, _, offset in definition._layout[2]:
                lines.append(f'_Static_assert(offsetof(struct {tag}, {field}) == {offset}, "{tag}.{field} offset");')
        declaration(f'{name}_rule', self.rule)
        declaration(f'{name}_candidate', self._candidate_record)
        lines.extend([f'struct {name}_route {{', '    __u32 disposition, count;', f'    struct {name}_rule data;',
            f'    struct {name}_candidate candidates[{self.max_candidates}];', '};',
            f'_Static_assert(sizeof(struct {name}_route) == {self.value_size}, "route size");',
            f'_Static_assert(offsetof(struct {name}_route, candidates) == {self._candidate_offset}, "candidate offset");',
            'struct sky_data_key { __u32 version, target; };',
            'SKY_MAP(gen_versions, BPF_MAP_TYPE_HASH, __u32, __u32);',
            'SKY_MAP(active_links, BPF_MAP_TYPE_HASH, struct sky_data_key, __u32);',
            f'SKY_MAP(routes, BPF_MAP_TYPE_HASH, struct sky_data_key, struct {name}_route);',
            'enum { SKY_FORWARD = 1, SKY_DROP = 2, SKY_PASS = 3 };',
            f'const char sky_data_definition[] SEC(".rodata.skydata") = {json.dumps(json.dumps(self.to_data(), separators=(",", ":")))};',
            'static __always_inline int sky_data_active(struct sky_context *ctx) {',
            '    __u32 *version = bpf_map_lookup_elem(&gen_versions, &ctx->generation);',
            '    if (!version || !ctx->ingress_port) return 0;',
            '    if (sky_is_local(ctx)) return 1;',
            '    struct sky_data_key key = {*version, ctx->ingress_port};',
            '    return bpf_map_lookup_elem(&active_links, &key) != 0;', '}',
            f'static __always_inline struct {name}_route *{name}_lookup(struct sky_context *ctx, __u32 packet_target) {{',
            '    __u32 *version = bpf_map_lookup_elem(&gen_versions, &ctx->generation);',
            '    if (!version) return 0;',
            '    struct sky_data_key key = {*version, packet_target};',
            '    return bpf_map_lookup_elem(&routes, &key);', '}',
            f'static __always_inline struct {name}_candidate *{name}_candidate_at(struct {name}_route *route, __u32 index) {{',
            f'    if (!route || index >= {self.max_candidates} || index >= route->count) return 0;',
            '    return &route->candidates[index];', '}', '#endif', ''])
        return '\n'.join(lines)
