"""Repository-local offline Tools; exports resolve to their canonical implementation."""
from importlib import import_module

__all__ = [
    "physical_world", "zone", "planner", "bpf_compiler", "runner", "performance",
    "traffic", "write_experiment_tools", "write_aggregate_index",
    "finalize_runtime_aggregate_index", "write_network_export", "write_traffic_export",
]

_FUNCTION_MODULES = {
    "write_experiment_tools": ".observation.experiment",
    "write_aggregate_index": ".observation._presentation.html",
    "finalize_runtime_aggregate_index": ".observation._presentation.html",
    "write_network_export": ".observation.planner.export",
    "write_traffic_export": ".observation.traffic.export",
}


def __getattr__(name):
    # Keep CLI modules unloaded until requested, including `python -m tools.…`.
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _FUNCTION_MODULES:
        value = getattr(import_module(_FUNCTION_MODULES[name], __name__), name)
    else:
        value = import_module(".observation." + name, __name__)
    globals()[name] = value
    return value
