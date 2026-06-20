#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from satellite.bpf_dataplane_canary import (
    build_metrics,
    host_interface_name,
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


if __name__ == "__main__":
    unittest.main()
