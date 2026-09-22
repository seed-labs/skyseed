"""Select the existing BPF backend without loading compilation or runtime."""
from importlib import import_module as _import_module
from typing import TYPE_CHECKING, Any
from .selection import ForwardingBackend

if TYPE_CHECKING:
    from . import bpf

__all__ = ["bpf", "linux", "ForwardingBackend", "compile_plan", "Runner"]


def __getattr__(name: str) -> Any:
    if name in {"compile_plan", "Runner"}:
        module = ".compiler" if name == "compile_plan" else ".runner"
        return getattr(_import_module(module, __name__), name)
    if name not in {"bpf", "linux"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = _import_module("." + name, __name__)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
