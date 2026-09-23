"""Reusable, bounded compiler continuation: data only, no writers or live owners."""
from copy import copy
from dataclasses import is_dataclass, fields
from types import MappingProxyType


def _clone_material_data(value):
    # This whitelist contains compiler-side accumulators only. Never traverse a
    # Model, executor, stream, lock, BPF handle or runtime adapter.
    mutable = {'_BpfTranslation', '_LinuxTranslation', '_BoundaryStateIndexes',
               '_RuntimeCapacityDemandAccumulator', '_MaterialCursor', '_NativeDemand'}
    if isinstance(value, dict):
        return {k: _clone_material_data(v) for k, v in value.items()}
    if isinstance(value, MappingProxyType):
        return MappingProxyType({k: _clone_material_data(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_clone_material_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_clone_material_data(v) for v in value)
    if isinstance(value, set):
        return set(value)
    if (is_dataclass(value) and not isinstance(value, type)
            and type(value).__module__.startswith('skyseed.')):
        result = copy(value)
        for field in fields(value):
            object.__setattr__(result, field.name, _clone_material_data(getattr(value, field.name)))
        return result
    if type(value).__name__ in mutable and type(value).__module__.startswith('skyseed.backend.'):
        result = copy(value)
        for name, item in vars(value).items():
            # Authored immutable program selection is an input, not checkpointed
            # private author memory. Remaining cursor fields are framework data.
            setattr(result, name, item if name == '_dataplane_program' else _clone_material_data(item))
        return result
    return value  # immutable identities, bytes, datetime and read-only environments


class _WriterContinuation:
    _fields = ('_backend', '_translation', '_previous', '_previous_boundary_time',
               '_projection', '_zone_prefixes', '_logical_ports', '_capacity_demand',
               '_sealed_metadata', '_delivery', '_dataplane_program')

    def __init__(self, writer):
        if not writer._finalized or writer._delivery is None or writer._delivery['final']:
            raise ValueError('checkpoint requires a nonterminal complete delivery')
        self._writer_type = type(writer)
        self._data = {key: (getattr(writer, key) if key == '_dataplane_program' else
                           _clone_material_data(getattr(writer, key))) for key in self._fields}

    def restore(self):
        value = object.__new__(self._writer_type)
        for key, item in self._data.items():
            setattr(value, key, item if key == '_dataplane_program' else _clone_material_data(item))
        value._finalized, value._failed, value._continued = True, False, False
        return value
