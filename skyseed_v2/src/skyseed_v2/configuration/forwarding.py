"""Pure forwarding selection shared by assembly and execution boundaries."""
from enum import StrEnum


class ForwardingBackend(StrEnum):
    BPF = "bpf"
    LINUX = "linux"


def _backend(value):
    try:
        return ForwardingBackend(value)
    except (ValueError, TypeError):
        raise ValueError("forwarding_backend must be 'bpf' or 'linux'") from None
