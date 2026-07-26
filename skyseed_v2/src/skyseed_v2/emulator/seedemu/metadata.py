"""Define SEED and Skyseed metadata naming conventions."""

from __future__ import annotations


SEED_METADATA_PREFIX = "org.seedsecuritylabs.seedemu.meta."
SKYSEED_METADATA_PREFIX = "skyseed."


def skyseed_metadata_key(name: str) -> str:
    """Return one namespaced Skyseed metadata key."""

    return f"{SKYSEED_METADATA_PREFIX}{name}"
