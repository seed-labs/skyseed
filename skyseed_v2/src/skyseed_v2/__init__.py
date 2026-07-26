"""Stable high-frequency Emulator entities and entity-bound values.

Configuration, operation functions and integration adapters remain public
from their owning modules so imports preserve architectural origin.
"""

from __future__ import annotations

import typing as _typing

if _typing.TYPE_CHECKING:
    from .emulator.entities import (
        GroundStation,
        GroundStationLocation,
        Orbit,
        OrbitParameters,
        Satellite,
        Shell,
    )


_ENTITY_EXPORTS = frozenset(
    {
        "GroundStation",
        "GroundStationLocation",
        "Orbit",
        "OrbitParameters",
        "Satellite",
        "Shell",
    }
)

__all__ = [
    "GroundStation",
    "GroundStationLocation",
    "Orbit",
    "OrbitParameters",
    "Satellite",
    "Shell",
]


def __getattr__(name: str) -> _typing.Any:
    """Load SEED-backed root entities only when a caller requests one."""

    if name not in _ENTITY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .emulator import entities

    value = getattr(entities, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _ENTITY_EXPORTS)
