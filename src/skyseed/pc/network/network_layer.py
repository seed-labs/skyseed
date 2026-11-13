"""Own shared queries, adopted routing and base/delivery plan histories."""
from bisect import bisect_right
from datetime import timedelta
from heapq import heappop, heappush
from types import MappingProxyType

from ..physical.physical_results import utc_time
from .forwarding_rules import indexed_rules, frozen_rules
from .route_plan import RoutePlan
from .route_plan_model import PeriodicRoutePlanModel
from .routing_model import GroundStationZoneRoutingModel
from .service_assignment_model import HighestElevationServiceAssignmentModel


class _LinkPlanReader:
    """Sweep ascending queries once; rewind only for an actual earlier query."""
    def __init__(self, plans):
        self.plans = plans
        self.starts = [plan.start_time for plan in plans]
        self.timestamp = None

    def state_at(self, timestamp):
        if timestamp == self.timestamp:
            return self.snapshot
        segment = bisect_right(self.starts, timestamp) - 1
        if segment < 0 or timestamp >= self.plans[segment].end_time:
            raise ValueError("link query lies outside prepared base coverage")
        if self.timestamp is None or timestamp < self.timestamp or segment != self.segment:
            self.segment = segment
            self.index = 0
            self.state = dict(self.plans[segment].initial_links)
        operations = self.plans[segment].operations
        while self.index < len(operations) and operations[self.index].timestamp <= timestamp:
            operations[self.index].apply(self.state)
            self.index += 1
        self.timestamp = timestamp
        self.snapshot = MappingProxyType(dict(self.state))
        return self.snapshot


def _continuous(plans, start, end, initial):
    cursor = start
    previous = indexed_rules(initial)
    for plan in plans:
        if plan.start_time != cursor or indexed_rules(plan.initial_rules) != previous:
            raise ValueError("route plans must have continuous coverage and initial state")
        cursor = plan.end_time
        previous = indexed_rules(plan.end_rules)
    if cursor != end:
        raise ValueError("route plans must cover the requested interval")


