from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ProjectIndependenceTests(unittest.TestCase):
    def test_active_python_has_no_legacy_satellite_import_or_path(self) -> None:
        violations: list[str] = []
        for owner in (PROJECT_ROOT / "src", PROJECT_ROOT / "examples"):
            for path in owner.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == "satellite" or alias.name.startswith("satellite."):
                                violations.append(f"{path}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        module = str(node.module or "")
                        if module == "satellite" or module.startswith("satellite."):
                            violations.append(f"{path}: from {module}")
                    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                        value = node.value.replace("\\", "/")
                        if "satellite/" in value:
                            violations.append(f"{path}:{node.lineno}: {node.value}")
        self.assertEqual(violations, [])

    def test_project_paths_never_select_a_parent_satellite_tree(self) -> None:
        from skyseed_v1.operations import paths

        owned = (
            paths.PROJECT_RUNTIME_ROOT,
            paths.PROJECT_CACHE_ROOT,
            paths.PROJECT_OUTPUT_ROOT,
        )
        for path in owned:
            self.assertTrue(path.resolve().is_relative_to(paths.PROJECT_ROOT.resolve()))


if __name__ == "__main__":
    unittest.main()
