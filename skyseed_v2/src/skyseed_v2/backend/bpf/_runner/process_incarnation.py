"""Runner-private Linux process-incarnation evidence."""

from __future__ import annotations

from pathlib import Path
import os


def _read_process_start_time_ticks(pid: int) -> int:
    """Return Linux ``/proc/<pid>/stat`` field 22 without misparsing comm."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("Runner process PID must be a positive int")
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot read node process identity: pid={pid}: {error}") from error
    closing_parenthesis = value.rfind(")")
    if closing_parenthesis < 0:
        raise RuntimeError(f"node process identity is malformed: {pid}")
    fields = value[closing_parenthesis + 1 :].split()
    try:
        start_time_ticks = int(fields[19])
    except (IndexError, ValueError) as error:
        raise RuntimeError(f"node process identity is malformed: {pid}") from error
    if start_time_ticks <= 0:
        raise RuntimeError(f"node process identity is invalid: {pid}")
    return start_time_ticks


def _read_process_netns_inode(pid: int) -> int:
    """Return the host-visible inode for one Linux process network namespace."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("Runner process PID must be a positive int")
    try:
        inode = os.stat(f"/proc/{pid}/ns/net", follow_symlinks=True).st_ino
    except OSError as error:
        raise RuntimeError(
            f"node process network namespace is unavailable: {pid}"
        ) from error
    if inode <= 0:
        raise RuntimeError(f"node process network namespace is invalid: {pid}")
    return inode


def _capture_process_incarnation(pid: int) -> tuple[int, int]:
    """Capture a race-guarded process start time and network namespace inode."""

    start_before = _read_process_start_time_ticks(pid)
    netns_inode = _read_process_netns_inode(pid)
    start_after = _read_process_start_time_ticks(pid)
    if start_before != start_after:
        raise RuntimeError(
            f"node process incarnation changed during discovery: {pid}"
        )
    return start_after, netns_inode


def _capture_process_incarnations(pids: tuple[int, ...]) -> list[dict[str, int]]:
    """Capture one complete, ordered PID set and reject ambiguous namespaces."""

    if not isinstance(pids, tuple) or not pids:
        raise ValueError("Runner process PID batch must be a non-empty tuple")
    if len(set(pids)) != len(pids):
        raise RuntimeError("Runner process PID batch contains duplicates")
    values = []
    seen_netns: dict[int, int] = {}
    for pid in pids:
        start_time_ticks, netns_inode = _capture_process_incarnation(pid)
        prior_pid = seen_netns.get(netns_inode)
        if prior_pid is not None:
            raise RuntimeError(
                "Runner process network namespace evidence is duplicated: "
                f"pids={prior_pid},{pid}, inode={netns_inode}"
            )
        seen_netns[netns_inode] = pid
        values.append(
            {
                "pid": pid,
                "process_start_time_ticks": start_time_ticks,
                "netns_inode": netns_inode,
            }
        )
    return values


__all__: list[str] = []
