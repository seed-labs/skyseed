"""Expose the repository-local Runner Inspection Tool facade."""

from .inspection import collect_runner_data, write_runner_tool

__all__ = ["collect_runner_data", "write_runner_tool"]
