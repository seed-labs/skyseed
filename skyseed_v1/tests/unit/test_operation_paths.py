from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from skyseed_v1.operations.paths import (
    SEED_EMULATOR_DEPENDENCY_ROOT,
    SOURCE_ROOT,
    prepare_user_entry_environment,
)


class OperationPathTests(unittest.TestCase):
    def test_user_entry_removes_only_retired_seed_override(self) -> None:
        with patch.dict(
            os.environ,
            {"PYTHONPATH": "/home/seed/seed-emulator:/tmp/user-library"},
            clear=False,
        ):
            removed = prepare_user_entry_environment()
            entries = os.environ["PYTHONPATH"].split(os.pathsep)

        self.assertEqual(removed, ("/home/seed/seed-emulator",))
        self.assertEqual(entries[:2], [str(SOURCE_ROOT), str(SEED_EMULATOR_DEPENDENCY_ROOT)])
        self.assertIn("/tmp/user-library", entries)
        self.assertNotIn("/home/seed/seed-emulator", entries)


if __name__ == "__main__":
    unittest.main()
