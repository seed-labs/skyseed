"""Own the sole Timeline-to-Plan-to-BPF incremental production cursor."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import timedelta
from pathlib import Path
from threading import RLock
from time import perf_counter
from collections.abc import Callable
from typing import Any

from skyseed_v2.shared._domain_history import (
    _DOMAIN_HISTORY_ARTIFACT_TYPE,
    _DOMAIN_HISTORY_FORMAT_VERSION,
    _DOMAIN_HISTORY_MANIFEST_NAME,
    _DOMAIN_HISTORY_STREAM_NAMES,
    _iter_verified_domain_history_stream,
    _read_domain_history_manifest,
    _summarize_domain_history_stream,
    _timedelta_to_data,
    _validate_domain_history_manifest_data,
    _write_domain_history_manifest_atomic,
)
from skyseed_v2.shared._durable_evidence import _append_jsonl, _reset_jsonl
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from skyseed_v2.simulator import (
    PhysicalFrame,
    PhysicalLinkModel,
    PhysicalLinkObservation,
    PhysicalWorld,
    Plan,
    Planner,
    ReachableRouteOutcome,
    SimulationTimeline,
)
from skyseed_v2.simulator.planning.evidence import (
    published_planning_evidence_from_data,
    published_planning_evidence_to_data,
)

from ..backend.bpf import BpfCompiledRealization
from ..backend.bpf.compiler import _compile_bpf_observed, compile_bpf


class _IncrementalRealizationProducer:
    """Advance one cursor through Planner-only or Compiler production.

    Precomputed callers drain this object before constructing a Runner artifact;
    no producer survives into live execution.
    When ``evidence_path`` is supplied it names a new directory containing
    three semantically owned JSONL streams: formal PhysicalFrames, published
    Planner evidence, and Compiler/realization lineage. A deterministic sibling
    manifest is published only after a bounded caller pulls terminal ``None``.
    """

    __slots__ = (
        "_timeline",
        "_physical_world",
        "_physical_link_model",
        "_planner",
        "_timestamps",
        "_request",
        "_plans",
        "_previous_realization",
        "_first_plan_id",
        "_latest_plan",
        "_first_formal_frame",
        "_first_formal_observation",
        "_mode",
        "_primed",
        "_formal_exhausted",
        "_finished",
        "_closed",
        "_lock",
        "_formal_frame_count",
        "_prehistory_frame_count",
        "_observation_count",
        "_plan_count",
        "_transition_count",
        "_realization_count",
        "_evidence_record_count",
        "_evidence_root",
        "_physical_frame_history_path",
        "_planning_history_path",
        "_compiler_history_path",
        "_manifest_path",
        "_formal_observation_sink",
        "_formal_observation_sink_failure",
        "_formal_observation_sink_failure_count",
        "_formal_observation_sink_seconds",
        "_domain_evidence_persistence_seconds",
        "_sealed",
    )

    def __init__(
        self,
        timeline: SimulationTimeline,
        physical_world: PhysicalWorld,
        physical_link_model: PhysicalLinkModel,
        planner: Planner,
        *,
        evidence_path: Path | None = None,
        formal_observation_sink: (
            Callable[[PhysicalLinkObservation], None] | None
        ) = None,
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
        self._physical_world = physical_world
        self._physical_link_model = physical_link_model
        self._planner = planner
        self._timestamps = iter(timeline.iter_timestamps())
        self._request = None
        self._plans: deque[Plan] = deque()
        self._previous_realization: BpfCompiledRealization | None = None
        self._first_plan_id: str | None = None
        self._latest_plan: Plan | None = None
        self._first_formal_frame: PhysicalFrame | None = None
        self._first_formal_observation: PhysicalLinkObservation | None = None
        self._mode: str | None = None
        self._primed = False
        self._formal_exhausted = False
        self._finished = False
        self._closed = False
        self._lock = RLock()
        self._formal_frame_count = 0
        self._prehistory_frame_count = 0
        self._observation_count = 0
        self._plan_count = 0
        self._transition_count = 0
        self._realization_count = 0
        self._evidence_record_count = 0
        self._evidence_root: Path | None = None
        self._physical_frame_history_path: Path | None = None
        self._planning_history_path: Path | None = None
        self._compiler_history_path: Path | None = None
        self._manifest_path: Path | None = None
        self._formal_observation_sink = formal_observation_sink
        self._formal_observation_sink_failure: str | None = None
        self._formal_observation_sink_failure_count = 0
        self._formal_observation_sink_seconds = 0.0
        self._domain_evidence_persistence_seconds = 0.0
        self._sealed = False
        if evidence_path is not None:
            self._initialize_evidence(Path(evidence_path))

    def produce_next_realization(
        self,
    ) -> BpfCompiledRealization | None:
        """Return the next realization, advancing formal frames as needed."""

        with self._lock:
            if self._closed:
                raise RuntimeError("realization producer is closed")
            self._select_mode("compiler")
            started = perf_counter()
            invocation_index = self._realization_count
            formal_frames_before = self._formal_frame_count
            prehistory_frames_before = self._prehistory_frame_count
            evidence_records_before = self._evidence_record_count
            try:
                plan = self._pull_next_plan()
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
                if observer is None:
                    realization = compile_bpf(
                        plan,
                        self._physical_world,
                        self._previous_realization,
                    )
                else:
                    realization = _compile_bpf_observed(
                        plan,
                        self._physical_world,
                        self._previous_realization,
                        execution_observer=observer,
                        compile_index=self._realization_count,
                    )
                self._persist_compiler_lineage(plan, realization)
                self._previous_realization = realization
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
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    pass
                raise

    def produce_next_plan(self) -> Plan | None:
        """Return the next Plan without invoking the Compiler.

        This private seam is the Planner-only stage of the same production
        cursor. Once either pull mode is selected, the other is prohibited.
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("realization producer is closed")
            self._select_mode("planner")
            started = perf_counter()
            invocation_index = self._plan_count
            formal_frames_before = self._formal_frame_count
            prehistory_frames_before = self._prehistory_frame_count
            evidence_records_before = self._evidence_record_count
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
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    pass
                raise

    def _select_mode(self, mode: str) -> None:
        if mode not in {"planner", "compiler"}:
            raise ValueError("producer mode must be planner or compiler")
        if self._mode is None:
            self._mode = mode
            return
        if self._mode != mode:
            raise RuntimeError(
                "producer cannot mix Planner-only and realization pull modes"
            )

    def _pull_next_plan(self) -> Plan | None:
        self._ensure_primed()
        while not self._plans:
            if self._advance_one_formal_frame():
                continue
            self._finish_planner()
            if not self._plans:
                self._seal_domain_history()
                return None
        plan = self._plans.popleft()
        evidence = self._planner.get_published_evidence(plan.plan_id)
        self._persist_planning_evidence(evidence)
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

        with self._lock:
            if self._latest_plan is None:
                raise RuntimeError("no Plan has been produced")
            return self._latest_plan

    def first_formal_input(
        self,
    ) -> tuple[PhysicalFrame, PhysicalLinkObservation]:
        """Return the exact first formal input consumed by Planner.

        This bounded private integration seam lets maintained examples render
        Physical Frame inspection after domain progression without computing a
        second frame or invoking a potentially stateful PhysicalLinkModel twice.
        """

        with self._lock:
            if (
                self._first_formal_frame is None
                or self._first_formal_observation is None
            ):
                raise RuntimeError("no formal Planner input has been consumed")
            return self._first_formal_frame, self._first_formal_observation

    def snapshot(self) -> dict[str, object]:
        """Return bounded cursor/frontier bookkeeping without live objects."""

        with self._lock:
            frontiers = self._planner.get_frontiers()
            return {
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
                "planner_finished": self._finished,
                "domain_history_sealed": self._sealed,
                "closed": self._closed,
                "latest_plan_id": (
                    None
                    if self._latest_plan is None
                    else self._latest_plan.plan_id
                ),
                "first_formal_input_available": (
                    self._first_formal_frame is not None
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
                    self._domain_evidence_persistence_seconds
                ),
                "frontiers": {
                    "physical_observation": _optional_timestamp(
                        frontiers.physical_observation
                    ),
                    "planning": _optional_timestamp(frontiers.planning),
                    "publication": _optional_timestamp(
                        frontiers.publication
                    ),
                },
                "evidence": (
                    None
                    if self._evidence_root is None
                    else {
                        "root": str(self._evidence_root),
                        "physical_frames": str(
                            self._physical_frame_history_path
                        ),
                        "planning": str(self._planning_history_path),
                        "compiler": str(self._compiler_history_path),
                        "manifest": (
                            None
                            if self._manifest_path is None
                            else str(self._manifest_path)
                        ),
                    }
                ),
            }

    def close(self) -> None:
        """Shut down Planner-owned workers exactly once."""

        with self._lock:
            if self._closed:
                return
            self._planner.shutdown(wait=True)
            self._closed = True

    def __enter__(self) -> _IncrementalRealizationProducer:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _ensure_primed(self) -> None:
        if self._primed:
            return
        self._request = self._planner.create_physical_link_request()
        interval = self._timeline.observation_interval
        required = self._planner.required_prehistory
        offsets = []
        offset = interval
        while offset <= required:
            offsets.append(offset)
            offset += interval
        for value in reversed(offsets):
            timestamp = self._timeline.start_time - value
            frame = self._physical_world.frame_at(timestamp)
            observation = self._physical_link_model.compute_observation(
                self._physical_world,
                frame,
                self._request,
            )
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
        self._persist_physical_frame(frame)
        observation = self._physical_link_model.compute_observation(
            self._physical_world,
            frame,
            self._request,
        )
        self._require_observation_timestamp(frame.timestamp, observation)
        self._plans.extend(self._planner.observe(frame, observation))
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
                observer.record(
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
        if self._first_formal_frame is None:
            self._first_formal_frame = frame
            self._first_formal_observation = observation
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
                "PhysicalLinkModel observation must match its PhysicalFrame"
            )

    def _initialize_evidence(self, root: Path) -> None:
        if root.is_symlink():
            raise ValueError("producer evidence directory must not be a symlink")
        destination = root.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()):
            raise FileExistsError(
                f"producer evidence directory is not empty: {destination}"
            )
        self._evidence_root = destination
        self._physical_frame_history_path = _reset_jsonl(
            destination / _DOMAIN_HISTORY_STREAM_NAMES["physical_frames"],
            {
                "record_type": "physical_frame_history_header",
                "format_version": 1,
                "coordinate_system": "WGS84-ECEF-metres",
                "formal_frames_only": True,
            },
        )
        self._planning_history_path = _reset_jsonl(
            destination / _DOMAIN_HISTORY_STREAM_NAMES["planning"],
            {
                "record_type": "published_planning_history_header",
                "format_version": 1,
                "codec": "skyseed-v2-planning-evidence",
            },
        )
        self._compiler_history_path = _reset_jsonl(
            destination / _DOMAIN_HISTORY_STREAM_NAMES["compiler"],
            {
                "record_type": "compiled_realization_history_header",
                "format_version": 2,
                "semantic_payload_owner": "sealed Runner artifact",
                "projection": "compact-exact-compiler-inspection-v1",
            },
        )

    def _persist_physical_frame(self, frame) -> None:
        if self._physical_frame_history_path is None:
            return
        path = self._physical_frame_history_path
        size_before = _safe_file_size(path)
        started = perf_counter()
        _append_jsonl(
            path,
            {
                "record_type": "physical_frame",
                "timestamp": frame.timestamp.isoformat(),
                "positions": [
                    {
                        "node": {
                            "asn": node_id.asn,
                            "entity_kind": node_id.entity_kind,
                            "entity_id": node_id.entity_id,
                        },
                        "x_m": position.x_m,
                        "y_m": position.y_m,
                        "z_m": position.z_m,
                    }
                    for node_id, position in sorted(
                        frame.positions.items(),
                        key=lambda value: (
                            value[0].asn,
                            value[0].entity_kind,
                            value[0].entity_id,
                        ),
                    )
                ],
            },
        )
        self._evidence_record_count += 1
        self._record_evidence_serialization(
            started,
            family="physical-frame",
            path=path,
            size_before=size_before,
            scope={"timestamp": frame.timestamp.isoformat()},
            work={"positions": len(frame.positions)},
        )

    def _persist_planning_evidence(self, evidence) -> None:
        if self._planning_history_path is None:
            return
        path = self._planning_history_path
        size_before = _safe_file_size(path)
        started = perf_counter()
        _append_jsonl(
            path,
            {
                "record_type": "published_planning_evidence",
                "data": published_planning_evidence_to_data(evidence),
            },
        )
        self._evidence_record_count += 1
        self._record_evidence_serialization(
            started,
            family="planning",
            path=path,
            size_before=size_before,
            scope={"plan_id": evidence.plan.plan_id},
            work={
                "plans": 1,
                "transitions": len(evidence.plan.transitions),
                "frames": len(evidence.frames),
            },
        )

    def _persist_compiler_lineage(
        self,
        plan: Plan,
        realization: BpfCompiledRealization,
    ) -> None:
        if self._compiler_history_path is None:
            return
        path = self._compiler_history_path
        size_before = _safe_file_size(path)
        started = perf_counter()
        compact = _compact_compiler_evidence(plan, realization)
        _append_jsonl(
            path,
            {
                "record_type": "compiled_realization",
                "realization_index": self._realization_count,
                "source_plan_id": plan.plan_id,
                "source_previous_plan_id": plan.previous_plan_id,
                "physical_world_fingerprint": (
                    realization.physical_world_fingerprint
                ),
                "input_state_fingerprint": plan.input_state_fingerprint,
                "output_state_fingerprint": plan.output_state_fingerprint,
                "start_timestamp": plan.start_timestamp.isoformat(),
                "end_timestamp": plan.end_timestamp.isoformat(),
                "activation_timestamps": [
                    value.isoformat()
                    for value in realization.activation_timestamps
                ],
                "intent_counts": {
                    "resource": len(realization.resource_realizations),
                    "parameter": len(realization.parameter_realizations),
                    "forwarding": len(realization.forwarding_realizations),
                    "zone_edge": len(realization.zone_edge_realizations),
                    "constraints": len(realization.constraints),
                },
                "semantic_precedence_folding": {
                    mode: count
                    for mode, count in realization.semantic_precedence_folding
                },
                **compact,
            },
        )
        self._evidence_record_count += 1
        self._record_evidence_serialization(
            started,
            family="compiler",
            path=path,
            size_before=size_before,
            scope={
                "plan_id": plan.plan_id,
                "realization_index": self._realization_count,
            },
            work={
                "realizations": 1,
                "activation_boundaries": len(
                    realization.activation_timestamps
                ),
            },
        )

    def _seal_domain_history(self) -> None:
        if self._evidence_root is None or self._sealed:
            return
        if (
            not self._formal_exhausted
            or not self._finished
            or self._plans
            or self._mode not in {"planner", "compiler"}
        ):
            raise RuntimeError(
                "domain-history can be sealed only after terminal exhaustion"
            )
        if self._latest_plan is None or self._first_plan_id is None:
            raise RuntimeError("domain-history has no terminal Plan lineage")
        if self._mode == "planner" and self._realization_count != 0:
            raise RuntimeError(
                "Planner-only domain-history cannot contain realizations"
            )
        if (
            self._mode == "compiler"
            and self._realization_count != self._plan_count
        ):
            raise RuntimeError(
                "Compiler domain-history has incomplete realization lineage"
            )
        end = self._timeline.end_time
        interval = self._timeline.observation_interval
        span = end - self._timeline.start_time
        if span % interval != timedelta(0):
            raise RuntimeError(
                "bounded Timeline end must be observation-interval aligned"
            )
        expected_formal_frames = span // interval + 1
        expected_prehistory_frames = self._planner.required_prehistory // interval
        if self._formal_frame_count != expected_formal_frames:
            raise RuntimeError("formal domain-history coverage is incomplete")
        if self._prehistory_frame_count != expected_prehistory_frames:
            raise RuntimeError("prehistory domain-history coverage is incomplete")
        if self._transition_count != self._formal_frame_count:
            raise RuntimeError("Plan transition coverage is incomplete")

        paths = {
            "physical_frames": self._physical_frame_history_path,
            "planning": self._planning_history_path,
            "compiler": self._compiler_history_path,
        }
        if any(path is None for path in paths.values()):
            raise RuntimeError("domain-history streams were not initialized")
        started = perf_counter()
        streams = {}
        for stream_name, path in paths.items():
            assert path is not None
            streams[stream_name] = {
                "path": _DOMAIN_HISTORY_STREAM_NAMES[stream_name],
                **_summarize_domain_history_stream(path),
            }
        frontiers = self._planner.get_frontiers()
        previous_realization = self._previous_realization
        manifest = {
            "artifact_type": _DOMAIN_HISTORY_ARTIFACT_TYPE,
            "format_version": _DOMAIN_HISTORY_FORMAT_VERSION,
            "terminal": True,
            "through": self._mode,
            "physical_world_fingerprint": (
                self._physical_world.semantic_fingerprint()
            ),
            "timeline": {
                "start_timestamp": self._timeline.start_time.isoformat(),
                "end_timestamp": end.isoformat(),
                "observation_interval": _timedelta_to_data(interval),
            },
            "counts": {
                "expected_prehistory_frames": expected_prehistory_frames,
                "expected_formal_frames": expected_formal_frames,
                "prehistory_frames": self._prehistory_frame_count,
                "formal_frames": self._formal_frame_count,
                "plans": self._plan_count,
                "transitions": self._transition_count,
                "realizations": self._realization_count,
            },
            "frontiers": {
                "physical_observation": _required_timestamp(
                    frontiers.physical_observation,
                    "physical-observation",
                ),
                "planning": _required_timestamp(
                    frontiers.planning, "planning"
                ),
                "publication": _required_timestamp(
                    frontiers.publication, "publication"
                ),
            },
            "lineage": {
                "first_plan_id": self._first_plan_id,
                "last_plan_id": self._latest_plan.plan_id,
                "last_plan_previous_plan_id": (
                    self._latest_plan.previous_plan_id
                ),
                "last_plan_output_state_fingerprint": (
                    self._latest_plan.output_state_fingerprint
                ),
                "last_realization_plan_id": (
                    None
                    if previous_realization is None
                    else previous_realization.source_plan_id
                ),
                "last_realization_output_state_fingerprint": (
                    None
                    if previous_realization is None
                    else previous_realization.output_state_fingerprint
                ),
            },
            "streams": streams,
        }
        _validate_domain_history_manifest_data(manifest, self._mode)
        manifest_path = _write_domain_history_manifest_atomic(
            self._evidence_root / _DOMAIN_HISTORY_MANIFEST_NAME,
            manifest,
        )
        self._manifest_path = manifest_path
        self._sealed = True
        self._evidence_record_count += 1
        self._record_evidence_serialization(
            started,
            family="domain-history-manifest",
            path=manifest_path,
            size_before=0,
            scope={"through": self._mode},
            work={"streams": len(streams), "terminal_manifests": 1},
        )

    def _execution_observer(self) -> _ExecutionObserver | None:
        return self._planner._get_execution_observer()

    def _record_evidence_serialization(
        self,
        started: float,
        *,
        family: str,
        path: Path,
        size_before: int,
        scope: dict[str, object],
        work: dict[str, int | float],
    ) -> None:
        duration_seconds = perf_counter() - started
        self._domain_evidence_persistence_seconds += duration_seconds
        observer = self._execution_observer()
        if observer is None:
            return
        observer.record(
            component="realization-producer",
            stage="evidence-serialization",
            phase="steady",
            duration_seconds=duration_seconds,
            scope={"family": family, **scope},
            work={
                **work,
                "records": 1,
                "output_bytes": max(
                    0, _safe_file_size(path) - size_before
                ),
            },
            started_monotonic=started,
        )

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
        observer.record(
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
                    self._evidence_record_count - evidence_records_before
                ),
            },
        )


