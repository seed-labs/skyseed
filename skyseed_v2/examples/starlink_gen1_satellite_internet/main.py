"""Build canonical Starlink Gen1 through one final architecture boundary."""

from __future__ import annotations

import argparse
from datetime import timedelta
from yaml import safe_dump, safe_load
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from time import perf_counter
from uuid import uuid4


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_PREFIX = "skyseed_v2_starlink_gen1_satellite_internet"
_PROJECT_NAME_FILE = "runtime/compose-project-name"
_REALIZATION_PRODUCER_IDENTITY = (
    "skyseed-v2-common-incremental-realization-producer-v1"
)
_THROUGH_STAGES = ("physical-world", "planner", "compiler")

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.starlink_gen1_satellite_internet.emu import (
    build_starlink_gen1_satellite_internet_emulator,
)
from examples.starlink_gen1_satellite_internet.simu import (
    STARLINK_GEN1_OBSERVATION_INTERVAL,
    STARLINK_GEN1_PLAN_BATCH_FRAMES,
    create_starlink_gen1_planner,
    create_starlink_gen1_simulator,
    starlink_gen1_model_descriptors,
)
from skyseed_v2.compose.launcher import (
    _InterfaceLessRuntimeDocker,
    _prepare_generated_output,
    _write_compose_build_plan,
    _write_compose_project_scripts,
)
from skyseed_v2.backend.bpf._runner_artifact import (
    _RunnerArtifactWriter,
)
from skyseed_v2.experiment._realization_producer import (
    _IncrementalRealizationProducer,
)
from skyseed_v2.experiment.execution import (
    _source_scope,
    _scope_records_digest,
    _write_execution_definition,
    _write_generated_run_script,
)
from skyseed_v2.experiment.traffic.probe_plan import (
    IncrementalTrafficProbePlanBuilder,
    write_traffic_probe_plan,
)
from skyseed_v2.simulator import (
    PhysicalFrame,
    PhysicalLinkObservation,
    Simulator,
)
from tools._detached_inspection import _DetachedInspectionExecutor
from tools._html import write_aggregate_index
from tools._inspection_evidence import _PhysicalLinkInspectionWriter
from tools.physical_world import write_physical_world_tool
from tools.zone import write_zone_tool




