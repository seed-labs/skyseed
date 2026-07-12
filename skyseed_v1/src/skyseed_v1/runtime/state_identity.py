"""Frozen/live host-state and runtime-epoch identity contracts."""

from __future__ import annotations

from typing import Any, Mapping


HOST_STATE_PREFIXES = ("live_state:", "frozen_prepare_host_state:")


def parse_host_state_identity(state_id: str) -> tuple[str, str] | None:
    """Return the exact snapshot/epoch identity for a supported state ID."""

    raw = str(state_id or "")
    for prefix in HOST_STATE_PREFIXES:
        if not raw.startswith(prefix):
            continue
        parts = raw[len(prefix) :].split(":", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0], parts[1]
    return None


def host_state_ids_equivalent(left: str, right: str) -> bool:
    """Accept exact IDs or frozen/live aliases with identical identities."""

    if str(left or "") == str(right or ""):
        return True
    left_identity = parse_host_state_identity(left)
    right_identity = parse_host_state_identity(right)
    return (
        left_identity is not None
        and right_identity is not None
        and left_identity == right_identity
    )


def runtime_epoch_identity_matches(
    *,
    record_intent: Mapping[str, Any],
    target_intent: Mapping[str, Any],
) -> bool:
    """Match all present plan/slice/snapshot identity fields fail-closed."""

    record = dict(record_intent or {})
    target = dict(target_intent or {})
    compared = 0
    for key in ("plan_id", "slice_id", "snapshot_id"):
        record_value = str(record.get(key) or "")
        target_value = str(target.get(key) or "")
        if not record_value and not target_value:
            continue
        compared += 1
        if record_value != target_value:
            return False
    return compared > 0


__all__ = [
    "HOST_STATE_PREFIXES",
    "host_state_ids_equivalent",
    "parse_host_state_identity",
    "runtime_epoch_identity_matches",
]
