"""SEED metadata naming and stable runtime identity formatting."""

from __future__ import annotations


SEED_METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."
SKYSEED_METADATA_PREFIX = "skyseed."


def skyseed_metadata_key(name: str) -> str:
    """Return one namespaced Skyseed metadata key."""

    return f"{SKYSEED_METADATA_PREFIX}{name}"


def format_runtime_node_reference(
    *, asn: int, registry_type: str, node_name: str
) -> str:
    """Format stable node identity without depending on Simulator contracts."""

    return f"{asn}/{registry_type}/{node_name}"
