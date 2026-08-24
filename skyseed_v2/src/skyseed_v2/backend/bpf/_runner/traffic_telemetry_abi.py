"""Define the Runner-private fixed traffic-telemetry event ABI."""

from __future__ import annotations

import socket
import struct
from typing import Any


_TELEMETRY_EVENT = struct.Struct("=QIIIIIIIBBBBBBHHH")
_TELEMETRY_DECISION_REDIRECT = 1
_TELEMETRY_DECISION_PASS = 2
_TELEMETRY_DECISION_DROP = 3
_TELEMETRY_REASON_REDIRECT = 1
_TELEMETRY_REASON_LOCAL = 2
_TELEMETRY_REASON_NON_IPV4 = 3
_TELEMETRY_REASON_TRUNCATED_L2 = 4
_TELEMETRY_REASON_TRUNCATED_IPV4 = 5
_TELEMETRY_REASON_NO_ACTIVE_GENERATION = 6
_TELEMETRY_REASON_INACTIVE_INGRESS = 7
_TELEMETRY_REASON_NO_ROUTE = 8
_TELEMETRY_DECISIONS = {
    _TELEMETRY_DECISION_REDIRECT: "redirect",
    _TELEMETRY_DECISION_PASS: "local/pass",
    _TELEMETRY_DECISION_DROP: "drop",
}
_TELEMETRY_REASONS = {
    _TELEMETRY_REASON_REDIRECT: "route-redirect",
    _TELEMETRY_REASON_LOCAL: "local-route",
    _TELEMETRY_REASON_NON_IPV4: "non-ipv4",
    _TELEMETRY_REASON_TRUNCATED_L2: "truncated-ethernet",
    _TELEMETRY_REASON_TRUNCATED_IPV4: "truncated-ipv4",
    _TELEMETRY_REASON_NO_ACTIVE_GENERATION: "no-active-generation",
    _TELEMETRY_REASON_INACTIVE_INGRESS: "inactive-ingress",
    _TELEMETRY_REASON_NO_ROUTE: "no-route",
}


def _telemetry_event_schema() -> dict[str, Any]:
    return {
        "name": "skyseed-tc-ingress-v1",
        "size_bytes": _TELEMETRY_EVENT.size,
        "byte_order": "native-fixed-v1",
        "egress_ifindex_zero": "non-redirect",
        "decisions": dict(_TELEMETRY_DECISIONS),
        "reasons": dict(_TELEMETRY_REASONS),
    }


def _decode_telemetry_event(payload: bytes) -> dict[str, Any]:
    if len(payload) != _TELEMETRY_EVENT.size:
        raise ValueError(
            "traffic telemetry event has an unexpected size: "
            f"{len(payload)}"
        )
    (
        timestamp_ns,
        node_slot,
        generation,
        ingress_ifindex,
        egress_ifindex,
        source_ipv4,
        destination_ipv4,
        packet_length,
        decision,
        reason,
        protocol,
        icmp_valid,
        icmp_type,
        icmp_code,
        icmp_identifier,
        icmp_sequence,
        _padding,
    ) = _TELEMETRY_EVENT.unpack(payload)
    try:
        decision_name = _TELEMETRY_DECISIONS[decision]
        reason_name = _TELEMETRY_REASONS[reason]
    except KeyError as error:
        raise ValueError("traffic telemetry event has an unknown enum") from error
    has_icmp = icmp_valid == 1
    if icmp_valid not in (0, 1):
        raise ValueError("traffic telemetry event has an invalid ICMP marker")
    return {
        "record_type": "packet",
        "observation": "ingress",
        "kernel_monotonic_ns": timestamp_ns,
        "node_slot": node_slot,
        "generation": generation,
        "ingress_ifindex": ingress_ifindex,
        "selected_egress_ifindex": (
            None if egress_ifindex == 0 else egress_ifindex
        ),
        "source_ipv4": socket.inet_ntoa(struct.pack("=I", source_ipv4)),
        "destination_ipv4": socket.inet_ntoa(
            struct.pack("=I", destination_ipv4)
        ),
        "packet_length": packet_length,
        "decision": decision_name,
        "reason": reason_name,
        "protocol": protocol,
        "icmp": {
            "valid": has_icmp,
            "type": icmp_type if has_icmp else None,
            "code": icmp_code if has_icmp else None,
            "identifier": (
                socket.ntohs(icmp_identifier) if has_icmp else None
            ),
            "sequence": socket.ntohs(icmp_sequence) if has_icmp else None,
        },
    }


__all__: list[str] = []
