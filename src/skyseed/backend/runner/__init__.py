"""Detached execution lifecycle shared by the BPF and Linux adapters."""
from importlib import import_module

__all__ = ["Runner"]

def __getattr__(name):
    if name == "Runner":
        return import_module(".lifecycle", __name__).Runner
    raise AttributeError(name)
