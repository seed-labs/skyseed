#!/usr/bin/env python3
# encoding: utf-8

"""Compiler for strict BPF-owned MPLS-like G/Z forwarding maps.

This module deliberately models satellite-core forwarding as FEC/label switching.
Only ingress classification can look at service targets. Intermediate core hops
are keyed by generation, ingress interface, label, and direction.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
from typing import Any, Iterable, Mapping, Sequence


LABEL_CARRIER = {
    "carrier": "tc_bpf_inserted_mpls_unicast_shim",
    "ethertype": "0x8847",
    "ingress": "push one MPLS-like shim at edge ingress",
    "core": "preserve or swap shim label across satellite-core redirects",
    "egress": "pop shim before UT/GW/Mini Internet handoff",
    "why_not_skb_mark": (
        "skb->mark is already used by parameter/qdisc handling and is not a "
        "packet-visible carrier; a real shim survives veth/netns redirects as "
        "packet bytes."
    ),
}

COUNTER_LABELS = (
    "classifier_hit",
    "label_push",
    "core_lfib_hit",
    "label_swap",
    "egress_handoff_hit",
    "label_pop",
    "miss",
    "drop_missing_label",
    "drop_missing_generation",
)

G_TO_Z = "g_to_z"
Z_TO_G = "z_to_g"


def assess_future_pre_attached_interface_inertness(
    *,
    bpf_attached: bool,
    interface_roles: Sequence[str],
    active_generation_entries_present: bool,
    classifier_key_present_for_active_generation: bool = False,
    core_lfib_key_present_for_active_generation: bool = False,
    egress_handoff_key_present_for_active_generation: bool = False,
    linux_bridge_forwarding: bool = False,
    linux_ip_route: bool = False,
    linux_mpls_route: bool = False,
    qdisc_clsact_attached: bool = True,
    link_up: bool = True,
) -> dict[str, Any]:
    """Audit whether a future attached interface is inert before activation."""

    roles = {
        str(role).strip().lower()
        for role in interface_roles
        if str(role).strip()
    }
    has_classifier_role = "classifier" in roles
    has_mpls_role = "mpls" in roles
    blockers: list[str] = []
    bypass_paths: list[dict[str, Any]] = []

    def add_path(path: str, *, blocked: bool, reason: str) -> None:
        bypass_paths.append(
            {
                "path": path,
                "blocked": bool(blocked),
                "reason": reason,
            }
        )

    if not bpf_attached:
        blockers.append("bpf_not_attached_to_future_interface")
    if not qdisc_clsact_attached:
        blockers.append("tc_clsact_qdisc_missing_bpf_not_on_ingress_path")
    if active_generation_entries_present:
        blockers.append("future_interface_has_current_active_generation_entries")
    if classifier_key_present_for_active_generation:
        blockers.append("future_classifier_key_present_for_current_generation")
    if core_lfib_key_present_for_active_generation:
        blockers.append("future_core_lfib_key_present_for_current_generation")
    if egress_handoff_key_present_for_active_generation:
        blockers.append("future_egress_handoff_key_present_for_current_generation")
    if linux_bridge_forwarding:
        blockers.append("linux_bridge_l2_bypass_present")
    if linux_ip_route:
        blockers.append("linux_ip_route_fallback_present")
    if linux_mpls_route:
        blockers.append("linux_mpls_route_fallback_present")

    bpf_on_path = bool(bpf_attached and qdisc_clsact_attached)
    explicit_inactive_drop = False
    expected_counter_delta: dict[str, int] = {}
    if not bpf_on_path:
        add_path(
            "tc_bpf_ingress",
            blocked=False,
            reason="bpf_or_clsact_missing_so_packets_can_bypass_bpf",
        )
    elif has_mpls_role and not has_classifier_role:
        blockers.append("tc_bpf_non_gz_ip_pass_allows_unclassified_ipv4")
        add_path(
            "tc_bpf_mpls_only_ipv4",
            blocked=False,
            reason="mpls_only_interface_role_returns_tc_act_ok_for_unclassified_ipv4",
        )
        expected_counter_delta = {
            "non_gz_ip_pass": 1,
            "pass": 1,
            "miss": 0,
            "drop": 0,
        }
    elif not roles:
        explicit_inactive_drop = True
        add_path(
            "tc_bpf_role_map_missing",
            blocked=True,
            reason="missing_interface_role_records_miss_and_drops",
        )
        expected_counter_delta = {"role_map_missing_count": 1, "miss": 1, "drop": 1}
    else:
        explicit_inactive_drop = True
        add_path(
            "tc_bpf_classifier_miss",
            blocked=True,
            reason="classifier_role_without_current_generation_key_records_miss_and_drops",
        )
        expected_counter_delta = {"miss": 1, "drop": 1}

    add_path(
        "linux_bridge_forwarding",
        blocked=not linux_bridge_forwarding,
        reason=(
            "bridge_forwarding_absent"
            if not linux_bridge_forwarding
            else "bridge_can_forward_without_generation_gate"
        ),
    )
    add_path(
        "linux_ip_route",
        blocked=not linux_ip_route,
        reason=(
            "ip_route_fallback_absent"
            if not linux_ip_route
            else "ip_route_can_forward_without_bpf_map_hit"
        ),
    )
    add_path(
        "linux_mpls_route",
        blocked=not linux_mpls_route,
        reason=(
            "mpls_route_fallback_absent"
            if not linux_mpls_route
            else "mpls_route_can_forward_without_bpf_lfib_hit"
        ),
    )
    add_path(
        "qdisc_link_state",
        blocked=bpf_on_path or not link_up,
        reason=(
            "clsact_bpf_attached_to_ingress"
            if bpf_on_path
            else "link_down_prevents_forwarding"
            if not link_up
            else "link_up_without_clsact_bpf_can_forward"
        ),
    )

    return {
        "safe": not blockers,
        "blockers": blockers,
        "required_state": {
            "bpf_attached": True,
            "qdisc_clsact_attached": True,
            "no_active_generation_entries_for_current_generation": True,
            "classifier_miss_drops_gz_traffic": True,
            "no_linux_bridge_forwarding": True,
            "no_linux_ip_route": True,
            "no_linux_mpls_route": True,
        },
        "observed_state": {
            "bpf_attached": bool(bpf_attached),
            "qdisc_clsact_attached": bool(qdisc_clsact_attached),
            "link_up": bool(link_up),
            "interface_roles": sorted(roles),
            "active_generation_entries_present": bool(active_generation_entries_present),
            "classifier_key_present_for_active_generation": bool(
                classifier_key_present_for_active_generation
            ),
            "core_lfib_key_present_for_active_generation": bool(
                core_lfib_key_present_for_active_generation
            ),
            "egress_handoff_key_present_for_active_generation": bool(
                egress_handoff_key_present_for_active_generation
            ),
            "linux_bridge_forwarding": bool(linux_bridge_forwarding),
            "linux_ip_route": bool(linux_ip_route),
            "linux_mpls_route": bool(linux_mpls_route),
        },
        "bypass_paths": bypass_paths,
        "explicit_inactive_drop_evidence": explicit_inactive_drop,
        "expected_counter_delta": expected_counter_delta,
        "linux_gz_fallback_command_count": int(
            bool(linux_ip_route or linux_mpls_route)
        ),
        "eligible_core_fallback_to_linux": int(
            bool(linux_ip_route or linux_mpls_route)
        ),
        "active_generation_flip_required": True,
    }


def audit_future_interface_physical_visibility(
    *,
    interface_ref: str,
    netns: str | None = None,
    dev: str | None = None,
    interface_exists: bool,
    link_up: bool = False,
    attached_to_bridge: bool = False,
    bridge_name: str | None = None,
    peer_exists: bool = False,
    peer_link_up: bool = False,
    peer_attached_to_reachable_path: bool = False,
    peer_netns: str | None = None,
    peer_dev: str | None = None,
    linux_ip_routes_present: bool = False,
    linux_mpls_routes_present: bool = False,
    active_generation_entry_present: bool = False,
) -> dict[str, Any]:
    """Audit whether TC_ACT_OK on a future interface has a physical path."""

    blockers: list[str] = []
    invisibility_enforcers: list[str] = []
    packet_after_tc_act_ok = "unknown"
    bridge_can_forward_after_ok = False

    if linux_ip_routes_present:
        blockers.append("linux_ip_route_fallback_present")
    if linux_mpls_routes_present:
        blockers.append("linux_mpls_route_fallback_present")

    if not interface_exists:
        invisibility_enforcers.append("interface_absent")
        packet_after_tc_act_ok = "no_device_no_ingress_path"
    elif not link_up:
        invisibility_enforcers.append("local_link_down")
        packet_after_tc_act_ok = "local_link_down_no_carrier"
    elif not peer_exists:
        invisibility_enforcers.append("peer_endpoint_absent")
        packet_after_tc_act_ok = "veth_peer_absent_or_unconnected"
    elif not peer_link_up:
        invisibility_enforcers.append("peer_link_down")
        packet_after_tc_act_ok = "peer_link_down_no_carrier"
    elif not attached_to_bridge and not peer_attached_to_reachable_path:
        invisibility_enforcers.append("no_bridge_or_reachable_peer_path")
        packet_after_tc_act_ok = "delivered_to_isolated_peer_only"
    else:
        bridge_can_forward_after_ok = bool(
            attached_to_bridge or peer_attached_to_reachable_path
        )
        packet_after_tc_act_ok = (
            "linux_bridge_or_peer_reachable_path"
            if bridge_can_forward_after_ok
            else "unknown"
        )
        blockers.append("bridge_peer_link_up_path_visible_after_tc_act_ok")

    bpf_authorized_forwarding = bool(active_generation_entry_present)
    if not bpf_authorized_forwarding:
        invisibility_enforcers.append("no_active_generation_entry")

    visible_risk = bool(
        bridge_can_forward_after_ok
        or linux_ip_routes_present
        or linux_mpls_routes_present
    )
    if visible_risk:
        classification = "EARLY_ATTACH_UNSAFE_PHYSICAL_VISIBLE"
    elif invisibility_enforcers:
        classification = "EARLY_ATTACH_SAFE_PHYSICAL_INVISIBLE"
    else:
        classification = "EARLY_ATTACH_VISIBILITY_UNKNOWN"

    return {
        "schema_version": "skyseed-future-interface-physical-visibility-v1",
        "interface_ref": str(interface_ref),
        "netns": netns,
        "dev": dev,
        "peer_netns": peer_netns,
        "peer_dev": peer_dev,
        "early_attach_classification": classification,
        "physical_visibility_proven": classification
        != "EARLY_ATTACH_VISIBILITY_UNKNOWN",
        "physically_visible_after_tc_act_ok": bool(visible_risk),
        "bpf_authorized_forwarding": bpf_authorized_forwarding,
        "bpf_attach_alone_changes_visibility": False,
        "lifecycle_boundary_required": (
            None if classification == "EARLY_ATTACH_VISIBILITY_UNKNOWN" else False
        ),
        "invisibility_enforced_by": invisibility_enforcers,
        "blockers": blockers,
        "observed_state": {
            "interface_exists": bool(interface_exists),
            "link_up": bool(link_up),
            "attached_to_bridge": bool(attached_to_bridge),
            "bridge_name": bridge_name,
            "peer_exists": bool(peer_exists),
            "peer_link_up": bool(peer_link_up),
            "peer_attached_to_reachable_path": bool(peer_attached_to_reachable_path),
            "linux_ip_routes_present": bool(linux_ip_routes_present),
            "linux_mpls_routes_present": bool(linux_mpls_routes_present),
            "active_generation_entry_present": bool(active_generation_entry_present),
        },
        "bridge_can_forward_if_tc_bpf_returns_ok": bool(bridge_can_forward_after_ok),
        "packet_after_tc_act_ok": packet_after_tc_act_ok,
    }


@dataclass(frozen=True)
class LinkSegment:
    link_id: str
    source_node_id: str
    source_interface: str
    destination_node_id: str
    destination_interface: str
    label: int


def stable_u32(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="little", signed=False)


def logical_ifindex(node_id: str, interface_name: str) -> int:
    return stable_u32(f"{node_id}:{interface_name}") & 0x7FFFFFFF


def _get(context: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return context.get(key, default)


def _segments(context: Mapping[str, Any], key: str) -> list[LinkSegment]:
    segments: list[LinkSegment] = []
    for raw in list(_get(context, key, ()) or ()):
        item = dict(raw)
        segments.append(
            LinkSegment(
                link_id=str(item["link_id"]),
                source_node_id=str(item["source_node_id"]),
                source_interface=str(item["source_interface"]),
                destination_node_id=str(item["destination_node_id"]),
                destination_interface=str(item["destination_interface"]),
                label=int(item["label"]),
            )
        )
    return segments


def _prefix_for_target(target_ip: str, traffic_scope: str) -> str:
    if traffic_scope == "special_prefix_override":
        network = ipaddress.ip_network(f"{target_ip}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{target_ip}/32", strict=False)
    return str(network)


def _flow_key(context: Mapping[str, Any]) -> int:
    seed = "|".join(
        [
            str(_get(context, "ut_id")),
            str(_get(context, "zone_id")),
            str(_get(context, "selected_gateway_id")),
            str(_get(context, "flow_id")),
            str(_get(context, "traffic_scope", "default_gateway_class")),
        ]
    )
    return stable_u32(seed)


def compile_bpf_owned_mpls_maps(
    contexts: Sequence[Mapping[str, Any]],
    *,
    generation: int = 0,
) -> dict[str, Any]:
    ingress_classifier_map: list[dict[str, Any]] = []
    core_lfib_map: list[dict[str, Any]] = []
    egress_handoff_map: list[dict[str, Any]] = []

    for context in contexts:
        forward_segments = _segments(context, "forward_segments")
        reverse_segments = _segments(context, "reverse_segments")
        if not forward_segments:
            continue

        flow_key = _flow_key(context)
        traffic_scope = str(_get(context, "traffic_scope", "default_gateway_class"))
        target_ip = str(_get(context, "target_ip"))
        first = forward_segments[0]
        initial_label = int(first.label)
        ingress_ifindex = logical_ifindex(
            str(_get(context, "serving_satellite_id")),
            str(_get(context, "ut_access_interface")),
        )
        ingress_classifier_map.append(
            {
                "key": {
                    "generation": generation,
                    "ingress_ifindex": ingress_ifindex,
                    "flow_key": flow_key,
                    "target_prefix": _prefix_for_target(target_ip, traffic_scope),
                    "service_class": traffic_scope,
                },
                "value": {
                    "fec_id": str(_get(context, "fec_id", _get(context, "flow_id"))),
                    "direction": G_TO_Z,
                    "initial_label": initial_label,
                    "out_ifindex": logical_ifindex(
                        first.source_node_id,
                        first.source_interface,
                    ),
                    "egress_node_id": first.source_node_id,
                    "egress_interface": first.source_interface,
                    "flow_id": str(_get(context, "flow_id")),
                    "ut_id": str(_get(context, "ut_id")),
                    "selected_gateway_id": str(_get(context, "selected_gateway_id")),
                    "label_action": "push",
                },
            }
        )

        for index, segment in enumerate(forward_segments):
            next_segment = (
                forward_segments[index + 1]
                if index + 1 < len(forward_segments)
                else None
            )
            ingress = logical_ifindex(segment.destination_node_id, segment.destination_interface)
            if next_segment is None:
                egress_handoff_map.append(
                    {
                        "key": {
                            "generation": generation,
                            "ingress_ifindex": ingress,
                            "label": int(segment.label),
                            "direction": G_TO_Z,
                        },
                        "value": {
                            "action": "pop_and_handoff",
                            "handoff": "gateway_or_mini_internet",
                            "node_id": segment.destination_node_id,
                            "interface": segment.destination_interface,
                            "flow_id": str(_get(context, "flow_id")),
                            "selected_gateway_id": str(_get(context, "selected_gateway_id")),
                            "label_action": "pop",
                        },
                    }
                )
                continue
            core_lfib_map.append(
                {
                    "key": {
                        "generation": generation,
                        "ingress_ifindex": ingress,
                        "label": int(segment.label),
                        "direction": G_TO_Z,
                    },
                    "value": {
                        "action": (
                            "swap"
                            if int(next_segment.label) != int(segment.label)
                            else "preserve"
                        ),
                        "out_label": int(next_segment.label),
                        "out_ifindex": logical_ifindex(
                            next_segment.source_node_id,
                            next_segment.source_interface,
                        ),
                        "egress_node_id": next_segment.source_node_id,
                        "egress_interface": next_segment.source_interface,
                        "flow_id": str(_get(context, "flow_id")),
                    },
                }
            )

        for index, segment in enumerate(reverse_segments):
            next_segment = (
                reverse_segments[index + 1]
                if index + 1 < len(reverse_segments)
                else None
            )
            ingress = logical_ifindex(segment.destination_node_id, segment.destination_interface)
            if next_segment is None:
                egress_handoff_map.append(
                    {
                        "key": {
                            "generation": generation,
                            "ingress_ifindex": ingress,
                            "label": int(segment.label),
                            "direction": Z_TO_G,
                        },
                        "value": {
                            "action": "pop_and_handoff",
                            "handoff": "ut_return",
                            "node_id": segment.destination_node_id,
                            "interface": segment.destination_interface,
                            "new_dst_ip": str(_get(context, "ut_access_ip")),
                            "flow_id": str(_get(context, "flow_id")),
                            "ut_id": str(_get(context, "ut_id")),
                            "label_action": "pop",
                        },
                    }
                )
                continue
            core_lfib_map.append(
                {
                    "key": {
                        "generation": generation,
                        "ingress_ifindex": ingress,
                        "label": int(segment.label),
                        "direction": Z_TO_G,
                    },
                    "value": {
                        "action": (
                            "swap"
                            if int(next_segment.label) != int(segment.label)
                            else "preserve"
                        ),
                        "out_label": int(next_segment.label),
                        "out_ifindex": logical_ifindex(
                            next_segment.source_node_id,
                            next_segment.source_interface,
                        ),
                        "egress_node_id": next_segment.source_node_id,
                        "egress_interface": next_segment.source_interface,
                        "flow_id": str(_get(context, "flow_id")),
                    },
                }
            )

    return {
        "schema_version": "skyseed-bpf-owned-mpls-like-dataplane-v1",
        "label_carrier": dict(LABEL_CARRIER),
        "active_generation": {"value": int(generation)},
        "ingress_classifier_map": ingress_classifier_map,
        "core_lfib_map": core_lfib_map,
        "egress_handoff_map": egress_handoff_map,
        "counters": {label: 0 for label in COUNTER_LABELS},
        "strict_contract": {
            "linux_gz_fallback_enabled": False,
            "intermediate_core_lookup_uses_dst_ip": False,
            "qdisc_owned_by_bpf": False,
            "lifecycle_owned_by_bpf": False,
        },
    }


def _find_entry(
    entries: Iterable[dict[str, Any]],
    key: Mapping[str, Any],
) -> dict[str, Any] | None:
    for entry in entries:
        entry_key = dict(entry.get("key") or {})
        if all(entry_key.get(name) == value for name, value in key.items()):
            return dict(entry)
    return None


def simulate_ingress_lookup(
    compiled: Mapping[str, Any],
    *,
    generation: int,
    ingress_ifindex: int,
    flow_key: int,
    target_ip: str,
    service_class: str,
) -> dict[str, Any]:
    prefixes = [
        _prefix_for_target(target_ip, service_class),
        str(ipaddress.ip_network(f"{target_ip}/32", strict=False)),
    ]
    for prefix in prefixes:
        entry = _find_entry(
            compiled.get("ingress_classifier_map", []),
            {
                "generation": generation,
                "ingress_ifindex": ingress_ifindex,
                "flow_key": flow_key,
                "target_prefix": prefix,
                "service_class": service_class,
            },
        )
        if entry:
            return {"ok": True, "action": "push", **dict(entry["value"])}
    return {
        "ok": False,
        "action": "drop",
        "reason": "ingress_classifier_miss_strict_no_linux_fallback",
        "linux_fallback": False,
    }


def simulate_core_lfib_lookup(
    compiled: Mapping[str, Any],
    *,
    generation: int,
    ingress_ifindex: int,
    label: int | None,
    direction: str,
    dst_ip: str | None = None,
) -> dict[str, Any]:
    if label is None:
        return {
            "ok": False,
            "action": "drop",
            "reason": "missing_label_strict_no_linux_fallback",
            "linux_fallback": False,
        }
    entry = _find_entry(
        compiled.get("core_lfib_map", []),
        {
            "generation": generation,
            "ingress_ifindex": ingress_ifindex,
            "label": int(label),
            "direction": direction,
        },
    )
    if not entry:
        return {
            "ok": False,
            "action": "drop",
            "reason": "core_lfib_miss_strict_no_linux_fallback",
            "linux_fallback": False,
            "dst_ip_considered": False,
        }
    value = dict(entry["value"])
    value["ok"] = True
    value["dst_ip_considered"] = False
    return value


def simulate_egress_handoff_lookup(
    compiled: Mapping[str, Any],
    *,
    generation: int,
    ingress_ifindex: int,
    label: int,
    direction: str,
) -> dict[str, Any]:
    entry = _find_entry(
        compiled.get("egress_handoff_map", []),
        {
            "generation": generation,
            "ingress_ifindex": ingress_ifindex,
            "label": int(label),
            "direction": direction,
        },
    )
    if not entry:
        return {
            "ok": False,
            "action": "drop",
            "reason": "egress_handoff_miss_strict_no_linux_fallback",
            "linux_fallback": False,
        }
    return {"ok": True, **dict(entry["value"])}
