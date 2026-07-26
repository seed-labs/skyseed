"""Public entry points for the Dynamic Slice inspection Tool."""

from .inspection import collect_dynamic_slice_data, write_dynamic_slice_tool

__all__ = ["collect_dynamic_slice_data", "write_dynamic_slice_tool"]
