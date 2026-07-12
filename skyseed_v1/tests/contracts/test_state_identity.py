from __future__ import annotations

import unittest

from skyseed_v1.runtime.state_identity import (
    host_state_ids_equivalent,
    parse_host_state_identity,
    runtime_epoch_identity_matches,
)


class RuntimeStateIdentityTests(unittest.TestCase):
    def test_exact_and_frozen_live_aliases_match(self) -> None:
        live = "live_state:snapshot-a:epoch-7"
        frozen = "frozen_prepare_host_state:snapshot-a:epoch-7"

        self.assertEqual(parse_host_state_identity(live), ("snapshot-a", "epoch-7"))
        self.assertTrue(host_state_ids_equivalent(live, live))
        self.assertTrue(host_state_ids_equivalent(live, frozen))

    def test_stale_or_unknown_state_ids_fail_closed(self) -> None:
        live = "live_state:snapshot-a:epoch-7"

        self.assertFalse(
            host_state_ids_equivalent(
                live,
                "frozen_prepare_host_state:snapshot-b:epoch-7",
            )
        )
        self.assertFalse(
            host_state_ids_equivalent(
                live,
                "frozen_prepare_host_state:snapshot-a:epoch-8",
            )
        )
        self.assertFalse(host_state_ids_equivalent(live, "unknown:snapshot-a:epoch-7"))

    def test_runtime_epoch_identity_requires_matching_present_fields(self) -> None:
        record = {
            "plan_id": "plan-a",
            "slice_id": "slice-7",
            "snapshot_id": "snapshot-a",
        }

        self.assertTrue(runtime_epoch_identity_matches(record_intent=record, target_intent=record))
        self.assertFalse(
            runtime_epoch_identity_matches(
                record_intent=record,
                target_intent={**record, "snapshot_id": "snapshot-b"},
            )
        )
        self.assertFalse(runtime_epoch_identity_matches(record_intent={}, target_intent={}))


if __name__ == "__main__":
    unittest.main()