def _compact_compiler_evidence(
    plan: Plan, realization: BpfCompiledRealization
) -> dict[str, object]:
    """Derive the exact bounded Compiler Tool projection while values are live."""

    operation_counts: dict[str, dict[str, int]] = {}
    by_time: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for family, values in (
        ("resources", realization.resource_realizations),
        ("forwarding", realization.forwarding_realizations),
        ("zone_edges", realization.zone_edge_realizations),
    ):
        counts = Counter(value.operation.value for value in values)
        operation_counts[family] = dict(sorted(counts.items()))
        for value in values:
            by_time[value.nominal_time.isoformat()][family][
                value.operation.value
            ] += 1
    operation_counts["parameters"] = {
        "update": len(realization.parameter_realizations)
    }
    for value in realization.parameter_realizations:
        by_time[value.nominal_time.isoformat()]["parameters"]["update"] += 1
    operation_counts["constraints"] = {
        "precedence": len(realization.constraints)
    }
    boundaries = []
    for boundary in realization.compiled_activation_boundaries:
        timestamp = boundary.timestamp.isoformat()
        operations = {
            family: dict(sorted(counts.items()))
            for family, counts in sorted(by_time[timestamp].items())
        }
        boundaries.append(
            {
                "timestamp": timestamp,
                "transition_fingerprint": boundary.transition_fingerprint,
                "operations": operations,
                "delta_counts": _compiler_delta_counts(operations),
                "operation_count": sum(
                    sum(counts.values()) for counts in operations.values()
                ),
            }
        )
    state = realization.output_planned_state
    reachable = sum(
        isinstance(value, ReachableRouteOutcome)
        for value in state.route_outcomes
    )
    return {
        "compact_summary": {
            "activation_boundaries": len(boundaries),
            "plan_transition_changes": sum(
                len(transition.established_connections)
                + len(transition.withdrawn_connection_ids)
                + len(transition.parameter_updated_connections)
                + len(transition.zone_service_transitions)
                + len(transition.route_outcome_transitions)
                for transition in plan.transitions
            ),
            "semantic_precedences": len(plan.semantic_event_precedences),
            "resource_intents": len(realization.resource_realizations),
            "parameter_intents": len(realization.parameter_realizations),
            "forwarding_intents": len(realization.forwarding_realizations),
            "zone_edge_intents": len(realization.zone_edge_realizations),
            "constraints": len(realization.constraints),
            "intended_connections": len(state.operational_connections),
            "intended_resources": len(realization.intended_resources),
            "intended_parameters": len(realization.intended_parameters),
            "intended_forwarding": len(realization.intended_forwarding),
            "intended_zone_edges": len(realization.intended_zone_edges),
            "intended_zones": len(state.zone_service_assignments),
            "planned_routes_reachable": reachable,
            "planned_routes_unreachable": len(state.route_outcomes) - reachable,
        },
        "operation_counts": operation_counts,
        "boundary_workloads": boundaries,
        "intended_state_timestamp": state.timestamp.isoformat(),
        "zone_prefixes": [
            {"zone_id": zone_id, "packet_prefix": packet_prefix}
            for zone_id, packet_prefix in realization.zone_prefixes
        ],
    }