def _guard_existing_output(output: Path) -> None:
    """Never replace controls while their Compose project exists."""

    if not output.exists() and not output.is_symlink():
        return
    project_file = output / _PROJECT_NAME_FILE
    if not project_file.is_file():
        legacy = output / ".compose-project-name"
        if legacy.is_file():
            project_file = legacy
        elif (output / "runtime/docker-compose.yml").is_file():
            raise RuntimeError(
                "existing Starlink output has no verifiable Compose identity; "
                "remove it explicitly before regenerating"
            )
        else:
            return
    project_name = project_file.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None:
        raise RuntimeError("existing output generation identity is invalid")
    try:
        result = subprocess.run(
            (
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError("cannot verify existing Starlink deployment") from error
    if result.returncode != 0:
        raise RuntimeError(
            "cannot verify existing Starlink deployment: "
            + result.stderr.strip()
        )
    if result.stdout.strip():
        raise RuntimeError(
            "the existing Starlink deployment still has containers; run "
            f"{output / 'cleanup.sh'} before regenerating"
        )


def _write_input_snapshots(output: Path, *, selected_shell: str | None = None) -> None:
    inputs = output / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for name in (
        "scenario.yaml",
        "shells.yaml",
        "ground_stations.yaml",
        "zones.yaml",
    ):
        (inputs / name).write_bytes((EXAMPLE_ROOT / name).read_bytes())
    if selected_shell is not None:
        scenario_path = inputs / "scenario.yaml"
        scenario = safe_load(scenario_path.read_text())
        scenario["skyseed_autonomous_systems"][0]["selected_shell"] = selected_shell
        scenario_path.write_text(safe_dump(scenario, sort_keys=False), encoding="utf-8")


def _append_phase(
    records: list[dict[str, object]] | None,
    name: str,
    category: str,
    seconds: float,
    *,
    additive: bool,
    interval_kind: str = "span",
    started_monotonic: float | None = None,
    ended_monotonic: float | None = None,
) -> None:
    if records is not None:
        if interval_kind not in {"span", "aggregate"}:
            raise ValueError("interval_kind must be span or aggregate")
        ended = perf_counter() if ended_monotonic is None else ended_monotonic
        started = (
            ended - seconds
            if started_monotonic is None and interval_kind == "span"
            else ended
            if started_monotonic is None
            else started_monotonic
        )
        records.append(
            {
                "phase": name,
                "category": category,
                "wall_seconds": seconds,
                "additive": additive,
                "interval_kind": interval_kind,
                "started_monotonic": started,
                "ended_monotonic": ended,
            }
        )


def _run_static_tools(
    simulator: Simulator,
    inspection: Path,
    *,
    physical_frame: PhysicalFrame | None = None,
    physical_link_observation: PhysicalLinkObservation | None = None,
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
    phase_records: list[dict[str, object]] | None = None,
):
    """Write static physical views without advancing the domain chain."""

    physical_world = simulator.get_physical_world()
    frame = (
        physical_world.frame_at(simulator.get_timeline().start_time)
        if physical_frame is None
        else physical_frame
    )
    calls = {
        "physical-world": lambda: write_physical_world_tool(
            physical_world,
            inspection / "physical-world",
            physical_frames_path=(
                inspection.parent / "domain-history/physical-frames.jsonl"
                if (
                    inspection.parent
                    / "domain-history/physical-frames.jsonl"
                ).is_file()
                else None
            ),
            physical_frame=frame,
            physical_link_observation=physical_link_observation,
            physical_link_model=(
                None
                if physical_link_observation is None
                else simulator.get_physical_link_model()
            ),
            physical_link_inspection_path=physical_link_inspection_path,
            model_descriptors=model_descriptors,
        ),
        "zone": lambda: write_zone_tool(
            physical_world, inspection / "zone"
        ),
    }
    results = {}
    for name, call in calls.items():
        started = perf_counter()
        try:
            results[name] = dict(call())
        except Exception as error:
            results[name] = {
                "status": "FAILED",
                "message": f"{type(error).__name__}: {error}",
            }
        _append_phase(
            phase_records,
            f"tool:{name}",
            "detached-inspection",
            perf_counter() - started,
            additive=True,
        )
    return results


def _run_physical_stage(simulator, output, *, phase_records=None):
    """Drain physical prediction/observations through the same producer cursor."""
    started = perf_counter()
    evidence = _PhysicalLinkInspectionWriter(output / 'inspection-evidence', simulator.get_physical_world())
    producer = _IncrementalRealizationProducer(simulator.get_timeline(),
        simulator.get_physical_world(), simulator.get_physical_link_model(),
        simulator.get_planner(), evidence_path=output / "domain-history",
        formal_observation_sink=evidence.record)
    try:
        while producer.produce_next_observation() is not None:
            pass
        evidence_finalize_failure = None
        try:
            evidence.finalize()
        except Exception as error:
            evidence_finalize_failure = f"{type(error).__name__}: {error}"
        frame, observation = producer.first_formal_input()
        snapshot = producer.snapshot()
        snapshot["formal_observation_finalize_failure"] = evidence_finalize_failure
    finally:
        producer.close()
        evidence.close()
    _append_phase(phase_records, 'physical-production', 'semantic-core',
                 perf_counter()-started, additive=True)
    return snapshot, frame, observation


def _write_generation_status(output, *, through, status, materials_seconds,
                             total_seconds, producer_snapshot, tool_results=None, model_descriptors=None):
    """Publish the consumer-visible material/Tool completion boundary."""
    import os
    payload = {
        'format_version': 1, 'artifact_type': 'finite-generation-status',
        'through': through, 'status': status,
        'materials_ready_seconds': materials_seconds,
        'tools_completion_seconds': None if tool_results is None else total_seconds,
        'total_elapsed_seconds': total_seconds,
        'tools_wall_seconds': None if tool_results is None else total_seconds-materials_seconds,
        'timing_scope': 'main entry to required material seal and controls; Tools measured afterward',
        'producer': producer_snapshot,
        'tools': tool_results,
        'environment': {'CONDA_DEFAULT_ENV': os.environ.get('CONDA_DEFAULT_ENV'),
            'CONDA_PREFIX': os.environ.get('CONDA_PREFIX'),
            'python_executable': sys.executable, 'python_version': sys.version},
        'configuration': model_descriptors or starlink_gen1_model_descriptors(),
    }
    path = output / 'generation-status.json'
    staging = path.with_suffix('.tmp')
    staging.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str)+'\n', encoding='utf-8')
    staging.replace(path)


