#!/usr/bin/env python3
# encoding: utf-8

"""Build a clean SEED-generated Iteration 2 small-scale satellite Internet example."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from seed_integration import (
    EXTERNAL_TEST_TARGETS,
    SATELLITE_ASN,
    SATELLITE_OPERATOR_ASN,
    MINI_INTERNET_HOSTS_PER_STUB_AS,
    MINI_INTERNET_IX_DISPLAY_NAMES,
    MINI_INTERNET_STUB_AS_EXCHANGES,
    MINI_INTERNET_TRANSIT_AS_LAYOUT,
    build_mini_internet,
    create_bootstrap_router,
    create_satellite_link,
    create_ut_placeholder,
    enable_mpls_readiness,
    resolve_platform,
    resolve_ut_identity_address,
)
from seedemu.compiler import Docker
from seedemu.core import Emulator
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, PeerRelationship, Routing


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SATELLITE_DIR / "output" / "iteration2_small_scale"
DEFAULT_CONFIG_DIR = SATELLITE_DIR / "configs" / "iteration2_small_scale"
DEFAULT_TOPOLOGY_JSON = DEFAULT_CONFIG_DIR / "topology.json"
DEFAULT_LINK_STATE_JSON = DEFAULT_CONFIG_DIR / "link_state.json"

ACCESS_SLOT_COUNT = 1
GATEWAY_SLOT_COUNT = 1

ORBIT_DEFINITIONS = (
    {
        "orbit_id": "orbit-a",
        "plane_label": "Plane A",
        "plane_index": 1,
        "satellite_ids": ("sat-a1", "sat-a2", "sat-a3"),
    },
    {
        "orbit_id": "orbit-b",
        "plane_label": "Plane B",
        "plane_index": 2,
        "satellite_ids": ("sat-b1", "sat-b2"),
    },
)
SATELLITES = tuple(
    satellite_id
    for orbit in ORBIT_DEFINITIONS
    for satellite_id in orbit["satellite_ids"]
)

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
)
GATEWAYS = tuple(entry["gateway_id"] for entry in GATEWAY_DEFINITIONS)
GATEWAY_BY_ID = {
    str(entry["gateway_id"]): dict(entry)
    for entry in GATEWAY_DEFINITIONS
}

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
)
USER_TERMINALS = tuple(entry["ut_id"] for entry in UT_DEFINITIONS)
UT_BY_ID = {
    str(entry["ut_id"]): dict(entry)
    for entry in UT_DEFINITIONS
}

FIXED_INTRA_ORBIT_LINKS = (
    (
        "sat-a1",
        "sat-a2",
        "a1-a2",
        "10.250.201.0/30",
        "10.250.201.1",
        "10.250.201.2",
        "front",
        "back",
        "orbit-a",
    ),
    (
        "sat-a2",
        "sat-a3",
        "a2-a3",
        "10.250.202.0/30",
        "10.250.202.1",
        "10.250.202.2",
        "front",
        "back",
        "orbit-a",
    ),
    (
        "sat-b1",
        "sat-b2",
        "b1-b2",
        "10.250.205.0/30",
        "10.250.205.1",
        "10.250.205.2",
        "front",
        "back",
        "orbit-b",
    ),
)

FIXED_CROSS_ORBIT_SLOT_GROUPS = (
    {
        "pair_key": "ab",
        "display_label": "Plane A <-> Plane B",
        "orbit_a_id": "orbit-a",
        "orbit_b_id": "orbit-b",
        "candidate_pairs": (
            {
                "satellite_a_id": "sat-a2",
                "satellite_b_id": "sat-b1",
                "satellite_a_direction": "right",
                "satellite_b_direction": "left",
            },
        ),
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the clean SEED-generated Iteration 2 small-scale topology."
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
        help="Iteration 2 topology inventory output path.",
    )
    parser.add_argument(
        "--link-state-json",
        default=str(DEFAULT_LINK_STATE_JSON),
        help="Initial runtime link-state output path.",
    )
    return parser.parse_args()


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
    for index, satellite_id in enumerate(SATELLITES, start=1):
        satellite_routers[satellite_id] = create_bootstrap_router(
            satellite_as,
            satellite_id,
            f"{satellite_id}-bootstrap",
            f"10.250.{index}.0/24",
            f"10.250.{index}.2",
        )
        enable_mpls_readiness(satellite_routers[satellite_id])

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
            f"ix{definition['ix_id']}",
            str(definition["ix_ip_address"]),
        )
        ebgp.addPrivatePeering(
            int(definition["ix_id"]),
            int(definition["upstream_asn"]),
            SATELLITE_OPERATOR_ASN,
            PeerRelationship.Provider,
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


def build_slot_inventory() -> Dict[str, List[Dict[str, object]]]:
    ut_slots: List[Dict[str, object]] = []
    for definition in UT_DEFINITIONS:
        ut_id = str(definition["ut_id"])
        ut_index = int(definition["ut_index"])
        third_octet = ut_index * 10 + 1
        interface_name = f"ua-u{ut_index}s0"
        ut_slots.append(
            {
                "slot_name": f"{ut_id}-access-slot0",
                "slot_type": "ut_access",
                "network_name": f"{ut_id}-access-slot0",
                "docker_network_name": f"{ut_id}-access-slot0",
                "subnet": f"10.90.{third_octet}.0/29",
                "bridge_gateway_ip": f"10.90.{third_octet}.6",
                "ut_id": ut_id,
                "region": definition["region"],
                "slot_index": 0,
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
        gateway_index = int(definition["gateway_index"])
        third_octet = gateway_index * 10 + 1
        interface_name = f"ga-g{gateway_index}s0"
        gateway_slots.append(
            {
                "slot_name": f"{gateway_id}-gateway-slot0",
                "slot_type": "gw_access",
                "network_name": f"{gateway_id}-gateway-slot0",
                "docker_network_name": f"{gateway_id}-gateway-slot0",
                "subnet": f"10.91.{third_octet}.0/29",
                "bridge_gateway_ip": f"10.91.{third_octet}.6",
                "gateway_id": gateway_id,
                "gateway_region": definition["region"],
                "slot_index": 0,
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
    for group in FIXED_CROSS_ORBIT_SLOT_GROUPS:
        pair_key = str(group["pair_key"])
        orbit_a_id = str(group["orbit_a_id"])
        orbit_b_id = str(group["orbit_b_id"])
        cross_plane_slots.append(
            {
                "slot_name": f"{pair_key}-cross-slot0",
                "slot_type": "cross_plane_isl",
                "network_name": f"{pair_key}-cross-slot0",
                "docker_network_name": f"{pair_key}-cross-slot0",
                "subnet": f"10.92.{subnet_offset}.0/29",
                "bridge_gateway_ip": f"10.92.{subnet_offset}.6",
                "slot_index": 0,
                "pair_key": pair_key,
                "display_label": str(group["display_label"]),
                "orbit_pair": [orbit_a_id, orbit_b_id],
                "orbit_a_id": orbit_a_id,
                "orbit_b_id": orbit_b_id,
                "orbit_a_satellite_ids": list(
                    next(
                        orbit["satellite_ids"]
                        for orbit in ORBIT_DEFINITIONS
                        if orbit["orbit_id"] == orbit_a_id
                    )
                ),
                "orbit_b_satellite_ids": list(
                    next(
                        orbit["satellite_ids"]
                        for orbit in ORBIT_DEFINITIONS
                        if orbit["orbit_id"] == orbit_b_id
                    )
                ),
                "endpoint_a_template": _endpoint_record(
                    node_id=f"<runtime-{orbit_a_id}-satellite>",
                    node_type="satellite",
                    interface_name="xp0a",
                    ip_address=f"10.92.{subnet_offset}.1/29",
                ),
                "endpoint_b_template": _endpoint_record(
                    node_id=f"<runtime-{orbit_b_id}-satellite>",
                    node_type="satellite",
                    interface_name="xp0b",
                    ip_address=f"10.92.{subnet_offset}.2/29",
                ),
                "candidate_pairs": [dict(pair) for pair in group["candidate_pairs"]],
            }
        )
        subnet_offset += 1

    return {
        "ut_access": ut_slots,
        "gateway_access": gateway_slots,
        "cross_plane_isl": cross_plane_slots,
    }


def build_topology_inventory() -> Dict[str, object]:
    fixed_links: List[Dict[str, object]] = []
    for (
        left,
        right,
        label,
        prefix,
        left_ip,
        right_ip,
        left_dir,
        right_dir,
        orbit_scope,
    ) in FIXED_INTRA_ORBIT_LINKS:
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
        "schema_version": "satellite-iteration2-small-scale-topology-v1",
        "description": "Clean SEED-generated Iteration 2 small-scale satellite Internet example.",
        "logical_port_labels": ["front", "back", "left", "right"],
        "prototype_scope": {
            "orbital_planes": len(ORBIT_DEFINITIONS),
            "satellites": len(SATELLITES),
            "gateways": len(GATEWAYS),
            "user_terminals": len(USER_TERMINALS),
            "access_slots_per_ut": ACCESS_SLOT_COUNT,
            "gateway_slots_per_gw": GATEWAY_SLOT_COUNT,
            "cross_plane_slot_count": len(FIXED_CROSS_ORBIT_SLOT_GROUPS),
            "internet_exchanges": len(MINI_INTERNET_IX_DISPLAY_NAMES),
            "mini_internet_transit_ases": len(MINI_INTERNET_TRANSIT_AS_LAYOUT),
            "mini_internet_stub_ases": len(MINI_INTERNET_STUB_AS_EXCHANGES),
            "example_profile": "iteration2_small_scale_clean_seed_generated",
        },
        "orbital_planes": [
            {
                "orbit_id": orbit["orbit_id"],
                "plane_label": orbit["plane_label"],
                "plane_index": orbit["plane_index"],
                "satellite_ids": list(orbit["satellite_ids"]),
                "same_plane_neighbor_model": "fixed_linear_or_ring_subset",
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
                "gateway_region": GATEWAY_BY_ID[gateway_id]["region"],
                "gateway_index": GATEWAY_BY_ID[gateway_id]["gateway_index"],
                "bootstrap_network": _bootstrap_inventory(
                    node_id=gateway_id,
                    node_type="gateway",
                    network_name=f"{gateway_id}-bootstrap",
                    subnet=f"10.65.{20 + index}.0/24",
                    ip_address=f"10.65.{20 + index}.2/24",
                    purpose="compile_time_carrier",
                ),
                "internet_uplink": {
                    "ix_id": GATEWAY_BY_ID[gateway_id]["ix_id"],
                    "network_name": f"ix{GATEWAY_BY_ID[gateway_id]['ix_id']}",
                    "ip_address": GATEWAY_BY_ID[gateway_id]["ix_ip_address"],
                    "peering_asn": SATELLITE_OPERATOR_ASN,
                    "region": GATEWAY_BY_ID[gateway_id]["region"],
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
                "region": UT_BY_ID[ut_id]["region"],
                "candidate_serving_satellite_ids": list(
                    UT_BY_ID[ut_id]["candidate_satellite_ids"]
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
                    "ix_id": GATEWAY_BY_ID[gateway_id]["ix_id"],
                    "ip_address": GATEWAY_BY_ID[gateway_id]["ix_ip_address"],
                }
                for gateway_id in GATEWAYS
            ],
        },
        "slot_inventory": build_slot_inventory(),
    }
    return topology


def build_initial_link_state(topology: Dict[str, object]) -> Dict[str, object]:
    slot_inventory = dict(topology.get("slot_inventory", {}))
    fixed_links = list(topology.get("fixed_links", {}).get("satellite_satellite", []))
    slots: List[Dict[str, object]] = []

    for slot in slot_inventory.get("ut_access", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "ut_access",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": slot.get("ut_id"),
                "preconnected_endpoints": [dict(slot.get("preconnected_endpoint", {}))],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    for slot in slot_inventory.get("gateway_access", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "gw_access",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": slot.get("gateway_id"),
                "preconnected_endpoints": [dict(slot.get("preconnected_endpoint", {}))],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    for slot in slot_inventory.get("cross_plane_isl", []):
        slots.append(
            {
                "slot_name": slot.get("slot_name"),
                "slot_type": "cross_plane_isl",
                "network_name": slot.get("network_name"),
                "docker_network_name": slot.get("docker_network_name"),
                "subnet": slot.get("subnet"),
                "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                "owner_node_id": None,
                "preconnected_endpoints": [],
                "dynamic_endpoints": [],
                "occupied": False,
                "binding": None,
            }
        )

    for link in fixed_links:
        endpoints = [dict(endpoint) for endpoint in link.get("endpoints", [])]
        slots.append(
            {
                "slot_name": link.get("link_id"),
                "slot_type": "fixed_isl",
                "network_name": link.get("network_name"),
                "docker_network_name": link.get("docker_network_name"),
                "subnet": link.get("subnet"),
                "bridge_gateway_ip": None,
                "owner_node_id": None,
                "preconnected_endpoints": endpoints,
                "dynamic_endpoints": [],
                "occupied": True,
                "binding": {
                    "satellite_a_id": endpoints[0].get("node_id") if endpoints else None,
                    "satellite_b_id": endpoints[1].get("node_id")
                    if len(endpoints) > 1
                    else None,
                },
            }
        )

    return {
        "schema_version": "satellite-phase2-slot-state-v1",
        "runtime_initialized": True,
        "topology_schema_version": topology.get("schema_version"),
        "slots": sorted(slots, key=lambda entry: str(entry.get("slot_name", ""))),
    }


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    topology_json = Path(args.topology_json).resolve()
    link_state_json = Path(args.link_state_json).resolve()

    emu = build_emulator()
    emu.render()
    emu.compile(
        Docker(
            selfManagedNetwork=True,
            platform=resolve_platform(args.platform),
            namingScheme="{name}",
        ),
        str(output_dir),
        override=True,
    )

    topology = build_topology_inventory()
    link_state = build_initial_link_state(topology)
    write_json(topology_json, topology)
    write_json(link_state_json, link_state)

    print("Iteration 2 small-scale topology compiled successfully.")
    print(f"Output directory: {output_dir}")
    print(f"Topology inventory: {topology_json}")
    print(f"Link state: {link_state_json}")
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
        f"cross_plane_isl={len(FIXED_CROSS_ORBIT_SLOT_GROUPS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
