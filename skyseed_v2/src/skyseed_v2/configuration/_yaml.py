"""Provide private fail-closed YAML parsing primitives for configuration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from yaml import YAMLError
from yaml.constructor import ConstructorError
from yaml.loader import SafeLoader
from yaml.nodes import MappingNode

from skyseed_v2.shared.time import normalize_utc_datetime


class _ConfigurationYamlError(ValueError):
    """Carry a private parser failure to the public owning loader."""


class _UniqueKeyYamlReader(SafeLoader):
    pass


def aware_datetime(value: Any, context: str) -> datetime:
    """Normalize one configuration datetime or raise a private parser error."""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise _ConfigurationYamlError(
                f"{context} must be an ISO 8601 timezone-aware datetime"
            ) from error
    try:
        return normalize_utc_datetime(value, context)
    except (TypeError, ValueError) as error:
        raise _ConfigurationYamlError(str(error)) from error


def _construct_unique_mapping(
    loader: _UniqueKeyYamlReader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyYamlReader.add_constructor(
    "tag:yaml.org,2002:map",
    _construct_unique_mapping,
)


def mapping(value: Any, context: str) -> dict[str, Any]:
    """Require a string-keyed mapping."""

    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise _ConfigurationYamlError(
            f"{context} must be a mapping with string keys"
        )
    return value


def sequence(value: Any, context: str) -> tuple[Any, ...]:
    """Require a YAML sequence and expose it as an immutable tuple."""

    if not isinstance(value, list):
        raise _ConfigurationYamlError(f"{context} must be a sequence")
    return tuple(value)


def fields(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    context: str,
) -> None:
    """Require the exact supported keys for one configuration mapping."""

    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise _ConfigurationYamlError(
            f"{context} is missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise _ConfigurationYamlError(
            f"{context} contains unknown fields: {sorted(unknown)}"
        )


def read_document(path: Path, context: str) -> dict[str, Any]:
    """Read one YAML document with unique-key and mapping enforcement."""

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise _ConfigurationYamlError(
            f"cannot read {context} file: {path}"
        ) from error
    try:
        payload = _UniqueKeyYamlReader(content).get_single_data()
    except YAMLError as error:
        raise _ConfigurationYamlError(
            f"{context} file is not valid YAML: {path}"
        ) from error
    return mapping(payload, context)


__all__: list[str] = []
