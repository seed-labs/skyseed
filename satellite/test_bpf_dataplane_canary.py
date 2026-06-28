#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from satellite.bpf_dataplane_canary import (
    SELECTOR_MODE_FULL_ELIGIBLE,
    bpf_map_capacities_for_scope,
    build_metrics,
    diagnose_experimental_scope_selection,
    host_interface_name,
    load_canary_maps,
    normalize_selector,
    select_experimental_scope,
    select_canary_scope,
)


class BpfDataplaneCanaryTests(unittest.TestCase):
    def test_selects_single_ut_access_scope_from_successful_runtime_ping(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skyseed-bpf-canary-test-") as tmp:
            root = Path(tmp)
            topology_path = root / "topology.json"
            link_state_path = root / "link_state.json"
            topology = {
                "slot_inventory": {
                    "ut_access": [
                        {
                            "slot_name": "ut3-access-slot0",
                            "slot_type": "ut_access",
                            "preconnected_endpoint": {
                                "node_id": "ut3",
                                "node_type": "user_terminal",
                                "interface_name": "ua-u3s0",
                                "ip_address": "10.90.3.2/29",
                            },
                            "dynamic_endpoint_template": {
                                "node_id": "<runtime-satellite>",
                                "node_type": "satellite",
                                "interface_name": "ua-u3s0",
                                "ip_address": "10.90.3.1/29",
                            },
                        }
                    ]
                }
            }
            link_state = {
                "slots": [
                    {
                        "slot_name": "ut3-access-slot0",
                        "occupied": True,
                        "binding": {"satellite_id": "stl-7"},
                        "runtime_parameters": {
                            "loss_percent": 0.25,
                            "delay_ms": 12.0,
                            "bandwidth_mbps": 50.0,
                        },
                    }
                ]
            }
            runtime_report = {
                "iterations": [
                    {
                        "node_local_execution": {
                            "ping_results": [
                                {
                                    "success": True,
                                    "flow_id": "ut3-default",
                                    "ut_id": "ut3",
                                    "ping_target_ip": "198.51.100.10",
                                    "target_host_node_id": "h-default",
                                    "serving_satellite_id": "stl-7",
                                    "selected_gateway_id": "gw5",
                                }
                            ]
                        }
                    }
                ]
            }
            topology_path.write_text(json.dumps(topology), encoding="utf-8")
            link_state_path.write_text(json.dumps(link_state), encoding="utf-8")

            with patch("satellite.bpf_dataplane_canary.interface_ifindex", return_value=42):
                scope = select_canary_scope(
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    runtime_report=runtime_report,
                )

            expected_ut_host = host_interface_name(
                slot_name="ut3-access-slot0",
                endpoint=topology["slot_inventory"]["ut_access"][0]["preconnected_endpoint"],
                role="preconnected",
            )
            expected_sat_host = host_interface_name(
                slot_name="ut3-access-slot0",
                endpoint={
                    **topology["slot_inventory"]["ut_access"][0]["dynamic_endpoint_template"],
                    "node_id": "stl-7",
                },
                role="candidate:stl-7",
            )
            self.assertEqual(scope["scope"], "single_ut_access_path")
            self.assertEqual(scope["target_ip"], "198.51.100.10")
            self.assertEqual(scope["ut_host_interface"], expected_ut_host)
            self.assertEqual(scope["satellite_host_interface"], expected_sat_host)
            self.assertEqual(scope["satellite_ifindex"], 42)
            self.assertEqual(scope["parameter_loss_ppm"], 2500)
            self.assertIn("canonical", scope["canonical_fallback_scope"])

    def test_metrics_schema_contains_required_canary_fields(self) -> None:
        metrics = build_metrics(
            enabled=True,
            attached_interfaces=["vhs123"],
            loaded_generation_count=3,
            loaded_fec_count=1,
            counters={
                "pass": 2,
                "drop": 0,
                "redirect": 2,
                "canonical_fallback": 1,
            },
            rollback_completed=True,
        )

        self.assertTrue(metrics["bpf_canary_enabled"])
        self.assertEqual(metrics["bpf_attached_interface_count"], 1)
        self.assertEqual(metrics["bpf_loaded_generation_count"], 3)
        self.assertEqual(metrics["bpf_loaded_fec_count"], 1)
        self.assertEqual(metrics["bpf_pass_counters"], 2)
        self.assertEqual(metrics["bpf_drop_counters"], 0)
        self.assertEqual(metrics["bpf_redirect_counters"], 2)
        self.assertEqual(metrics["canonical_fallback_count"], 1)
        self.assertTrue(metrics["bpf_rollback_completed"])
        self.assertEqual(metrics["bpf_backend_mode"], "experimental_bpf_backend")
        self.assertTrue(metrics["bpf_enabled"])
        self.assertEqual(metrics["bpf_scope"], "semantic_intent_canary_subset")
        self.assertEqual(metrics["loaded_runtime_generation_count"], 3)
        self.assertEqual(metrics["loaded_gz_fec_count"], 1)
        self.assertTrue(metrics["canonical_runtime_unchanged"])

    def test_selects_scope_from_semantic_intent_and_selector(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skyseed-bpf-backend-test-") as tmp:
            root = Path(tmp)
            topology_path = root / "topology.json"
            link_state_path = root / "link_state.json"
            topology = {
                "slot_inventory": {
                    "ut_access": [
                        {
                            "slot_name": "ut3-access-slot0",
                            "slot_type": "ut_access",
                            "preconnected_endpoint": {
                                "node_id": "ut3",
                                "node_type": "user_terminal",
                                "interface_name": "ua-u3s0",
                                "ip_address": "10.90.3.2/29",
                            },
                            "dynamic_endpoint_template": {
                                "node_id": "<runtime-satellite>",
                                "node_type": "satellite",
                                "interface_name": "ua-u3s0",
                                "ip_address": "10.90.3.1/29",
                            },
                        }
                    ]
                }
            }
            link_state = {
                "slots": [
                    {
                        "slot_name": "ut3-access-slot0",
                        "occupied": True,
                        "binding": {"satellite_id": "stl-7"},
                        "runtime_parameters": {
                            "loss_percent": 0.0,
                            "delay_ms": 9.0,
                            "bandwidth_mbps": 70.0,
                        },
                    }
                ]
            }
            runtime_report = {
                "iterations": [
                    {
                        "node_local_execution": {
                            "ping_results": [
                                {
                                    "success": True,
                                    "flow_id": "ut3-default",
                                    "ut_id": "ut3",
                                    "ping_target_ip": "198.51.100.10",
                                    "target_host_node_id": "h-default",
                                    "serving_satellite_id": "stl-7",
                                    "selected_gateway_id": "gw5",
                                }
                            ]
                        }
                    }
                ]
            }
            compiled = {
                "bpf_maps": {
                    "runtime": {
                        "active_generation": {"value": 2},
                        "fec_forwarding": [
                            {
                                "generation_id": 2,
                                "active": True,
                                "fec_id": "g-gw5-zone-a",
                                "fec_kind": "G",
                                "selected_gateway_id": "gw5",
                                "zone_id": "zone-a",
                                "epoch_id": "runtimeepoch-2",
                                "source": "gz_fec_plan",
                            },
                            {
                                "generation_id": 2,
                                "active": True,
                                "fec_id": "g-gw9-zone-b",
                                "fec_kind": "G",
                                "selected_gateway_id": "gw9",
                                "zone_id": "zone-b",
                            },
                        ],
                    },
                    "parameter": {"parameter_generation": {"value": 4}},
                }
            }
            topology_path.write_text(json.dumps(topology), encoding="utf-8")
            link_state_path.write_text(json.dumps(link_state), encoding="utf-8")

            with patch("satellite.bpf_dataplane_canary.interface_ifindex", return_value=77):
                scope = select_experimental_scope(
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    runtime_report=runtime_report,
                    compiled=compiled,
                    selector={
                        "gw_ids": ["gw5"],
                        "zone_ids": ["zone-a"],
                        "max_scope_entries": 1,
                    },
                )

            self.assertEqual(scope["scope"], "semantic_intent_canary_subset")
            self.assertEqual(scope["active_runtime_generation"], 2)
            self.assertEqual(scope["active_parameter_generation"], 4)
            self.assertEqual(scope["attached_interfaces"], [scope["entries"][0]["ut_host_interface"]])
            self.assertEqual(scope["entries"][0]["fec"]["fec_id"], "g-gw5-zone-a")
            self.assertEqual(scope["entries"][0]["satellite_ifindex"], 77)
            self.assertEqual(scope["target_ips"], ["198.51.100.10"])

    def test_full_eligible_selector_uses_full_scope_safety_limit(self) -> None:
        canary = normalize_selector({"max_scope_entries": 999})
        self.assertEqual(canary["mode"], "semantic_intent_canary_subset")
        self.assertEqual(canary["max_scope_entries"], 16)

        full = normalize_selector(
            {
                "mode": SELECTOR_MODE_FULL_ELIGIBLE,
                "max_scope_entries": 999,
                "safety_limits": {"max_scope_entries": 128},
            }
        )
        self.assertEqual(full["mode"], SELECTOR_MODE_FULL_ELIGIBLE)
        self.assertEqual(full["max_scope_entries"], 128)

    def test_full_eligible_scope_prefers_bpf_owned_runtime_packet_routes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skyseed-bpf-backend-test-") as tmp:
            root = Path(tmp)
            topology_path = root / "topology.json"
            link_state_path = root / "link_state.json"
            topology_path.write_text(json.dumps({"slot_inventory": {}}), encoding="utf-8")
            link_state_path.write_text(json.dumps({"slots": []}), encoding="utf-8")

            runtime_report = {
                "iterations": [
                    {
                        "node_local_execution": {
                            "ping_results": [
                                {
                                    "success": True,
                                    "flow_id": "ut-internet-egress:ut2027",
                                    "ut_id": "ut2027",
                                    "ping_target_ip": "10.154.0.129",
                                    "target_host_node_id": "as154-host-new",
                                    "serving_satellite_id": "stl-940047",
                                    "selected_gateway_id": "gw207",
                                }
                            ],
                            "backend": {
                                "bpf_owned_readiness": {
                                    "ready": True,
                                    "ok": True,
                                    "provider": "bpf_owned",
                                    "generation_id": 0,
                                    "fallback_to_linux": False,
                                    "packet_route_entry_count": 2,
                                    "attached_interfaces": [
                                        "netns:stl-940047:ua-u2027s0",
                                        "netns:stl-940047:ga-g207s0",
                                        "netns:gw207:ix106",
                                        "netns:gw207:ga-g207s0",
                                    ],
                                    "resolved_packet_route_entries": [
                                        {
                                            "direction": "g_to_z_access_ingress",
                                            "flow_id": "ut-internet-egress:ut2027",
                                            "ut_id": "ut2027",
                                            "target_ip": "10.154.0.129",
                                            "selected_gateway_id": "gw207",
                                            "serving_satellite_id": "stl-940047",
                                            "slot_name": "ut2027-access-slot0",
                                            "ingress_interface": "netns:stl-940047:ua-u2027s0",
                                            "egress_interface": "netns:stl-940047:ga-g207s0",
                                            "ingress_ifindex": 11,
                                            "egress_ifindex": 12,
                                            "satellite_ifindex": 12,
                                            "dst_mac": "02:aa:aa:aa:aa:aa",
                                            "src_mac": "02:bb:bb:bb:bb:bb",
                                        },
                                        {
                                            "direction": "z_to_g_nat_ingress",
                                            "flow_id": "ut-internet-egress:ut2027",
                                            "ut_id": "ut2027",
                                            "target_ip": "10.242.207.159",
                                            "selected_gateway_id": "gw207",
                                            "serving_satellite_id": "stl-940047",
                                            "slot_name": "gw207-gateway-slot0",
                                            "ingress_interface": "netns:gw207:ix106",
                                            "egress_interface": "netns:gw207:ga-g207s0",
                                            "ingress_ifindex": 21,
                                            "egress_ifindex": 22,
                                            "satellite_ifindex": 22,
                                            "new_dst_ip": "10.97.235.2",
                                            "dst_mac": "02:cc:cc:cc:cc:cc",
                                            "src_mac": "02:dd:dd:dd:dd:dd",
                                        },
                                    ],
                                }
                            },
                        }
                    }
                ]
            }
            compiled = {
                "bpf_maps": {
                    "runtime": {
                        "active_generation": {"value": 0},
                        "generation_status": [{"generation_id": 0, "installed": True}],
                        "fec_forwarding": [
                            {
                                "generation_id": 0,
                                "fec_key_u32": 101,
                                "active": True,
                            }
                        ],
                    },
                    "parameter": {
                        "parameter_generation": {"value": 0},
                        "param_status": [{"generation_id": 0, "installed": True}],
                        "parameter_values": [],
                    },
                }
            }

            scope = select_experimental_scope(
                topology_path=topology_path,
                link_state_path=link_state_path,
                runtime_report=runtime_report,
                compiled=compiled,
                selector={
                    "mode": SELECTOR_MODE_FULL_ELIGIBLE,
                    "max_scope_entries": 8,
                },
            )

            self.assertEqual(scope["scope"], SELECTOR_MODE_FULL_ELIGIBLE)
            self.assertEqual(
                scope["semantic_intent_sources"]["scope"],
                "full eligible strict BPF-owned packet-route/MPLS LFIB scope from runtime bpf_owned_readiness evidence",
            )
            self.assertEqual(len(scope["entries"]), 2)
            self.assertEqual(scope["entries"][0]["direction"], "g_to_z_access_ingress")
            self.assertEqual(
                scope["entries"][0]["egress_interface"],
                "netns:stl-940047:ga-g207s0",
            )
            self.assertEqual(scope["entries"][1]["direction"], "z_to_g_nat_ingress")
            self.assertEqual(scope["entries"][1]["new_dst_ip"], "10.97.235.2")
            self.assertEqual(
                scope["attached_interfaces"],
                [
                    "netns:gw207:ga-g207s0",
                    "netns:gw207:ix106",
                    "netns:stl-940047:ga-g207s0",
                    "netns:stl-940047:ua-u2027s0",
                ],
            )
            self.assertEqual(scope["target_ips"], ["10.154.0.129"])
            self.assertEqual(
                scope["bpf_owned_readiness_source"]["packet_route_entry_count"],
                2,
            )

    def test_full_eligible_scope_failure_reports_candidate_rejections(self) -> None:
        runtime_report = {"iterations": [{"node_local_execution": {}}]}
        diagnostics = diagnose_experimental_scope_selection(
            runtime_report=runtime_report,
            selector={
                "mode": SELECTOR_MODE_FULL_ELIGIBLE,
                "max_scope_entries": 8,
            },
        )

        self.assertEqual(
            diagnostics["requested_scope"]["mode"],
            SELECTOR_MODE_FULL_ELIGIBLE,
        )
        self.assertEqual(
            diagnostics["candidate_scope"]["bpf_owned_readiness_record_count"],
            0,
        )
        self.assertIn(
            "no_bpf_owned_readiness_records_with_scope_entries",
            {
                str(item.get("reason"))
                for item in diagnostics["rejected_scope_entries"]
            },
        )

    def test_full_eligible_scope_selects_mpls_lfib_readiness_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skyseed-bpf-mpls-scope-test-") as tmp:
            root = Path(tmp)
            topology_path = root / "topology.json"
            link_state_path = root / "link_state.json"
            topology_path.write_text("{}", encoding="utf-8")
            link_state_path.write_text("{}", encoding="utf-8")
            runtime_report = {
                "iterations": [
                    {
                        "node_local_execution": {
                            "backend": {
                                "bpf_owned_readiness": {
                                    "ready": True,
                                    "ok": True,
                                    "provider": "bpf_owned",
                                    "generation_id": 2,
                                    "fallback_to_linux": False,
                                    "packet_route_entry_count": 0,
                                    "resolved_packet_route_entries": [],
                                    "attached_interfaces": [
                                        "netns:stl-940047:ua-u2001s0",
                                        "netns:stl-940047:ga-g201s0",
                                    ],
                                    "resolved_mpls_flow_entries": [
                                        {
                                            "direction": "g_to_z",
                                            "flow_id": "ut-internet-egress:ut2001",
                                            "ut_id": "ut2001",
                                            "target_ip": "10.154.0.129",
                                            "map_type": "ingress_classifier_map",
                                            "ingress_interface": (
                                                "netns:stl-940047:ua-u2001s0"
                                            ),
                                            "out_interface": (
                                                "netns:stl-940047:ga-g201s0"
                                            ),
                                            "ingress_ifindex": 6014,
                                            "out_ifindex": 5628,
                                        }
                                    ],
                                }
                            }
                        }
                    }
                ]
            }
            compiled = {
                "bpf_maps": {
                    "runtime": {
                        "active_generation": {"value": 2},
                        "generation_status": [{"generation_id": 2, "installed": True}],
                        "fec_forwarding": [],
                    },
                    "parameter": {
                        "parameter_generation": {"value": 0},
                        "param_status": [{"generation_id": 0, "installed": True}],
                        "parameter_values": [],
                    },
                }
            }

            scope = select_experimental_scope(
                topology_path=topology_path,
                link_state_path=link_state_path,
                runtime_report=runtime_report,
                compiled=compiled,
                selector={
                    "mode": SELECTOR_MODE_FULL_ELIGIBLE,
                    "max_scope_entries": 4,
                },
            )
            diagnostics = diagnose_experimental_scope_selection(
                runtime_report=runtime_report,
                selector={
                    "mode": SELECTOR_MODE_FULL_ELIGIBLE,
                    "max_scope_entries": 4,
                },
            )

        self.assertEqual(scope["scope"], SELECTOR_MODE_FULL_ELIGIBLE)
        self.assertEqual(len(scope["entries"]), 1)
        self.assertEqual(scope["entries"][0]["scope_evidence"], "mpls_lfib")
        self.assertEqual(
            scope["entries"][0]["egress_interface"],
            "netns:stl-940047:ga-g201s0",
        )
        self.assertEqual(scope["target_ips"], ["10.154.0.129"])
        self.assertEqual(
            scope["bpf_owned_readiness_source"]["scope_entry_source"],
            "mpls_lfib",
        )
        self.assertEqual(
            diagnostics["candidate_scope"]["candidate_mpls_flow_entry_count"],
            1,
        )
        self.assertEqual(
            diagnostics["candidate_scope"]["selector_matched_entry_count"],
            1,
        )

    def test_full_eligible_scope_loads_compiled_map_counts(self) -> None:
        compiled = {
            "bpf_maps": {
                "runtime": {
                    "generation_status": [
                        {"generation_id": 0, "installed": True},
                        {"generation_id": 1, "installed": True},
                    ],
                    "fec_forwarding": [
                        {
                            "generation_id": 0,
                            "fec_key_u32": 101,
                            "active": True,
                        },
                        {
                            "generation_id": 0,
                            "fec_key_u32": 102,
                            "active": False,
                        },
                        {
                            "generation_id": 1,
                            "fec_key_u32": 103,
                            "active": True,
                        },
                    ],
                },
                "parameter": {
                    "param_status": [
                        {"generation_id": 0, "installed": True},
                        {"generation_id": 1, "installed": True},
                        {"generation_id": 2, "installed": True},
                    ],
                    "parameter_values": [
                        {
                            "generation_id": 0,
                            "link_id": "link-a",
                            "link_key_u32": 201,
                            "flow_key_u32": 0,
                            "active": True,
                            "loss_ppm": 0,
                            "mark": 301,
                        },
                        {
                            "generation_id": 1,
                            "link_id": "link-a",
                            "link_key_u32": 201,
                            "flow_key_u32": 0,
                            "active": True,
                            "loss_ppm": 12,
                            "mark": 302,
                        },
                        {
                            "generation_id": 1,
                            "link_id": "link-b",
                            "link_key_u32": 202,
                            "flow_key_u32": 0,
                            "active": False,
                            "loss_ppm": 0,
                            "mark": 303,
                        },
                        {
                            "generation_id": 2,
                            "link_id": "link-b",
                            "link_key_u32": 202,
                            "flow_key_u32": 0,
                            "active": True,
                            "loss_ppm": 5,
                            "mark": 304,
                        },
                    ],
                },
            }
        }
        scope = {
            "scope": SELECTOR_MODE_FULL_ELIGIBLE,
            "selector": normalize_selector(
                {
                    "mode": SELECTOR_MODE_FULL_ELIGIBLE,
                    "max_scope_entries": 8,
                }
            ),
            "entries": [
                {
                    "target_ip": "198.51.100.10",
                    "satellite_ifindex": 42,
                    "parameter_loss_ppm": 0,
                    "parameter_mark": 77,
                    "slot_name": "ut3-access-slot0",
                },
                {
                    "target_ip": "198.51.100.10",
                    "satellite_ifindex": 99,
                    "parameter_loss_ppm": 0,
                    "parameter_mark": 88,
                    "slot_name": "ut4-access-slot0",
                }
            ],
        }

        capacities = bpf_map_capacities_for_scope(compiled=compiled, scope=scope)
        self.assertEqual(capacities["runtime_generations"], 2)
        self.assertEqual(capacities["gz_fec_entries"], 4)
        self.assertEqual(capacities["parameter_generations"], 3)
        self.assertEqual(capacities["parameter_entries"], 5)

        ok_update = {"returncode": 0, "stdout": "", "stderr": ""}
        with patch("satellite.bpf_dataplane_canary.update_u32_map", return_value=ok_update), patch(
            "satellite.bpf_dataplane_canary.update_hash_map",
            return_value=ok_update,
        ):
            result = load_canary_maps(
                compiled=compiled,
                scope=scope,
                active_runtime_generation=0,
                active_parameter_generation=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["loaded_generation_count"], 2)
        self.assertEqual(result["loaded_fec_count"], 3)
        self.assertEqual(result["loaded_parameter_generation_count"], 3)
        self.assertEqual(result["loaded_parameter_link_count"], 2)
        self.assertEqual(result["loaded_parameter_entry_count"], 4)
        self.assertEqual(result["loaded_dataplane_route_count"], 1)
        self.assertEqual(result["loaded_dataplane_parameter_entry_count"], 1)


if __name__ == "__main__":
    unittest.main()
