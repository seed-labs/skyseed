"""Runner-local traffic telemetry abi mechanism."""

from __future__ import annotations

import socket
import struct
from typing import Any


_TELEMETRY_EVENT = struct.Struct("=QIIIIIIIBBBBBBHHHiI")


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
    0: "unknown",
    _TELEMETRY_DECISION_REDIRECT: "redirect",
    _TELEMETRY_DECISION_PASS: "pass",
    _TELEMETRY_DECISION_DROP: "drop",
}


_TELEMETRY_REASONS = {
    0: "unknown",
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
        "name": "skyseed-tc-ingress-v3",
        "size_bytes": _TELEMETRY_EVENT.size,
        "byte_order": "little-fixed-v3",
        "egress_ifindex_zero": "unknown",
        "packet_facts": "observed-before-handler",
        "result_metadata": "handler-reported",
        "pass": "TC_ACT_OK, not proof of local delivery",
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
        tc_action,
        facts,
    ) = _TELEMETRY_EVENT.unpack(payload)
    try:
        decision_name = _TELEMETRY_DECISIONS[decision]
        reason_name = _TELEMETRY_REASONS.get(reason, "unknown")
    except KeyError as error:
        raise ValueError("traffic telemetry event has an unknown enum") from error
    has_ipv4 = bool(facts & 1)
    if facts & ~3:
        raise ValueError("traffic telemetry event has unknown fact flags")
    has_icmp = icmp_valid == 1
    if icmp_valid not in (0, 1):
        raise ValueError("traffic telemetry event has an invalid ICMP marker")
    return {
        "record_type": "packet",
        "observation": "local-output" if facts & 2 else "ingress",
        "local_origin": bool(facts & 2),
        "kernel_monotonic_ns": timestamp_ns,
        "node_slot": node_slot,
        "generation": generation,
        "ingress_ifindex": ingress_ifindex,
        "selected_egress_ifindex": (
            None if egress_ifindex == 0 else egress_ifindex
        ),
        "source_ipv4": socket.inet_ntoa(struct.pack("=I", source_ipv4)) if has_ipv4 else None,
        "destination_ipv4": socket.inet_ntoa(
            struct.pack("=I", destination_ipv4)
        ) if has_ipv4 else None,
        "packet_length": packet_length,
        "decision": decision_name,
        "reason": reason_name,
        "protocol": protocol if has_ipv4 else None,
        "tc_action": tc_action,
        "reason_code": reason,
        "metadata_source": "handler",
        "ipv4_observed": has_ipv4,
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
