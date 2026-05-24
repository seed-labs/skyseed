#!/usr/bin/env python3
# encoding: utf-8

"""Reusable static MPLS control definitions for the satellite example."""

from __future__ import annotations

import ipaddress
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class StaticMplsPathRole:
    role: str
    node_id: str


@dataclass(frozen=True)
class LinkStateEndpointExpectation:
    node_id: str
    interface_name: str
    ip_address: str
    logical_direction: Optional[str] = None


@dataclass(frozen=True)
class LinkStateSlotRequirement:
    slot_name: str
    slot_type: str
    binding: Dict[str, str]
    dynamic_endpoints: Tuple[LinkStateEndpointExpectation, ...]
    preconnected_endpoints: Tuple[LinkStateEndpointExpectation, ...] = ()


@dataclass(frozen=True)
class StaticMplsLspProfile:
    profile_id: str
    description: str
    path_roles: Tuple[StaticMplsPathRole, ...]
    required_slots: Tuple[LinkStateSlotRequirement, ...]
    gateway_slot: str
    gateway_binding: Dict[str, str]
    gateway_attach_request: str
    cross_slot: str
    cross_binding: Dict[str, str]
    cross_connect_request: str
    ping_source: str
    ping_target: str
    forward_push_label: int
    forward_swap_label: int
    reverse_push_label: int
    reverse_swap_label: int
    ingress_node_id: str
    ingress_interface: str
    ingress_next_hop: str
    transit_node_id: str
    transit_ingress_interface: str
    transit_egress_interface: str
    transit_next_hop: str
    transit_reverse_next_hop: str
    egress_node_id: str
    egress_interface: str
    egress_next_hop: str


DEFAULT_STATIC_LSP_PROFILE = StaticMplsLspProfile(
    profile_id="sat-a1:sat-b1:gw1",
    description="Minimal static MPLS LSP from sat-a1 to gw1 via sat-b1.",
    path_roles=(
        StaticMplsPathRole(role="ingress", node_id="sat-a1"),
        StaticMplsPathRole(role="transit", node_id="sat-b1"),
        StaticMplsPathRole(role="egress", node_id="gw1"),
    ),
    required_slots=(
        LinkStateSlotRequirement(
            slot_name="gw1-gateway-slot1",
            slot_type="gw_access",
            binding={"gateway_id": "gw1", "satellite_id": "sat-b1"},
            dynamic_endpoints=(
                LinkStateEndpointExpectation(
                    node_id="sat-b1",
                    interface_name="ga-g1s1",
                    ip_address="10.91.12.1/29",
                ),
            ),
            preconnected_endpoints=(
                LinkStateEndpointExpectation(
                    node_id="gw1",
                    interface_name="ga-g1s1",
                    ip_address="10.91.12.2/29",
                ),
            ),
        ),
        LinkStateSlotRequirement(
            slot_name="ab-cross-slot0",
            slot_type="cross_plane_isl",
            binding={
                "satellite_a_id": "sat-a1",
                "satellite_b_id": "sat-b1",
                "satellite_a_direction": "left",
                "satellite_b_direction": "right",
            },
            dynamic_endpoints=(
                LinkStateEndpointExpectation(
                    node_id="sat-a1",
                    interface_name="xp0a",
                    ip_address="10.92.1.1/29",
                    logical_direction="left",
                ),
                LinkStateEndpointExpectation(
                    node_id="sat-b1",
                    interface_name="xp0b",
                    ip_address="10.92.1.2/29",
                    logical_direction="right",
                ),
            ),
        ),
    ),
    gateway_slot="gw1-gateway-slot1",
    gateway_binding={"gateway_id": "gw1", "satellite_id": "sat-b1"},
    gateway_attach_request="gw1:sat-b1",
    cross_slot="ab-cross-slot0",
    cross_binding={
        "satellite_a_id": "sat-a1",
        "satellite_b_id": "sat-b1",
        "satellite_a_direction": "left",
        "satellite_b_direction": "right",
    },
    cross_connect_request="sat-a1:sat-b1:left:right",
    ping_source="10.250.1.2",
    ping_target="10.154.0.129",
    forward_push_label=100,
    forward_swap_label=200,
    reverse_push_label=300,
    reverse_swap_label=400,
    ingress_node_id="sat-a1",
    ingress_interface="xp0a",
    ingress_next_hop="10.92.1.2",
    transit_node_id="sat-b1",
    transit_ingress_interface="xp0b",
    transit_egress_interface="ga-g1s1",
    transit_next_hop="10.91.12.2",
    transit_reverse_next_hop="10.92.1.1",
    egress_node_id="gw1",
    egress_interface="ga-g1s1",
    egress_next_hop="10.91.12.1",
)


def get_static_mpls_profile(profile_id: str) -> StaticMplsLspProfile:
    if profile_id != DEFAULT_STATIC_LSP_PROFILE.profile_id:
        raise ValueError(
            "unsupported static MPLS profile: "
            f"{profile_id}. Supported profile: {DEFAULT_STATIC_LSP_PROFILE.profile_id}"
        )
    return DEFAULT_STATIC_LSP_PROFILE


def _validate_link_state_endpoints(
    actual_endpoints: Sequence[Dict[str, Any]],
    expected_endpoints: Sequence[LinkStateEndpointExpectation],
) -> List[str]:
    issues: List[str] = []
    actual_by_node = {str(endpoint.get("node_id")): endpoint for endpoint in actual_endpoints}
    for expected in expected_endpoints:
        actual = actual_by_node.get(expected.node_id)
        if actual is None:
            issues.append(f"missing_endpoint:{expected.node_id}")
            continue
        if str(actual.get("interface_name")) != expected.interface_name:
            issues.append(
                f"endpoint_interface_mismatch:{expected.node_id}:"
                f"{actual.get('interface_name')}!={expected.interface_name}"
            )
        if str(actual.get("ip_address")) != expected.ip_address:
            issues.append(
                f"endpoint_ip_mismatch:{expected.node_id}:"
                f"{actual.get('ip_address')}!={expected.ip_address}"
            )
        expected_direction = expected.logical_direction
        actual_direction = actual.get("logical_direction")
        if expected_direction is not None and str(actual_direction) != expected_direction:
            issues.append(
                f"endpoint_direction_mismatch:{expected.node_id}:"
                f"{actual_direction}!={expected_direction}"
            )
    return issues


def validate_static_mpls_path(
    link_state: Dict[str, Any],
    profile: StaticMplsLspProfile,
) -> List[str]:
    if not link_state.get("runtime_initialized", False):
        return ["runtime_not_initialized"]

    slot_entries = {
        str(entry.get("slot_name")): entry
        for entry in link_state.get("slots", [])
    }
    issues: List[str] = []
    for requirement in profile.required_slots:
        entry = slot_entries.get(requirement.slot_name)
        if entry is None:
            issues.append(f"missing_slot:{requirement.slot_name}")
            continue
        if str(entry.get("slot_type")) != requirement.slot_type:
            issues.append(
                f"slot_type_mismatch:{requirement.slot_name}:"
                f"{entry.get('slot_type')}!={requirement.slot_type}"
            )
        if not entry.get("occupied", False):
            issues.append(f"slot_not_occupied:{requirement.slot_name}")
            continue
        if entry.get("binding") != requirement.binding:
            issues.append(f"slot_binding_mismatch:{requirement.slot_name}")
        issues.extend(
            f"{requirement.slot_name}:{issue}"
            for issue in _validate_link_state_endpoints(
                entry.get("dynamic_endpoints", []),
                requirement.dynamic_endpoints,
            )
        )
        issues.extend(
            f"{requirement.slot_name}:preconnected:{issue}"
            for issue in _validate_link_state_endpoints(
                entry.get("preconnected_endpoints", []),
                requirement.preconnected_endpoints,
            )
        )
    return issues


def static_mpls_ready_node_ids(profile: StaticMplsLspProfile) -> List[str]:
    return sorted({profile.ingress_node_id, profile.transit_node_id, profile.egress_node_id})


