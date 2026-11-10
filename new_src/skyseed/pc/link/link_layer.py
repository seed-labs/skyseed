"""Physical handoff, model calls and retained base/execution link plans."""
from bisect import bisect_right
from math import dist, isfinite
from types import MappingProxyType

from ..physical.earth_geometry import LIGHT_SPEED_M_S
from ..physical.physical_results import utc_time
from .link_values import ConnectionCandidate, LinkProperties, LinkConfiguration
from .link_plan import LinkPlan
from .link_selection_model import RetainFirstLinkSelectionModel
from .link_plan_model import PeriodicLinkPlanModel


class LinkLayer:
    def __init__(self, physical, *, isl_bandwidth_bps=1_000_000_000,
                 ground_bandwidth_bps=500_000_000):
        self.physical = physical
        self.scene = physical.scene
        for value in (isl_bandwidth_bps, ground_bandwidth_bps):
            if not isfinite(value) or value < 0:
                raise ValueError("base bandwidth must be finite and nonnegative")
        self.isl_bandwidth_bps = isl_bandwidth_bps
        self.ground_bandwidth_bps = ground_bandwidth_bps
        self._selected = MappingProxyType({})
        self._snapshot = None
        self._base_plans = []
        self._base_starts = []
        self._execution_plans = []
        self.set_selection_hook(RetainFirstLinkSelectionModel)
        self.set_property_hook(None)
        self.set_plan_model(PeriodicLinkPlanModel)
        self.set_perturbation_hook(None)

    def _model(self, model_class, options, method, *, optional=False):
        if self._base_plans:
            raise ValueError("configure models before generation; use a new layer for another experiment")
        if model_class is None and optional:
            return None
        if not isinstance(model_class, type):
            raise TypeError("register a model class")
        model = model_class(**({} if options is None else options))
        if not callable(getattr(model, method, None)):
            raise TypeError(f"{model_class.__name__} must implement {method}")
        model.layer = self
        return model

    def set_selection_hook(self, model_class, *, model_options=None):
        self.selection_model = self._model(model_class, model_options, "select_links", optional=True)

    def set_property_hook(self, model_class, *, model_options=None):
        self.property_model = self._model(model_class, model_options, "adjust_properties", optional=True)

    def set_plan_model(self, model_class, *, model_options=None):
        self.plan_model = self._model(model_class, model_options, "generate_plan")

    def set_perturbation_hook(self, model_class, *, model_options=None):
        self.perturbation_model = self._model(model_class, model_options, "perturb", optional=True)

    @property
    def selected_links(self):
        """Last adopted selection, including connections still being prepared."""
        return self._selected

    def adopt_selection(self, links):
        """Called by the generator when it adopts an evaluated selection."""
        links = tuple(links)
        selected = {link.id: link for link in links}
        if len(selected) != len(links):
            raise ValueError("duplicate adopted connection")
        self._selected = MappingProxyType(selected)

    @property
    def previous_base_plan(self):
        return self._base_plans[-1] if self._base_plans else None

    @property
    def base_plans(self):
        return tuple(self._base_plans)

    @property
    def execution_plans(self):
        return tuple(self._execution_plans)

    def candidates_at(self, timestamp):
        snapshot = self.physical.snapshot_at(timestamp)
        reports = self.physical.candidates(snapshot)
        pairs = {}
        for source, values in reports.items():
            for report in values:
                identity = tuple(sorted((source, report.peer_id)))
                pairs.setdefault(identity, {})[source] = report
        self._snapshot = snapshot
        return tuple(ConnectionCandidate(identity, pair) for identity, pair in pairs.items())

    def evaluate_properties(self, timestamp, candidates):
        """Estimate properties for this exact set without adopting any links."""
        timestamp = utc_time(timestamp)
        candidates = tuple(candidates)
        missing = set()
        for candidate in candidates:
            if any(report.propagation_delay_s is None for report in candidate.reports.values()):
                missing.update(candidate.endpoints)
        snapshot = self._snapshot
        if missing and (snapshot is None or snapshot.timestamp != timestamp
                        or not missing <= snapshot.states.keys()):
            snapshot = self.physical.snapshot_at(timestamp, sorted(missing))
            self._snapshot = snapshot
        links = []
        for candidate in candidates:
            satellite_pair = all(self.scene.get_node(node).kind == "satellite"
                                 for node in candidate.endpoints)
            bandwidth = self.isl_bandwidth_bps if satellite_pair else self.ground_bandwidth_bps
            directions = {}
            for source, report in candidate.reports.items():
                delay = report.propagation_delay_s
                if delay is None:
                    delay = dist(snapshot[source].position_m, snapshot[report.peer_id].position_m)
                    delay /= LIGHT_SPEED_M_S
                directions[source] = LinkProperties(candidate.id, bandwidth, delay)
            first, second = candidate.endpoints
            properties = directions.get(first, directions.get(second))
            reverse = directions.get(second)
            if reverse == properties:
                reverse = None
            links.append(LinkConfiguration(candidate, properties, reverse))
        if self.property_model is None:
            return tuple(links)
        adjusted = tuple(self.property_model.adjust_properties(self.scene, timestamp, tuple(links)))
        originals = {link.id: link.candidate for link in links}
        if len(adjusted) != len(links) or {link.id for link in adjusted} != originals.keys():
            raise ValueError("property model must preserve the selected connection set")
        for link in adjusted:
            if link.candidate is not originals[link.id] and link.candidate != originals[link.id]:
                raise ValueError("property model must retain the physical candidate")
        return adjusted

    def evaluate_connections(self, timestamp, candidates=None):
        timestamp = utc_time(timestamp)
        if candidates is None:
            candidates = self.candidates_at(timestamp)
        candidates = tuple(candidates)
        selected = candidates
        if self.selection_model is not None:
            selected = tuple(self.selection_model.select_links(self.scene, timestamp, candidates))
            originals = {candidate.id: candidate for candidate in candidates}
            seen = set()
            for candidate in selected:
                original = originals.get(candidate.id)
                if candidate.id in seen or (original is not candidate and original != candidate):
                    raise ValueError("selection must return a subset of the input candidates")
                seen.add(candidate.id)
        return self.evaluate_properties(timestamp, selected)

    def _read_plans(self, plans, start, end):
        index = bisect_right(self._base_starts, start) - 1
        if index < 0 or end > plans[-1].end_time:
            raise ValueError("requested interval is outside the available plans")
        first = plans[index].slice(start, min(end, plans[index].end_time))
        if first.end_time == end:
            return first
        operations = list(first.operations)
        for plan in plans[index + 1:]:
            if plan.start_time >= end:
                break
            operations.extend(operation for operation in plan.operations if operation.timestamp < end)
        return LinkPlan(start, end, first.initial_links, tuple(operations))

    def base_plan(self, start_time, end_time):
        """Read a stored interval; never calls any model."""
        start, end = utc_time(start_time), utc_time(end_time)
        if start >= end or not self._base_plans:
            raise ValueError("a nonempty stored interval is required")
        return self._read_plans(self._base_plans, start, end)

    def generate_base_plan(self, start_time, end_time):
        """Reuse covered intervals and append only contiguous uncovered time."""
        start, end = utc_time(start_time), utc_time(end_time)
        if start >= end:
            raise ValueError("generation requires start_time < end_time")
        frontier = start
        if self._base_plans:
            frontier = self._base_plans[-1].end_time
            if start < self._base_starts[0] or start > frontier:
                raise ValueError("new generation must continue the existing interval; use a new layer otherwise")
            if end <= frontier:
                return self.base_plan(start, end)
        plan = self.plan_model.generate_plan(self.scene, frontier, end)
        if not isinstance(plan, LinkPlan) or (plan.start_time, plan.end_time) != (frontier, end):
            raise ValueError("plan model must return LinkPlan covering the requested interval")
        if self._base_plans and plan.initial_links != self._base_plans[-1].end_links:
            raise ValueError("plan initial state must continue the preceding base plan")
        self._base_starts.append(frontier)
        self._base_plans.append(plan)
        return plan if start == frontier else self.base_plan(start, end)

    def generate_plan(self, start_time, end_time):
        """Return the execution branch; each base segment is perturbed once."""
        base = self.generate_base_plan(start_time, end_time)
        for plan in self._base_plans[len(self._execution_plans):]:
            if plan.start_time >= base.end_time:
                break
            execution = plan
            if self.perturbation_model is not None:
                execution = self.perturbation_model.perturb(plan)
                if not isinstance(execution, LinkPlan) or (
                    execution.start_time, execution.end_time
                ) != (plan.start_time, plan.end_time):
                    raise ValueError("perturbation must preserve the plan interval")
                configurations = list(execution.initial_links.values())
                configurations.extend(operation.configuration for operation in execution.operations
                                      if operation.configuration is not None)
                for configuration in configurations:
                    for node_id in configuration.candidate.endpoints:
                        self.scene.get_node(node_id)
            if self._execution_plans and execution.initial_links != self._execution_plans[-1].end_links:
                raise ValueError("perturbation must continue the preceding execution state")
            self._execution_plans.append(execution)
        return self._read_plans(self._execution_plans, base.start_time, base.end_time)
