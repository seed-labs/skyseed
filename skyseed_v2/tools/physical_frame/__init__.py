"""Public entry points for repository-local Physical Frame inspection."""

from .inspection import collect_physical_frame_data, write_physical_frame_tool

__all__ = ["collect_physical_frame_data", "write_physical_frame_tool"]