def static_mpls_install_specs(
    profile: StaticMplsLspProfile,
) -> List[Tuple[str, List[str], str]]:
    return [
        (
            profile.transit_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.transit_ingress_interface}.input=1"],
            f"enable MPLS input on {profile.transit_node_id} {profile.transit_ingress_interface}",
        ),
        (
            profile.egress_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.egress_interface}.input=1"],
            f"enable MPLS input on {profile.egress_node_id} {profile.egress_interface}",
        ),
        (
            profile.transit_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.transit_egress_interface}.input=1"],
            f"enable reverse MPLS input on {profile.transit_node_id} {profile.transit_egress_interface}",
        ),
        (
            profile.ingress_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.ingress_interface}.input=1"],
            f"enable reverse MPLS input on {profile.ingress_node_id} {profile.ingress_interface}",
        ),
        (
            profile.ingress_node_id,
            [
                "ip",
                "route",
                "replace",
                f"{profile.ping_target}/32",
                "encap",
                "mpls",
                str(profile.forward_push_label),
                "via",
                "inet",
                profile.ingress_next_hop,
                "dev",
                profile.ingress_interface,
            ],
            f"install ingress push route on {profile.ingress_node_id}",
        ),
        (
            profile.transit_node_id,
            [
                "ip",
                "-f",
                "mpls",
                "route",
                "replace",
                str(profile.forward_push_label),
                "as",
                str(profile.forward_swap_label),
                "via",
                "inet",
                profile.transit_next_hop,
                "dev",
                profile.transit_egress_interface,
            ],
            f"install transit swap LFIB on {profile.transit_node_id}",
        ),
        (
            profile.egress_node_id,
            [
                "ip",
                "-f",
                "mpls",
                "route",
                "replace",
                str(profile.forward_swap_label),
                "dev",
                "lo",
            ],
            f"install egress pop LFIB on {profile.egress_node_id}",
        ),
        (
            profile.egress_node_id,
            [
                "ip",
                "route",
                "replace",
                f"{profile.ping_source}/32",
                "encap",
                "mpls",
                str(profile.reverse_push_label),
                "via",
                "inet",
                profile.egress_next_hop,
                "dev",
                profile.egress_interface,
            ],
            f"install reverse push route on {profile.egress_node_id}",
        ),
        (
            profile.transit_node_id,
            [
                "ip",
                "-f",
                "mpls",
                "route",
                "replace",
                str(profile.reverse_push_label),
                "as",
                str(profile.reverse_swap_label),
                "via",
                "inet",
                profile.transit_reverse_next_hop,
                "dev",
                profile.transit_ingress_interface,
            ],
            f"install reverse swap LFIB on {profile.transit_node_id}",
        ),
        (
            profile.ingress_node_id,
            [
                "ip",
                "-f",
                "mpls",
                "route",
                "replace",
                str(profile.reverse_swap_label),
                "dev",
                "lo",
            ],
            f"install ingress reverse pop LFIB on {profile.ingress_node_id}",
        ),
    ]


def static_mpls_cleanup_specs(
    profile: StaticMplsLspProfile,
) -> List[Tuple[str, List[str], bool, str]]:
    return [
        (
            profile.ingress_node_id,
            ["ip", "-f", "mpls", "route", "del", str(profile.reverse_swap_label)],
            True,
            f"delete ingress reverse pop LFIB on {profile.ingress_node_id}",
        ),
        (
            profile.transit_node_id,
            ["ip", "-f", "mpls", "route", "del", str(profile.reverse_push_label)],
            True,
            f"delete reverse swap LFIB on {profile.transit_node_id}",
        ),
        (
            profile.egress_node_id,
            ["ip", "route", "del", f"{profile.ping_source}/32"],
            True,
            f"delete reverse push route on {profile.egress_node_id}",
        ),
        (
            profile.egress_node_id,
            ["ip", "-f", "mpls", "route", "del", str(profile.forward_swap_label)],
            True,
            f"delete egress pop LFIB on {profile.egress_node_id}",
        ),
        (
            profile.transit_node_id,
            ["ip", "-f", "mpls", "route", "del", str(profile.forward_push_label)],
            True,
            f"delete transit swap LFIB on {profile.transit_node_id}",
        ),
        (
            profile.ingress_node_id,
            ["ip", "route", "del", f"{profile.ping_target}/32"],
            True,
            f"delete ingress push route on {profile.ingress_node_id}",
        ),
        (
            profile.ingress_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.ingress_interface}.input=0"],
            True,
            f"disable reverse MPLS input on {profile.ingress_node_id} {profile.ingress_interface}",
        ),
        (
            profile.transit_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.transit_egress_interface}.input=0"],
            True,
            f"disable reverse MPLS input on {profile.transit_node_id} {profile.transit_egress_interface}",
        ),
        (
            profile.egress_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.egress_interface}.input=0"],
            True,
            f"disable MPLS input on {profile.egress_node_id} {profile.egress_interface}",
        ),
        (
            profile.transit_node_id,
            ["sysctl", "-w", f"net.mpls.conf.{profile.transit_ingress_interface}.input=0"],
            True,
            f"disable MPLS input on {profile.transit_node_id} {profile.transit_ingress_interface}",
        ),
    ]


def static_mpls_route_show_specs(
    profile: StaticMplsLspProfile,
) -> List[Tuple[str, List[str], str]]:
    return [
        (
            profile.ingress_node_id,
            ["ip", "route", "show", f"{profile.ping_target}/32"],
            f"show {profile.ingress_node_id} push route",
        ),
        (
            profile.transit_node_id,
            ["ip", "-f", "mpls", "route", "show"],
            f"show {profile.transit_node_id} LFIB",
        ),
        (
            profile.egress_node_id,
            ["ip", "route", "show", f"{profile.ping_source}/32"],
            f"show {profile.egress_node_id} reverse route",
        ),
        (
            profile.egress_node_id,
            ["ip", "-f", "mpls", "route", "show"],
            f"show {profile.egress_node_id} pop LFIB",
        ),
    ]


def static_mpls_push_capture_command(profile: StaticMplsLspProfile) -> List[str]:
    return [
        "timeout",
        "5",
        "tcpdump",
        "-ni",
        profile.transit_ingress_interface,
        "-c",
        "1",
        "-vv",
        "-l",
        "mpls",
    ]


def static_mpls_pop_capture_command(profile: StaticMplsLspProfile) -> List[str]:
    return [
        "timeout",
        "5",
        "tcpdump",
        "-ni",
        "any",
        "-c",
        "1",
        "-vv",
        "-l",
        f"icmp and host {profile.ping_target}",
    ]


def static_mpls_ping_command(profile: StaticMplsLspProfile) -> List[str]:
    return [
        "ping",
        "-c",
        "3",
        "-W",
        "1",
        profile.ping_target,
    ]


@dataclass(frozen=True)
class ActiveLinkEndpoint:
    node_id: str
    interface_name: str
    ip_address: str
    logical_direction: Optional[str] = None

    @property
    def host_ip(self) -> str:
        return str(ipaddress.ip_interface(self.ip_address).ip)


@dataclass(frozen=True)
class ActiveLink:
    slot_name: str
    slot_type: str
    subnet: str
    binding: Dict[str, str]
    endpoints: Tuple[ActiveLinkEndpoint, ActiveLinkEndpoint]

    def endpoint_for(self, node_id: str) -> ActiveLinkEndpoint:
        for endpoint in self.endpoints:
            if endpoint.node_id == node_id:
                return endpoint
        raise KeyError(f"{node_id} is not attached to {self.slot_name}")

    def peer_endpoint(self, node_id: str) -> ActiveLinkEndpoint:
        for endpoint in self.endpoints:
            if endpoint.node_id != node_id:
                return endpoint
        raise KeyError(f"{self.slot_name} does not have a peer for {node_id}")

    def connects(self, first_node_id: str, second_node_id: str) -> bool:
        endpoint_ids = {endpoint.node_id for endpoint in self.endpoints}
        return {first_node_id, second_node_id} == endpoint_ids


