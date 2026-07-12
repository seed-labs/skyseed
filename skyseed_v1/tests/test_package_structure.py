from __future__ import annotations

import unittest
from pathlib import Path

import skyseed_v1


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackageStructureTests(unittest.TestCase):
    def test_package_identity_is_importable(self) -> None:
        self.assertEqual(skyseed_v1.__version__, "1.0.0.dev0")
        package_file = Path(skyseed_v1.__file__).resolve()
        self.assertTrue(package_file.is_relative_to((PROJECT_ROOT / "src").resolve()))
        self.assertEqual(package_file.parent.name, "skyseed_v1")

    def test_governing_documents_exist(self) -> None:
        for relative_path in (
            "ARCHITECTURE_EN.md",
            "ARCHITECTURE_ZH.md",
            "MIGRATION_EN.md",
            "MIGRATION_ZH.md",
            "docs/migration/baseline_EN.md",
            "docs/migration/baseline_ZH.md",
            "docs/migration/validation_matrix_EN.md",
            "docs/migration/validation_matrix_ZH.md",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_generated_output_is_not_a_source_package(self) -> None:
        self.assertFalse((PROJECT_ROOT / "src" / "skyseed_v1" / "output").exists())

    def test_human_documents_have_matching_language_pairs(self) -> None:
        markdown = {
            path.relative_to(PROJECT_ROOT)
            for path in PROJECT_ROOT.rglob("*.md")
            if "output" not in path.relative_to(PROJECT_ROOT).parts
        }
        unsuffixed = {
            path for path in markdown if not path.stem.endswith(("_EN", "_ZH"))
        }
        english = {
            Path(str(path)[: -len("_EN.md")])
            for path in markdown
            if path.name.endswith("_EN.md")
        }
        chinese = {
            Path(str(path)[: -len("_ZH.md")])
            for path in markdown
            if path.name.endswith("_ZH.md")
        }

        self.assertEqual(unsuffixed, set())
        self.assertEqual(english, chinese)


if __name__ == "__main__":
    unittest.main()