def _run_planner_stage(
    simulator: Simulator,
    output: Path,
    *,
    phase_records: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], PhysicalFrame, PhysicalLinkObservation]:
    """Drain the one producer through sealed Planner-only evidence."""

    evidence_writer = _PhysicalLinkInspectionWriter(
        output / "inspection-evidence",
        simulator.get_physical_world(),
    )
    producer = _IncrementalRealizationProducer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        simulator.get_planner(),
        evidence_path=output / "domain-history",
        formal_observation_sink=evidence_writer.record,
    )
    try:
        production_loop_started = perf_counter()
        producer_seconds = 0.0
        sealing_seconds = 0.0
        sealing_started = production_loop_started
        sealing_ended = production_loop_started
        while True:
            started = perf_counter()
            plan = producer.produce_next_plan()
            elapsed = perf_counter() - started
            if plan is None:
                sealing_seconds += elapsed
                sealing_started = started
                sealing_ended = started + elapsed
                break
            producer_seconds += elapsed
        production_loop_ended = perf_counter()
        physical_frame, physical_link_observation = (
            producer.first_formal_input()
        )
        evidence_finalize_started = perf_counter()
        evidence_finalize_failure = None
        try:
            evidence_writer.finalize()
        except Exception as error:
            evidence_finalize_failure = f"{type(error).__name__}: {error}"
        evidence_finalize_ended = perf_counter()
        evidence_seconds = evidence_finalize_ended - evidence_finalize_started
        snapshot = producer.snapshot()
        snapshot["formal_observation_finalize_failure"] = (
            evidence_finalize_failure
        )
    finally:
        producer.close()
        evidence_writer.close()
    _append_phase(
        phase_records,
        "semantic-producer",
        "semantic-core",
        producer_seconds,
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "inspection-candidate-capture",
        "inspection-evidence-capture",
        float(snapshot["formal_observation_sink_seconds"]),
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "domain-evidence-persistence",
        "persistence",
        float(snapshot["domain_evidence_persistence_seconds"]),
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "planner-production-loop",
        "semantic-core",
        production_loop_ended - production_loop_started,
        additive=False,
        started_monotonic=production_loop_started,
        ended_monotonic=production_loop_ended,
    )
    _append_phase(
        phase_records,
        "domain-sealing",
        "sealing-finalization",
        sealing_seconds,
        additive=True,
        started_monotonic=sealing_started,
        ended_monotonic=sealing_ended,
    )
    _append_phase(
        phase_records,
        "physical-link-inspection-evidence-finalize",
        "detached-inspection",
        evidence_seconds,
        additive=True,
        started_monotonic=evidence_finalize_started,
        ended_monotonic=evidence_finalize_ended,
    )
    timeline = simulator.get_timeline()
    formal_boundary_count = (timeline.end_time - timeline.start_time) // timeline.observation_interval + 1
    if (
        snapshot["formal_frame_count"] != formal_boundary_count
        or snapshot["transition_count"]
        != formal_boundary_count
        or snapshot["plan_count"] != (formal_boundary_count + STARLINK_GEN1_PLAN_BATCH_FRAMES - 1) // STARLINK_GEN1_PLAN_BATCH_FRAMES
        or snapshot["realization_count"] != 0
        or snapshot["domain_history_sealed"] is not True
    ):
        raise RuntimeError(
            "canonical Planner producer did not seal every Timeline frame/transition "
            "and complete frame batches"
        )
    return snapshot, physical_frame, physical_link_observation


