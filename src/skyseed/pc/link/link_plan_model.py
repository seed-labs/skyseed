"""Plan-generation contract and a continuous five-second reference generator."""
from dataclasses import replace
from datetime import datetime, timedelta
from heapq import heappop, heappush
from math import isfinite
from typing import TYPE_CHECKING, Protocol

from skyseed.scene import Scene
from .link_values import LinkConfiguration, LinkEvaluation
from .link_plan import LinkOperation, LinkPlan

if TYPE_CHECKING:
    from .link_layer import LinkLayer


class LinkPlanModel(Protocol):
    layer: "LinkLayer"  # Bound by the layer after construction.

    def generate_plan(self, scene: Scene, start_time: datetime, end_time: datetime) -> LinkPlan: ...


class PeriodicLinkPlanModel:
    def __init__(self, *, interval=timedelta(seconds=5),
                 isl_setup_duration=timedelta(seconds=5),
                 ground_setup_duration=timedelta(seconds=5),
                 delay_relative_threshold=0.10,
                 max_delay_hold=timedelta(seconds=120), observer=None):
        if not isinstance(interval, timedelta) or interval <= timedelta(0):
            raise ValueError("interval must be a positive timedelta")
        for duration in (isl_setup_duration, ground_setup_duration):
            if not isinstance(duration, timedelta) or duration < timedelta(0):
                raise ValueError("setup duration must be a nonnegative timedelta")
        if not isfinite(delay_relative_threshold) or delay_relative_threshold < 0:
            raise ValueError("delay_relative_threshold must be finite and nonnegative")
        if max_delay_hold is not None and (
            not isinstance(max_delay_hold, timedelta) or max_delay_hold <= timedelta(0)
        ):
            raise ValueError("max_delay_hold must be positive or None")
        self.interval = interval
        self.isl_setup_duration = isl_setup_duration
        self.ground_setup_duration = ground_setup_duration
        self.delay_relative_threshold = delay_relative_threshold
        self.max_delay_hold = max_delay_hold
        self.observer = observer
        self._next_evaluation = None
        self._pending = {}
        self._ready = []
        self._delay_adopted_at = {}

    def _updated(self, previous, fresh, timestamp):
        directions = {}
        changed = previous.candidate.reports.keys() != fresh.candidate.reports.keys()
        for source in fresh.candidate.reports:
            value = fresh.properties_from(source)
            before = previous.properties_from(source)
            clock = (fresh.id, source)
            if before is None:
                self._delay_adopted_at[clock] = timestamp
            elif value.delay_s != before.delay_s:
                difference = abs(value.delay_s - before.delay_s)
                threshold = abs(before.delay_s) * self.delay_relative_threshold
                expired = (self.max_delay_hold is not None
                           and timestamp - self._delay_adopted_at[clock] >= self.max_delay_hold)
                if difference >= threshold or expired:
                    self._delay_adopted_at[clock] = timestamp
                else:
                    value = replace(value, delay_s=before.delay_s)
            directions[source] = value
            changed |= value != before
        if not changed:
            return None
        first, second = fresh.candidate.endpoints
        properties = directions.get(first, directions.get(second))
        reverse = directions.get(second)
        if reverse == properties:
            reverse = None
        return LinkConfiguration(fresh.candidate, properties, reverse)

    def _next_ready(self):
        while self._ready:
            timestamp, identity = self._ready[0]
            if self._pending.get(identity) == timestamp:
                return timestamp
            heappop(self._ready)
        return None

    def generate_plan(self, scene, start_time, end_time):
        previous = self.layer.previous_base_plan
        initial = {} if previous is None else previous.end_links
        active = dict(initial)
        operations = []
        if self._next_evaluation is None:
            self._next_evaluation = start_time

        while True:
            ready = self._next_ready()
            timestamp = self._next_evaluation
            if ready is not None:
                timestamp = min(timestamp, ready)
            if timestamp >= end_time:
                break
            evaluated = timestamp == self._next_evaluation
            if evaluated:
                candidates = self.layer.candidates_at(timestamp)
                configurations = self.layer.evaluate_connections(timestamp, candidates)
                selected = {link.id: link for link in configurations}
                old_selection = self.layer.selected_links
                for identity in sorted(old_selection.keys() - selected.keys()):
                    self._pending.pop(identity, None)
                    if identity in active:
                        del active[identity]
                        operations.append(LinkOperation(timestamp, "withdraw", identity))
                    for source in identity:
                        self._delay_adopted_at.pop((identity, source), None)
                for identity, configuration in selected.items():
                    if identity not in old_selection:
                        satellite_pair = all(scene.get_node(node).kind == "satellite" for node in identity)
                        duration = self.isl_setup_duration if satellite_pair else self.ground_setup_duration
                        completion = timestamp + duration
                        self._pending[identity] = completion
                        heappush(self._ready, (completion, identity))
                    elif identity in active:
                        update = self._updated(active[identity], configuration, timestamp)
                        if update is not None:
                            active[identity] = update
                            operations.append(LinkOperation(timestamp, "update", identity, update))
                self.layer.adopt_selection(configurations)
                self._next_evaluation += self.interval

            # Selection at a coincident sample wins: a withdrawn pending link
            # is never briefly established at that same instant.
            while self._next_ready() == timestamp:
                _, identity = heappop(self._ready)
                del self._pending[identity]
                configuration = self.layer.selected_links[identity]
                active[identity] = configuration
                for source in configuration.candidate.reports:
                    self._delay_adopted_at[identity, source] = timestamp
                operations.append(LinkOperation(timestamp, "establish", identity, configuration))
            if evaluated and self.observer is not None:
                self.observer(LinkEvaluation(timestamp, candidates, self.layer.selected_links, self._pending))
        return LinkPlan(start_time, end_time, initial, tuple(operations))
