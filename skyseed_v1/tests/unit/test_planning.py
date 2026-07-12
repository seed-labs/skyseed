from __future__ import annotations

import unittest

from skyseed_v1.core.ir.contracts import build_mock_topology_snapshot
from skyseed_v1.planning.routing import DefaultPlannerSkeleton


class PlanningTests(unittest.TestCase):
    def test_default_planner_preserves_valid_contract_flow(self) -> None:
        plan = DefaultPlannerSkeleton().plan(build_mock_topology_snapshot())

        self.assertEqual(plan.plan_id, "rtplan-c1ed91e16d997154")
        self.assertTrue(plan.validation_result().is_valid)
        self.assertEqual(len(plan.node_subplans), 3)


if __name__ == "__main__":
    unittest.main()
