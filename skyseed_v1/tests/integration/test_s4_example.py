from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from skyseed_v1.runtime.profile import load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "s4_operational_clean"


class S4OperationalCleanExampleTests(unittest.TestCase):
    def test_python_composition_resolves_validated_s4_profile(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf")
        args = dict(profile["runner_args"])

        self.assertEqual(profile["planned_shell"], "S4")
        self.assertEqual(args["max_satellites"], 348)
        self.assertEqual(args["horizon_seconds"], 240)
        self.assertEqual(args["sample_interval"], 10)
        self.assertEqual(args["loop_count"], 4)
        self.assertEqual(profile["_profile_source_kind"], "python_experiment_composition")

    def test_direct_run_file_supports_cheap_inspection(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(EXAMPLE_ROOT / "run.py"), "inspect"],
            cwd=str(PROJECT_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["constellation_shells"], ["S4"])
        self.assertEqual(payload["validation_issues"], [])


if __name__ == "__main__":
    unittest.main()
