"""Public responsibility modules and the retained Emulator entity/value exports.

Configuration, operation functions and integration adapters remain public
from their owning modules so imports preserve architectural origin.
"""

from __future__ import annotations

import typing as _typing
from importlib import import_module as _import_module

if _typing.TYPE_CHECKING:
    from . import backend, deployment, configuration, emulator, adapters, experiment, simulator
    from .emulator.entities import (
        EmulatorGroundStation,
        GroundStationLocation,
        EmulatorOrbit,
        OrbitParameters,
        EmulatorSatellite,
        EmulatorShell,
    )


_ENTITY_EXPORTS = frozenset(
    {
        "EmulatorGroundStation",
        "GroundStationLocation",
        "EmulatorOrbit",
        "OrbitParameters",
        "EmulatorSatellite",
        "EmulatorShell",
    }
)

_MODULE_EXPORTS = {
    "emulator", "simulator", "experiment", "configuration",
    "adapters", "backend", "deployment",
}

__all__ = [
    "emulator", "simulator", "experiment", "configuration",
    "adapters", "backend", "deployment",
    "EmulatorGroundStation",
    "GroundStationLocation",
    "EmulatorOrbit",
    "OrbitParameters",
    "EmulatorSatellite",
    "EmulatorShell",
]


def __getattr__(name: str) -> _typing.Any:
    """Resolve a selected public module or entity without loading other owners."""

    if name in _MODULE_EXPORTS:
        value = _import_module("." + name, __name__)
        globals()[name] = value
        return value

    if name not in _ENTITY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .emulator import entities

    value = getattr(entities, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _ENTITY_EXPORTS | _MODULE_EXPORTS)