@dataclass(frozen=True)
class UtEgressMplsIntent:
    intent_id: str
    ut_id: str
    stable_identity: str
    default_ping_target: str
    forward_label_seed: int
    reverse_label_seed: int


@dataclass(frozen=True)
class UtEgressMplsPlan:
    intent: UtEgressMplsIntent
    ut_slot: ActiveLink
    gateway_slot: ActiveLink
    satellite_path: Tuple[str, ...]
    forward_links: Tuple[ActiveLink, ...]
    reverse_links: Tuple[ActiveLink, ...]
    forward_labels: Tuple[int, ...]
    reverse_labels: Tuple[int, ...]
    local_exclusion_prefixes: Tuple[str, ...]
    policy_guardrail_prefixes: Tuple[str, ...]
    policy_table_id: int
    policy_rule_priority: int

    @property
    def ut_id(self) -> str:
        return self.intent.ut_id

    @property
    def stable_identity(self) -> str:
        return self.intent.stable_identity

    @property
    def destination(self) -> str:
        return self.intent.default_ping_target

    @property
    def default_ping_target(self) -> str:
        return self.intent.default_ping_target

    @property
    def serving_satellite_id(self) -> str:
        return self.ut_slot.peer_endpoint(self.ut_id).node_id

    @property
    def selected_gateway_id(self) -> str:
        gateway_endpoint = next(
            endpoint for endpoint in self.gateway_slot.endpoints
            if endpoint.node_id.startswith("gw")
        )
        return gateway_endpoint.node_id

    @property
    def selected_gateway_satellite_id(self) -> str:
        return self.gateway_slot.peer_endpoint(self.selected_gateway_id).node_id

    @property
    def profile_id(self) -> str:
        return self.intent.intent_id

    @property
    def forward_mpls_nodes(self) -> Tuple[str, ...]:
        return (*self.satellite_path, self.selected_gateway_id)

    @property
    def reverse_mpls_nodes(self) -> Tuple[str, ...]:
        return tuple(reversed(self.forward_mpls_nodes))

    @property
    def ut_endpoint(self) -> ActiveLinkEndpoint:
        return self.ut_slot.endpoint_for(self.ut_id)

    @property
    def serving_satellite_ut_endpoint(self) -> ActiveLinkEndpoint:
        return self.ut_slot.peer_endpoint(self.ut_id)

    @property
    def gateway_endpoint(self) -> ActiveLinkEndpoint:
        return self.gateway_slot.endpoint_for(self.selected_gateway_id)

    @property
    def gateway_satellite_endpoint(self) -> ActiveLinkEndpoint:
        return self.gateway_slot.peer_endpoint(self.selected_gateway_id)

    @property
    def required_slot_names(self) -> Tuple[str, ...]:
        slot_names = [self.ut_slot.slot_name]
        slot_names.extend(link.slot_name for link in self.forward_links)
        return tuple(slot_names)

    @property
    def swap_nodes(self) -> Tuple[str, ...]:
        return self.forward_mpls_nodes[1:-1]

    @property
    def active_access_slot_name(self) -> str:
        return self.ut_slot.slot_name

    @property
    def active_access_interface(self) -> str:
        return self.ut_endpoint.interface_name

    @property
    def active_access_ip(self) -> str:
        return self.ut_endpoint.host_ip

    @property
    def serving_satellite_access_interface(self) -> str:
        return self.serving_satellite_ut_endpoint.interface_name

    @property
    def serving_satellite_access_ip(self) -> str:
        return self.serving_satellite_ut_endpoint.host_ip

    @property
    def return_target_prefix(self) -> str:
        return f"{self.active_access_ip}/32"

    @property
    def default_egress_prefix(self) -> str:
        return "default"

    @property
    def gateway_nat_interface(self) -> str:
        return gateway_nat_realization_for_gateway(self.selected_gateway_id)["nat_interface"]

    @property
    def gateway_nat_source_ip(self) -> str:
        return gateway_nat_realization_for_gateway(self.selected_gateway_id)["nat_source_ip"]


@dataclass(frozen=True)
class UtEgressCommandSpec:
    node_id: str
    command: Tuple[str, ...]
    stage: str
    category: str
    allow_failure: bool = False
    shared_key: Optional[str] = None
    purpose: Optional[str] = None
    expected_state: Optional[str] = None


@dataclass(frozen=True)
class UtEgressPolicyPreparationPlan:
    ut_id: str
    policy_table_id: int
    policy_rule_priority: int
    source_prefixes: Tuple[str, ...]
    candidate_serving_satellite_ids: Tuple[str, ...]
    guardrail_prefixes: Tuple[str, ...]



GATEWAY_NAT_REALIZATIONS: Dict[str, Dict[str, str]] = {
    "gw1": {
        "nat_interface": "ix100",
        "nat_source_ip": "10.65.21.2",
    },
    "gw2": {
        "nat_interface": "ix101",
        "nat_source_ip": "10.65.22.2",
    },
    "gw3": {
        "nat_interface": "ix102",
        "nat_source_ip": "10.65.23.2",
    },
}


def gateway_nat_realization_for_gateway(gateway_id: str) -> Dict[str, str]:
    try:
        return GATEWAY_NAT_REALIZATIONS[gateway_id]
    except KeyError as exc:
        supported = ", ".join(sorted(GATEWAY_NAT_REALIZATIONS))
        raise ValueError(
            f"unsupported gateway NAT realization: {gateway_id}. Supported gateways: {supported}"
        ) from exc


SUPPORTED_UT_EGRESS_INTENTS = {
    "ut1": UtEgressMplsIntent(
        intent_id="ut1-egress-to-miniinternet",
        ut_id="ut1",
        stable_identity="100.64.0.1",
        default_ping_target="10.154.0.129",
        forward_label_seed=100,
        reverse_label_seed=200,
    ),
    "ut2": UtEgressMplsIntent(
        intent_id="ut2-egress-to-miniinternet",
        ut_id="ut2",
        stable_identity="100.64.0.2",
        default_ping_target="10.154.0.129",
        forward_label_seed=300,
        reverse_label_seed=400,
    ),
    "ut3": UtEgressMplsIntent(
        intent_id="ut3-egress-to-miniinternet",
        ut_id="ut3",
        stable_identity="100.64.0.3",
        default_ping_target="10.154.0.129",
        forward_label_seed=500,
        reverse_label_seed=600,
    ),
}


def get_ut_egress_intent(ut_id: str) -> UtEgressMplsIntent:
    try:
        return SUPPORTED_UT_EGRESS_INTENTS[ut_id]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_UT_EGRESS_INTENTS))
        raise ValueError(
            f"unsupported UT egress intent: {ut_id}. Supported UTs: {supported}"
        ) from exc


def supported_ut_egress_ut_ids() -> Tuple[str, ...]:
    return tuple(sorted(SUPPORTED_UT_EGRESS_INTENTS))


def _sorted_prefixes(prefixes: Iterable[str]) -> Tuple[str, ...]:
    networks = {
        ipaddress.ip_network(str(prefix), strict=False)
        for prefix in prefixes
        if str(prefix)
    }
    return tuple(
        str(network)
        for network in sorted(
            networks,
            key=lambda item: (item.version, int(item.network_address), item.prefixlen),
        )
    )


def _collapse_prefixes(prefixes: Iterable[str]) -> Tuple[str, ...]:
    networks = [
        ipaddress.ip_network(str(prefix), strict=False)
        for prefix in prefixes
        if str(prefix)
    ]
    return tuple(str(network) for network in ipaddress.collapse_addresses(networks))


