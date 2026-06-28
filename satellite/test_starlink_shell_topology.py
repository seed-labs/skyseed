#!/usr/bin/env python3
# encoding: utf-8

import unittest

from experiment_topology import ExperimentTerminalProfile, ExperimentTimeSlice
from starlink_shell_topology import (
    StarlinkShellLayout,
    StarlinkSatelliteLayout,
    _gateway_transit_ixs_for_mini_internet,
    _mini_internet_exchange_records,
    _zone_return_prefixes_from_ut_slots,
    build_starlink_preview_scenario,
    build_starlink_shell_slot_inventory,
)


class StarlinkShellTopologyTests(unittest.TestCase):
    def test_gateway_ix_extension_normalizes_offset_and_inventory(self) -> None:
        gateways = (
            {"gateway_id": "gw-a", "upstream_asn": 3, "ix_id": 206},
            {"gateway_id": "gw-b", "upstream_asn": 3, "ix_id": 207},
            {"gateway_id": "gw-c", "upstream_asn": 4, "ix_id": 202},
        )

        self.assertEqual(
            _gateway_transit_ixs_for_mini_internet(
                gateways,
                ix_id_offset=100,
            ),
            {3: [106, 107], 4: [102]},
        )

        records = _mini_internet_exchange_records(gateways, ix_id_offset=100)
        ix_ids = [record["ix_id"] for record in records]

        self.assertIn(200, ix_ids)
        self.assertIn(205, ix_ids)
        self.assertIn(206, ix_ids)
        self.assertIn(207, ix_ids)

    def test_zone_return_prefixes_are_aggregated_per_zone(self) -> None:
        prefixes = _zone_return_prefixes_from_ut_slots(
            terminal_profiles=(
                ExperimentTerminalProfile(
                    ut_id="ut101",
                    slot_name="ut101-access-slot0",
                    zone_id="zone-a",
                    candidate_satellite_ids=("stl-1",),
                ),
                ExperimentTerminalProfile(
                    ut_id="ut102",
                    slot_name="ut102-access-slot0",
                    zone_id="zone-a",
                    candidate_satellite_ids=("stl-1",),
                ),
                ExperimentTerminalProfile(
                    ut_id="ut201",
                    slot_name="ut201-access-slot0",
                    zone_id="zone-b",
                    candidate_satellite_ids=("stl-1",),
                ),
            ),
            slot_inventory={
                "ut_access": [
                    {
                        "ut_id": "ut101",
                        "preconnected_endpoint": {
                            "ip_address": "10.90.101.2/29",
                        },
                    },
                    {
                        "ut_id": "ut102",
                        "preconnected_endpoint": {
                            "ip_address": "10.90.102.2/29",
                        },
                    },
                    {
                        "ut_id": "ut201",
                        "preconnected_endpoint": {
                            "ip_address": "10.90.201.2/29",
                        },
                    },
                ],
            },
        )

        self.assertEqual(prefixes["zone-a"], ["10.90.100.0/22"])
        self.assertEqual(prefixes["zone-b"], ["10.90.201.2/32"])

    def test_zone_catalog_prefix_override_becomes_special_prefix_policy(self) -> None:
        layout = StarlinkShellLayout(
            scenario_name="unit",
            planes=(),
            satellites=(
                StarlinkSatelliteLayout(
                    satellite_id="stl-a",
                    satellite_name="A",
                    norad_id=1,
                    orbit_id="plane-a",
                    plane_index=1,
                    plane_label="A",
                    satellite_index_in_plane=1,
                    inclination_deg=0.0,
                    raan_deg=0.0,
                    mean_anomaly_deg=0.0,
                    mean_motion_rev_per_day=1.0,
                ),
            ),
            fixed_links=(),
            cross_plane_slot_groups=(),
        )

        scenario = build_starlink_preview_scenario(
            layout=layout,
            time_slices=(ExperimentTimeSlice("slice-1", 0.0, 10.0),),
            ut_definitions=[
                {
                    "ut_id": "ut1",
                    "zone_id": "zone-a",
                    "latitude_deg": 64.1,
                    "longitude_deg": -21.9,
                }
            ],
            gateway_definitions=[
                {
                    "gateway_id": "gw1",
                    "latitude_deg": 64.1,
                    "longitude_deg": -21.9,
                },
                {
                    "gateway_id": "gw5",
                    "latitude_deg": 64.2,
                    "longitude_deg": -51.7,
                },
            ],
            zone_definitions=[
                {
                    "zone_id": "zone-a",
                    "display_name": "Zone A",
                    "region": "north",
                    "latitude_deg": 64.1,
                    "longitude_deg": -21.9,
                    "prefix_overrides": [
                        {
                            "prefix": "10.160.0.0/24",
                            "gateway": "gw5",
                            "preferred_gateway_ids": ["gw5", "gw1"],
                            "policy_name": "zone_a_as160_remote_override",
                        }
                    ],
                }
            ],
        )

        policies = list(scenario.special_prefix_override_policies)

        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0].policy_name, "zone_a_as160_remote_override")
        self.assertEqual(policies[0].zone_id, "zone-a")
        self.assertEqual(policies[0].destination_prefix, "10.160.0.0/24")
        self.assertEqual(policies[0].preferred_gateway_id, "gw5")
        self.assertEqual(list(policies[0].preferred_gateway_ids), ["gw5", "gw1"])
        self.assertEqual(policies[0].selection_mode, "stable_preferred_gateway_order")

    def test_slot_inventory_can_prepare_handover_and_resident_cross_plane_slots(self) -> None:
        layout = StarlinkShellLayout(
            scenario_name="unit",
            planes=(),
            satellites=(
                StarlinkSatelliteLayout(
                    satellite_id="stl-a",
                    satellite_name="A",
                    norad_id=1,
                    orbit_id="plane-a",
                    plane_index=1,
                    plane_label="A",
                    satellite_index_in_plane=1,
                    inclination_deg=0.0,
                    raan_deg=0.0,
                    mean_anomaly_deg=0.0,
                    mean_motion_rev_per_day=1.0,
                ),
                StarlinkSatelliteLayout(
                    satellite_id="stl-b",
                    satellite_name="B",
                    norad_id=2,
                    orbit_id="plane-b",
                    plane_index=2,
                    plane_label="B",
                    satellite_index_in_plane=1,
                    inclination_deg=0.0,
                    raan_deg=0.0,
                    mean_anomaly_deg=0.0,
                    mean_motion_rev_per_day=1.0,
                ),
                StarlinkSatelliteLayout(
                    satellite_id="stl-c",
                    satellite_name="C",
                    norad_id=3,
                    orbit_id="plane-b",
                    plane_index=2,
                    plane_label="B",
                    satellite_index_in_plane=2,
                    inclination_deg=0.0,
                    raan_deg=0.0,
                    mean_anomaly_deg=0.0,
                    mean_motion_rev_per_day=1.0,
                ),
            ),
            fixed_links=(),
            cross_plane_slot_groups=(
                {
                    "pair_key": "p001p002",
                    "display_label": "P1 <-> P2",
                    "orbit_a_id": "plane-a",
                    "orbit_b_id": "plane-b",
                    "orbit_a_satellite_ids": ["stl-a"],
                    "orbit_b_satellite_ids": ["stl-b", "stl-c"],
                    "candidate_pairs": [
                        {
                            "satellite_a_id": "stl-a",
                            "satellite_b_id": "stl-b",
                            "satellite_a_direction": "right",
                            "satellite_b_direction": "left",
                        },
                        {
                            "satellite_a_id": "stl-a",
                            "satellite_b_id": "stl-c",
                            "satellite_a_direction": "right",
                            "satellite_b_direction": "left",
                        },
                    ],
                },
            ),
        )

        inventory = build_starlink_shell_slot_inventory(
            layout,
            ut_definitions=[
                {"ut_id": "ut1", "ut_index": 1, "region": "r1"},
            ],
            gateway_definitions=[
                {"gateway_id": "gw1", "gateway_index": 1, "region": "r1"},
            ],
            ut_slots_per_terminal=2,
            gateway_slots_per_gateway=2,
            cross_plane_slot_model="candidate_pair",
        )

        self.assertEqual(len(inventory["ut_access"]), 2)
        self.assertEqual(len(inventory["gateway_access"]), 2)
        self.assertEqual(len(inventory["cross_plane_isl"]), 2)
        self.assertEqual(
            [slot["slot_index"] for slot in inventory["ut_access"]],
            [0, 1],
        )
        self.assertTrue(
            all(
                len(slot["candidate_pairs"]) == 1
                and slot["cross_plane_slot_model"] == "candidate_pair_resident"
                for slot in inventory["cross_plane_isl"]
            )
        )
        self.assertEqual(
            len(
                {
                    slot["endpoint_a_template"]["interface_name"]
                    for slot in inventory["cross_plane_isl"]
                }
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
