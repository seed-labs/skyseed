from __future__ import annotations

import ast
import unittest
from pathlib import Path

from skyseed_v1.adapters.seedemu import integration
from skyseed_v1.operations.paths import SEED_EMULATOR_DEPENDENCY_ROOT
import seedemu


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "skyseed_v1"


class ModuleBoundaryTests(unittest.TestCase):
    def test_source_has_no_migrated_flat_imports(self) -> None:
        forbidden = {
            "contracts",
            "full_s4_replay_equivalence",
            "linux_mpls_runtime",
            "return_path_audit",
            "runtime_execution_trace",
            "runtime_operation_metrics",
            "runtime_queue_coverage_audit",
            "seed_integration",
            "skyseed_preflight",
            "temporal_run_inspector",
        }
        violations = []
        for path in SOURCE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = (
                    node.module
                    if isinstance(node, ast.ImportFrom) and node.level == 0
                    else None
                )
                if module and module.split(".")[0] in forbidden:
                    violations.append(f"{path.relative_to(SOURCE_ROOT)}:{module}")
        self.assertEqual(violations, [])

    def test_seed_constants_are_owned_by_the_adapter(self) -> None:
        self.assertEqual(integration.SATELLITE_ASN, 250)
        self.assertTrue(integration.MINI_INTERNET_STUB_AS_EXCHANGES)
        self.assertEqual(integration.__name__, "skyseed_v1.adapters.seedemu.integration")
        selected = (SEED_EMULATOR_DEPENDENCY_ROOT / "seedemu").resolve()
        loaded = Path(next(iter(seedemu.__path__))).resolve()
        if selected.exists():
            self.assertEqual(loaded, selected)
        self.assertNotEqual(loaded, Path("/home/seed/seed-emulator/seedemu"))

    def test_preview_front_end_has_no_package_path_as_runtime_attribute(self) -> None:
        path = SOURCE_ROOT / "operations" / "preview.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        invalid_attributes = []
        invalid_flags = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id == "skyseed_v1":
                    invalid_attributes.append(f"line {node.lineno}: {ast.unparse(node)}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("--skyseed_v1.")
            ):
                invalid_flags.append(f"line {node.lineno}: {node.value}")
        self.assertEqual(invalid_attributes, [])
        self.assertEqual(invalid_flags, [])
        source = path.read_text(encoding="utf-8")
        self.assertIn("args.orbit_data", source)
        self.assertIn("planner = UtInternetExperimentPlanner()", source)


if __name__ == "__main__":
    unittest.main()