def topology_internet_egress_exclusion_prefixes(
    topology: Dict[str, Any],
) -> Tuple[str, ...]:
    prefixes: List[str] = []
    for satellite in topology.get("satellites", []):
        bootstrap = satellite.get("bootstrap_network", {})
        if bootstrap.get("subnet"):
            prefixes.append(str(bootstrap.get("subnet")))
    for gateway in topology.get("gateways", []):
        bootstrap = gateway.get("bootstrap_network", {})
        if bootstrap.get("subnet"):
            prefixes.append(str(bootstrap.get("subnet")))
        uplink = gateway.get("internet_uplink", {})
        uplink_ip = uplink.get("ip_address")
        if uplink_ip:
            prefixes.append(
                str(ipaddress.ip_network(f"{uplink_ip}/24", strict=False))
            )
    for ut in topology.get("user_terminals", []):
        bootstrap = ut.get("bootstrap_network", {})
        if bootstrap.get("subnet"):
            prefixes.append(str(bootstrap.get("subnet")))
        identity = ut.get("identity_interface", {})
        if identity.get("ip_address"):
            prefixes.append(str(identity.get("ip_address")))
    fixed_links = topology.get("fixed_links", {})
    for link_group in fixed_links.values():
        for link in link_group:
            subnet = link.get("subnet")
            if subnet:
                prefixes.append(str(subnet))
    slot_inventory = topology.get("slot_inventory", {})
    for slot_group in slot_inventory.values():
        for slot in slot_group:
            subnet = slot.get("subnet")
            if subnet:
                prefixes.append(str(subnet))
    return _sorted_prefixes(prefixes)


def topology_ut_egress_guardrail_prefixes(
    topology: Dict[str, Any],
) -> Tuple[str, ...]:
    prefixes: List[str] = []
    gateway_bootstrap_subnets = [
        str(gateway.get("bootstrap_network", {}).get("subnet"))
        for gateway in topology.get("gateways", [])
        if gateway.get("bootstrap_network", {}).get("subnet")
    ]
    prefixes.extend(gateway_bootstrap_subnets)

    if topology.get("slot_inventory", {}).get("ut_access") or topology.get("slot_inventory", {}).get(
        "gateway_access"
    ):
        prefixes.append("10.90.0.0/15")
    if topology.get("slot_inventory", {}).get("cross_plane_isl"):
        prefixes.append("10.92.0.0/16")

    gateway_uplink_prefixes = []
    for gateway in topology.get("gateways", []):
        uplink = gateway.get("internet_uplink", {})
        if uplink.get("ip_address"):
            gateway_uplink_prefixes.append(
                str(ipaddress.ip_network(f"{uplink.get('ip_address')}/24", strict=False))
            )
    prefixes.extend(_collapse_prefixes(gateway_uplink_prefixes))

    raw_local_prefixes = topology_internet_egress_exclusion_prefixes(topology)
    if any(prefix.startswith("10.250.") for prefix in raw_local_prefixes):
        prefixes.append("10.250.0.0/16")
    if any(prefix.startswith("100.64.") for prefix in raw_local_prefixes):
        prefixes.append("100.64.0.0/29")
    return _sorted_prefixes(prefixes)


def _node_bootstrap_subnet(topology: Dict[str, Any], node_id: str) -> Optional[str]:
    for satellite in topology.get("satellites", []):
        if satellite.get("satellite_id") == node_id:
            bootstrap = satellite.get("bootstrap_network", {})
            subnet = bootstrap.get("subnet")
            return str(subnet) if subnet else None
    for gateway in topology.get("gateways", []):
        if gateway.get("gateway_id") == node_id:
            bootstrap = gateway.get("bootstrap_network", {})
            subnet = bootstrap.get("subnet")
            return str(subnet) if subnet else None
    for ut in topology.get("user_terminals", []):
        if ut.get("ut_id") == node_id:
            bootstrap = ut.get("bootstrap_network", {})
            subnet = bootstrap.get("subnet")
            return str(subnet) if subnet else None
    return None


def _node_connected_prefixes(
    topology: Dict[str, Any],
    active_links: Sequence[ActiveLink],
    node_id: str,
) -> Tuple[str, ...]:
    prefixes: List[str] = []
    bootstrap_subnet = _node_bootstrap_subnet(topology, node_id)
    if bootstrap_subnet:
        prefixes.append(bootstrap_subnet)
    for link in active_links:
        if any(endpoint.node_id == node_id for endpoint in link.endpoints):
            prefixes.append(link.subnet)
    return _sorted_prefixes(prefixes)


def _ut_policy_table_id(ut_id: str) -> int:
    return 1000 + int(ut_id.removeprefix("ut"))


def _ut_policy_rule_priority(ut_id: str) -> int:
    return 1000 + int(ut_id.removeprefix("ut"))


def ut_egress_policy_preparation_plan(
    topology: Dict[str, Any],
    ut_id: str,
) -> UtEgressPolicyPreparationPlan:
    get_ut_egress_intent(ut_id)
    source_prefixes: List[str] = []
    candidate_satellite_ids: set[str] = set()
    for slot in topology.get("slot_inventory", {}).get("ut_access", []):
        if slot.get("ut_id") != ut_id:
            continue
        preconnected = slot.get("preconnected_endpoint", {})
        if preconnected.get("ip_address"):
            source_prefixes.append(
                str(ipaddress.ip_interface(str(preconnected.get("ip_address"))).ip) + "/32"
            )
        candidate_satellite_ids.update(str(node_id) for node_id in slot.get("candidate_satellite_ids", []))
    if not source_prefixes:
        raise ValueError(f"no ut_access source prefixes found for {ut_id}")
    if not candidate_satellite_ids:
        raise ValueError(f"no candidate serving satellites found for {ut_id}")
    return UtEgressPolicyPreparationPlan(
        ut_id=ut_id,
        policy_table_id=_ut_policy_table_id(ut_id),
        policy_rule_priority=_ut_policy_rule_priority(ut_id),
        source_prefixes=_sorted_prefixes(source_prefixes),
        candidate_serving_satellite_ids=tuple(sorted(candidate_satellite_ids)),
        guardrail_prefixes=topology_ut_egress_guardrail_prefixes(topology),
    )


def compute_all_attached_ut_egress_plans(
    topology: Dict[str, Any],
    link_state: Dict[str, Any],
) -> Tuple[Tuple[UtEgressMplsPlan, ...], List[str]]:
    plans: List[UtEgressMplsPlan] = []
    issues: List[str] = []
    for ut_id in supported_ut_egress_ut_ids():
        plan, errors = compute_ut_egress_plan(topology, link_state, ut_id)
        if plan is not None:
            plans.append(plan)
            continue
        if errors == [f"ut_not_attached:{ut_id}"]:
            continue
        issues.extend(errors)
    if issues:
        return (), issues
    if not plans:
        return (), ["no_supported_ut_attached"]
    return tuple(sorted(plans, key=lambda plan: plan.ut_id)), []



