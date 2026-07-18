"""Private validation helpers shared by static input specifications."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from math import isfinite


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if not value[0].isalnum() or any(
        not (character.isalnum() or character in "_.-")
        for character in value
    ):
        raise ValueError(
            f"{field_name} must start with an alphanumeric character and "
            "contain only alphanumeric characters, '_', '.', or '-'"
        )
    return value


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _angle(value: float, field_name: str) -> float:
    normalized = float(value)
    if not isfinite(normalized) or not 0.0 <= normalized < 360.0:
        raise ValueError(f"{field_name} must be between 0 and 360 (exclusive)")
    return normalized


def _positive_integer(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _asn(value: int, field_name: str) -> int:
    normalized = _positive_integer(value, field_name)
    if normalized > 4_294_967_295:
        raise ValueError(f"{field_name} must be a valid 32-bit ASN")
    return normalized


def _ipv4_address(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an IPv4 address")
    try:
        return str(IPv4Address(value))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an IPv4 address") from error


def _ipv4_network(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an IPv4 network prefix")
    try:
        return str(IPv4Network(value, strict=True))
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be a canonical IPv4 network prefix"
        ) from error
