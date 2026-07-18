"""Shared validation for stable Skyseed identifiers."""

from __future__ import annotations


def validate_identifier(value: str, field_name: str) -> str:
    """Validate an identifier shared across architectural layers."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if not value[0].isalnum() or any(
        not (character.isalnum() or character in "_.-") for character in value
    ):
        raise ValueError(
            f"{field_name} must start with an alphanumeric character and contain "
            "only alphanumeric characters, '_', '.', or '-'"
        )
    return value


__all__ = ["validate_identifier"]
