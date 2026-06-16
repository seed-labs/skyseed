#!/usr/bin/env python3
# encoding: utf-8

import unittest

from experiment_topology import ExperimentTerminalProfile
from starlink_shell_topology import _zone_return_prefixes_from_ut_slots


class StarlinkShellTopologyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