def compute_ut_egress_plan(
    topology: Dict[str, Any],
    link_state: Dict[str, Any],
    ut_id: str,
) -> Tuple[Optional[UtEgressMplsPlan], List[str]]:
    if not link_state.get("runtime_initialized", False):
        return None, ["runtime_not_initialized"]

    intent = get_ut_egress_intent(ut_id)
    active_links = _active_links_from_link_state(link_state)
    ut_links = _find_ut_access_links(active_links, ut_id)
    if len(ut_links) == 0:
        return None, [f"ut_not_attached:{ut_id}"]
    if len(ut_links) > 1:
        ambiguous_slots = ",".join(sorted(link.slot_name for link in ut_links))
        return None, [f"ambiguous_ut_attachment:{ut_id}:{ambiguous_slots}"]
    ut_link = ut_links[0]

    gateway_links = _find_gateway_access_links(active_links)
    if not gateway_links:
        return None, ["no_attached_gateway"]

    satellite_links = [
        link
        for link in active_links
        if link.slot_type in {"fixed_isl", "cross_plane_isl"}
    ]
    adjacency = _build_satellite_adjacency(satellite_links)
    serving_satellite_id = ut_link.peer_endpoint(ut_id).node_id

    candidate_paths: List[Tuple[int, str, Tuple[str, ...], ActiveLink]] = []
    for gateway_link in gateway_links:
        gateway_id = next(
            endpoint.node_id
            for endpoint in gateway_link.endpoints
            if endpoint.node_id.startswith("gw")
        )
        attached_satellite_id = gateway_link.peer_endpoint(gateway_id).node_id
        path = _shortest_satellite_path(
            adjacency=adjacency,
            start_node_id=serving_satellite_id,
            goal_node_id=attached_satellite_id,
        )
        if path is None:
            continue
        candidate_paths.append(
            (len(path) - 1, gateway_id, tuple(path), gateway_link)
        )

    if not candidate_paths:
        return None, [f"no_satellite_path_from:{serving_satellite_id}"]

    _, _, satellite_path, gateway_link = min(
        candidate_paths,
        key=lambda item: (item[0], item[1], item[2]),
    )

    forward_links: List[ActiveLink] = []
    for first_node_id, second_node_id in zip(satellite_path, satellite_path[1:]):
        matching_link = _find_link_between_nodes(
            satellite_links,
            first_node_id,
            second_node_id,
        )
        if matching_link is None:
            return None, [
                f"missing_active_link:{first_node_id}:{second_node_id}"
            ]
        forward_links.append(matching_link)
    forward_links.append(gateway_link)

    link_count = len(forward_links)
    if link_count == 0:
        return None, [f"invalid_empty_path:{intent.intent_id}"]

    forward_labels = tuple(intent.forward_label_seed + offset for offset in range(link_count))
    reverse_labels = tuple(intent.reverse_label_seed + offset for offset in range(link_count))
    local_exclusion_prefixes = topology_internet_egress_exclusion_prefixes(topology)
    return (
        UtEgressMplsPlan(
            intent=intent,
            ut_slot=ut_link,
            gateway_slot=gateway_link,
            satellite_path=satellite_path,
            forward_links=tuple(forward_links),
            reverse_links=tuple(reversed(forward_links)),
            forward_labels=forward_labels,
            reverse_labels=reverse_labels,
            local_exclusion_prefixes=local_exclusion_prefixes,
            policy_guardrail_prefixes=topology_ut_egress_guardrail_prefixes(topology),
            policy_table_id=_ut_policy_table_id(ut_id),
            policy_rule_priority=_ut_policy_rule_priority(ut_id),
        ),
        [],
    )



def ut_egress_policy_table_shared_key(plan: UtEgressMplsPlan) -> str:
    return f"source-policy:{plan.ut_id}:table={plan.policy_table_id}"


def ut_egress_default_route_shared_key(plan: UtEgressMplsPlan) -> str:
    return f"default-egress-route:{plan.ut_id}"


def ut_egress_mpls_input_shared_key(node_id: str, interface_name: str) -> str:
    return f"mpls-input:{node_id}:{interface_name}"


def ut_egress_lfib_shared_key(node_id: str, label: int) -> str:
    return f"lfib:{node_id}:label={label}"


def ut_egress_delivery_route_shared_key(node_id: str, prefix: str, interface_name: str) -> str:
    return f"delivery-route:{node_id}:prefix={prefix}:dev={interface_name}"


def ut_egress_reverse_route_shared_key(node_id: str, prefix: str) -> str:
    return f"reverse-route:{node_id}:prefix={prefix}"


def ut_egress_gateway_nat_shared_key(plan: UtEgressMplsPlan) -> str:
    return (
        f"gateway-nat:{plan.selected_gateway_id}:"
        f"src={plan.active_access_ip}/32:oif={plan.gateway_nat_interface}"
    )



def ut_egress_ready_node_ids(plan: UtEgressMplsPlan) -> List[str]:
    return sorted(
        {
            *plan.satellite_path,
            plan.selected_gateway_id,
        }
    )


def ut_egress_policy_prepare_specs(
    prep_plan: UtEgressPolicyPreparationPlan,
) -> List[UtEgressCommandSpec]:
    specs: List[UtEgressCommandSpec] = []
    keep_prefixes = " ".join(prep_plan.guardrail_prefixes)
    for node_id in prep_plan.candidate_serving_satellite_ids:
        for source_prefix in prep_plan.source_prefixes:
            source_ip = str(ipaddress.ip_interface(source_prefix).ip)
            specs.append(
                UtEgressCommandSpec(
                    node_id=node_id,
                    command=(
                        "sh",
                        "-lc",
                        (
                            "ip rule show | grep -Fq "
                            f"'from {source_ip} lookup {prep_plan.policy_table_id}' "
                            "|| "
                            f"ip rule add pref {prep_plan.policy_rule_priority} "
                            f"from {source_prefix} table {prep_plan.policy_table_id}"
                        ),
                    ),
                    stage=f"install source-policy rule on {node_id} for {source_prefix}",
                    category="static_guardrail",
                    shared_key=f"source-policy:{prep_plan.ut_id}:{node_id}:{source_prefix}",
                    purpose=(
                        "Preserve a stable source-policy skeleton on every candidate "
                        "serving satellite so runtime scenario switches only need to "
                        "update the policy-table default egress path."
                    ),
                    expected_state=(
                        f"ip rule shows from {source_ip} lookup {prep_plan.policy_table_id}"
                    ),
                )
            )
        for prefix in prep_plan.guardrail_prefixes:
            specs.append(
                UtEgressCommandSpec(
                    node_id=node_id,
                    command=(
                        "ip",
                        "route",
                        "replace",
                        "unreachable",
                        prefix,
                        "table",
                        str(prep_plan.policy_table_id),
                    ),
                    stage=f"install internal guardrail route on {node_id} for {prefix}",
                    category="static_guardrail",
                    shared_key=f"guardrail:{prep_plan.ut_id}:{node_id}:{prefix}",
                    purpose=(
                        "Keep internal/control/runtime prefixes out of the UT egress "
                        "default policy path."
                    ),
                    expected_state=f"table {prep_plan.policy_table_id} contains unreachable {prefix}",
                )
            )
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        f"KEEP=' {keep_prefixes} '; "
                        f"for prefix in $(ip route show table {prep_plan.policy_table_id} "
                        " | awk '/^unreachable / {print $2}'); do "
                        'case "$KEEP" in '
                        '*" $prefix "*) ;; '
                        f'*) ip route del unreachable "$prefix" table {prep_plan.policy_table_id} '
                        '>/dev/null 2>&1 || true ;; '
                        "esac; done"
                    ),
                ),
                stage=f"normalize internal guardrail routes on {node_id}",
                category="static_guardrail",
                shared_key=f"guardrail-normalize:{prep_plan.ut_id}:{node_id}",
                purpose=(
                    "Prune legacy fine-grained exclusion routes after static guardrails "
                    "have been aggregated, while preserving the policy-table default route."
                ),
            )
        )
    return specs


