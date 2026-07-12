from __future__ import annotations

import hashlib
import json
import unittest

from skyseed_v1.core.ir import (
    NetworkMutationPlan,
    RuntimeUpdatePlan,
    TopologySnapshot,
)
from skyseed_v1.core.ir.contracts import build_mock_contract_flow


def canonical_sha256(value: object) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


class V1IntermediateRepresentationTests(unittest.TestCase):
    def test_mock_contract_flow_remains_valid(self) -> None:
        flow = build_mock_contract_flow()

        self.assertTrue(flow["validation_summary"]["all_valid"])
        self.assertEqual(
            [item["contract_type"] for item in flow["validation_summary"]["contracts"]],
            ["TopologySnapshot", "RuntimeUpdatePlan", "NetworkMutationPlan"],
        )

    def test_ir_types_have_stable_frozen_dataclass_behavior(self) -> None:
        for contract_type in (TopologySnapshot, RuntimeUpdatePlan, NetworkMutationPlan):
            with self.subTest(contract_type=contract_type.__name__):
                self.assertTrue(contract_type.__dataclass_params__.frozen)

    def test_mock_contract_flow_fingerprint_is_stable(self) -> None:
        self.assertEqual(
            canonical_sha256(build_mock_contract_flow()),
            "6cf5f6be577b7f528f99d93b029867a9f76a2415c906944557675a9ae23f5f2f",
        )


if __name__ == "__main__":
    unittest.main()
