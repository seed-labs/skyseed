"""Optional active Traffic, assembled only at experiment boundaries."""
from importlib import import_module

__all__ = ["TrafficProbePlan", "TrafficProbePlanBuilder", "TrafficProbeExecutor",
           "read_traffic_probe_plan", "write_traffic_probe_plan"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    module = ".probe_execution" if name == "TrafficProbeExecutor" else ".probe_plan"
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value