def _run_compiler_stage(
    simulator: Simulator,
    output: Path,
    *,
    phase_records: list[dict[str, object]] | None = None,
    delivery_ends: tuple | None = None,
) -> tuple[
    Path,
    dict[str, object],
    PhysicalFrame,
    PhysicalLinkObservation,
]:
    """Drain the common producer completely, then seal the Runner artifact."""

    evidence_writer = _PhysicalLinkInspectionWriter(
        output / "inspection-evidence",
        simulator.get_physical_world(),
    )
    producer = _IncrementalRealizationProducer(
        simulator.get_timeline(),
        simulator.get_physical_world(),
        simulator.get_physical_link_model(),
        simulator.get_planner(),
        evidence_path=output / "domain-history",
        formal_observation_sink=evidence_writer.record,
    )
    timeline = simulator.get_timeline()
    delivery_ends = (timeline.end_time,) if delivery_ends is None else tuple(delivery_ends)
    if (not delivery_ends or delivery_ends[-1] != timeline.end_time
        or any(end < timeline.start_time or (end - timeline.start_time) % timeline.observation_interval for end in delivery_ends)
        or any(a >= b for a, b in zip(delivery_ends, delivery_ends[1:]))):
        raise ValueError("delivery endpoints must partition the complete formal Timeline")
    delivery_index = 0
    sealed_deliveries = []
    staging = output / ".runner-artifact"
    writer = _RunnerArtifactWriter(
        staging,
        final=len(delivery_ends) == 1,
        execution_observer=simulator.get_planner()._get_execution_observer(),
    )
    traffic_builder = IncrementalTrafficProbePlanBuilder(physical_world=simulator.get_physical_world())
    from concurrent.futures import ThreadPoolExecutor
    from collections import deque
    packaging = ThreadPoolExecutor(max_workers=1, thread_name_prefix='skyseed-materials')
    pending_packages = deque()
    def package_pair(plan, realization):
        started = perf_counter()
        writer.append(realization)
        writer_duration = perf_counter()-started
        started = perf_counter()
        traffic_builder.append(plan, realization)
        return writer_duration, perf_counter()-started
    material_loop_complete = False
    try:
        production_loop_started = perf_counter()
        producer_seconds = 0.0
        writer_seconds = 0.0
        traffic_seconds = 0.0
        sealing_seconds = 0.0
        sealing_started = production_loop_started
        sealing_ended = production_loop_started
        while True:
            started = perf_counter()
            realization = producer.produce_next_realization(delivery_end=delivery_ends[delivery_index])
            elapsed = perf_counter() - started
            if realization is None:
                sealing_seconds += elapsed
                sealing_started = started
                sealing_ended = started + elapsed
                if delivery_index + 1 < len(delivery_ends):
                    while pending_packages:
                        writer_duration, traffic_duration = pending_packages.popleft().result()
                        writer_seconds += writer_duration
                        traffic_seconds += traffic_duration
                    seal_started = perf_counter()
                    sealed_deliveries.append(writer.finalize())
                    _append_phase(phase_records, "delivered-batch-seal", "sealing-finalization",
                                  perf_counter() - seal_started, additive=False)
                    writer.close()
                    delivery_index += 1
                    writer = _RunnerArtifactWriter(output / f".runner-artifact-{delivery_index:03d}",
                        continuation=writer, final=delivery_index + 1 == len(delivery_ends),
                        execution_observer=simulator.get_planner()._get_execution_observer())
                    continue
                break
            producer_seconds += elapsed
            pending_packages.append(packaging.submit(package_pair, producer.latest_plan, realization))
            # At most two immutable compiled batches in flight, in lineage order.
            if len(pending_packages) >= 2:
                writer_duration, traffic_duration = pending_packages.popleft().result()
                writer_seconds += writer_duration
                traffic_seconds += traffic_duration
        while pending_packages:
            writer_duration, traffic_duration = pending_packages.popleft().result()
            writer_seconds += writer_duration
            traffic_seconds += traffic_duration
        production_loop_ended = perf_counter()
        physical_frame, physical_link_observation = (
            producer.first_formal_input()
        )
        evidence_finalize_started = perf_counter()
        evidence_finalize_failure = None
        try:
            evidence_writer.finalize()
        except Exception as error:
            evidence_finalize_failure = f"{type(error).__name__}: {error}"
        evidence_finalize_ended = perf_counter()
        evidence_seconds = evidence_finalize_ended - evidence_finalize_started
        snapshot = producer.snapshot()
        snapshot["formal_observation_finalize_failure"] = (
            evidence_finalize_failure
        )
        material_loop_complete = True
    finally:
        packaging.shutdown(wait=True, cancel_futures=True)
        if not material_loop_complete:
            writer.close()
        producer.close()
        evidence_writer.close()
    handoff_finalize_started = perf_counter()
    try:
        sealed_deliveries.append(writer.finalize())
        manifest = sealed_deliveries[0]
    finally:
        writer.close()
    handoff_finalize_ended = perf_counter()
    handoff_finalize_seconds = handoff_finalize_ended - handoff_finalize_started
    traffic_snapshot = traffic_builder.snapshot()
    started = perf_counter()
    write_traffic_probe_plan(
        traffic_builder.finalize(),
        output / "inspection/traffic/probe-plan.json",
    )
    traffic_seconds += perf_counter() - started
    snapshot["traffic_incremental_projection"] = traffic_snapshot
    snapshot["delivered_batches"] = [
        {"manifest": str(path), **{key: value for key, value in json.loads(path.read_text()).items()
                                 if key in {"delivery", "timeline", "binary", "realization_count"}}}
        for path in sealed_deliveries]
    expected_plan_count = sum((value["timeline"]["boundary_count"] + STARLINK_GEN1_PLAN_BATCH_FRAMES - 1)
                              // STARLINK_GEN1_PLAN_BATCH_FRAMES for value in snapshot["delivered_batches"])
    _append_phase(
        phase_records,
        "semantic-producer",
        "semantic-core",
        producer_seconds,
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "inspection-candidate-capture",
        "inspection-evidence-capture",
        float(snapshot["formal_observation_sink_seconds"]),
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "domain-evidence-persistence",
        "persistence",
        float(snapshot["domain_evidence_persistence_seconds"]),
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "runner-artifact-writer",
        "persistence",
        writer_seconds,
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "traffic-probe-plan",
        "detached-inspection",
        traffic_seconds,
        additive=False,
        interval_kind="aggregate",
    )
    _append_phase(
        phase_records,
        "compiler-production-loop",
        "semantic-core",
        production_loop_ended - production_loop_started,
        additive=False,
        started_monotonic=production_loop_started,
        ended_monotonic=production_loop_ended,
    )
    _append_phase(
        phase_records,
        "domain-sealing",
        "sealing-finalization",
        sealing_seconds,
        additive=True,
        started_monotonic=sealing_started,
        ended_monotonic=sealing_ended,
    )
    _append_phase(
        phase_records,
        "handoff-finalization",
        "sealing-finalization",
        handoff_finalize_seconds,
        additive=True,
        started_monotonic=handoff_finalize_started,
        ended_monotonic=handoff_finalize_ended,
    )
    _append_phase(
        phase_records,
        "physical-link-inspection-evidence-finalize",
        "detached-inspection",
        evidence_seconds,
        additive=True,
        started_monotonic=evidence_finalize_started,
        ended_monotonic=evidence_finalize_ended,
    )
    timeline = simulator.get_timeline()
    formal_boundary_count = (timeline.end_time - timeline.start_time) // timeline.observation_interval + 1
    if (
        snapshot["formal_frame_count"] != formal_boundary_count
        or snapshot["transition_count"] != formal_boundary_count
        or snapshot["plan_count"] != expected_plan_count
        or snapshot["realization_count"] != snapshot["plan_count"]
        or snapshot["domain_history_sealed"] is not True
    ):
        raise RuntimeError(
            "canonical producer did not emit every Timeline frame/transition and "
            "complete Plan/realization batches"
        )
    return (
        manifest,
        snapshot,
        physical_frame,
        physical_link_observation,
    )


def _write_runtime_package(
    output: Path,
    *,
    emulator,
    simulator: Simulator,
    staged_manifest: Path,
    staged_batch_manifests: tuple[Path, ...] = (),
    example_root: Path = EXAMPLE_ROOT,
    additional_example_roots: tuple[Path, ...] = (),
    batch_lead_fraction: float = 0.75,
    batch_capacity_factor: int = 2,
    phase_records: list[dict[str, object]] | None = None,
) -> Path:
    """Publish the persisted Compiler stream and static deployment package."""

    if staged_manifest != output / ".runner-artifact/manifest.json":
        raise ValueError("unexpected Runner artifact staging manifest")
    started_package = perf_counter()
    compose_project_name = f"{COMPOSE_PROJECT_PREFIX}-{uuid4().hex[:16]}"

    started = perf_counter()
    _append_phase(
        phase_records,
        "runtime-input-snapshots",
        "persistence",
        perf_counter() - started,
        additive=True,
    )

    started = perf_counter()
    emulator.render()
    _append_phase(
        phase_records,
        "runtime-emulator-render",
        "persistence",
        perf_counter() - started,
        additive=True,
    )

    started = perf_counter()
    emulator.compile(_InterfaceLessRuntimeDocker(), str(output / "runtime"))
    _append_phase(
        phase_records,
        "runtime-static-package-compile",
        "persistence",
        perf_counter() - started,
        additive=True,
    )

    started = perf_counter()
    project_file = output / _PROJECT_NAME_FILE
    project_file.parent.mkdir(parents=True, exist_ok=True)
    project_file.write_text(f"{compose_project_name}\n", encoding="utf-8")
    build_plan = _write_compose_build_plan(output / "runtime")
    _append_phase(
        phase_records,
        "runtime-compose-build-plan",
        "persistence",
        perf_counter() - started,
        additive=True,
    )

    started = perf_counter()
    artifact_directory = output / "runtime/runner-artifact"
    if artifact_directory.exists():
        raise FileExistsError(
            f"Runner artifact already exists: {artifact_directory}"
        )
    shutil.move(str(staged_manifest.parent), artifact_directory)
    artifact_manifest = artifact_directory / "manifest.json"
    continued_manifests = []
    for index, path in enumerate(staged_batch_manifests, 1):
        destination = output / f"runtime/runner-artifact-{index:03d}"
        shutil.move(str(path.parent), destination)
        continued_manifests.append(str((destination / "manifest.json").relative_to(output)))
    _append_phase(
        phase_records,
        "runner-artifact-publication",
        "persistence",
        perf_counter() - started,
        additive=True,
    )

    probe_plan_path = output / "inspection/traffic/probe-plan.json"
    if not probe_plan_path.is_file():
        raise RuntimeError(
            "runtime package requires the incremental Traffic probe plan"
        )

    started = perf_counter()
    timeline = simulator.get_timeline()
    _write_execution_definition(
        output,
        generated_output_identity=compose_project_name,
        timeline_start=timeline.start_time,
        timeline_end=timeline.end_time,
        observation_interval=timeline.observation_interval,
        project_root=PROJECT_ROOT,
        example_root=example_root,
        runner_batches=tuple(continued_manifests),
        batch_lead_fraction=batch_lead_fraction,
        batch_capacity_factor=batch_capacity_factor,
        runner_artifact=str(
            artifact_manifest.relative_to(output).as_posix()
        ),
        realization_producer=_REALIZATION_PRODUCER_IDENTITY,
    )
    if additional_example_roots:
        # Preserve the shared policy scope and also identify its composition
        # caller. This uses the existing execution source-scope contract.
        definition_path = output / "runtime/execution.json"
        definition = json.loads(definition_path.read_text())
        fingerprint = definition["source_fingerprint"]
        fingerprint["scopes"].extend(
            _source_scope(f"example-composition-{index}", root,
                          includes=("main.py", "emu.py", "simu.py"))
            for index, root in enumerate(additional_example_roots)
        )
        fingerprint["digest"] = _scope_records_digest(fingerprint["scopes"])
        definition_path.write_text(json.dumps(definition, indent=2, sort_keys=True) + "\n")
    _append_phase(
        phase_records,
        "runtime-execution-definition",
        "persistence",
        perf_counter() - started,
        additive=True,
    )

    started = perf_counter()
    _write_compose_project_scripts(
        output,
        compose_project_name,
        startup_evidence_path="runtime/deployment-startup.json",
        controlled_startup=True,
        build_plan_path=str(build_plan.relative_to(output).as_posix()),
        python_executable=sys.executable,
    )
    _write_generated_run_script(
        output,
        python_executable=sys.executable,
        compiler_tool_directory="bpf-compiler",
        tool_package_parent=PROJECT_ROOT,
    )
    _append_phase(
        phase_records,
        "runtime-script-publication",
        "persistence",
        perf_counter() - started,
        additive=True,
    )
    package_ended = perf_counter()
    _append_phase(
        phase_records,
        "runtime-packaging",
        "persistence",
        package_ended - started_package,
        additive=False,
        started_monotonic=started_package,
        ended_monotonic=package_ended,
    )
    return artifact_manifest


def _run_dynamic_tools(
    output: Path,
    *,
    through: str,
    model_descriptors: dict[str, object] | None = None,
    phase_records: list[dict[str, object]] | None = None,
) -> tuple[dict[str, dict[str, object]], tuple[dict[str, object], ...]]:
    """Run semantic Tools as detached side work after domain success."""

    batch_started = perf_counter()
    inspection = output / "inspection"
    executor = _DetachedInspectionExecutor(
        output / ".inspection-control", max_workers=2
    )
    try:
        planning_path = output / "domain-history/planning.jsonl.gz"
        executor.submit(
            "planner",
            {
                "planner_history_path": planning_path,
                "physical_frames_path": (
                    output / "domain-history/physical-frames.jsonl"
                ),
                "physical_link_inspection_path": (
                    output
                    / "inspection-evidence/physical-link-observations.jsonl.gz"
                ),
                "model_descriptors": model_descriptors or {},
            },
            inspection / "planner",
        )
        if through == "compiler":
            executor.submit(
                "compiler",
                {
                    "compiler_history_path": (
                        output / "domain-history/compiler.jsonl"
                    ),
                    "execution_definition_path": output / "runtime/execution.json",
                    "observation_path": (
                        inspection
                        / "observation/execution-observations.jsonl"
                    ),
                },
                inspection / "bpf-compiler",
            )
        observation_path = (
            inspection / "observation/execution-observations.jsonl"
        )
        executor.submit(
            "observation",
            {"observation_path": observation_path},
            inspection / "observation",
        )
        records = executor.join()
    except BaseException:
        executor.terminate()
        raise
    batch_ended = perf_counter()
    _append_phase(
        phase_records,
        "detached-tool-batch",
        "detached-inspection",
        batch_ended - batch_started,
        additive=True,
        started_monotonic=batch_started,
        ended_monotonic=batch_ended,
    )
    results = {}
    for record in records:
        task = str(record["task"])
        key = "bpf-compiler" if task == "compiler" else task
        result = executor.result(task)
        results[key] = (
            dict(result)
            if result is not None
            else {
                "status": "FAILED",
                "message": str(record.get("message", "Tool failed")),
            }
        )
        _append_phase(
            phase_records,
            f"tool:{key}",
            "detached-inspection",
            float(record.get("wall_seconds", 0.0)),
            additive=False,
            started_monotonic=(
                None
                if record.get("started_monotonic") is None
                else float(record["started_monotonic"])
            ),
            ended_monotonic=(
                None
                if record.get("ended_monotonic") is None
                else float(record["ended_monotonic"])
            ),
        )
    return results, records


def _write_tool_records(
    inspection: Path, records: tuple[dict[str, object], ...]
) -> None:
    inspection.mkdir(parents=True, exist_ok=True)
    (inspection / "tool-executions.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "artifact_type": "inspection-tool-executions",
                "tools": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_orchestration_timing(
    inspection: Path,
    records: list[dict[str, object]],
    *,
    total_wall_seconds: float,
    run_origin_monotonic: float,
) -> None:
    categories: dict[str, float] = {}
    attributed = 0.0
    for record in records:
        if not record["additive"]:
            continue
        seconds = float(record["wall_seconds"])
        category = str(record["category"])
        categories[category] = categories.get(category, 0.0) + seconds
        attributed += seconds
    payload = {
        "format_version": 2,
        "artifact_type": "example-orchestration-timing",
        "clock": "time.perf_counter wall seconds",
        "records": [
            {
                **record,
                "run_relative_start_seconds": max(
                    0.0,
                    float(record["started_monotonic"])
                    - run_origin_monotonic,
                ),
                "run_relative_end_seconds": max(
                    0.0,
                    float(record["ended_monotonic"])
                    - run_origin_monotonic,
                ),
            }
            for record in records
        ],
        "category_totals": categories,
        "total_wall_seconds": total_wall_seconds,
        "attributed_additive_seconds": attributed,
        "unattributed_residual_seconds": max(
            0.0, total_wall_seconds - attributed
        ),
        "interpretation": {
            "semantic-core": "authoritative production work",
            "inspection-evidence-capture": (
                "queue submission/wait; serialization runs in a bounded Tool worker"
            ),
            "persistence": "serialization, packaging, and actual writes; see separate operation timings",
            "detached-inspection": "Tool-owned side work outside semantics",
            "sealing-finalization": "terminal sealing, manifests, and hashes",
            "other": "assembly and orchestration not in the other categories",
            "non_additive": (
                "per-Tool worker timings may overlap the detached batch wall"
            ),
            "span": (
                "measured monotonic start/end; eligible for timeline geometry"
            ),
            "aggregate": (
                "interleaved accumulated duration; reported numerically and "
                "never drawn as a fabricated contiguous interval"
            ),
        },
    }
    inspection.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    (inspection / "orchestration-timing.json").write_text(
        text, encoding="utf-8"
    )
    (inspection / "orchestration-timing.js").write_text(
        "window.SKYSEED_ORCHESTRATION_TIMING="
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--through",
        choices=_THROUGH_STAGES,
        default="compiler",
        help="generation boundary (default: compiler)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--shell", default=None,
                        help="explicit shell ID from shells.yaml (default: scenario.yaml)")
    parser.add_argument("--observation-interval-seconds", type=float,
                        default=STARLINK_GEN1_OBSERVATION_INTERVAL.total_seconds(),
                        help="formal observation cadence; policy durations stay in seconds")
    return parser


def _write_optional_observations(simulator):
    try:
        simulator._write_execution_observations()
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return None


def main(argv: list[str] | None = None) -> int:
    total_started = perf_counter()
    phase_records: list[dict[str, object]] = []
    arguments = _parser().parse_args(argv)
    output = arguments.output.expanduser().resolve()
    started = perf_counter()
    _guard_existing_output(output)
    _prepare_generated_output(output, replace=True)
    _append_phase(
        phase_records,
        "output-preparation",
        "other",
        perf_counter() - started,
        additive=True,
    )
    started = perf_counter()
    _write_input_snapshots(output, selected_shell=arguments.shell)
    emulator = build_starlink_gen1_satellite_internet_emulator(output / "inputs/scenario.yaml")
    simulator = create_starlink_gen1_simulator(
        emulator, observation_interval=timedelta(seconds=arguments.observation_interval_seconds)
    )
    model_descriptors = starlink_gen1_model_descriptors(
        observation_interval=simulator.get_timeline().observation_interval
    )
    _append_phase(
        phase_records,
        "example-assembly",
        "other",
        perf_counter() - started,
        additive=True,
    )
    inspection = output / "inspection"
    results: dict[str, dict[str, object]] = {}
    records: tuple[dict[str, object], ...] = ()
    producer_snapshot = None

    started = perf_counter()
    create_starlink_gen1_planner(simulator)
    _append_phase(
        phase_records,
        "planner-assembly",
        "other",
        perf_counter() - started,
        additive=True,
    )
    observation_path = (
        inspection / "observation/execution-observations.jsonl"
    )
    simulator._enable_execution_observation(
        observation_path,
        metadata={
            "scenario_identity": "starlink_gen1_satellite_internet",
            "timeline_start": (
                simulator.get_timeline().start_time.isoformat()
            ),
            "timeline_end": simulator.get_timeline().end_time.isoformat(),
            "observation_interval_seconds": (
                simulator.get_timeline()
                .observation_interval.total_seconds()
            ),
            "through": arguments.through,
            "progression": "physical-world-planner-plan-compiler",
        },
    )
    if arguments.through == "physical-world":
        producer_snapshot, physical_frame, physical_link_observation = _run_physical_stage(
            simulator, output, phase_records=phase_records)
    elif arguments.through == "planner":
        (
            producer_snapshot,
            physical_frame,
            physical_link_observation,
        ) = _run_planner_stage(
            simulator, output, phase_records=phase_records
        )
    else:
        (
            staged_manifest,
            producer_snapshot,
            physical_frame,
            physical_link_observation,
        ) = _run_compiler_stage(
            simulator, output, phase_records=phase_records
        )
        runtime_manifest = _write_runtime_package(
            output,
            emulator=emulator,
            simulator=simulator,
            staged_manifest=staged_manifest,
            phase_records=phase_records,
        )
        print(f"Runner artifact: {runtime_manifest}")
    started = perf_counter()
    observation_failure = _write_optional_observations(simulator)
    if observation_failure is not None:
        results["observation-evidence"] = {"status": "FAILED", "message": observation_failure}
    _append_phase(
        phase_records,
        "observation-evidence-write",
        "persistence",
        perf_counter() - started,
        additive=True,
    )
    materials_seconds = perf_counter()-total_started
    _write_generation_status(output, through=arguments.through, status='MATERIALS_READY',
        materials_seconds=materials_seconds, total_seconds=materials_seconds,
        producer_snapshot=producer_snapshot, model_descriptors=model_descriptors)
    print(f'Materials ready after {materials_seconds:.3f}s: {output}', flush=True)
    results.update(
        _run_static_tools(
            simulator,
            inspection,
            physical_frame=physical_frame,
            physical_link_observation=physical_link_observation,
            physical_link_inspection_path=(
                output
                / "inspection-evidence/physical-link-observations.jsonl.gz"
            ),
            model_descriptors=model_descriptors,
            phase_records=phase_records,
        )
    )
    try:
        dynamic, records = ({}, ()) if arguments.through == "physical-world" else _run_dynamic_tools(
            output,
            through=arguments.through,
            model_descriptors=model_descriptors,
            phase_records=phase_records,
        )
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        names = ["planner", "observation"]
        if arguments.through == "compiler":
            names.append("bpf-compiler")
        dynamic = {
            name: {"status": "FAILED", "message": message}
            for name in names
        }
        records = (
            {
                "task": "inspection-batch",
                "status": "FAILED",
                "message": message,
            },
        )
    results.update(dynamic)
    finalization_started = perf_counter()
    try:
        _write_tool_records(inspection, records)
    except Exception as error:
        results["tool-execution-records"] = {
            "status": "FAILED",
            "message": f"{type(error).__name__}: {error}",
        }
    counts = dict(results.get("physical-world", {}).get("counts", {}))
    try:
        write_aggregate_index(inspection, results, counts)
    except Exception as error:
        results["aggregate"] = {
            "status": "FAILED",
            "message": f"{type(error).__name__}: {error}",
        }
    _append_phase(
        phase_records,
        "final-aggregate-and-hash-publication",
        "sealing-finalization",
        perf_counter() - finalization_started,
        additive=True,
    )
    _write_orchestration_timing(
        inspection,
        phase_records,
        total_wall_seconds=perf_counter() - total_started,
        run_origin_monotonic=total_started,
    )

    _write_generation_status(output, through=arguments.through, status=('TOOLS_FAILED' if any(r.get('status') not in {'PASS', 'COMPLETE'} for r in results.values()) else 'TOOLS_COMPLETE'),
        materials_seconds=materials_seconds, total_seconds=perf_counter()-total_started,
        producer_snapshot=producer_snapshot, tool_results=results, model_descriptors=model_descriptors)

    print(f"Inspection index: {inspection / 'index.html'}")
    if producer_snapshot is not None:
        print(
            "canonical producer: "
            f"{producer_snapshot['formal_frame_count']} frames / "
            f"{producer_snapshot['transition_count']} transitions / "
            f"{producer_snapshot['plan_count']} Plans / "
            f"{producer_snapshot['realization_count']} realizations"
        )
        if arguments.through == "compiler":
            print(f"start deployment: {output / 'start.sh'}")
            print(f"run experiment: {output / 'run.sh'}")
            print(f"launch start + run: {output / 'launch.sh'}")
            print(f"teardown deployment: {output / 'cleanup.sh'}")
    failures = sorted(
        name
        for name, result in results.items()
        if result.get("status") not in {"PASS", "COMPLETE"}
    )
    if failures:
        print(
            "Inspection side-work failures did not invalidate domain output: "
            + ", ".join(failures),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
