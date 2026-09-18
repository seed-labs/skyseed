"""Select BPF compilation, compiled values or the existing Runner independently."""
from importlib import import_module as _import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import compiler, runner
    from skyseed_v2.backend.bpf.compiler import compile_bpf
    from skyseed_v2.backend.bpf.runner.lifecycle import Runner
    from skyseed_v2.backend.bpf.compiler.result import BpfCompiledActivationBoundary, BpfCompilationResult

_OBJECT_MODULES = {
    "compile_bpf": ".compiler",
    "Runner": ".runner",
    "BpfCompiledActivationBoundary": ".compiler.result",
    "BpfCompilationResult": ".compiler.result",
}

__all__ = [
    "BpfCompiledActivationBoundary",
    "BpfCompilationResult",
    "Runner",
    "compile_bpf",
    "compiler",
    "runner",
]


def __getattr__(name: str) -> Any:
    if name in {"compiler", "runner"}:
        value = _import_module("." + name, __name__)
    elif name in _OBJECT_MODULES:
        value = getattr(_import_module(_OBJECT_MODULES[name], __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
