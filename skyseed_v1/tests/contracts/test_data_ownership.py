from __future__ import annotations

import hashlib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CanonicalDataOwnershipTests(unittest.TestCase):
    def test_moved_s4_inputs_are_byte_identical_to_the_baseline(self) -> None:
        expected = {
            "data/orbits/planned_shell_orbit_data_s1_s5.json": "6494f39207fd916fdda700c5895800a25ea5e998e6978c4ca335753f1eb4be04",
            "data/ground/starlink_gateways.json": "bf578a64691f301a7a614ac3c4e9d996e0f41ea720acbb84e50661c7f83187f1",
            "data/ground/s4_gz_rich_validation_ut_catalog_32.json": "fcb655d14fea8dfc4a72ec4966166e950c6ae05920b1ddce92a5037436ab6381",
            "data/ground/s4_gz_rich_validation_ut_gateway_policy_32.json": "7dad88a8d1b75b1a2708ef76bdb916eaa0e0ad390c297217ad67e88360a7f648",
            "data/ground/s4_gz_rich_validation_zone_catalog_16.json": "c91a7f186074fdaf661110dc71e02bd812cb39d31fa69687cd720f3161504ecd",
            "data/ground/s4_visualization_gateway_binding_12.json": "8b92b09a73ea412c9c8daf05ee758c722a07e162c9cfc0df4477c796f71b1d3a",
        }
        for relative, digest in expected.items():
            with self.subTest(path=relative):
                payload = (PROJECT_ROOT / relative).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_canonical_data_is_owned_inside_the_project(self) -> None:
        for relative in (
            "data/orbits/planned_shell_orbit_data_s1_s5.json",
            "data/ground/starlink_gateways.json",
        ):
            path = (PROJECT_ROOT / relative).resolve()
            self.assertTrue(path.is_file(), msg=relative)
            self.assertTrue(path.is_relative_to(PROJECT_ROOT.resolve()), msg=relative)


if __name__ == "__main__":
    unittest.main()
