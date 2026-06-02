#!/usr/bin/env python3
# encoding: utf-8

"""Build the slot-based satellite prototype topology.

This Phase 2 prototype keeps the topology logically grounded while scaling it to
an observable mid-sized constellation:

- 6 user terminals
- 12 satellites split across three logical orbital planes
- 4 gateways mapped to distinct miniInternet regions/IXes
- fixed same-plane ISLs compiled by SEED
- slot-based dynamic UT/GW/cross-plane links managed at runtime by controller.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from seedemu.compiler import Docker, Platform
    from seedemu.core import Emulator, OptionMode, OptionRegistry
    from seedemu.layers import Base, Ebgp, Ibgp, Ospf, PeerRelationship, Routing
except ModuleNotFoundError as exc:
    raise SystemExit(
        "seedemu is not importable. Install the local dependency first with "
        "`cd /home/seed/seed-emulator-upstream-dev && pip install -e .`."
    ) from exc


SATELLITE_ASN = 250
SATELLITE_OPERATOR_ASN = 65000
MINI_INTERNET_HOSTS_PER_STUB_AS = 2

DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = DEFAULT_DIR / "output"
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
DEFAULT_TOPOLOGY_JSON = DEFAULT_CONFIG_DIR / "topology.json"

ACCESS_SLOT_COUNT = 2
GATEWAY_SLOT_COUNT = 2
CROSS_PLANE_SLOT_COUNT_PER_PAIR = 4
MPLS_PLATFORM_LABEL_CAPACITY = 2000

ORBIT_DEFINITIONS = (
    {
        "orbit_id": "orbit-a",
        "plane_label": "Plane A",
        "plane_index": 1,
        "satellite_prefix": "a",
        "satellite_ids": ("sat-a1", "sat-a2", "sat-a3", "sat-a4"),
    },
    {
        "orbit_id": "orbit-b",
        "plane_label": "Plane B",
        "plane_index": 2,
        "satellite_prefix": "b",
        "satellite_ids": ("sat-b1", "sat-b2", "sat-b3", "sat-b4"),
    },
    {
        "orbit_id": "orbit-c",
        "plane_label": "Plane C",
        "plane_index": 3,
        "satellite_prefix": "c",
        "satellite_ids": ("sat-c1", "sat-c2", "sat-c3", "sat-c4"),
    },
)

SATELLITES = tuple(
    satellite_id
    for orbit in ORBIT_DEFINITIONS
    for satellite_id in orbit["satellite_ids"]
)
ORBIT_SATELLITES = {
    str(orbit["orbit_id"]): tuple(str(satellite_id) for satellite_id in orbit["satellite_ids"])
    for orbit in ORBIT_DEFINITIONS
}

GATEWAY_DEFINITIONS = (
    {
        "gateway_id": "gw1",
        "gateway_index": 1,
        "region": "east",
        "ix_id": 100,
        "ix_ip_address": "10.100.0.250",
        "upstream_asn": 2,
    },
    {
        "gateway_id": "gw2",
        "gateway_index": 2,
        "region": "west",
        "ix_id": 101,
        "ix_ip_address": "10.101.0.250",
        "upstream_asn": 12,
    },
    {
        "gateway_id": "gw3",
        "gateway_index": 3,
        "region": "south",
        "ix_id": 103,
        "ix_ip_address": "10.103.0.250",
        "upstream_asn": 3,
    },
    {
        "gateway_id": "gw4",
        "gateway_index": 4,
        "region": "north",
        "ix_id": 104,
        "ix_ip_address": "10.104.0.250",
        "upstream_asn": 4,
    },
)
GATEWAYS = tuple(entry["gateway_id"] for entry in GATEWAY_DEFINITIONS)
GATEWAY_DEFINITIONS_BY_ID = {
    str(entry["gateway_id"]): dict(entry) for entry in GATEWAY_DEFINITIONS
}
GATEWAY_IX_BINDINGS = {
    str(entry["gateway_id"]): {
        "ix_id": int(entry["ix_id"]),
        "ip_address": str(entry["ix_ip_address"]),
        "region": str(entry["region"]),
    }
    for entry in GATEWAY_DEFINITIONS
}
GATEWAY_TRANSIT_PEERINGS = tuple(
    (int(entry["ix_id"]), int(entry["upstream_asn"]), SATELLITE_OPERATOR_ASN)
    for entry in GATEWAY_DEFINITIONS
)

UT_DEFINITIONS = (
    {
        "ut_id": "ut1",
        "ut_index": 1,
        "region": "east",
        "candidate_satellite_ids": ("sat-a1", "sat-a2", "sat-b1"),
    },
    {
        "ut_id": "ut2",
        "ut_index": 2,
        "region": "east-center",
        "candidate_satellite_ids": ("sat-a2", "sat-a3", "sat-b2"),
    },
    {
        "ut_id": "ut3",
        "ut_index": 3,
        "region": "center",
        "candidate_satellite_ids": ("sat-b1", "sat-b2", "sat-c1"),
    },
    {
        "ut_id": "ut4",
        "ut_index": 4,
        "region": "center-west",
        "candidate_satellite_ids": ("sat-b2", "sat-b3", "sat-c2"),
    },
    {
        "ut_id": "ut5",
        "ut_index": 5,
        "region": "west",
        "candidate_satellite_ids": ("sat-c2", "sat-c3", "sat-c4"),
    },
    {
        "ut_id": "ut6",
        "ut_index": 6,
        "region": "west-north",
        "candidate_satellite_ids": ("sat-a3", "sat-a4", "sat-b4"),
    },
)
USER_TERMINALS = tuple(entry["ut_id"] for entry in UT_DEFINITIONS)
UT_DEFINITIONS_BY_ID = {
    str(entry["ut_id"]): dict(entry) for entry in UT_DEFINITIONS
}

CROSS_PLANE_GROUPS = (
    {
        "pair_key": "ab",
        "display_label": "A-B corridor",
        "orbit_a_id": "orbit-a",
        "orbit_b_id": "orbit-b",
    },
    {
        "pair_key": "bc",
        "display_label": "B-C corridor",
        "orbit_a_id": "orbit-b",
        "orbit_b_id": "orbit-c",
    },
)


def _orbit_prefix(orbit_id: str) -> str:
    for orbit in ORBIT_DEFINITIONS:
        if orbit["orbit_id"] == orbit_id:
            return str(orbit["satellite_prefix"])
    raise ValueError(f"unknown orbit_id: {orbit_id}")


def _generated_fixed_intra_orbit_links() -> Tuple[
    Tuple[str, str, str, str, str, str, str, str, str],
    ...,
]:
    links: List[Tuple[str, str, str, str, str, str, str, str, str]] = []
    subnet_index = 201
    for orbit in ORBIT_DEFINITIONS:
        satellite_ids = list(orbit["satellite_ids"])
        prefix = _orbit_prefix(str(orbit["orbit_id"]))
        for index, left in enumerate(satellite_ids):
            right = satellite_ids[(index + 1) % len(satellite_ids)]
            left_label = int(left.removeprefix(f"sat-{prefix}"))
            right_label = int(right.removeprefix(f"sat-{prefix}"))
            links.append(
                (
                    str(left),
                    str(right),
                    f"{prefix}{left_label}-{prefix}{right_label}",
                    f"10.250.{subnet_index}.0/30",
                    f"10.250.{subnet_index}.1",
                    f"10.250.{subnet_index}.2",
                    "front",
                    "back",
                    str(orbit["orbit_id"]),
                )
            )
            subnet_index += 1
    return tuple(links)


FIXED_INTRA_ORBIT_LINKS = _generated_fixed_intra_orbit_links()

MINI_INTERNET_IX_DISPLAY_NAMES = {
    100: "NYC-100",
    101: "San Jose-101",
    102: "Chicago-102",
    103: "Miami-103",
    104: "Boston-104",
    105: "Huston-105",
}

MINI_INTERNET_TRANSIT_AS_LAYOUT = {
    2: {
        "exchanges": [100, 101, 102, 105],
        "links": [(100, 101), (101, 102), (100, 105)],
    },
    3: {
        "exchanges": [100, 103, 104, 105],
        "links": [(100, 103), (100, 105), (103, 105), (103, 104)],
    },
    4: {
        "exchanges": [100, 102, 104],
        "links": [(100, 104), (102, 104)],
    },
    11: {
        "exchanges": [102, 105],
        "links": [(102, 105)],
    },
    12: {
        "exchanges": [101, 104],
        "links": [(101, 104)],
    },
}

MINI_INTERNET_STUB_AS_EXCHANGES = {
    150: 100,
    151: 100,
    152: 101,
    153: 101,
    154: 102,
    160: 103,
    161: 103,
    162: 103,
    163: 104,
    164: 104,
    170: 105,
    171: 105,
}

MINI_INTERNET_RS_PEERS = {
    100: [2, 3, 4],
    102: [2, 4],
    104: [3, 4],
    105: [2, 3],
}

MINI_INTERNET_PRIVATE_PEERINGS = (
    (100, [2], [150, 151]),
    (100, [3], [150]),
    (101, [2], [12]),
    (101, [12], [152, 153]),
    (102, [2, 4], [11, 154]),
    (102, [11], [154]),
    (103, [3], [160, 161, 162]),
    (104, [3, 4], [12]),
    (104, [4], [163]),
    (104, [12], [164]),
    (105, [3], [11, 170]),
    (105, [11], [171]),
)

EXTERNAL_TEST_TARGETS = (
    {
        "asn": 154,
        "node_id": "as154-host-new",
        "ip_address": "10.154.0.129",
        "description": "Customized miniInternet host retained from B00 for UT reachability tests.",
    },
    {
        "asn": 160,
        "node_id": "as160-host1",
        "ip_address": "10.160.0.71",
        "description": "Additional miniInternet host used for multi-UT continuous handover verification.",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the slot-based satellite prototype topology."
    )
    parser.add_argument(
        "--platform",
        choices=("amd", "arm"),
        default="amd",
        help="Target Docker platform.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Compiler output directory.",
    )
    parser.add_argument(
        "--topology-json",
        default=str(DEFAULT_TOPOLOGY_JSON),
        help="Controller-visible topology inventory output path.",
    )
    return parser.parse_args()


def resolve_platform(name: str) -> Platform:
    if name == "amd":
        return Platform.AMD64
    if name == "arm":
        return Platform.ARM64
    raise ValueError(f"unsupported platform: {name}")


def resolve_ut_identity_address(ut_id: str) -> str:
    suffix = int(ut_id.removeprefix("ut"))
    return f"100.64.0.{suffix}"


def create_bootstrap_router(
    the_as,
    node_name: str,
    network_name: str,
    prefix: str,
    address: str,
    extra_software: Tuple[str, ...] = (),
):
    the_as.createNetwork(network_name, prefix)
    router = the_as.createRouter(node_name)
    router.joinNetwork(network_name, address)
    router.addSoftware("tcpdump")
    router.addSoftware("traceroute")
    for software_name in extra_software:
        router.addSoftware(software_name)
    return router


def enable_mpls_readiness(node) -> None:
    node.appendStartCommand(
        f"sysctl -w net.mpls.platform_labels={MPLS_PLATFORM_LABEL_CAPACITY} || true",
        isPostConfigCommand=True,
    )


def create_ut_placeholder(
    satellite_as,
    ut_id: str,
    network_name: str,
    prefix: str,
    router_address: str,
    ut_address: str,
):
    satellite_as.createNetwork(network_name, prefix)
    bootstrap_router = satellite_as.createRouter(f"{ut_id}-placeholder-rtr")
    bootstrap_router.joinNetwork(network_name, router_address)

    ut = satellite_as.createHost(ut_id)
    ut.joinNetwork(network_name, ut_address)
    ut.appendStartCommand("ip route del default || true", isPostConfigCommand=True)
    ut.appendStartCommand("ip link add dummy0 type dummy", isPostConfigCommand=True)
    ut.appendStartCommand("ip link set dummy0 up", isPostConfigCommand=True)
    ut.appendStartCommand(
        f"ip addr add {resolve_ut_identity_address(ut_id)}/32 dev dummy0",
        isPostConfigCommand=True,
    )
    ut.addSoftware("curl")
    ut.addSoftware("tcpdump")
    ut.addSoftware("traceroute")
    return ut


def create_satellite_link(
    satellite_as,
    left_router,
    right_router,
    network_name: str,
    prefix: str,
    left_address: str,
    right_address: str,
) -> None:
    satellite_as.createNetwork(network_name, prefix)
    left_router.joinNetwork(network_name, left_address)
    right_router.joinNetwork(network_name, right_address)


def create_unique_transit_as(
    base: Base,
    *,
    asn: int,
    exchanges: List[int],
    intra_ix_links: List[Tuple[int, int]],
) -> None:
    transit_as = base.createAutonomousSystem(asn)
    routers: Dict[int, object] = {}

    for ix in exchanges:
        router_name = f"as{asn}-r{ix}"
        routers[ix] = transit_as.createRouter(router_name)
        routers[ix].joinNetwork(f"ix{ix}")

    for left_ix, right_ix in intra_ix_links:
        network_name = f"net_{left_ix}_{right_ix}"
        transit_as.createNetwork(network_name)
        routers[left_ix].joinNetwork(network_name)
        routers[right_ix].joinNetwork(network_name)


def create_unique_stub_as_with_hosts(
    base: Base,
    *,
    asn: int,
    exchange: int,
    hosts_total: int,
) -> None:
    stub_as = base.createAutonomousSystem(asn)
    stub_as.createNetwork("net0")

    router = stub_as.createRouter(f"as{asn}-router0")
    router.joinNetwork("net0")
    router.joinNetwork(f"ix{exchange}")

    for host_index in range(hosts_total):
        host = stub_as.createHost(f"as{asn}-host{host_index}")
        host.joinNetwork("net0")


def build_mini_internet(base: Base, ebgp: Ebgp) -> None:
    for ix_id, display_name in MINI_INTERNET_IX_DISPLAY_NAMES.items():
        ix = base.createInternetExchange(ix_id)
        ix.getPeeringLan().setDisplayName(display_name)

    for asn, layout in MINI_INTERNET_TRANSIT_AS_LAYOUT.items():
        create_unique_transit_as(
            base,
            asn=asn,
            exchanges=list(layout["exchanges"]),
            intra_ix_links=list(layout["links"]),
        )

    for asn, exchange in MINI_INTERNET_STUB_AS_EXCHANGES.items():
        create_unique_stub_as_with_hosts(
            base,
            asn=asn,
            exchange=exchange,
            hosts_total=MINI_INTERNET_HOSTS_PER_STUB_AS,
        )

    as154 = base.getAutonomousSystem(154)
    host_new = as154.createHost("as154-host-new")
    host_new.joinNetwork("net0", address="10.154.0.129")

    disable_rp_filter = OptionRegistry().sysctl_netipv4_conf_rp_filter(
        {"all": False, "default": False, "net0": False},
        mode=OptionMode.RUN_TIME,
    )
    host_new.setOption(disable_rp_filter)
    host_new.setOption(
        OptionRegistry().sysctl_netipv4_udp_rmem_min(
            5000,
            mode=OptionMode.RUN_TIME,
        )
    )

    for ix_id, peers in MINI_INTERNET_RS_PEERS.items():
        ebgp.addRsPeers(ix_id, peers)

    for ix_id, providers, customers in MINI_INTERNET_PRIVATE_PEERINGS:
        ebgp.addPrivatePeerings(
            ix_id,
            list(providers),
            list(customers),
            PeerRelationship.Provider,
        )


def build_emulator() -> Emulator:
    emu = Emulator()
    base = Base()
    routing = Routing()
    ebgp = Ebgp()
    ibgp = Ibgp()
    ospf = Ospf()

    build_mini_internet(base, ebgp)

    satellite_as = base.createAutonomousSystem(SATELLITE_ASN)
    operator_as = base.createAutonomousSystem(SATELLITE_OPERATOR_ASN)

    satellite_routers: Dict[str, object] = {}
    for satellite_index, satellite_id in enumerate(SATELLITES, start=1):
        satellite_routers[satellite_id] = create_bootstrap_router(
            satellite_as,
            satellite_id,
            f"{satellite_id}-bootstrap",
            f"10.250.{satellite_index}.0/24",
            f"10.250.{satellite_index}.2",
        )
        enable_mpls_readiness(satellite_routers[satellite_id])

    gateway_routers: Dict[str, object] = {}
    for definition in GATEWAY_DEFINITIONS:
        gateway_id = str(definition["gateway_id"])
        gateway_index = int(definition["gateway_index"])
        gateway_routers[gateway_id] = create_bootstrap_router(
            operator_as,
            gateway_id,
            f"{gateway_id}-bootstrap",
            f"10.65.{20 + gateway_index}.0/24",
            f"10.65.{20 + gateway_index}.2",
            extra_software=("iptables",),
        )
        enable_mpls_readiness(gateway_routers[gateway_id])
        gateway_routers[gateway_id].joinNetwork(
            f"ix{GATEWAY_IX_BINDINGS[gateway_id]['ix_id']}",
            GATEWAY_IX_BINDINGS[gateway_id]["ip_address"],
        )

    for definition in UT_DEFINITIONS:
        ut_id = str(definition["ut_id"])
        ut_index = int(definition["ut_index"])
        create_ut_placeholder(
            satellite_as,
            ut_id,
            f"{ut_id}-placeholder",
            f"10.250.{100 + ut_index}.0/30",
            f"10.250.{100 + ut_index}.1",
            f"10.250.{100 + ut_index}.2",
        )

    for left, right, label, prefix, left_ip, right_ip, _left_dir, _right_dir, _orbit in FIXED_INTRA_ORBIT_LINKS:
        create_satellite_link(
            satellite_as,
            satellite_routers[left],
            satellite_routers[right],
            f"link-{label}",
            prefix,
            left_ip,
            right_ip,
        )

    for ix_id, upstream_asn, gateway_asn in GATEWAY_TRANSIT_PEERINGS:
        ebgp.addPrivatePeering(
            ix_id,
            upstream_asn,
            gateway_asn,
            PeerRelationship.Provider,
        )

    ospf.maskAsn(SATELLITE_ASN)
    ospf.maskAsn(SATELLITE_OPERATOR_ASN)
    ibgp.maskAsn(SATELLITE_ASN)
    ibgp.maskAsn(SATELLITE_OPERATOR_ASN)

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    return emu


def _bootstrap_inventory(
    *,
    node_id: str,
    node_type: str,
    network_name: str,
    subnet: str,
    ip_address: str,
    purpose: str,
) -> Dict[str, object]:
    return {
        "network_name": network_name,
        "docker_network_name": network_name,
        "subnet": subnet,
        "purpose": purpose,
        "endpoint": {
            "node_id": node_id,
            "node_type": node_type,
            "network_name": network_name,
            "docker_network_name": network_name,
            "interface_name": network_name,
            "ip_address": ip_address,
        },
    }


def _endpoint_record(
    *,
    node_id: str,
    node_type: str,
    interface_name: str,
    ip_address: str,
    logical_direction: str | None = None,
) -> Dict[str, object]:
    record: Dict[str, object] = {
        "node_id": node_id,
        "node_type": node_type,
        "interface_name": interface_name,
        "ip_address": ip_address,
    }
    if logical_direction is not None:
        record["logical_direction"] = logical_direction
    return record


def build_slot_inventory() -> Dict[str, List[Dict[str, object]]]:
    ut_slots: List[Dict[str, object]] = []
    for definition in UT_DEFINITIONS:
        ut_id = str(definition["ut_id"])
        ut_index = int(definition["ut_index"])
        for slot_index in range(ACCESS_SLOT_COUNT):
            third_octet = ut_index * 10 + slot_index + 1
            interface_name = f"ua-u{ut_index}s{slot_index}"
            ut_slots.append(
                {
                    "slot_name": f"{ut_id}-access-slot{slot_index}",
                    "slot_type": "ut_access",
                    "network_name": f"{ut_id}-access-slot{slot_index}",
                    "docker_network_name": f"{ut_id}-access-slot{slot_index}",
                    "subnet": f"10.90.{third_octet}.0/29",
                    "bridge_gateway_ip": f"10.90.{third_octet}.6",
                    "ut_id": ut_id,
                    "region": definition["region"],
                    "slot_index": slot_index,
                    "candidate_satellite_ids": list(definition["candidate_satellite_ids"]),
                    "preconnected_endpoint": _endpoint_record(
                        node_id=ut_id,
                        node_type="user_terminal",
                        interface_name=interface_name,
                        ip_address=f"10.90.{third_octet}.2/29",
                    ),
                    "dynamic_endpoint_template": _endpoint_record(
                        node_id="<runtime-satellite>",
                        node_type="satellite",
                        interface_name=interface_name,
                        ip_address=f"10.90.{third_octet}.1/29",
                    ),
                }
            )

    gateway_slots: List[Dict[str, object]] = []
    for definition in GATEWAY_DEFINITIONS:
        gateway_id = str(definition["gateway_id"])
        gw_index = int(definition["gateway_index"])
        for slot_index in range(GATEWAY_SLOT_COUNT):
            third_octet = gw_index * 10 + slot_index + 1
            interface_name = f"ga-g{gw_index}s{slot_index}"
            gateway_slots.append(
                {
                    "slot_name": f"{gateway_id}-gateway-slot{slot_index}",
                    "slot_type": "gw_access",
                    "network_name": f"{gateway_id}-gateway-slot{slot_index}",
                    "docker_network_name": f"{gateway_id}-gateway-slot{slot_index}",
                    "subnet": f"10.91.{third_octet}.0/29",
                    "bridge_gateway_ip": f"10.91.{third_octet}.6",
                    "gateway_id": gateway_id,
                    "gateway_region": definition["region"],
                    "slot_index": slot_index,
                    "candidate_satellite_ids": list(SATELLITES),
                    "preconnected_endpoint": _endpoint_record(
                        node_id=gateway_id,
                        node_type="gateway",
                        interface_name=interface_name,
                        ip_address=f"10.91.{third_octet}.2/29",
                    ),
                    "dynamic_endpoint_template": _endpoint_record(
                        node_id="<runtime-satellite>",
                        node_type="satellite",
                        interface_name=interface_name,
                        ip_address=f"10.91.{third_octet}.1/29",
                    ),
                }
            )

    cross_plane_slots: List[Dict[str, object]] = []
    subnet_offset = 1
    interface_offset = 0
    for group in CROSS_PLANE_GROUPS:
        pair_key = str(group["pair_key"])
        orbit_a_id = str(group["orbit_a_id"])
        orbit_b_id = str(group["orbit_b_id"])
        for slot_index in range(CROSS_PLANE_SLOT_COUNT_PER_PAIR):
            third_octet = subnet_offset
            interface_index = interface_offset + slot_index
            cross_plane_slots.append(
                {
                    "slot_name": f"{pair_key}-cross-slot{slot_index}",
                    "slot_type": "cross_plane_isl",
                    "network_name": f"{pair_key}-cross-slot{slot_index}",
                    "docker_network_name": f"{pair_key}-cross-slot{slot_index}",
                    "subnet": f"10.92.{third_octet}.0/29",
                    "bridge_gateway_ip": f"10.92.{third_octet}.6",
                    "slot_index": slot_index,
                    "pair_key": pair_key,
                    "display_label": group["display_label"],
                    "orbit_pair": [orbit_a_id, orbit_b_id],
                    "orbit_a_id": orbit_a_id,
                    "orbit_b_id": orbit_b_id,
                    "orbit_a_satellite_ids": list(ORBIT_SATELLITES[orbit_a_id]),
                    "orbit_b_satellite_ids": list(ORBIT_SATELLITES[orbit_b_id]),
                    "endpoint_a_template": _endpoint_record(
                        node_id=f"<runtime-{orbit_a_id}-satellite>",
                        node_type="satellite",
                        interface_name=f"xp{interface_index}a",
                        ip_address=f"10.92.{third_octet}.1/29",
                    ),
                    "endpoint_b_template": _endpoint_record(
                        node_id=f"<runtime-{orbit_b_id}-satellite>",
                        node_type="satellite",
                        interface_name=f"xp{interface_index}b",
                        ip_address=f"10.92.{third_octet}.2/29",
                    ),
                }
            )
            subnet_offset += 1
        interface_offset += CROSS_PLANE_SLOT_COUNT_PER_PAIR

    return {
        "ut_access": ut_slots,
        "gateway_access": gateway_slots,
        "cross_plane_isl": cross_plane_slots,
    }


def emit_topology_inventory(path: Path) -> None:
    fixed_links: List[Dict[str, object]] = []
    for left, right, label, prefix, left_ip, right_ip, left_dir, right_dir, orbit_scope in FIXED_INTRA_ORBIT_LINKS:
        network_name = f"link-{label}"
        fixed_links.append(
            {
                "link_id": f"fixed-{label}",
                "link_type": "satellite_satellite_fixed_intra_orbit",
                "network_name": network_name,
                "docker_network_name": network_name,
                "subnet": prefix,
                "orbit_scope": orbit_scope,
                "endpoints": [
                    {
                        **_endpoint_record(
                            node_id=left,
                            node_type="satellite",
                            interface_name=network_name,
                            ip_address=left_ip,
                            logical_direction=left_dir,
                        ),
                        "network_name": network_name,
                        "docker_network_name": network_name,
                    },
                    {
                        **_endpoint_record(
                            node_id=right,
                            node_type="satellite",
                            interface_name=network_name,
                            ip_address=right_ip,
                            logical_direction=right_dir,
                        ),
                        "network_name": network_name,
                        "docker_network_name": network_name,
                    },
                ],
            }
        )

    topology = {
        "schema_version": "satellite-phase2-slot-topology-v1",
        "description": (
            "Controller-visible static topology inventory for the slot-based "
            "satellite dynamic-link prototype."
        ),
        "logical_port_labels": ["front", "back", "left", "right"],
        "prototype_scope": {
            "orbital_planes": len(ORBIT_DEFINITIONS),
            "satellites": len(SATELLITES),
            "gateways": len(GATEWAYS),
            "user_terminals": len(USER_TERMINALS),
            "access_slots_per_ut": ACCESS_SLOT_COUNT,
            "gateway_slots_per_gw": GATEWAY_SLOT_COUNT,
            "cross_plane_slot_count": len(CROSS_PLANE_GROUPS)
            * CROSS_PLANE_SLOT_COUNT_PER_PAIR,
            "internet_exchanges": len(MINI_INTERNET_IX_DISPLAY_NAMES),
            "mini_internet_transit_ases": len(MINI_INTERNET_TRANSIT_AS_LAYOUT),
            "mini_internet_stub_ases": len(MINI_INTERNET_STUB_AS_EXCHANGES),
        },
        "orbital_planes": [
            {
                "orbit_id": orbit["orbit_id"],
                "plane_label": orbit["plane_label"],
                "plane_index": orbit["plane_index"],
                "satellite_ids": list(orbit["satellite_ids"]),
                "same_plane_neighbor_model": "fixed_ring",
                "cross_plane_neighbor_model": "runtime_dynamic_slot_pairing",
            }
            for orbit in ORBIT_DEFINITIONS
        ],
        "internet_exchanges": [
            {
                "ix_id": ix_id,
                "network_name": f"ix{ix_id}",
                "display_name": display_name,
            }
            for ix_id, display_name in sorted(MINI_INTERNET_IX_DISPLAY_NAMES.items())
        ],
        "satellites": [
            {
                "satellite_id": satellite_id,
                "orbit_id": next(
                    str(orbit["orbit_id"])
                    for orbit in ORBIT_DEFINITIONS
                    if satellite_id in orbit["satellite_ids"]
                ),
                "plane_label": next(
                    str(orbit["plane_label"])
                    for orbit in ORBIT_DEFINITIONS
                    if satellite_id in orbit["satellite_ids"]
                ),
                "plane_index": next(
                    int(orbit["plane_index"])
                    for orbit in ORBIT_DEFINITIONS
                    if satellite_id in orbit["satellite_ids"]
                ),
                "satellite_index_in_plane": int(satellite_id[-1]),
                "logical_ports": {"front": None, "back": None, "left": None, "right": None},
                "interface_role_separation": {
                    "same_plane_isl": ["front", "back"],
                    "cross_plane_isl": ["left", "right"],
                    "ut_facing": "runtime_slot",
                    "gw_facing": "runtime_slot",
                },
                "bootstrap_network": _bootstrap_inventory(
                    node_id=satellite_id,
                    node_type="satellite",
                    network_name=f"{satellite_id}-bootstrap",
                    subnet=f"10.250.{index}.0/24",
                    ip_address=f"10.250.{index}.2/24",
                    purpose="compile_time_carrier",
                ),
            }
            for index, satellite_id in enumerate(SATELLITES, start=1)
        ],
        "gateways": [
            {
                "gateway_id": gateway_id,
                "role": "satellite_operator_border_gateway",
                "asn": SATELLITE_OPERATOR_ASN,
                "gateway_region": GATEWAY_DEFINITIONS_BY_ID[gateway_id]["region"],
                "gateway_index": GATEWAY_DEFINITIONS_BY_ID[gateway_id]["gateway_index"],
                "bootstrap_network": _bootstrap_inventory(
                    node_id=gateway_id,
                    node_type="gateway",
                    network_name=f"{gateway_id}-bootstrap",
                    subnet=f"10.65.{20 + index}.0/24",
                    ip_address=f"10.65.{20 + index}.2/24",
                    purpose="compile_time_carrier",
                ),
                "internet_uplink": {
                    "ix_id": GATEWAY_IX_BINDINGS[gateway_id]["ix_id"],
                    "network_name": f"ix{GATEWAY_IX_BINDINGS[gateway_id]['ix_id']}",
                    "ip_address": GATEWAY_IX_BINDINGS[gateway_id]["ip_address"],
                    "peering_asn": SATELLITE_OPERATOR_ASN,
                    "region": GATEWAY_IX_BINDINGS[gateway_id]["region"],
                },
            }
            for index, gateway_id in enumerate(GATEWAYS, start=1)
        ],
        "user_terminals": [
            {
                "ut_id": ut_id,
                "identity_interface": {
                    "if_name": "dummy0",
                    "ip_address": f"{resolve_ut_identity_address(ut_id)}/32",
                    "semantics": "stable_internal_identity_not_publicly_advertised",
                },
                "region": UT_DEFINITIONS_BY_ID[ut_id]["region"],
                "candidate_serving_satellite_ids": list(
                    UT_DEFINITIONS_BY_ID[ut_id]["candidate_satellite_ids"]
                ),
                "bootstrap_network": _bootstrap_inventory(
                    node_id=ut_id,
                    node_type="user_terminal",
                    network_name=f"{ut_id}-placeholder",
                    subnet=f"10.250.10{index}.0/30",
                    ip_address=f"10.250.10{index}.2/30",
                    purpose="compile_time_carrier_only",
                ),
            }
            for index, ut_id in enumerate(USER_TERMINALS, start=1)
        ],
        "fixed_links": {
            "satellite_satellite": fixed_links,
        },
        "mini_internet": {
            "transit_ases": [
                {
                    "asn": asn,
                    "exchange_ids": list(layout["exchanges"]),
                    "internal_links": list(layout["links"]),
                }
                for asn, layout in sorted(MINI_INTERNET_TRANSIT_AS_LAYOUT.items())
            ],
            "stub_ases": [
                {
                    "asn": asn,
                    "exchange_id": exchange,
                    "host_node_ids": [
                        f"as{asn}-host{host_index}"
                        for host_index in range(MINI_INTERNET_HOSTS_PER_STUB_AS)
                    ] + (["as154-host-new"] if asn == 154 else []),
                }
                for asn, exchange in sorted(MINI_INTERNET_STUB_AS_EXCHANGES.items())
            ],
            "external_test_targets": list(EXTERNAL_TEST_TARGETS),
            "gateway_ix_peerings": [
                {
                    "gateway_id": gateway_id,
                    "ix_id": GATEWAY_IX_BINDINGS[gateway_id]["ix_id"],
                    "ip_address": GATEWAY_IX_BINDINGS[gateway_id]["ip_address"],
                }
                for gateway_id in GATEWAYS
            ],
        },
        "slot_inventory": build_slot_inventory(),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(topology, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    emu = build_emulator()
    output_dir = Path(args.output).resolve()
    topology_json = Path(args.topology_json).resolve()

    emu.render()
    emit_topology_inventory(topology_json)
    emu.compile(
        Docker(
            selfManagedNetwork=True,
            platform=resolve_platform(args.platform),
            namingScheme="{name}",
        ),
        str(output_dir),
        override=True,
    )

    print("Slot-based satellite topology compiled successfully.")
    print(f"Output directory: {output_dir}")
    print(f"Topology inventory: {topology_json}")
    print(f"Satellites: {', '.join(SATELLITES)}")
    print(f"Gateways/AS{SATELLITE_OPERATOR_ASN}: {', '.join(GATEWAYS)}")
    print(f"User terminals: {', '.join(USER_TERMINALS)}")
    print(
            "MiniInternet: "
            f"ix={len(MINI_INTERNET_IX_DISPLAY_NAMES)}, "
            f"transit_as={len(MINI_INTERNET_TRANSIT_AS_LAYOUT)}, "
            f"stub_as={len(MINI_INTERNET_STUB_AS_EXCHANGES)}"
        )
    print(
        "Slot inventory: "
        f"ut_access={len(USER_TERMINALS) * ACCESS_SLOT_COUNT}, "
        f"gateway_access={len(GATEWAYS) * GATEWAY_SLOT_COUNT}, "
        f"cross_plane_isl={len(CROSS_PLANE_GROUPS) * CROSS_PLANE_SLOT_COUNT_PER_PAIR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
