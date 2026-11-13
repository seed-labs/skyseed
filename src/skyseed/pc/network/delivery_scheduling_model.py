"""Optional scheduling interface and a small fixed per-node delay model."""
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, Sequence

from skyseed.scene import Scene
from .route_plan import RoutePlan

if TYPE_CHECKING:
    from .network_layer import NetworkLayer


class DeliverySchedulingModel(Protocol):
    layer: "NetworkLayer"

    def schedule_plans(self, scene: Scene, route_plans: Sequence[RoutePlan]) -> Sequence[RoutePlan]: ...


class FixedDelayDeliverySchedulingModel:
    """Illustrative delivery conditions; no protocol or transport simulation."""
    def __init__(self, *, node_delays=None):
        self.node_delays = {} if node_delays is None else dict(node_delays)
        for delay in self.node_delays.values():
            if not isinstance(delay, timedelta) or delay < timedelta(0):
                raise ValueError("node delivery delay must be a nonnegative timedelta")

    def schedule_plans(self, scene, route_plans):
        operations = []
        for plan in route_plans:
            for operation in plan.operations:
                delay = self.node_delays.get(operation.node_id, timedelta(0))
                operations.append(replace(operation, timestamp=operation.timestamp + delay))
        operations.sort(key=lambda operation: operation.timestamp)
        end = route_plans[-1].end_time
        if operations:
            end = max(end, operations[-1].timestamp + timedelta(microseconds=1))
        return (RoutePlan(route_plans[0].start_time, end, route_plans[0].initial_rules,
                          tuple(operations)),)
