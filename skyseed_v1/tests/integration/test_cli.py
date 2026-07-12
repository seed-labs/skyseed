from __future__ import annotations

import ast
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from skyseed_v1.cli import main as cli


REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_ROOT = REPO_ROOT / "skyseed_v1" / "src" / "skyseed_v1" / "cli"


class CliTests(unittest.TestCase):
    def test_list_examples_exposes_the_canonical_s4_scenario(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main(["list-examples"])

        self.assertEqual(result, 0)
        self.assertEqual(
            output.getvalue(),
            "s4_operational_clean\ns5_operational_clean\n",
        )

    def test_run_only_maps_user_options_to_the_profile_runner(self) -> None:
        with patch("skyseed_v1.runtime.profile.main", return_value=0) as runner:
            result = cli.main(
                [
                    "run",
                    "--example",
                    "s4_operational_clean",
                    "--run-root",
                    "/tmp/cli-contract",
                    "--run-id",
                    "contract-run",
                    "--dry-run",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(result, 0)
        runner.assert_called_once_with(
            [
                "--profile",
                "rich_s4_operational_clean_bpf",
                "--run-root",
                "/tmp/cli-contract",
                "--format",
                "json",
                "--run-id",
                "contract-run",
                "--dry-run",
            ]
        )

    def test_cli_modules_do_not_import_platform_implementation_layers(self) -> None:
        forbidden = {
            "skyseed_v1.backends",
            "skyseed_v1.compilers",
            "skyseed_v1.planning",
            "skyseed_v1.topology",
        }
        for path in sorted(CLI_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imports.update(
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            )
            violations = sorted(
                name
                for name in imports
                if any(name == owner or name.startswith(owner + ".") for owner in forbidden)
            )
            self.assertEqual(violations, [], msg=f"{path.name}: {violations}")

    def test_legacy_executable_paths_are_retired(self) -> None:
        retired = (
            REPO_ROOT / "satellite" / "apps" / "run_starlink_shell_experiment.py",
            REPO_ROOT / "satellite" / "apps" / "run_skyseed_validation_profile.py",
        )
        for path in retired:
            self.assertFalse(path.exists(), msg=str(path))


if __name__ == "__main__":
    unittest.main()