def ut_egress_install_specs(
    plan: UtEgressMplsPlan,
) -> List[UtEgressCommandSpec]:
    specs: List[UtEgressCommandSpec] = []
    for node_id, interface_name in _mpls_input_interfaces(plan):
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=("sysctl", "-w", f"net.mpls.conf.{interface_name}.input=1"),
                stage=f"enable MPLS input on {node_id} {interface_name}",
                category="dynamic_lfib",
                shared_key=ut_egress_mpls_input_shared_key(node_id, interface_name),
                purpose="Enable MPLS label processing on the active ingress interface.",
                expected_state=f"net.mpls.conf.{interface_name}.input=1",
            )
        )

    specs.append(
        UtEgressCommandSpec(
            node_id=plan.ut_id,
            command=(
                "ip",
                "route",
                "replace",
                plan.default_egress_prefix,
                "via",
                plan.serving_satellite_ut_endpoint.host_ip,
                "dev",
                plan.ut_endpoint.interface_name,
                "src",
                plan.active_access_ip,
            ),
            stage=f"install UT default egress route on {plan.ut_id}",
            category="dynamic_path",
            shared_key=ut_egress_default_route_shared_key(plan),
            purpose="Point UT-originated Internet traffic at the current serving satellite.",
            expected_state=(
                f"default via {plan.serving_satellite_ut_endpoint.host_ip} dev "
                f"{plan.ut_endpoint.interface_name} src {plan.active_access_ip}"
            ),
        )
    )

    first_forward_link = plan.forward_links[0]
    specs.append(
        UtEgressCommandSpec(
            node_id=plan.serving_satellite_id,
            command=(
                "ip",
                "route",
                "replace",
                plan.default_egress_prefix,
                "encap",
                "mpls",
                str(plan.forward_labels[0]),
                "via",
                "inet",
                first_forward_link.peer_endpoint(plan.serving_satellite_id).host_ip,
                "dev",
                first_forward_link.endpoint_for(plan.serving_satellite_id).interface_name,
                "table",
                str(plan.policy_table_id),
            ),
            stage=(
                "install default egress route on "
                f"{plan.serving_satellite_id} table {plan.policy_table_id}"
            ),
            category="dynamic_path",
            shared_key=ut_egress_policy_table_shared_key(plan),
            purpose=(
                "Install the current selected-gateway/path default egress action in "
                "the UT-specific source policy table."
            ),
            expected_state=(
                f"table {plan.policy_table_id} default encap mpls {plan.forward_labels[0]}"
            ),
        )
    )
    forward_nodes = plan.forward_mpls_nodes
    for index in range(1, len(forward_nodes) - 1):
        node_id = forward_nodes[index]
        incoming_label = plan.forward_labels[index - 1]
        outgoing_label = plan.forward_labels[index]
        outgoing_link = plan.forward_links[index]
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=(
                    "ip",
                    "-f",
                    "mpls",
                    "route",
                    "replace",
                    str(incoming_label),
                    "as",
                    str(outgoing_label),
                    "via",
                    "inet",
                    outgoing_link.peer_endpoint(node_id).host_ip,
                    "dev",
                    outgoing_link.endpoint_for(node_id).interface_name,
                ),
                stage=f"install forward LFIB on {node_id}",
                category="dynamic_lfib",
                shared_key=ut_egress_lfib_shared_key(node_id, incoming_label),
                purpose="Swap the forward MPLS label toward the selected gateway path.",
                expected_state=f"LFIB {incoming_label} -> {outgoing_label}",
            )
        )

    specs.append(
        UtEgressCommandSpec(
            node_id=plan.selected_gateway_id,
            command=(
                "ip",
                "-f",
                "mpls",
                "route",
                "replace",
                str(plan.forward_labels[-1]),
                "dev",
                "lo",
            ),
            stage=f"install forward pop entry on {plan.selected_gateway_id}",
            category="dynamic_lfib",
            shared_key=ut_egress_lfib_shared_key(plan.selected_gateway_id, plan.forward_labels[-1]),
            purpose="Terminate forward MPLS traffic on the currently selected gateway.",
            expected_state=f"LFIB pop label {plan.forward_labels[-1]} on {plan.selected_gateway_id}",
        )
    )

    specs.extend(
        [
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        "iptables -C FORWARD "
                        f"-i {plan.gateway_endpoint.interface_name} "
                        f"-o {plan.gateway_nat_interface} "
                        f"-s {plan.active_access_ip}/32 -j ACCEPT "
                        "2>/dev/null "
                        "|| "
                        "iptables -A FORWARD "
                        f"-i {plan.gateway_endpoint.interface_name} "
                        f"-o {plan.gateway_nat_interface} "
                        f"-s {plan.active_access_ip}/32 -j ACCEPT"
                    ),
                ),
                stage=f"install forward filter rule on {plan.selected_gateway_id}",
                category="dynamic_gateway_nat",
                shared_key=ut_egress_gateway_nat_shared_key(plan) + ":forward",
                purpose="Allow UT traffic to cross the selected gateway toward the miniInternet uplink.",
                expected_state="FORWARD rule present for outbound UT traffic",
            ),
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        "iptables -C FORWARD "
                        f"-i {plan.gateway_nat_interface} "
                        f"-o {plan.gateway_endpoint.interface_name} "
                        f"-d {plan.active_access_ip}/32 "
                        "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT "
                        "2>/dev/null "
                        "|| "
                        "iptables -A FORWARD "
                        f"-i {plan.gateway_nat_interface} "
                        f"-o {plan.gateway_endpoint.interface_name} "
                        f"-d {plan.active_access_ip}/32 "
                        "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                    ),
                ),
                stage=f"install reverse filter rule on {plan.selected_gateway_id}",
                category="dynamic_gateway_nat",
                shared_key=ut_egress_gateway_nat_shared_key(plan) + ":reverse",
                purpose="Allow return traffic from the miniInternet back toward the UT access subnet.",
                expected_state="FORWARD rule present for inbound established traffic",
            ),
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        "iptables -t nat -C POSTROUTING "
                        f"-s {plan.active_access_ip}/32 "
                        f"-o {plan.gateway_nat_interface} "
                        f"-j SNAT --to-source {plan.gateway_nat_source_ip} "
                        "2>/dev/null "
                        "|| "
                        "iptables -t nat -A POSTROUTING "
                        f"-s {plan.active_access_ip}/32 "
                        f"-o {plan.gateway_nat_interface} "
                        f"-j SNAT --to-source {plan.gateway_nat_source_ip}"
                    ),
                ),
                stage=f"install SNAT rule on {plan.selected_gateway_id}",
                category="dynamic_gateway_nat",
                shared_key=ut_egress_gateway_nat_shared_key(plan),
                purpose="Realize CGNAT-like outbound source NAT on the selected gateway.",
                expected_state=f"SNAT to {plan.gateway_nat_source_ip} is present",
            ),
        ]
    )

    first_reverse_link = plan.reverse_links[0]
    specs.append(
        UtEgressCommandSpec(
            node_id=plan.selected_gateway_id,
            command=(
                "ip",
                "route",
                "replace",
                plan.return_target_prefix,
                "encap",
                "mpls",
                str(plan.reverse_labels[0]),
                "via",
                "inet",
                first_reverse_link.peer_endpoint(plan.selected_gateway_id).host_ip,
                "dev",
                first_reverse_link.endpoint_for(plan.selected_gateway_id).interface_name,
            ),
            stage=f"install reverse ingress route on {plan.selected_gateway_id}",
            category="dynamic_gateway_nat",
            shared_key=ut_egress_reverse_route_shared_key(
                plan.selected_gateway_id,
                plan.return_target_prefix,
            ),
            purpose="Send return traffic from the selected gateway back into the reverse MPLS path.",
            expected_state=f"{plan.return_target_prefix} encap mpls {plan.reverse_labels[0]}",
        )
    )

    reverse_nodes = plan.reverse_mpls_nodes
    for index in range(1, len(reverse_nodes) - 1):
        node_id = reverse_nodes[index]
        incoming_label = plan.reverse_labels[index - 1]
        outgoing_label = plan.reverse_labels[index]
        outgoing_link = plan.reverse_links[index]
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=(
                    "ip",
                    "-f",
                    "mpls",
                    "route",
                    "replace",
                    str(incoming_label),
                    "as",
                    str(outgoing_label),
                    "via",
                    "inet",
                    outgoing_link.peer_endpoint(node_id).host_ip,
                    "dev",
                    outgoing_link.endpoint_for(node_id).interface_name,
                ),
                stage=f"install reverse LFIB on {node_id}",
                category="dynamic_lfib",
                shared_key=ut_egress_lfib_shared_key(node_id, incoming_label),
                purpose="Swap the reverse MPLS label back toward the serving satellite.",
                expected_state=f"LFIB {incoming_label} -> {outgoing_label}",
            )
        )

    specs.append(
        UtEgressCommandSpec(
            node_id=plan.serving_satellite_id,
            command=(
                "ip",
                "-f",
                "mpls",
                "route",
                "replace",
                str(plan.reverse_labels[-1]),
                "dev",
                "lo",
            ),
            stage=f"install reverse pop entry on {plan.serving_satellite_id}",
            category="dynamic_lfib",
            shared_key=ut_egress_lfib_shared_key(plan.serving_satellite_id, plan.reverse_labels[-1]),
            purpose="Terminate reverse MPLS traffic on the active serving satellite.",
            expected_state=f"LFIB pop label {plan.reverse_labels[-1]} on {plan.serving_satellite_id}",
        )
    )
    specs.append(
        UtEgressCommandSpec(
            node_id=plan.serving_satellite_id,
            command=(
                "ip",
                "route",
                "replace",
                plan.return_target_prefix,
                "via",
                plan.ut_endpoint.host_ip,
                "dev",
                plan.serving_satellite_ut_endpoint.interface_name,
            ),
            stage=f"install UT delivery route on {plan.serving_satellite_id}",
            category="dynamic_path",
            shared_key=ut_egress_delivery_route_shared_key(
                plan.serving_satellite_id,
                plan.return_target_prefix,
                plan.serving_satellite_ut_endpoint.interface_name,
            ),
            purpose="Deliver decapsulated return traffic back to the active UT access subnet.",
            expected_state=(
                f"{plan.return_target_prefix} via {plan.ut_endpoint.host_ip} dev "
                f"{plan.serving_satellite_ut_endpoint.interface_name}"
            ),
        )
    )
    return specs


