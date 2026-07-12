#!/usr/bin/env python3
# encoding: utf-8

"""Shared SEED/seedemu integration helpers for satellite topology builders.

This module intentionally stays close to seedemu primitives. Satellite-specific
constellation layout and slice policy should live in the topology/planner layers.
"""

from __future__ import annotations

import sys
from typing import Dict, Iterable, List, Sequence, Tuple

from skyseed_v1.operations.paths import SEED_EMULATOR_DEPENDENCY_ROOT


SEED_EMULATOR_DEPENDENCY_DIR = SEED_EMULATOR_DEPENDENCY_ROOT
if SEED_EMULATOR_DEPENDENCY_DIR.exists():
    rendered_seed_path = str(SEED_EMULATOR_DEPENDENCY_DIR)
    if rendered_seed_path in sys.path:
        sys.path.remove(rendered_seed_path)
    sys.path.insert(0, rendered_seed_path)

try:
    from seedemu.compiler import Platform
    from seedemu.core import OptionMode, OptionRegistry
    from seedemu.layers import Base, Ebgp, PeerRelationship
except ModuleNotFoundError as exc:
    raise SystemExit(
        "seedemu is not importable. Install the local dependency first with "
        "clone seed-emulator next to the Skyseed checkout (or set "
        "SKYSEED_SEEDEMU_ROOT), switch it to master, and install it editable."
    ) from exc


SATELLITE_ASN = 250
SATELLITE_OPERATOR_ASN = 65000
MINI_INTERNET_HOSTS_PER_STUB_AS = 2
MPLS_PLATFORM_LABEL_CAPACITY = 2000

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
        "ip_address": "10.160.0.72",
        "description": "Additional miniInternet host used for multi-UT continuous handover verification.",
    },
)


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
    include_traceroute: bool = True,
):
    the_as.createNetwork(network_name, prefix)
    router = the_as.createRouter(node_name)
    router.joinNetwork(network_name, address)
    router.addSoftware("tcpdump")
    if include_traceroute:
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
    include_traceroute: bool = True,
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
    if include_traceroute:
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
    node_name_prefix: str = "",
) -> None:
    transit_as = base.createAutonomousSystem(asn)
    routers: Dict[int, object] = {}

    for ix in exchanges:
        router_name = f"{node_name_prefix}as{asn}-r{ix}"
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
    node_name_prefix: str = "",
) -> None:
    stub_as = base.createAutonomousSystem(asn)
    stub_as.createNetwork("net0")

    router = stub_as.createRouter(f"{node_name_prefix}as{asn}-router0")
    router.joinNetwork("net0")
    router.joinNetwork(f"ix{exchange}")

    for host_index in range(hosts_total):
        host = stub_as.createHost(f"{node_name_prefix}as{asn}-host{host_index}")
        host.joinNetwork("net0")


def _shift_ix_id(ix_id: int, ix_id_offset: int) -> int:
    return int(ix_id) + int(ix_id_offset)


def build_mini_internet(
    base: Base,
    ebgp: Ebgp,
    *,
    node_name_prefix: str = "",
    ix_id_offset: int = 0,
    transit_asns: Sequence[int] | None = None,
    stub_asns: Sequence[int] | None = None,
    extra_transit_ixs: Dict[int, Sequence[int]] | None = None,
) -> None:
    selected_transit_asns = (
        {int(asn) for asn in transit_asns}
        if transit_asns is not None
        else set(MINI_INTERNET_TRANSIT_AS_LAYOUT)
    )
    selected_stub_asns = (
        {int(asn) for asn in stub_asns}
        if stub_asns is not None
        else set(MINI_INTERNET_STUB_AS_EXCHANGES)
    )
    selected_ix_ids = {
        ix_id
        for asn, layout in MINI_INTERNET_TRANSIT_AS_LAYOUT.items()
        if asn in selected_transit_asns
        for ix_id in layout["exchanges"]
    }
    selected_ix_ids.update(
        exchange
        for asn, exchange in MINI_INTERNET_STUB_AS_EXCHANGES.items()
        if asn in selected_stub_asns
    )
    extra_transit_ixs_by_asn = {
        int(asn): {int(ix_id) for ix_id in ix_ids}
        for asn, ix_ids in dict(extra_transit_ixs or {}).items()
    }
    selected_ix_ids.update(
        ix_id
        for asn, ix_ids in extra_transit_ixs_by_asn.items()
        if asn in selected_transit_asns
        for ix_id in ix_ids
    )
    display_names = {
        **MINI_INTERNET_IX_DISPLAY_NAMES,
        **{
            ix_id: f"Gateway-IX-{ix_id}"
            for ix_id in selected_ix_ids
            if ix_id not in MINI_INTERNET_IX_DISPLAY_NAMES
        },
    }
    for ix_id, display_name in display_names.items():
        if ix_id not in selected_ix_ids:
            continue
        shifted_ix_id = _shift_ix_id(ix_id, ix_id_offset)
        ix = base.createInternetExchange(shifted_ix_id)
        ix.getPeeringLan().setDisplayName(display_name)

    for asn, layout in MINI_INTERNET_TRANSIT_AS_LAYOUT.items():
        if asn not in selected_transit_asns:
            continue
        layout_exchanges = [int(ix_id) for ix_id in layout["exchanges"]]
        extra_ixs = sorted(extra_transit_ixs_by_asn.get(int(asn), set()))
        exchanges = sorted(set(layout_exchanges).union(extra_ixs))
        anchor_ix = layout_exchanges[0]
        links = list(layout["links"]) + [
            (anchor_ix, ix_id)
            for ix_id in extra_ixs
            if ix_id not in layout_exchanges
        ]
        create_unique_transit_as(
            base,
            asn=asn,
            exchanges=[_shift_ix_id(ix_id, ix_id_offset) for ix_id in exchanges],
            intra_ix_links=[
                (
                    _shift_ix_id(left_ix, ix_id_offset),
                    _shift_ix_id(right_ix, ix_id_offset),
                )
                for left_ix, right_ix in links
            ],
            node_name_prefix=node_name_prefix,
        )

    for asn, exchange in MINI_INTERNET_STUB_AS_EXCHANGES.items():
        if asn not in selected_stub_asns:
            continue
        create_unique_stub_as_with_hosts(
            base,
            asn=asn,
            exchange=_shift_ix_id(exchange, ix_id_offset),
            hosts_total=MINI_INTERNET_HOSTS_PER_STUB_AS,
            node_name_prefix=node_name_prefix,
        )

    if 154 in selected_stub_asns:
        as154 = base.getAutonomousSystem(154)
        host_new = as154.createHost(f"{node_name_prefix}as154-host-new")
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
        eligible_peers = [peer for peer in peers if peer in selected_transit_asns]
        if ix_id not in selected_ix_ids or not eligible_peers:
            continue
        ebgp.addRsPeers(_shift_ix_id(ix_id, ix_id_offset), eligible_peers)

    for ix_id, providers, customers in MINI_INTERNET_PRIVATE_PEERINGS:
        if ix_id not in selected_ix_ids:
            continue
        eligible_providers = [asn for asn in providers if asn in selected_transit_asns]
        eligible_customers = [
            asn
            for asn in customers
            if asn in selected_transit_asns or asn in selected_stub_asns
        ]
        if not eligible_providers or not eligible_customers:
            continue
        ebgp.addPrivatePeerings(
            _shift_ix_id(ix_id, ix_id_offset),
            eligible_providers,
            eligible_customers,
            PeerRelationship.Provider,
        )
