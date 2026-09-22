"""Own the sole Timeline-to-Plan-to-BPF incremental production cursor."""

from __future__ import annotations
from skyseed_v2.shared._shutdown import _shutdown_owned

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock, get_ident, Condition, Event
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from collections.abc import Callable
from typing import Any

from .output.history_writer import _GenerationHistory
from skyseed_v2.shared.recording import PerformanceRecorder, record_performance
from .output.physical_links import _PhysicalLinkInspectionWriter
from skyseed_v2.backend.execution_materials.sealing import _RunnerArtifactWriter
from skyseed_v2.simulator import (
    PhysicalLinkModel,
    PhysicalLinkQueryResult,
    PhysicalWorld,
    Plan,
    Planner,
    SimulationTimeline,
)

from skyseed_v2.simulator.planner.diagnostics import PlanningFrontiers
from ..backend.bpf import BpfCompilationResult
from skyseed_v2.backend.compiler import _compile_bpf_observed, compile_bpf, compile_plan, _compile_plan_observed
from skyseed_v2.backend.selection import _backend, _validate_program


class Producer:
    """Advance one cursor through physical, planning or compiler production.

    Layer: experiment computation. Public because the Controller owns this complete
    computation pipeline; it is not a deployment or runtime owner.
    Offline callers drain and finish it. A retained experiment keeps the cursor
    idle between finite requests and extends only its uncomputed suffix.
    When ``evidence_path`` is supplied it names a new directory containing
    three semantically owned JSONL streams: formal PhysicalPositionSnapshots, published
    Planner evidence, and Compiler/realization lineage. Completed request manifests publish bounded readable prefixes; terminal sealing
    is separate from keeping the computation session alive.
    """

    def __init__(
        self,
        timeline: SimulationTimeline,
        physical_world: PhysicalWorld,
        physical_link_model: PhysicalLinkModel,
        planner: Planner,
        *,
        evidence_path: Path | None = None,
        formal_observation_sink: (
            Callable[[PhysicalLinkQueryResult], None] | None
        ) = None,
        retain: bool = False,
        dataplane_program=None,
        forwarding_backend="bpf",
    ) -> None:
        if not isinstance(timeline, SimulationTimeline):
            raise TypeError("timeline must be a SimulationTimeline")
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(planner, Planner):
            raise TypeError("planner must be a Planner")
        compute_observation = getattr(
            physical_link_model, "compute_observation", None
        )
        if not callable(compute_observation):
            raise TypeError(
                "physical_link_model must provide compute_observation"
            )
        if formal_observation_sink is not None and not callable(
            formal_observation_sink
        ):
            raise TypeError("formal_observation_sink must be callable or None")
        planner_world_fingerprint, planner_interval = (
            planner._producer_input_contract()
        )
        if planner_world_fingerprint != physical_world.semantic_fingerprint():
            raise ValueError(
                "Planner and producer must use the same PhysicalWorld"
            )
        if planner_interval != timeline.observation_interval:
            raise ValueError(
                "Planner and producer must use the Timeline observation interval"
            )
        self._timeline = timeline
        self._initial_end = timeline.end_time
        self._retain = retain
        self._forwarding_backend = _backend(forwarding_backend)
        _validate_program(self._forwarding_backend, dataplane_program)
        self._dataplane_program = dataplane_program
        self._condition = Condition()
        self._stop = Event()
        self._requested_end = timeline.end_time
        self._batch_duration = None
        self._production_state = "NEW"
        self._sealed_end = None
        self._sealed_batches = 0
        self._idle_seconds = 0.0
        self._packaging_peak = 0
        self._physical_world = physical_world
        self._physical_link_model = physical_link_model
        self._planner = planner
        self._physical_queries = planner._physical_queries
        self._required_prehistory = planner.required_prehistory
        self._timestamps = iter(timeline.iter_timestamps())
        self._request = None
        self._plans: deque[Plan] = deque()
        self._previous_result: BpfCompilationResult | None = None
        self._first_plan_id: str | None = None
        self._latest_plan: Plan | None = None
        self._mode: str | None = None
        self._primed = False
        self._formal_exhausted = False
        self._finished = False
        self._closed = False
        self._lock = RLock()
        self._operation_owner = None
        self._operation_depth = 0
        self._formal_frame_count = 0
        self._prehistory_frame_count = 0
        self._observation_count = 0
        self._plan_count = 0
        self._transition_count = 0
        self._realization_count = 0
        self._formal_observation_sink = formal_observation_sink
        self._formal_observation_sink_failure: str | None = None
        self._formal_observation_sink_failure_count = 0
        self._formal_observation_sink_seconds = 0.0
        self._prediction_prepared = False
        self._current_formal_input = None
        self._prediction_counts = {}
        self._history = _GenerationHistory(evidence_path, self._execution_observer)

    def request_extension(self, *, additional_duration=None, end_time=None, delivery_duration=None):
        """Request a suffix; the production thread alone extends the calculation cursor."""
        if (additional_duration is None) == (end_time is None):
            raise ValueError("choose additional_duration or end_time")
        with self._condition:
            if not self._retain or self._stop.is_set() or self._closed:
                raise RuntimeError("continuation requires the original live computation session")
            target = self._requested_end + additional_duration if additional_duration is not None else end_time
            self._validate_duration(target - self._requested_end)
            if delivery_duration is not None:
                self._validate_duration(delivery_duration)
                self._batch_duration = delivery_duration
            self._requested_end = target
            self._condition.notify_all()
        return self.snapshot()

    def _validate_duration(self, duration):
        if not isinstance(duration, timedelta) or duration <= timedelta(0) or duration % self._timeline.observation_interval:
            raise ValueError("duration must be positive and aligned to the original observation cadence")

    def cancel_computation(self):
        """Stop new computation admission; produce() drains already accepted packaging."""
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def _next_delivery(self, completed, initial):
        with self._condition:
            while not self._stop.is_set() and completed >= self._requested_end:
                self._production_state = "IDLE"
                started = perf_counter()
                self._condition.wait()
                self._idle_seconds += perf_counter() - started
            if self._stop.is_set():
                return None
            self._production_state = "COMPUTING"
            end = initial.popleft() if initial else min(self._requested_end, completed + self._batch_duration)
            target = self._requested_end
        if self._formal_exhausted and target > self._timeline.end_time:
            self.extend(target)
        return min(end, self._timeline.end_time)

    def produce(self, output, *, through, delivery_ends, publish=None, traffic=None,
                delivery_duration=None, completed=None):
        """Own finite/retained calculation, bounded packaging, sealing and draining.

        publish receives completed writer facts. The optional Traffic consumer
        receives already produced Plan/result pairs and owns its own format.
        """
        if self._production_state != "NEW":
            raise RuntimeError("Producer.produce has already been invoked")
        self._production_state = "COMPUTING"
        self._batch_duration = delivery_duration
        initial = deque(delivery_ends[1:])
        end = delivery_ends[0]
        output = Path(output)
        evidence = None
        try:
            evidence = _PhysicalLinkInspectionWriter(output / "results/physical-world/candidates", self._physical_world)
            self._formal_observation_sink = evidence.record
        except Exception as error:
            self._formal_observation_sink_failure = str(error)
        def close_inspection():
            if evidence is not None:
                try:
                    evidence.close()
                except Exception as error:
                    self._formal_observation_sink_failure = str(error)
        def publish_range():
            history = self._history.publish(self._history_facts(), terminal=not self._retain)
            candidates = None
            if evidence is not None and self._formal_observation_sink_failure is None:
                try:
                    candidates = evidence.publish()
                except Exception as error:
                    self._formal_observation_sink_failure = str(error)
            if completed is not None:
                completed(history, candidates, self.snapshot())

        writer = None
        pending = deque()
        packaging = None
        failure = None
        try:
            if through != "compiler":
                advance = self.produce_next_observation if through == "physical-world" else self.produce_next_plan
                while not self._stop.is_set():
                    while not self._stop.is_set() and advance() is not None:
                        pass
                    if self._stop.is_set():
                        break
                    publish_range()
                    if not self._retain:
                        break
                    # Partial cutoffs publish complete requests, with no backend batches.
                    if self._next_delivery(self._timeline.end_time, deque()) is None:
                        self.finish()
                        break
            else:
                packaging = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skyseed-materials")
                writer = _RunnerArtifactWriter(output / ".control/runner-artifact", final=not self._retain and not initial,
                    execution_observer=self._execution_observer(), dataplane_program=self._dataplane_program, forwarding_backend=self._forwarding_backend)
                def package_pair(plan, result):
                    writer.append(result)
                    if traffic is not None:
                        traffic.append(plan, result)
                index = 0
                while not self._stop.is_set():
                    while not self._stop.is_set():
                        result = self.produce_next_realization(delivery_end=end)
                        if result is None:
                            break
                        pending.append(packaging.submit(package_pair, self.latest_plan, result))
                        self._packaging_peak = max(self._packaging_peak, len(pending))
                        if len(pending) >= 2:
                            pending[0].result()
                            pending.popleft()
                    while pending:
                        pending[0].result()
                        pending.popleft()
                    if self._stop.is_set():
                        break
                    manifest = writer.finalize()
                    metadata = writer.sealed_metadata()
                    writer.close()
                    probe_path = None if traffic is None else traffic.seal_batch(
                        output, index=index, end_time=end + self._timeline.observation_interval,
                        final=not self._retain and not initial)
                    self.flush_evidence()
                    self._sealed_end, self._sealed_batches = end, index + 1
                    if publish is not None:
                        publish(index, manifest, metadata, probe_path, self.snapshot())
                    if self._formal_exhausted:
                        publish_range()
                    if self._retain:
                        next_end = self._next_delivery(end, initial)
                        if next_end is None:
                            if self._formal_exhausted:
                                self.finish()
                            break
                    elif initial:
                        next_end = initial.popleft()
                    else:
                        break
                    index += 1
                    writer = _RunnerArtifactWriter(output / f".control/runner-artifact-{index:03d}",
                        continuation=writer, final=not self._retain and not initial,
                        execution_observer=self._execution_observer())
                    end = next_end
            try:
                if evidence is not None:
                    evidence.finalize()
            except Exception as error:
                self._formal_observation_sink_failure = f"{type(error).__name__}: {error}"
        except BaseException as error:
            failure = error
            self._production_state = "FAILED"
            raise
        finally:
            # Accepted packaging must settle before writer/Planner release.
            while pending:
                future = pending[0]
                try:
                    future.result()
                except BaseException as error:
                    if failure is None:
                        failure = error
                    elif error is not failure:
                        failure.add_note(f"accepted packaging also failed: {type(error).__name__}: {error}")
                if future.done():
                    pending.popleft()
            for cleanup in (
                lambda: packaging.shutdown(wait=True) if packaging is not None else None,
                lambda: writer.close() if writer is not None else None,
                close_inspection, self.close,
            ):
                try:
                    cleanup()
                except BaseException as error:
                    if failure is None:
                        failure = error
                    elif error is not failure:
                        failure.add_note(f"Producer drain also failed: {type(error).__name__}: {error}")
                        for note in getattr(error, "__notes__", ()):
                            failure.add_note(note)
            if self._production_state != "FAILED":
                self._production_state = "CLOSED"
                if failure is not None:
                    self._production_state = "FAILED"
                    raise failure
        return self.snapshot()

    @contextmanager
    def _operation(self):
        # The chronological cursor has one caller. Lock only the ownership
        # claim, never geometry, compilation, file I/O, or worker joins.
        caller = get_ident()
        with self._lock:
            if self._operation_owner not in (None, caller):
                raise RuntimeError("producer cursor already has an active caller")
            self._operation_owner = caller
            self._operation_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._operation_depth -= 1
                if not self._operation_depth:
                    self._operation_owner = None

    def produce_next_realization(
        self,
        *,
        delivery_end: datetime | None = None,
    ) -> BpfCompilationResult | None:
        """Return the next realization, advancing formal frames as needed."""

        with self._operation():
            if self._closed:
                raise RuntimeError("realization producer is closed")
            self._select_mode("compiler")
            started = perf_counter()
            invocation_index = self._realization_count
            formal_frames_before = self._formal_frame_count
            prehistory_frames_before = self._prehistory_frame_count
            evidence_records_before = self._history._evidence_record_count
            try:
                plan = self._pull_next_plan(delivery_end=delivery_end)
                if plan is None:
                    self._record_producer_total(
                        started,
                        invocation_index=invocation_index,
                        plan=None,
                        realization_produced=False,
                        formal_frames_before=formal_frames_before,
                        prehistory_frames_before=prehistory_frames_before,
                        evidence_records_before=evidence_records_before,
                    )
                    return None
                observer = self._execution_observer()
                if self._forwarding_backend == "linux":
                    if observer is None:
                        realization = compile_plan(plan, self._physical_world, self._previous_result,
                                                   forwarding_backend=self._forwarding_backend)
                    else:
                        realization = _compile_plan_observed(plan, self._physical_world, self._previous_result,
                            forwarding_backend=self._forwarding_backend, execution_observer=observer,
                            compile_index=self._realization_count)
                elif observer is None:
                    realization = compile_bpf(
                        plan,
                        self._physical_world,
                        self._previous_result,
                    )
                else:
                    realization = _compile_bpf_observed(
                        plan,
                        self._physical_world,
                        self._previous_result,
                        execution_observer=observer,
                        compile_index=self._realization_count,
                    )
                self._history._persist_compiler_lineage(plan, realization, self._realization_count)
                self._previous_result = realization
                self._realization_count += 1
                self._record_producer_total(
                    started,
                    invocation_index=invocation_index,
                    plan=plan,
                    realization_produced=True,
                    formal_frames_before=formal_frames_before,
                    prehistory_frames_before=prehistory_frames_before,
                    evidence_records_before=evidence_records_before,
                )
                return realization
            except BaseException as error:
                try:
                    self.close()
                except BaseException as cleanup_error:
                    if cleanup_error is not error:
                        error.add_note(f"Producer cleanup also failed: {cleanup_error}")
                        for note in getattr(cleanup_error, "__notes__", ()):
                            error.add_note(note)
                raise

    def produce_next_observation(self):
        """Advance the physical cutoff of the same finite generation path."""
        with self._operation():
            if self._closed:
                raise RuntimeError('realization producer is closed')
            self._select_mode('physical-world')
            try:
                self._ensure_prediction()
                if self._advance_one_formal_frame():
                    return self._current_formal_input
                if not self._retain:
                    self._finished = True
                    self._seal_domain_history()
                return None
            except BaseException as error:
                try:
                    self.close()
                except BaseException as cleanup_error:
                    if cleanup_error is not error:
                        error.add_note(f"Producer cleanup also failed: {cleanup_error}")
                        for note in getattr(cleanup_error, "__notes__", ()):
                            error.add_note(note)
                raise

    def produce_next_plan(self) -> Plan | None:
        """Return the next Plan without invoking the Compiler.

        This pull operation is the Planner-only stage of the same production
        cursor. Once either pull mode is selected, the other is prohibited.
        """

        with self._operation():
            if self._closed:
                raise RuntimeError("realization producer is closed")
            self._select_mode("planner")
            started = perf_counter()
            invocation_index = self._plan_count
            formal_frames_before = self._formal_frame_count
            prehistory_frames_before = self._prehistory_frame_count
            evidence_records_before = self._history._evidence_record_count
            try:
                plan = self._pull_next_plan()
                self._record_producer_total(
                    started,
                    invocation_index=invocation_index,
                    plan=plan,
                    realization_produced=False,
                    formal_frames_before=formal_frames_before,
                    prehistory_frames_before=prehistory_frames_before,
                    evidence_records_before=evidence_records_before,
                )
                return plan
            except BaseException as error:
                try:
                    self.close()
                except BaseException as cleanup_error:
                    if cleanup_error is not error:
                        error.add_note(f"Producer cleanup also failed: {cleanup_error}")
                        for note in getattr(cleanup_error, "__notes__", ()):
                            error.add_note(note)
                raise

    def _select_mode(self, mode: str) -> None:
        if mode not in {"physical-world", "planner", "compiler"}:
            raise ValueError("unknown producer stage cutoff")
        if self._mode is None:
            self._mode = mode
            return
        if self._mode != mode:
            raise RuntimeError(
                "producer cannot mix Planner-only and realization pull modes"
            )

    def _pull_next_plan(self, *, delivery_end: datetime | None = None) -> Plan | None:
        if delivery_end is not None:
            start, end = self._timeline.start_time, self._timeline.end_time
            interval = self._timeline.observation_interval
            if (not isinstance(delivery_end, datetime)
                or delivery_end.tzinfo is None
                or not start <= delivery_end <= end
                or (delivery_end - start) % interval):
                raise ValueError("delivery_end must be a formal Timeline timestamp")
            if self._latest_plan is not None and delivery_end < self._latest_plan.end_timestamp:
                raise ValueError("delivered coverage cannot move backwards")
        self._ensure_primed()
        while not self._plans:
            next_time = (self._timeline.start_time
                         + self._formal_frame_count * self._timeline.observation_interval)
            if ((delivery_end is not None and (self._retain or delivery_end < self._timeline.end_time)
                 and next_time > delivery_end)
                or (self._retain and next_time > self._timeline.end_time)):
                self._plans.extend(self._planner.flush())
                if not self._plans:
                    self._formal_exhausted = next_time > self._timeline.end_time
                    return None
                break
            if self._advance_one_formal_frame():
                continue
            self._finish_planner()
            if not self._plans:
                self._seal_domain_history()
                return None
        plan = self._plans.popleft()
        evidence = self._planner.get_published_evidence(plan.plan_id)
        self._history._persist_planning_evidence(evidence)
        self._planner.release_published_through(plan.plan_id)
        if self._first_plan_id is None:
            self._first_plan_id = plan.plan_id
        self._latest_plan = plan
        self._plan_count += 1
        self._transition_count += len(plan.transitions)
        return plan

    @property
    def latest_plan(self) -> Plan:
        """Return the latest successfully produced Plan."""

        with self._operation():
            if self._latest_plan is None:
                raise RuntimeError("no Plan has been produced")
            return self._latest_plan

    def extend(self, end_time: datetime) -> None:
        """Extend this live cursor; the original definition remains finite and immutable."""
        with self._operation():
            if not self._retain or self._closed or self._finished:
                raise RuntimeError("continuation requires the original retained Producer")
            timeline = self._timeline
            if end_time <= timeline.end_time or (end_time - timeline.start_time) % timeline.observation_interval:
                raise ValueError("continuation endpoint must increase on the original cadence")
            if not self._formal_exhausted or self._plans:
                raise RuntimeError("drain the requested calculation before extending it")
            next_time = timeline.start_time + self._formal_frame_count * timeline.observation_interval
            suffix = SimulationTimeline(next_time, end_time, timeline.observation_interval)
            self._timeline = SimulationTimeline(timeline.start_time, end_time, timeline.observation_interval)
            with self._condition:
                self._requested_end = max(self._requested_end, end_time)
            self._timestamps = iter(suffix.iter_timestamps())
            self._formal_exhausted = False
            self._prediction_prepared = False
            self._ensure_prediction(start_timestamp=next_time)

    def finish(self) -> None:
        """Explicitly end retained computation after draining its finite endpoint."""
        with self._operation():
            if not self._formal_exhausted or self._plans:
                raise RuntimeError("computation must be drained before finishing")
            if self._mode == "physical-world":
                self._finished = True
            else:
                self._finish_planner()
            self._seal_domain_history()

    def flush_evidence(self) -> None:
        with self._operation():
            self._history._evidence_writer.flush()

    def snapshot(self) -> dict[str, object]:
        """Return bounded cursor/frontier bookkeeping without live objects."""

        with self._lock:
            frontiers = self._frontiers()
            return {
                "initial_end": self._initial_end.isoformat(),
                "requested_end": self._requested_end.isoformat(),
                "computation_range_end": self._timeline.end_time.isoformat(),
                "production_state": self._production_state,
                "cancellation_requested": self._stop.is_set(),
                "sealed_material_end": None if self._sealed_end is None else self._sealed_end.isoformat(),
                "sealed_batches": self._sealed_batches,
                "packaging_peak": self._packaging_peak,
                "idle_seconds": self._idle_seconds,
                "retained": self._retain,
                "physical_prediction": dict(self._prediction_counts),
                "evidence_queue_peak": self._history._evidence_writer.peak_pending,
                "evidence_queue_wait_seconds": self._history._evidence_writer.wait_seconds,
                "formal_frame_count": self._formal_frame_count,
                "prehistory_frame_count": self._prehistory_frame_count,
                "physical_link_observation_count": self._observation_count,
                "total_physical_link_observation_count": (
                    self._observation_count + self._prehistory_frame_count
                ),
                "plan_count": self._plan_count,
                "transition_count": self._transition_count,
                "realization_count": self._realization_count,
                "buffered_plan_count": len(self._plans),
                "pull_mode": self._mode,
                "formal_input_exhausted": self._formal_exhausted,
                "planner_finished": self._finished and self._mode != "physical-world",
                "domain_history_sealed": self._history._sealed,
                "closed": self._closed,
                "latest_plan_id": (
                    None
                    if self._latest_plan is None
                    else self._latest_plan.plan_id
                ),
                "formal_observation_sink_failure_count": (
                    self._formal_observation_sink_failure_count
                ),
                "formal_observation_sink_failure": (
                    self._formal_observation_sink_failure
                ),
                "formal_observation_sink_seconds": (
                    self._formal_observation_sink_seconds
                ),
                "domain_evidence_persistence_seconds": (
                    self._history._domain_evidence_persistence_seconds
                ),
                "frontiers": {
                    "physical_observation": _optional_timestamp(
                        (self._current_formal_input[0].timestamp if self._mode == "physical-world" and self._current_formal_input is not None
                         else frontiers.physical_observation)
                    ),
                    "planning": _optional_timestamp(frontiers.planning),
                    "publication": _optional_timestamp(
                        frontiers.publication
                    ),
                },
                "evidence": (
                    None
                    if self._history._evidence_root is None
                    else {
                        "root": str(self._history._evidence_root),
                        "physical_frames": str(
                            self._history._physical_frame_history_path
                        ),
                        "planning": str(self._history._planning_history_path),
                        "compiler": str(self._history._compiler_history_path),
                        "manifest": (
                            None
                            if self._history._manifest_path is None
                            else str(self._history._manifest_path)
                        ),
                    }
                ),
            }

    def close(self) -> None:
        """Drain accepted evidence, then close optional resources of advanced owners."""

        with self._operation():
            if self._closed:
                return
            failure = None
            try:
                self._history._evidence_writer.finish()
            except BaseException as error:
                failure = error
            try:
                _shutdown_owned((self._planner, self._physical_link_model, self._physical_world), wait=True)
            except BaseException as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note(f"Model shutdown also failed: {error}")
                    for note in getattr(error, "__notes__", ()):
                        failure.add_note(note)
            self._closed = True
            if failure is not None:
                raise failure

    def __enter__(self) -> Producer:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except BaseException as error:
            if exc is None:
                raise
            exc.add_note(f"Producer close also failed: {type(error).__name__}: {error}")
            for note in getattr(error, "__notes__", ()):
                exc.add_note(note)

    def _ensure_prediction(self, *, start_timestamp=None) -> None:
        if self._prediction_prepared:
            return
        queries = self._physical_queries if self._mode == "physical-world" else self._planner
        self._request = queries.create_physical_link_request()
        interval = self._timeline.observation_interval
        required = self._required_prehistory
        prediction = getattr(self._physical_link_model, 'predict_contacts', None)
        isl_tail, ground_tail = queries._physical_prediction_requirements()
        if callable(prediction) and (isl_tail or ground_tail):
            started = perf_counter()
            self._prediction_counts = prediction(
                self._physical_world, self._request,
                start_timestamp=(self._timeline.start_time-required if start_timestamp is None else start_timestamp),
                end_timestamp=self._timeline.end_time,
                observation_interval=interval,
                inter_satellite_tail=isl_tail, ground_station_tail=ground_tail,
            )
            observer = self._execution_observer()
            if observer is not None:
                record_performance(observer,component='physical-link', stage='physical-contact-prediction',
                    phase='one-shot', duration_seconds=perf_counter()-started,
                    work=self._prediction_counts, started_monotonic=started)
        self._prediction_prepared = True

    def _compute_physical_observation(self, frame):
        supports = getattr(self._physical_link_model, "supports_ground_station_candidate_limits", None)
        limits = supports() if callable(supports) else False
        if not isinstance(limits, bool):
            raise TypeError("supports_ground_station_candidate_limits must return bool")
        queries = self._physical_queries if self._mode == "physical-world" else self._planner
        request = queries._candidate_search_request(self._request,
            supports_ground_station_limits=limits)
        expansions = 0
        decision_seconds = 0.0
        search_started = perf_counter()
        while True:
            observation = self._physical_link_model.compute_observation(self._physical_world, frame, request)
            decision_started = perf_counter()
            expanded = queries._expanded_candidate_request(request, observation)
            decision_seconds += perf_counter() - decision_started
            if expanded is None:
                observer = self._execution_observer()
                if observer is not None:
                    record_performance(observer,component="physical-link", stage="candidate-search", phase="one-shot",
                        duration_seconds=decision_seconds, scope={"timestamp": frame.timestamp.isoformat()},
                        work={"ground_search_expansions": expansions,
                              "ground_candidate_limit": request.ground_station_candidate_limit or 0})
                    record_performance(observer,component="physical-link", stage="candidate-search-total", phase="one-shot",
                        duration_seconds=perf_counter()-search_started, duration_role="rollup",
                        started_monotonic=search_started, scope={"timestamp": frame.timestamp.isoformat()})
                return observation
            request = expanded
            expansions += 1

    def _ensure_primed(self) -> None:
        if self._primed:
            return
        self._ensure_prediction()
        interval = self._timeline.observation_interval
        required = self._required_prehistory
        offsets = []
        offset = interval
        while offset <= required:
            offsets.append(offset)
            offset += interval
        for value in reversed(offsets):
            timestamp = self._timeline.start_time - value
            frame = self._physical_world.frame_at(timestamp)
            observation = self._compute_physical_observation(frame)
            self._require_observation_timestamp(frame.timestamp, observation)
            self._planner.prime(
                frame,
                observation,
                formal_start=self._timeline.start_time,
            )
            self._prehistory_frame_count += 1
        self._primed = True

    def _advance_one_formal_frame(self) -> bool:
        if self._formal_exhausted:
            return False
        try:
            timestamp = next(self._timestamps)
        except StopIteration:
            self._formal_exhausted = True
            return False
        assert self._request is not None
        frame = self._physical_world.frame_at(timestamp)
        self._history._persist_physical_frame(frame)
        observation = self._compute_physical_observation(frame)
        self._require_observation_timestamp(frame.timestamp, observation)
        if self._mode != "physical-world":
            self._plans.extend(self._planner.observe(frame, observation))
        self._current_formal_input = (frame, observation)
        if self._formal_observation_sink is not None:
            capture_started = perf_counter()
            captured = True
            try:
                self._formal_observation_sink(observation)
            except Exception as error:
                captured = False
                self._formal_observation_sink_failure_count += 1
                self._formal_observation_sink_failure = (
                    f"{type(error).__name__}: {error}"
                )
                self._formal_observation_sink = None
            observer = self._execution_observer()
            capture_seconds = perf_counter() - capture_started
            self._formal_observation_sink_seconds += capture_seconds
            if observer is not None:
                record_performance(observer,
                    component="inspection-evidence",
                    stage="physical-link-candidate-capture",
                    phase="steady",
                    duration_seconds=capture_seconds,
                    scope={"timestamp": observation.timestamp.isoformat()},
                    work={
                        "observations_attempted": 1,
                        "observations_captured": int(captured),
                        "capture_failures": int(not captured),
                    },
                    started_monotonic=capture_started,
                )
        self._formal_frame_count += 1
        self._observation_count += 1
        return True

    def _finish_planner(self) -> None:
        if self._finished:
            return
        self._plans.extend(
            self._planner.finish(end_timestamp=self._timeline.end_time)
        )
        self._finished = True

    @staticmethod
    def _require_observation_timestamp(timestamp, observation) -> None:
        if getattr(observation, "timestamp", None) != timestamp:
            raise ValueError(
                "PhysicalLinkModel observation must match its PhysicalPositionSnapshot"
            )

    def _seal_domain_history(self):
        self._history.seal(self._history_facts())

    def _history_facts(self):
        return {
            "formal_exhausted": self._formal_exhausted, "finished": self._finished,
            "plans": bool(self._plans), "mode": self._mode, "latest_plan": self._latest_plan,
            "first_plan_id": self._first_plan_id, "realization_count": self._realization_count,
            "plan_count": self._plan_count, "timeline": self._timeline,
            "formal_frame_count": self._formal_frame_count, "prehistory_frame_count": self._prehistory_frame_count,
            "transition_count": self._transition_count, "previous_result": self._previous_result,
            "required_prehistory": self._required_prehistory,
            "frontiers": self._frontiers(),
            "physical_world_fingerprint": self._physical_world.semantic_fingerprint(),
        }

    def _frontiers(self):
        if self._mode == "physical-world":
            timestamp = None if self._current_formal_input is None else self._current_formal_input[0].timestamp
            return PlanningFrontiers(timestamp, None, None)
        return self._planner.get_frontiers()

    def _execution_observer(self) -> PerformanceRecorder | None:
        if self._mode == "physical-world":
            return self._physical_world._get_execution_observer()
        return self._planner._get_execution_observer()

    def _record_producer_total(
        self,
        started: float,
        *,
        invocation_index: int,
        plan: Plan | None,
        realization_produced: bool,
        formal_frames_before: int,
        prehistory_frames_before: int,
        evidence_records_before: int,
    ) -> None:
        observer = self._execution_observer()
        if observer is None:
            return
        formal_frames = self._formal_frame_count - formal_frames_before
        prehistory_frames = (
            self._prehistory_frame_count - prehistory_frames_before
        )
        produced = plan is not None
        record_performance(observer,
            component="realization-producer",
            stage="producer-total",
            phase="steady" if produced else "idle",
            duration_seconds=perf_counter() - started,
            duration_role="rollup",
            scope={
                "invocation_index": invocation_index,
                "plan_id": None if plan is None else plan.plan_id,
                "terminal": not produced,
            },
            work={
                "formal_frames": formal_frames,
                "prehistory_frames": prehistory_frames,
                "plans": int(produced),
                "transitions": 0 if plan is None else len(plan.transitions),
                "realizations": int(realization_produced),
                "evidence_records": (
                    self._history._evidence_record_count - evidence_records_before
                ),
            },
        )


def _optional_timestamp(value: Any) -> str | None:
    return None if value is None else value.isoformat()