def ut_egress_cleanup_specs(
    plan: UtEgressMplsPlan,
    *,
    preserve_mpls_input_interfaces: Optional[Iterable[Tuple[str, str]]] = None,
) -> List[UtEgressCommandSpec]:
    specs: List[UtEgressCommandSpec] = []
    preserved_inputs = set(preserve_mpls_input_interfaces or ())

    specs.extend(
        [
            UtEgressCommandSpec(
                node_id=plan.ut_id,
                command=("ip", "route", "del", plan.default_egress_prefix),
                stage=f"delete UT default egress route on {plan.ut_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_default_route_shared_key(plan),
            ),
            UtEgressCommandSpec(
                node_id=plan.serving_satellite_id,
                command=("ip", "route", "del", plan.default_egress_prefix, "table", str(plan.policy_table_id)),
                stage=f"delete source-policy default egress route on {plan.serving_satellite_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_policy_table_shared_key(plan),
            ),
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        "iptables -t nat -D POSTROUTING "
                        f"-s {plan.active_access_ip}/32 "
                        f"-o {plan.gateway_nat_interface} "
                        f"-j SNAT --to-source {plan.gateway_nat_source_ip}"
                    ),
                ),
                stage=f"delete SNAT rule on {plan.selected_gateway_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_gateway_nat_shared_key(plan),
            ),
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        "iptables -D FORWARD "
                        f"-i {plan.gateway_endpoint.interface_name} "
                        f"-o {plan.gateway_nat_interface} "
                        f"-s {plan.active_access_ip}/32 -j ACCEPT"
                    ),
                ),
                stage=f"delete forward filter rule on {plan.selected_gateway_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_gateway_nat_shared_key(plan) + ":forward",
            ),
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=(
                    "sh",
                    "-lc",
                    (
                        "iptables -D FORWARD "
                        f"-i {plan.gateway_nat_interface} "
                        f"-o {plan.gateway_endpoint.interface_name} "
                        f"-d {plan.active_access_ip}/32 "
                        "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                    ),
                ),
                stage=f"delete reverse filter rule on {plan.selected_gateway_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_gateway_nat_shared_key(plan) + ":reverse",
            ),
        ]
    )

    specs.extend(
        [
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=("ip", "-f", "mpls", "route", "del", str(plan.forward_labels[-1])),
                stage=f"delete forward pop entry on {plan.selected_gateway_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_lfib_shared_key(plan.selected_gateway_id, plan.forward_labels[-1]),
            ),
        ]
    )

    forward_nodes = plan.forward_mpls_nodes
    for index in range(1, len(forward_nodes) - 1):
        node_id = forward_nodes[index]
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=("ip", "-f", "mpls", "route", "del", str(plan.forward_labels[index - 1])),
                stage=f"delete forward LFIB on {node_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_lfib_shared_key(node_id, plan.forward_labels[index - 1]),
            )
        )

    specs.extend(
        [
            UtEgressCommandSpec(
                node_id=plan.selected_gateway_id,
                command=("ip", "route", "del", plan.return_target_prefix),
                stage=f"delete reverse ingress route on {plan.selected_gateway_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_reverse_route_shared_key(
                    plan.selected_gateway_id,
                    plan.return_target_prefix,
                ),
            ),
            UtEgressCommandSpec(
                node_id=plan.serving_satellite_id,
                command=("ip", "-f", "mpls", "route", "del", str(plan.reverse_labels[-1])),
                stage=f"delete reverse pop entry on {plan.serving_satellite_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_lfib_shared_key(plan.serving_satellite_id, plan.reverse_labels[-1]),
            ),
            UtEgressCommandSpec(
                node_id=plan.serving_satellite_id,
                command=("ip", "route", "del", plan.return_target_prefix),
                stage=f"delete UT delivery route on {plan.serving_satellite_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_delivery_route_shared_key(
                    plan.serving_satellite_id,
                    plan.return_target_prefix,
                    plan.serving_satellite_ut_endpoint.interface_name,
                ),
            ),
        ]
    )

    reverse_nodes = plan.reverse_mpls_nodes
    for index in range(1, len(reverse_nodes) - 1):
        node_id = reverse_nodes[index]
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=("ip", "-f", "mpls", "route", "del", str(plan.reverse_labels[index - 1])),
                stage=f"delete reverse LFIB on {node_id}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_lfib_shared_key(node_id, plan.reverse_labels[index - 1]),
            )
        )

    for node_id, interface_name in reversed(_mpls_input_interfaces(plan)):
        if (node_id, interface_name) in preserved_inputs:
            continue
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=("sysctl", "-w", f"net.mpls.conf.{interface_name}.input=0"),
                stage=f"disable MPLS input on {node_id} {interface_name}",
                category="cleanup",
                allow_failure=True,
                shared_key=ut_egress_mpls_input_shared_key(node_id, interface_name),
            )
        )
    return specs


def ut_egress_route_show_specs(
    plan: UtEgressMplsPlan,
) -> List[UtEgressCommandSpec]:
    specs: List[UtEgressCommandSpec] = [
        UtEgressCommandSpec(
            node_id=plan.ut_id,
            command=("ip", "route", "show", plan.default_egress_prefix),
            stage=f"show {plan.ut_id} default egress route",
            category="verify",
        ),
        UtEgressCommandSpec(
            node_id=plan.serving_satellite_id,
            command=("ip", "rule", "show"),
            stage=f"show {plan.serving_satellite_id} source-policy rules",
            category="verify",
        ),
        UtEgressCommandSpec(
            node_id=plan.serving_satellite_id,
            command=("ip", "route", "show", "table", str(plan.policy_table_id)),
            stage=f"show {plan.serving_satellite_id} source-policy table",
            category="verify",
        ),
        UtEgressCommandSpec(
            node_id=plan.selected_gateway_id,
            command=("ip", "-f", "mpls", "route", "show"),
            stage=f"show {plan.selected_gateway_id} LFIB",
            category="verify",
        ),
        UtEgressCommandSpec(
            node_id=plan.selected_gateway_id,
            command=("sh", "-lc", "iptables -t nat -S"),
            stage=f"show {plan.selected_gateway_id} NAT table",
            category="verify",
        ),
    ]
    for node_id in plan.swap_nodes:
        specs.append(
            UtEgressCommandSpec(
                node_id=node_id,
                command=("ip", "-f", "mpls", "route", "show"),
                stage=f"show {node_id} LFIB",
                category="verify",
            )
        )
    specs.append(
        UtEgressCommandSpec(
            node_id=plan.serving_satellite_id,
            command=("ip", "-f", "mpls", "route", "show"),
            stage=f"show {plan.serving_satellite_id} reverse-pop LFIB",
            category="verify",
        )
    )
    return specs