class NetworkLayer:
    def __init__(self, link):
        self.link = link
        self.physical = link.physical
        self.scene = link.scene
        self._base_plans = []
        self._delivery_plans = []
        self._base_starts = []
        self._delivery_starts = []
        self._adopted = MappingProxyType({})
        self._link_reader = None
        self._service_timestamp = None
        self._future_operations = []
        self._delivery_state = None
        self._sequence = 0
        self._last_delivery_at = {}
        self.precomputed_routing = True
        self.set_routing_hook(GroundStationZoneRoutingModel)
        self.set_service_assignment_model(HighestElevationServiceAssignmentModel)
        self.set_plan_model(PeriodicRoutePlanModel)
        self.set_delivery_scheduling_hook(None)

    def _model(self, model_class, options, method, *, optional=False):
        self._configurable()
        if model_class is None and optional:
            return None
        if not isinstance(model_class, type):
            raise TypeError("register a model class")
        model = model_class(**({} if options is None else options))
        if not callable(getattr(model, method, None)):
            raise TypeError(f"{model_class.__name__} must implement {method}")
        model.layer = self
        return model

    def _configurable(self):
        if self._base_plans:
            raise ValueError("configure before generation; use a new layer for another experiment")

    def set_routing_hook(self, model_class, *, model_options=None):
        self.routing_model = self._model(model_class, model_options, "compute_routes", optional=True)

    def set_service_assignment_model(self, model_class, *, model_options=None):
        self.service_assignment_model = self._model(model_class, model_options, "assign_service")
        self._service_timestamp = None

    def set_plan_model(self, model_class, *, model_options=None):
        self.plan_model = self._model(model_class, model_options, "generate_plans")

    def set_delivery_scheduling_hook(self, model_class, *, model_options=None):
        self.delivery_scheduling_model = self._model(model_class, model_options, "schedule_plans", optional=True)

    def set_precomputed_routing(self, enabled):
        self._configurable()
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self.precomputed_routing = enabled

    @property
    def adopted_rules(self):
        return self._adopted

    @property
    def base_plans(self):
        return tuple(self._base_plans)

    @property
    def delivery_plans(self):
        return tuple(self._delivery_plans)

    def adopt_routes(self, rules):
        self._adopted = self._checked_rules(rules)

    def _checked_rules(self, rules):
        tables = indexed_rules(rules)
        for node, table in tables.items():
            self.scene.get_node(node)
            for rule in table.values():
                if rule.destination.kind == "node":
                    self.scene.get_node(rule.destination.identity)
                elif rule.destination.kind == "endpoint":
                    self.scene.get_endpoint(rule.destination.identity)
                if rule.exit is not None:
                    self.scene.get_node(rule.exit.peer)
        return frozen_rules(tables)

    def link_state_at(self, timestamp, link_plans=None):
        timestamp = utc_time(timestamp)
        if link_plans is not None:
            plans = tuple(link_plans)
            if self._link_reader is None or self._link_reader.plans != plans:
                self._link_reader = _LinkPlanReader(plans)
                self._service_timestamp = None
        else:
            reader = self._link_reader
            if (reader is None or not reader.plans
                    or not reader.plans[0].start_time <= timestamp < reader.plans[-1].end_time):
                self._link_reader = _LinkPlanReader(self.link.base_plans)
        return self._link_reader.state_at(timestamp)

    def service_assignments_at(self, timestamp):
        timestamp = utc_time(timestamp)
        if timestamp == self._service_timestamp:
            return self._service_result
        snapshot = self.physical.snapshot_at(timestamp)
        result = dict(self.service_assignment_model.assign_service(self.scene, snapshot))
        for endpoint, satellite in result.items():
            self.scene.get_endpoint(endpoint)
            if satellite is not None and self.scene.get_node(satellite).kind != "satellite":
                raise ValueError("service assignment must name a satellite or None")
        self._service_timestamp = timestamp
        self._service_result = MappingProxyType(result)
        return self._service_result

    def compute_routes(self, timestamp, link_plans):
        timestamp = utc_time(timestamp)
        self.link_state_at(timestamp, link_plans)
        if self.routing_model is None:
            return MappingProxyType({})
        return self._checked_rules(self.routing_model.compute_routes(self.scene, timestamp, link_plans))

    def _read(self, plans, starts, start, end):
        index = bisect_right(starts, start) - 1
        if index < 0 or start >= end or end > plans[-1].end_time:
            raise ValueError("route interval is outside retained coverage")
        result = []
        while index < len(plans) and plans[index].start_time < end:
            plan = plans[index]
            result.append(plan.slice(max(start, plan.start_time), min(end, plan.end_time)))
            index += 1
        return tuple(result)

    def base_plans_between(self, start_time, end_time):
        return self._read(self._base_plans, self._base_starts, utc_time(start_time), utc_time(end_time))

    def delivery_plans_between(self, start_time, end_time):
        start, end = utc_time(start_time), utc_time(end_time)
        index = bisect_right(self._delivery_starts, start) - 1
        if index < 0 or start >= end or end > self._delivery_plans[-1].end_time:
            raise ValueError("route interval is outside retained coverage")
        result = []
        while start < end:
            plan = self._delivery_plans[index]
            stop = end
            if index + 1 < len(self._delivery_plans):
                stop = min(stop, self._delivery_starts[index + 1])
            selected = plan.slice(start, stop)
            # A time query returns complete state evolution, not a new batch.
            result.append(RoutePlan(start, stop, selected.initial_rules,
                                    tuple(selected.iter_state_operations())))
            start = stop
            index += 1
        return tuple(result)

    def delivery_state_at(self, timestamp):
        """Query the latest combined delivery history, including known tails."""
        timestamp = utc_time(timestamp)
        index = bisect_right(self._delivery_starts, timestamp) - 1
        if index < 0:
            raise ValueError("route query is outside retained coverage")
        return self._delivery_plans[index].state_at(timestamp)

    def _schedule(self, base):
        if self.delivery_scheduling_model is None:
            scheduled = base
        else:
            scheduled = tuple(self.delivery_scheduling_model.schedule_plans(self.scene, base))
        if not scheduled:
            raise ValueError("delivery scheduling must preserve the generated forwarding updates")
        _continuous(scheduled, scheduled[0].start_time, scheduled[-1].end_time, base[0].initial_rules)
        expected, actual = {}, {}
        for plan in base:
            for operation in plan.operations:
                expected.setdefault(operation.node_id, []).append((operation.kind, operation.rule))
        operations = []
        for plan in scheduled:
            if plan.context_operations:
                raise ValueError("delivery models return new updates; the layer supplies prior context")
            for operation in plan.operations:
                actual.setdefault(operation.node_id, []).append((operation.kind, operation.rule))
        if actual != expected:
            raise ValueError("delivery scheduling must preserve each node's forwarding content and order")
        for plan in scheduled:
            for operation in plan.operations:
                previous = self._last_delivery_at.get(operation.node_id, base[0].start_time)
                if operation.timestamp < max(previous, base[0].start_time):
                    raise ValueError("delivery update precedes retained history or an earlier node update")
                self._last_delivery_at[operation.node_id] = operation.timestamp
                operations.append(operation)
                heappush(self._future_operations, (operation.timestamp, self._sequence, operation))
                self._sequence += 1
        return tuple(operations)

    def _delivery_batch(self, base):
        start, end = base[0].start_time, base[-1].end_time
        if self._delivery_state is None:
            self._delivery_state = indexed_rules(base[0].initial_rules)
        while self._future_operations and self._future_operations[0][0] < start:
            heappop(self._future_operations)[2].apply(self._delivery_state)
        context = tuple(item[2] for item in sorted(self._future_operations))
        operations = self._schedule(base)
        for sequence in (context, operations):
            if sequence:
                end = max(end, sequence[-1].timestamp + timedelta(microseconds=1))
        plan = RoutePlan(start, end, frozen_rules(self._delivery_state), operations, context)
        self._delivery_starts.append(start)
        self._delivery_plans.append(plan)
        return plan

    def generate_plans(self, start_time, end_time, *, link_lookahead=timedelta(0)):
        if not self.precomputed_routing or self.routing_model is None:
            return ()
        start, end = utc_time(start_time), utc_time(end_time)
        if start >= end or not isinstance(link_lookahead, timedelta) or link_lookahead < timedelta(0):
            raise ValueError("use a nonempty route interval and nonnegative timedelta lookahead")
        frontier = start
        if self._base_plans:
            frontier = self._base_plans[-1].end_time
            if start < self._base_starts[0] or start > frontier:
                raise ValueError("new route generation must continue existing coverage")
            if end <= frontier:
                return self.delivery_plans_between(start, end)
        prepared = self.link.generate_base_plan(frontier, end + link_lookahead)
        link_plans = (prepared,)
        self._link_reader = _LinkPlanReader(link_plans)
        initial = self._adopted
        base = tuple(self.plan_model.generate_plans(self.scene, frontier, end, link_plans))
        _continuous(base, frontier, end, initial)
        for plan in base:
            if plan.context_operations:
                raise ValueError("base route generation must not include delivery context")
            self._checked_rules(plan.initial_rules)
            for operation in plan.operations:
                self._checked_rules({operation.node_id: (operation.rule,)})
        self.adopt_routes(base[-1].end_rules)
        self._base_plans.extend(base)
        self._base_starts.extend(plan.start_time for plan in base)
        batch = self._delivery_batch(base)
        prefix = self.delivery_plans_between(start, frontier) if start < frontier else ()
        return (*prefix, batch)
