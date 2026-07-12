from __future__ import annotations

import unittest

from skyseed_v1.core.interfaces import (
    OrbitTopologyProducer,
    Planner,
    TopologySlicer,
    VisibilityModel,
    ZoneGenerator,
)
from skyseed_v1.core.ir import (
    OrbitTopologySlice,
    VisibilitySlice,
    ZoneGenerationInput,
    ZoneProjection,
)


class TopologyInterfaceTests(unittest.TestCase):
    def test_interfaces_remain_abstract(self) -> None:
        for interface in (
            OrbitTopologyProducer,
            Planner,
            VisibilityModel,
            ZoneGenerator,
            TopologySlicer,
        ):
            with self.subTest(interface=interface.__name__):
                self.assertTrue(interface.__abstractmethods__)

    def test_topology_pipeline_ir_is_frozen(self) -> None:
        for ir_type in (
            ZoneGenerationInput,
            OrbitTopologySlice,
            VisibilitySlice,
            ZoneProjection,
        ):
            with self.subTest(ir_type=ir_type.__name__):
                self.assertTrue(ir_type.__dataclass_params__.frozen)


if __name__ == "__main__":
    unittest.main()
