from __future__ import annotations

import ast
import inspect
import textwrap
import unittest

from skyseed_v1.runtime.profile import run_profile


class ProfileFinalizationTests(unittest.TestCase):
    def test_normal_path_blockers_are_computed_before_they_are_read(self) -> None:
        tree = ast.parse(textwrap.dedent(inspect.getsource(run_profile)))
        stores: list[int] = []
        loads: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id != "normal_path_blockers":
                continue
            if isinstance(node.ctx, ast.Store):
                stores.append(node.lineno)
            elif isinstance(node.ctx, ast.Load):
                loads.append(node.lineno)

        self.assertTrue(stores, "normal-path blockers must be evaluated")
        self.assertTrue(loads, "normal-path blockers must affect final acceptance")
        self.assertLess(min(stores), min(loads))
        self.assertTrue(all(line > min(stores) for line in loads))


if __name__ == "__main__":
    unittest.main()
