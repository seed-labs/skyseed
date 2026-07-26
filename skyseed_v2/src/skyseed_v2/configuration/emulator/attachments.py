"""Define immutable attachment values for static Emulator construction."""

from __future__ import annotations

from dataclasses import dataclass

from skyseed_v2.shared.identifier import validate_identifier

from ._validation import _ipv4_address, _positive_integer


@dataclass(frozen=True, slots=True)
class InternetExchangeAttachment:
    """Describe one node-to-IX attachment relation.

    Layer: immutable configuration auxiliary value. Programmatic input or the
    YAML reader creates it and static Emulator construction applies it. It is
    not a GroundStation property or SEED interface and is public from
    configuration for programmatic input.
    """

    ix_id: int
    address: str

    def __post_init__(self) -> None:
        _positive_integer(self.ix_id, "ix_id")
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


@dataclass(frozen=True, slots=True)
class LocalNetworkAttachment:
    """Describe one ordinary Router attachment to an AS-owned network.

    Layer: immutable configuration auxiliary value. A RouterConfig owns it and
    static Emulator construction applies it through ``joinNetwork``. It is not
    a SEED interface or Network and is public from configuration.
    """

    network_id: str
    address: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "network_id",
            validate_identifier(self.network_id, "network_id"),
        )
        object.__setattr__(
            self, "address", _ipv4_address(self.address, "address")
        )


__all__ = [
    "InternetExchangeAttachment",
    "LocalNetworkAttachment",
]
