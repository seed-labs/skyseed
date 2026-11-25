"""SkySEED's paper-aligned Scene and SEED construction interfaces.

Importing Scene does not import SEED or start calculation or execution.
"""
from .scene import Scene

__all__ = ["Scene", "SkyEmulator", "SkyBase", "SkyAS", "PlanningControl", "PlanCompiler", "save_plans", "load_plans",
           "Runner", "Deployment", "Store"]


def __getattr__(name):
    if name in {"Runner", "Deployment"}:
        from . import runner
        return getattr(runner, name)
    if name == "Store":
        from .store import Store
        return Store
    if name in {"PlanningControl", "PlanCompiler", "save_plans", "load_plans"}:
        from . import pc
        return getattr(pc, name)
    if name in {"SkyEmulator", "SkyBase", "SkyAS"}:
        from . import emulator
        return getattr(emulator, name)
    raise AttributeError(f"skyseed has no implemented interface {name!r}")