def resolve_ut_egress_ping_target(
    plan: UtEgressMplsPlan,
    ping_target: Optional[str] = None,
) -> str:
    resolved_target = ping_target or plan.default_ping_target
    ping_address = ipaddress.ip_address(resolved_target)
    if (
        ping_address.is_unspecified
        or ping_address.is_loopback
        or ping_address.is_multicast
        or ping_address.is_link_local
    ):
        raise ValueError(
            f"unsupported ping target {resolved_target} for {plan.ut_id}; "
            "expected a routable non-local Internet target"
        )
    for prefix in plan.local_exclusion_prefixes:
        if ping_address in ipaddress.ip_network(prefix):
            raise ValueError(
                f"unsupported ping target {resolved_target} for {plan.ut_id}; "
                "target falls inside controller-managed local/internal exclusions: "
                + ", ".join(plan.local_exclusion_prefixes)
            )
    return resolved_target


def ut_egress_push_capture_command(plan: UtEgressMplsPlan) -> Tuple[str, List[str]]:
    receiving_link = plan.forward_links[0]
    receiving_node = plan.forward_mpls_nodes[1]
    receiving_interface = receiving_link.endpoint_for(receiving_node).interface_name
    return (
        receiving_node,
        [
            "timeout",
            "5",
            "tcpdump",
            "-ni",
            receiving_interface,
            "-c",
            "1",
            "-vv",
            "-l",
            "mpls",
        ],
    )


def ut_egress_pop_capture_command(
    plan: UtEgressMplsPlan,
    ping_target: Optional[str] = None,
) -> Tuple[str, List[str]]:
    resolved_target = resolve_ut_egress_ping_target(plan, ping_target)
    return (
        plan.selected_gateway_id,
        [
            "timeout",
            "5",
            "tcpdump",
            "-ni",
            "any",
            "-c",
            "1",
            "-vv",
            "-l",
            (
                "icmp and src host "
                f"{plan.active_access_ip} and dst host {resolved_target}"
            ),
        ],
    )


def ut_egress_ping_command(
    plan: UtEgressMplsPlan,
    ping_target: Optional[str] = None,
) -> List[str]:
    resolved_target = resolve_ut_egress_ping_target(plan, ping_target)
    return [
        "ping",
        "-I",
        plan.active_access_interface,
        "-c",
        "3",
        "-W",
        "1",
        resolved_target,
    ]


def ut_access_probe_command(plan: UtEgressMplsPlan) -> List[str]:
    return [
        "ping",
        "-I",
        plan.active_access_interface,
        "-c",
        "1",
        "-W",
        "1",
        plan.serving_satellite_access_ip,
    ]


def ut_egress_access_capture_command(
    plan: UtEgressMplsPlan,
    ping_target: Optional[str] = None,
) -> Tuple[str, List[str]]:
    resolved_target = resolve_ut_egress_ping_target(plan, ping_target)
    return (
        plan.serving_satellite_id,
        [
            "timeout",
            "5",
            "tcpdump",
            "-ni",
            plan.serving_satellite_access_interface,
            "-c",
            "1",
            "-vv",
            "-l",
            (
                "icmp and src host "
                f"{plan.active_access_ip} and dst host {resolved_target}"
            ),
        ],
    )


def _mpls_input_interfaces(plan: UtEgressMplsPlan) -> List[Tuple[str, str]]:
    enabled: List[Tuple[str, str]] = []
    seen = set()
    for link, receiving_node in zip(
        plan.forward_links,
        plan.forward_mpls_nodes[1:],
    ):
        key = (receiving_node, link.endpoint_for(receiving_node).interface_name)
        if key not in seen:
            seen.add(key)
            enabled.append(key)
    for link, receiving_node in zip(
        plan.reverse_links,
        plan.reverse_mpls_nodes[1:],
    ):
        key = (receiving_node, link.endpoint_for(receiving_node).interface_name)
        if key not in seen:
            seen.add(key)
            enabled.append(key)
    return enabled


def ut_egress_mpls_input_interface_set(
    plans: Iterable[UtEgressMplsPlan],
) -> set[Tuple[str, str]]:
    enabled: set[Tuple[str, str]] = set()
    for plan in plans:
        enabled.update(_mpls_input_interfaces(plan))
    return enabled



def _active_links_from_link_state(link_state: Dict[str, Any]) -> List[ActiveLink]:
    active_links: List[ActiveLink] = []
    for slot in link_state.get("slots", []):
        slot_type = str(slot.get("slot_type"))
        if not slot.get("occupied", False):
            continue
        endpoints = _slot_endpoints(slot)
        if len(endpoints) != 2:
            continue
        active_links.append(
            ActiveLink(
                slot_name=str(slot.get("slot_name")),
                slot_type=slot_type,
                subnet=str(slot.get("subnet")),
                binding=dict(slot.get("binding") or {}),
                endpoints=endpoints,
            )
        )
    return active_links


def _slot_endpoints(slot: Dict[str, Any]) -> Tuple[ActiveLinkEndpoint, ...]:
    endpoints: List[ActiveLinkEndpoint] = []
    for entry in slot.get("dynamic_endpoints", []):
        endpoints.append(_to_active_endpoint(entry))
    for entry in slot.get("preconnected_endpoints", []):
        endpoints.append(_to_active_endpoint(entry))
    return tuple(endpoints)


def _to_active_endpoint(raw: Dict[str, Any]) -> ActiveLinkEndpoint:
    return ActiveLinkEndpoint(
        node_id=str(raw.get("node_id")),
        interface_name=str(raw.get("interface_name")),
        ip_address=str(raw.get("ip_address")),
        logical_direction=raw.get("logical_direction"),
    )


def _find_ut_access_links(
    active_links: Sequence[ActiveLink],
    ut_id: str,
) -> List[ActiveLink]:
    ut_links: List[ActiveLink] = []
    for link in active_links:
        if link.slot_type != "ut_access":
            continue
        endpoint_ids = {endpoint.node_id for endpoint in link.endpoints}
        if ut_id in endpoint_ids:
            ut_links.append(link)
    return ut_links


def _find_gateway_access_links(active_links: Sequence[ActiveLink]) -> List[ActiveLink]:
    gateway_links = []
    for link in active_links:
        if link.slot_type != "gw_access":
            continue
        if any(endpoint.node_id.startswith("gw") for endpoint in link.endpoints):
            gateway_links.append(link)
    return gateway_links


def _build_satellite_adjacency(
    links: Iterable[ActiveLink],
) -> Dict[str, List[Tuple[str, ActiveLink]]]:
    adjacency: Dict[str, List[Tuple[str, ActiveLink]]] = defaultdict(list)
    for link in links:
        first, second = link.endpoints
        adjacency[first.node_id].append((second.node_id, link))
        adjacency[second.node_id].append((first.node_id, link))
    for node_id in adjacency:
        adjacency[node_id].sort(key=lambda item: item[0])
    return dict(adjacency)


def _shortest_satellite_path(
    *,
    adjacency: Dict[str, List[Tuple[str, ActiveLink]]],
    start_node_id: str,
    goal_node_id: str,
) -> Optional[List[str]]:
    if start_node_id == goal_node_id:
        return [start_node_id]
    visited = {start_node_id}
    queue = deque([(start_node_id, [start_node_id])])
    while queue:
        current_node_id, path = queue.popleft()
        for next_node_id, _ in adjacency.get(current_node_id, []):
            if next_node_id in visited:
                continue
            next_path = [*path, next_node_id]
            if next_node_id == goal_node_id:
                return next_path
            visited.add(next_node_id)
            queue.append((next_node_id, next_path))
    return None


def _find_link_between_nodes(
    links: Sequence[ActiveLink],
    first_node_id: str,
    second_node_id: str,
) -> Optional[ActiveLink]:
    for link in links:
        if link.connects(first_node_id, second_node_id):
            return link
    return None
