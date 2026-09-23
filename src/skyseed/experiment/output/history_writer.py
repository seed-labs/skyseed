"""Persist and seal experiment generation history from Producer facts."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from skyseed.shared._durable_evidence import _OrderedEvidenceWriter
from skyseed.shared.recording import record_performance
from skyseed.simulator import Plan, ReachableRouteOutcome, UnreachableRouteOutcome, LocalRouteOutcome
from skyseed.backend.bpf import BpfCompilationResult
from skyseed.simulator.planner.evidence import published_planning_evidence_to_data
from .history import (
    _DOMAIN_HISTORY_ARTIFACT_TYPE, _DOMAIN_HISTORY_FORMAT_VERSION,
    _DOMAIN_HISTORY_MANIFEST_NAME, _DOMAIN_HISTORY_STREAM_NAMES,
    _timedelta_to_data, _validate_domain_history_manifest_data,
    _write_domain_history_manifest_atomic,
)

class _GenerationHistory:
    def __init__(self, root, observer):
        self._observer = observer
        self._evidence_writer = _OrderedEvidenceWriter()
        self._evidence_root = None
        self._physical_frame_history_path = None
        self._planning_history_path = None
        self._compiler_history_path = None
        self._manifest_path = None
        self._sealed = False
        self._last_publication = None
        self._evidence_record_count = 0
        self._domain_evidence_persistence_seconds = 0.0
        if root is not None:
            try:
                self._initialize_evidence(Path(root))
            except BaseException as error:
                try:
                    self._evidence_writer.finish()
                except BaseException as cleanup:
                    error.add_note(f'history acquisition cleanup: {cleanup}')
                raise

    def _execution_observer(self):
        return self._observer()

    def _initialize_evidence(self, root: Path) -> None:
        if root.is_symlink():
            raise ValueError("producer evidence directory must not be a symlink")
        destination = root.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if any((destination / name).exists() for name in _DOMAIN_HISTORY_STREAM_NAMES.values()):
            raise FileExistsError(f"producer history already exists: {destination}")
        self._evidence_root = destination
        self._physical_frame_history_path = self._evidence_writer.reset(
            destination / _DOMAIN_HISTORY_STREAM_NAMES["physical_frames"],
            {
                "record_type": "physical_frame_history_header",
                "format_version": 1,
                "coordinate_system": "WGS84-ECEF-metres",
                "formal_frames_only": True,
            },
        )
        self._planning_history_path = self._evidence_writer.reset(
            destination / _DOMAIN_HISTORY_STREAM_NAMES["planning"],
            {
                "record_type": "published_planning_history_header",
                "format_version": 2,
                "codec": "skyseed-v2-planning-evidence",
            },
        )
        self._compiler_history_path = self._evidence_writer.reset(
            destination / _DOMAIN_HISTORY_STREAM_NAMES["compiler"],
            {
                "record_type": "compiled_realization_history_header",
                "format_version": 3,
                "semantic_payload_owner": "sealed Runner artifact",
                "projection": "compact-exact-compiler-inspection-v2",
            },
        )

    def _persist_physical_frame(self, frame) -> None:
        if self._physical_frame_history_path is not None:
            self._evidence_writer.submit(self._write_physical_frame, frame)
            self._evidence_record_count += 1

    def _write_physical_frame(self, frame) -> None:
        if self._physical_frame_history_path is None:
            return
        path = self._physical_frame_history_path
        started = perf_counter()
        serialized_bytes = self._evidence_writer.append(
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
        self._record_evidence_serialization(
            started,
            family="physical-frame",
            path=path,
            size_before=0,
            serialized_bytes=serialized_bytes,
            scope={"timestamp": frame.timestamp.isoformat()},
            work={"positions": len(frame.positions)},
        )

    def _persist_planning_evidence(self, evidence) -> None:
        if self._planning_history_path is not None:
            self._evidence_writer.submit(self._write_planning_evidence, evidence)
            self._evidence_record_count += 1

    def _write_planning_evidence(self, evidence) -> None:
        if self._planning_history_path is None:
            return
        path = self._planning_history_path
        started = perf_counter()
        serialized_bytes = self._evidence_writer.append(
            path,
            {
                "record_type": "published_planning_evidence",
                "data": published_planning_evidence_to_data(evidence),
            },
        )
        self._record_evidence_serialization(
            started,
            family="planning",
            path=path,
            size_before=0,
            serialized_bytes=serialized_bytes,
            scope={"plan_id": evidence.plan.plan_id},
            work={
                "plans": 1,
                "transitions": len(evidence.plan.transitions),
                "frames": len(evidence.frames),
            },
        )

    def _persist_compiler_lineage(self, plan, realization, realization_index) -> None:
        if self._compiler_history_path is not None:
            self._evidence_writer.submit(self._write_compiler_lineage,
                plan, realization, realization_index)
            self._evidence_record_count += 1

    def _write_compiler_lineage(
        self,
        plan: Plan,
        realization: BpfCompilationResult,
        realization_index: int,
    ) -> None:
        if self._compiler_history_path is None:
            return
        path = self._compiler_history_path
        started = perf_counter()
        compact = _compact_compiler_evidence(plan, realization)
        serialized_bytes = self._evidence_writer.append(
            path,
            {
                "record_type": "compiled_realization",
                "realization_index": realization_index,
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
        self._record_evidence_serialization(
            started,
            family="compiler",
            path=path,
            size_before=0,
            serialized_bytes=serialized_bytes,
            scope={
                "plan_id": plan.plan_id,
                "realization_index": realization_index,
            },
            work={
                "realizations": 1,
                "activation_boundaries": len(
                    realization.activation_timestamps
                ),
            },
        )

    def seal(self, facts) -> None:
        self._evidence_writer.finish()
        if not self._sealed:
            self.publish(facts, terminal=True)
            self._sealed = True

    def publish(self, facts, *, terminal=False):
        if self._sealed:
            return self._last_publication
        self._evidence_writer.flush()
        if self._evidence_root is None:
            return None
        if (
            not facts["formal_exhausted"]
            or (terminal and not facts["finished"])
            or facts["plans"]
            or facts["mode"] not in {"physical-world", "planner", "compiler"}
        ):
            raise RuntimeError(
                "domain-history requires a drained completed range"
            )
        physical_only = facts["mode"] == "physical-world"
        if not physical_only and (facts["latest_plan"] is None or facts["first_plan_id"] is None):
            raise RuntimeError("domain-history has no terminal Plan lineage")
        if facts["mode"] == "planner" and facts["realization_count"] != 0:
            raise RuntimeError(
                "Planner-only domain-history cannot contain realizations"
            )
        if (
            facts["mode"] == "compiler"
            and facts["realization_count"] != facts["plan_count"]
        ):
            raise RuntimeError(
                "Compiler domain-history has incomplete realization lineage"
            )
        end = facts["timeline"].end_time
        interval = facts["timeline"].observation_interval
        span = end - facts["timeline"].start_time
        if span % interval != timedelta(0):
            raise RuntimeError(
                "bounded Timeline end must be observation-interval aligned"
            )
        expected_formal_frames = span // interval + 1
        expected_prehistory_frames = 0 if physical_only else facts["required_prehistory"] // interval
        if facts["formal_frame_count"] != expected_formal_frames:
            raise RuntimeError("formal domain-history coverage is incomplete")
        if facts["prehistory_frame_count"] != expected_prehistory_frames:
            raise RuntimeError("prehistory domain-history coverage is incomplete")
        if facts["transition_count"] != (0 if physical_only else facts["formal_frame_count"]):
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
                **self._evidence_writer.sealed_summary(path),
            }
        frontiers = facts["frontiers"]
        previous_result = facts["previous_result"]
        manifest = {
            "artifact_type": _DOMAIN_HISTORY_ARTIFACT_TYPE,
            "format_version": _DOMAIN_HISTORY_FORMAT_VERSION,
            "terminal": terminal,
            "through": facts["mode"],
            "physical_world_fingerprint": (
                facts["physical_world_fingerprint"]
            ),
            "timeline": {
                "start_timestamp": facts["timeline"].start_time.isoformat(),
                "end_timestamp": end.isoformat(),
                "observation_interval": _timedelta_to_data(interval),
            },
            "counts": {
                "expected_prehistory_frames": expected_prehistory_frames,
                "expected_formal_frames": expected_formal_frames,
                "prehistory_frames": facts["prehistory_frame_count"],
                "formal_frames": facts["formal_frame_count"],
                "plans": facts["plan_count"],
                "transitions": facts["transition_count"],
                "realizations": facts["realization_count"],
            },
            "frontiers": {
                "physical_observation": (end.isoformat() if physical_only else _required_timestamp(
                    frontiers.physical_observation, "physical-observation")),
                "planning": None if physical_only else _required_timestamp(
                    frontiers.planning, "planning"
                ),
                "publication": None if physical_only else _required_timestamp(
                    frontiers.publication, "publication"
                ),
            },
            "lineage": {
                "first_plan_id": facts["first_plan_id"],
                "last_plan_id": None if physical_only else facts["latest_plan"].plan_id,
                "last_plan_previous_plan_id": (
                    None if physical_only else facts["latest_plan"].previous_plan_id
                ),
                "last_plan_output_state_fingerprint": (
                    None if physical_only else facts["latest_plan"].output_state_fingerprint
                ),
                "last_realization_plan_id": (
                    None
                    if previous_result is None
                    else previous_result.source_plan_id
                ),
                "last_realization_output_state_fingerprint": (
                    None
                    if previous_result is None
                    else previous_result.output_state_fingerprint
                ),
            },
            "streams": streams,
        }
        _validate_domain_history_manifest_data(manifest, facts["mode"])
        manifest_path = _write_domain_history_manifest_atomic(
            self._evidence_root / "experiment" / _DOMAIN_HISTORY_MANIFEST_NAME,
            manifest,
        )
        self._manifest_path = manifest_path
        self._evidence_record_count += 1
        self._record_evidence_serialization(
            started,
            family="domain-history-manifest",
            path=manifest_path,
            size_before=0,
            scope={"through": facts["mode"]},
            work={"streams": len(streams), "completed_manifests": 1},
        )
        self._last_publication = manifest
        return manifest

    def _record_evidence_serialization(
        self,
        started: float,
        *,
        family: str,
        path: Path,
        size_before: int,
        scope: dict[str, object],
        work: dict[str, int | float],
        serialized_bytes: int | None = None,
    ) -> None:
        duration_seconds = perf_counter() - started
        self._domain_evidence_persistence_seconds += duration_seconds
        observer = self._execution_observer()
        if observer is None:
            return
        record_performance(observer,
            component="realization-producer",
            stage="evidence-serialization",
            phase="steady",
            duration_seconds=duration_seconds,
            scope={"family": family, **scope},
            work={
                **work,
                "records": 1,
                **({"serialized_bytes": serialized_bytes} if serialized_bytes is not None else {
                    "output_bytes": max(0, _safe_file_size(path) - size_before),
                }),
            },
            started_monotonic=started,
        )


def _compact_compiler_evidence(
    plan: Plan, realization: BpfCompilationResult
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
            "semantic_precedences": sum(len(t.semantic_event_precedences) for t in plan.transitions),
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
            "planned_routes_unreachable": sum(isinstance(o, UnreachableRouteOutcome) for o in state.route_outcomes),
        },
        "operation_counts": operation_counts,
        "boundary_workloads": boundaries,
        "intended_state_timestamp": state.timestamp.isoformat(),
        "zone_prefixes": [
            {"zone_id": zone_id[1], "network_scope": zone_id[0], "packet_prefix": packet_prefix}
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