def _compiler_delta_counts(
    operations: dict[str, dict[str, int]],
) -> dict[str, int]:
    result = {"add": 0, "update": 0, "remove": 0}
    for counts in operations.values():
        for operation, count in counts.items():
            if operation in {"add", "establish"}:
                result["add"] += count
            elif operation in {"change", "update"}:
                result["update"] += count
            elif operation in {"remove", "withdraw"}:
                result["remove"] += count
    return result


def _optional_timestamp(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _read_persisted_plan_sequence(path: Path) -> tuple[Plan, ...]:
    """Read the authoritative Plan stream aligned with persisted realizations."""

    manifest = _read_domain_history_manifest(path, minimum_through="planner")
    rows = _iter_verified_domain_history_stream(manifest, "planning")
    try:
        header = next(rows)
    except StopIteration as error:  # pragma: no cover - manifest count rejects it
        raise ValueError("planning history is empty") from error
    if header != {
        "record_type": "published_planning_history_header",
        "format_version": 1,
        "codec": "skyseed-v2-planning-evidence",
    }:
        raise ValueError("planning history header is incompatible")
    plans = []
    for row in rows:
        if set(row) != {"record_type", "data"} or row["record_type"] != (
            "published_planning_evidence"
        ):
            raise ValueError("planning history row is incompatible")
        data = row["data"]
        if not isinstance(data, dict):
            raise ValueError("planning history evidence must be an object")
        plans.append(published_planning_evidence_from_data(data).plan)
    return tuple(plans)


def _required_timestamp(value: Any, frontier_name: str) -> str:
    if value is None:
        raise RuntimeError(
            f"terminal domain-history lacks {frontier_name} frontier"
        )
    return value.isoformat()


def _safe_file_size(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


__all__: list[str] = []
