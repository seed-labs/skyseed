"""Select the existing BPF backend without loading compilation or runtime."""
from importlib import import_module as _import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import bpf

__all__ = ["bpf"]


def __getattr__(name: str) -> Any:
    if name != "bpf":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = _import_module(".bpf", __name__)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
