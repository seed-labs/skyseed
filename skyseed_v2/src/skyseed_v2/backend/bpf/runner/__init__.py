"""Select the sole runtime owner without loading Linux execution on module selection."""
from importlib import import_module

__all__ = ["Runner"]

def __getattr__(name):
    if name == "Runner":
        value = import_module(".lifecycle", __name__).Runner
        globals()[name] = value
        return value
    raise AttributeError(name)
