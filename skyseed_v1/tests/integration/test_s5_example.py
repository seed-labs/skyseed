from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skyseed_v1.runtime.profile import (
    ProfileContractError,
    list_profiles,
    load_profile,
    validate_canonical_profile_consistency,
    run_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "s5_operational_clean"


class S5OperationalCleanExampleTests(unittest.TestCase):
    def test_examples_have_no_second_scenario_or_source_profile(self) -> None:
        for example in ("s4_operational_clean", "s5_operational_clean"):
            root = PROJECT_ROOT / "examples" / example
            self.assertFalse((root / "scenario.py").exists())
            self.assertFalse((root / "profile.json").exists())

    def test_only_canonical_example_profiles_are_discoverable(self) -> None:
        self.assertEqual(
            list_profiles(),
            ["rich_s4_operational_clean_bpf", "rich_s5_operational_clean_bpf"],
        )
        with self.assertRaises(ProfileContractError):
            load_profile("retired_or_legacy_profile")

    def test_profile_is_self_contained_with_explicit_shell_capacity(self) -> None:
        profile = load_profile("rich_s5_operational_clean_bpf")
        args = dict(profile["runner_args"])

        self.assertEqual(profile["planned_shell"], "S5")
        self.assertEqual(args["planned_shell_id"], "S5")
        self.assertEqual(args["max_satellites"], 172)
        self.assertEqual(args["horizon_seconds"], 240)
        self.assertEqual(args["sample_interval"], 10)
        self.assertEqual(args["loop_count"], 4)
        self.assertEqual(args["forwarding_backend"], "bpf_owned")
        self.assertEqual(args["max_uts"], 9)
        self.assertEqual(args["max_gateways"], 5)
        self.assertIn("s5_operational_clean_ut_catalog_9.json", args["ut_catalog"])
        self.assertEqual(
            profile["docker_dummy_network_capacity"]["required_dummy_networks"],
            605,
        )
        self.assertEqual(
            profile["startup_materialization"]["expected_containers"],
            195,
        )
        self.assertEqual(validate_canonical_profile_consistency(profile), [])

    def test_duplicate_profile_fields_are_assertions_not_second_authorities(self) -> None:
        profile = load_profile("rich_s5_operational_clean_bpf")
        profile["runner_args"] = {
            **dict(profile["runner_args"]),
            "planned_shell_id": "S4",
        }

        self.assertIn(
            "planned_shell:'S5'!='S4'",
            validate_canonical_profile_consistency(profile),
        )

    def test_direct_file_inspection_is_ready(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(EXAMPLE_ROOT / "run.py"), "inspect"],
            cwd=str(PROJECT_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["constellation_shells"], ["S5"])
        self.assertEqual(payload["satellite_count"], 172)
        self.assertEqual(payload["selected_ut_count"], 9)
        self.assertEqual(payload["selected_gateway_count"], 5)
        self.assertEqual(payload["selected_zone_count"], 6)
        self.assertEqual(payload["horizon_seconds"], 240)
        self.assertEqual(payload["sample_interval_seconds"], 10)
        self.assertEqual(payload["validation_issues"], [])

    def test_dry_run_writes_effective_profile_beside_example_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = run_profile(
                profile_name="rich_s5_operational_clean_bpf",
                run_root=Path(temporary),
                run_id="effective-profile-contract",
                dry_run=True,
                progress_mode="off",
            )
            effective = Path(summary["run_dir"]) / "effective_profile.json"

            self.assertTrue(effective.is_file())
            payload = json.loads(effective.read_text(encoding="utf-8"))
            self.assertEqual(payload["runner_args"]["planned_shell_id"], "S5")
            self.assertNotIn("_profile_path", payload)


if __name__ == "__main__":
    unittest.main()
