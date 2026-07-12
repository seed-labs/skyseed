from __future__ import annotations

import unittest

from skyseed_v1.backends.bpf.canary import DEFAULT_SOURCE
from skyseed_v1.backends.bpf.live import DEFAULT_MPLS_SOURCE


class BpfSourceOwnershipTests(unittest.TestCase):
    def test_active_native_sources_follow_backend_owner(self) -> None:
        for source in (DEFAULT_SOURCE, DEFAULT_MPLS_SOURCE):
            with self.subTest(source=source.name):
                self.assertTrue(source.is_file())
                self.assertEqual(source.parent.name, "native")
                self.assertEqual(source.parent.parent.name, "bpf")


if __name__ == "__main__":
    unittest.main()
