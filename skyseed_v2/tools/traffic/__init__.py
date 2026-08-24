"""Expose the repository-local Traffic / Path Replay Tool facade."""

from .inspection import collect_traffic_replay_data, write_traffic_replay_tool

__all__ = ["collect_traffic_replay_data", "write_traffic_replay_tool"]
