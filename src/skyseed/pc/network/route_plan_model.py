"""Route generation interface and a continuous periodic implementation."""
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, Sequence

from skyseed.scene import Scene
from ..link import LinkPlan
from .forwarding_rules import indexed_rules
from .route_plan import RoutePlan, RouteOperation

if TYPE_CHECKING:
    from .network_layer import NetworkLayer


class RoutePlanModel(Protocol):
    layer: "NetworkLayer"

    def generate_plans(self, scene: Scene, start_time: datetime, end_time: datetime,
                       link_plans: Sequence[LinkPlan]) -> Sequence[RoutePlan]: ...


class PeriodicRoutePlanModel:
    def __init__(self, *, interval=timedelta(seconds=5)):
        if not isinstance(interval, timedelta) or interval <= timedelta(0):
            raise ValueError("route interval must be a positive timedelta")
        self.interval = interval
        self._next_evaluation = None

    def generate_plans(self, scene, start_time, end_time, link_plans):
        initial = self.layer.adopted_rules
        previous = indexed_rules(initial)
        operations = []
        if self._next_evaluation is None:
            self._next_evaluation = start_time
        while self._next_evaluation < end_time:
            timestamp = self._next_evaluation
            rules = self.layer.compute_routes(timestamp, link_plans)
            current = indexed_rules(rules)
            for node in sorted(previous.keys() | current.keys()):
                old, new = previous.get(node, {}), current.get(node, {})
                for key in sorted(old.keys() - new.keys()):
                    operations.append(RouteOperation(timestamp, node, "withdraw", old[key]))
                for key in sorted(new):
                    if key not in old:
                        operations.append(RouteOperation(timestamp, node, "install", new[key]))
                    elif old[key] != new[key]:
                        operations.append(RouteOperation(timestamp, node, "replace", new[key]))
            previous = current
            self.layer.adopt_routes(rules)
            self._next_evaluation += self.interval
        return (RoutePlan(start_time, end_time, initial, tuple(operations)),)
