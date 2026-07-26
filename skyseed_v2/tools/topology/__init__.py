"""Public entry points for the repository-local Topology inspection Tool."""

from .inspection import collect_topology_data, write_topology_tool

__all__ = ["collect_topology_data", "write_topology_tool"]
