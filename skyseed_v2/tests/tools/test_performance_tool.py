from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator
from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
from tests._support import drain_simulator as run_complete_static_chain
from skyseed_v2.backend.bpf.compiler import _compile_bpf_observed
from skyseed_v2.experiment import execution
from skyseed_v2.backend.bpf.execution_materials.sealing import _write_runner_artifact
from tools.traffic.active.probe_plan import (
    TrafficProbePlanBuilder,
    write_traffic_probe_plan,
)
from skyseed_v2.experiment.output.observation import _ExecutionObserver
from tools.performance import (
    collect_performance_data,
    read_execution_observations,
    write_performance_tool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCES = tuple(sorted((PROJECT_ROOT / "tools/performance").glob("*.py")))


@pytest.mark.parametrize("with_planner", [False, True])
def test_prediction_queries_do_not_become_prehistory_or_formal_transitions(with_planner):
    from datetime import datetime, timedelta, timezone
    from tools.performance.projection import _timeline_summary

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = [dict(component="physical-world", stage="physical-projection",
                    scope={"timestamp": (start+timedelta(seconds=seconds)).isoformat()}, work={})
               for seconds in (-5, -2.5, 0, 2.5, 5, 7.5, 10, 12.5, 15)]
    if with_planner:
        records.extend(dict(component="planner", stage="planning-total",
                            scope={"timestamp": (start+timedelta(seconds=seconds)).isoformat()},
                            work={"formal_transitions": 1}) for seconds in (0, 5, 10))
    value = _timeline_summary(tuple(records), {"timeline_start": start.isoformat(),
        "timeline_end": (start+timedelta(seconds=10)).isoformat(), "observation_interval_seconds": 5})
    assert value["physical_frame_unit_count"] == 3
    assert value["prehistory_physical_frame_unit_count"] == 1
    assert value["additional_physical_query_unit_count"] == 5
    assert value["formal_transition_unit_count"] == (3 if with_planner else 0)
    assert value["simulation_start_timestamp"] == start.isoformat()
    assert value["simulation_end_timestamp"] == (start+timedelta(seconds=10)).isoformat()


def _records(path: Path) -> Path:
    observer = _ExecutionObserver(
        {"scenario": "observation-tool-test", "formal_interval_seconds": 5}
    )
    timestamps = (
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:05+00:00",
    )
    for timestamp in timestamps:
        observer.record(
            component="physical-world",
            stage="physical-projection",
            phase="one-shot",
            duration_seconds=2.0,
            duration_role="rollup",
            scope={"timestamp": timestamp},
            work={"positions": 3},
            resources={"rss_bytes": 100},
        )
        observer.record(
            component="physical-link",
            stage="physical-link-observation",
            phase="one-shot",
            duration_seconds=0.02,
            scope={"timestamp": timestamp},
            work={"pairs_evaluated": 3},
        )
    stages = (
        "physical-input-validation",
        "isl-candidate-selection",
        "acquisition/lifecycle-advancement",
        "ground-station-planning",
        "zone-planning",
        "parameter-planning",
        "route-planning",
        "coordination",
        "state-advancement",
    )
    for index, timestamp in enumerate(timestamps):
        phase = "bootstrap" if index == 0 else "steady"
        for stage_index, stage in enumerate(stages):
            observer.record(
                component="planner",
                stage=stage,
                phase=phase,
                duration_seconds=0.01 * (stage_index + index + 1),
                duration_role=(
                    "rollup"
                    if stage == "ground-station-planning"
                    else "stage"
                ),
                scope={
                    "timestamp": timestamp,
                },
                work={"items": stage_index + 1},
            )
        for stage in (
            "ground-station-selection-and-ranking",
            "ground-station-validation",
            "ground-station-evidence-projection",
        ):
            observer.record(
                component="planner",
                stage=stage,
                phase=phase,
                duration_seconds=0.005,
                scope={"timestamp": timestamp},
                work={"items": 1},
            )
        observer.record(
            component="planner",
            stage="planning-total",
            phase=phase,
            duration_seconds=0.5,
            duration_role="rollup",
            scope={"timestamp": timestamp},
            work={"formal_transitions": int(index > 0)},
        )
        if index > 0:
            for stage, role, duration in (
                ("plan-creation", "stage", 0.04),
                ("publication-total", "rollup", 0.08),
            ):
                observer.record(
                    component="planner",
                    stage=stage,
                    phase=phase,
                    duration_seconds=duration,
                    duration_role=role,
                    scope={
                        "plan_id": f"plan-{index}",
                        "start_timestamp": timestamp,
                        "end_timestamp": timestamp,
                    },
                    work={"plans": 1, "transitions": 1},
                )
    for stage, role, duration in (
        ("plan-lineage", "stage", 0.1),
        ("semantic-to-bpf-lowering", "stage", 0.2),
        ("compile-total", "rollup", 0.35),
    ):
        observer.record(
            component="compiler",
            stage=stage,
            phase="steady",
            duration_seconds=duration,
            duration_role=role,
            scope={"plan_id": "plan-1", "timestamp": timestamps[1]},
            work={"activation_boundaries": 1},
        )
    for component, stage in (
        ("realization-producer", "producer-total"),
        ("runner-artifact-writer", "writer-total"),
        ("runner", "runner-total"),
        ("traffic", "traffic-total"),
    ):
        observer.record(
            component=component,
            stage=stage,
            phase="steady",
            duration_seconds=0.4,
            duration_role="rollup",
            scope={"timestamp": timestamps[1], "invocation_index": 1},
            work={"input_bytes": 10, "output_bytes": 20},
            resources={"rss_bytes": 200},
        )
    return observer.write_jsonl(path)


def _percentile_records(path: Path) -> Path:
    observer = _ExecutionObserver({"scenario": "percentile-proof"})
    for phase, duration, route_entries in (
        ("bootstrap", 100.0, 5),
        ("steady", 1.0, 10),
        ("steady", 3.0, 30),
    ):
        observer.record(
            component="planner",
            stage="route-planning",
            phase=phase,
            duration_seconds=duration,
            work={"route_entries": route_entries},
        )
    observer.record(
        component="planner",
        stage="resource-sample",
        phase="steady",
        duration_seconds=0.0,
        duration_role="sample",
        work={"sample_units": 1},
        resources={"rss_bytes": 123_456},
    )
    return observer.write_jsonl(path)


def test_projection_uses_actual_pipeline_groups_and_duration_roles(tmp_path) -> None:
    data = collect_performance_data(_records(tmp_path / "records.jsonl"))
    groups = {value["label"]: value for value in data["pipeline_groups"]}

    for label in (
        "physical projection",
        "physical-link observation",
        "ISL selection",
        "acquisition / lifecycle",
        "GroundStation planning",
        "GroundStation selection / ranking",
        "GroundStation validation",
        "GroundStation evidence projection",
        "Zone planning",
        "parameter planning",
        "Route planning",
        "semantic transition / application",
        "coordination / precedence",
        "Plan publication",
        "Compiler",
        "realization producer",
        "Runner artifact writer",
        "Runner",
        "Traffic",
    ):
        assert groups[label]["observations"] > 0
    planner = next(
        value for value in data["components"] if value["component"] == "planner"
    )
    route = next(
        value for value in planner["stages"] if value["label"] == "route-planning"
    )
    assert route["bootstrap_run_seconds"] == 0.07
    assert route["steady_p50_seconds"] == 0.08
    assert route["work_totals"] == {"items": 14}
    assert data["timeline"]["formal_transition_unit_count"] == 1
    assert data["timeline"]["physical_frame_unit_count"] == 1
    assert data["timeline"]["prehistory_physical_frame_unit_count"] == 1
    assert data["timeline"]["plan_publication_unit_count"] == 1
    assert data["timeline"]["compiler_realization_unit_count"] == 1
    assert data["timeline"]["formal_interval_seconds"] == 5.0
    assert {row["duration_role"] for row in data["top_level_timing_series"]} == {
        "rollup"
    }
    assert {row["label"] for row in data["ground_station_timing"]} == {
        "ground-station-planning",
        "ground-station-selection-and-ranking",
        "ground-station-validation",
        "ground-station-evidence-projection",
    }


def test_execution_timeline_uses_exact_span_union_and_overlap_wall(
    tmp_path,
) -> None:
    observer = _ExecutionObserver({"scenario": "span-overlap-proof"})
    origin = observer._run_origin_monotonic
    for component, stage, role, start, end in (
        ("planner", "planning-total", "rollup", 1.0, 5.0),
        ("planner", "ground-station-planning", "stage", 2.0, 4.0),
        ("compiler", "compile-total", "rollup", 3.0, 6.0),
    ):
        observer.record(
            component=component,
            stage=stage,
            phase="steady",
            duration_seconds=end - start,
            duration_role=role,
            started_monotonic=origin + start,
            ended_monotonic=origin + end,
        )
    data = collect_performance_data(
        observer.write_jsonl(tmp_path / "span-overlap.jsonl")
    )
    timeline = data["execution_timeline"]
    statistics = timeline["statistics"]

    assert timeline["available"] is True
    assert timeline["start_seconds"] == pytest.approx(1.0)
    assert timeline["end_seconds"] == pytest.approx(6.0)
    assert statistics["busy_union_seconds"] == pytest.approx(5.0)
    assert statistics["overlap_seconds"] == pytest.approx(3.0)
    assert statistics["overlap_ratio"] == pytest.approx(0.6)
    assert statistics["cross_component_overlap_seconds"] == pytest.approx(2.0)
    assert statistics["cross_component_overlap_ratio"] == pytest.approx(0.4)
    assert statistics["maximum_active_distinct_components"] == 2
    assert statistics["excess_span_seconds"] == pytest.approx(4.0)
    assert statistics["maximum_concurrency"] == 3
    assert statistics["idle_gap_seconds"] == 0.0
    assert statistics["component_active_wall_seconds"] == pytest.approx(
        {"compiler": 3.0, "planner": 4.0}
    )
    assert all(
        "work" in span and "resources" in span
        for lane in timeline["lanes"]
        for span in lane["spans"]
    )


def test_projection_separates_bootstrap_from_steady_percentiles(tmp_path) -> None:
    data = collect_performance_data(
        _percentile_records(tmp_path / "percentiles.jsonl")
    )
    planner = next(
        value for value in data["components"] if value["component"] == "planner"
    )
    stage = next(
        value for value in planner["stages"] if value["label"] == "route-planning"
    )
    assert stage == {
        "label": "route-planning",
        "duration_role": "stage",
        "observations": 3,
        "bootstrap_count": 1,
        "bootstrap_run_seconds": 100.0,
        "steady_count": 2,
        "steady_p50_seconds": 2.0,
        "steady_p95_seconds": 3.0,
        "steady_max_seconds": 3.0,
        "one_shot_count": 0,
        "one_shot_p50_seconds": None,
        "one_shot_p95_seconds": None,
        "one_shot_max_seconds": None,
        "run_seconds": 104.0,
        "work_totals": {"route_entries": 45},
        "resource_maxima": {},
    }
    sample = next(
        value
        for value in planner["stages"]
        if value["label"] == "resource-sample"
    )
    assert sample["duration_role"] == "sample"
    assert sample["steady_count"] == 1
    assert sample["bootstrap_run_seconds"] is None
    assert sample["steady_p50_seconds"] is None
    assert sample["run_seconds"] is None
    assert sample["resource_maxima"] == {"rss_bytes": 123_456}
    assert "recorded_duration_seconds" not in data
    assert "total_seconds" not in planner


def test_writer_retains_external_raw_records_and_checksums(tmp_path) -> None:
    source = _records(tmp_path / "records.jsonl")
    output = tmp_path / "output"
    result = write_performance_tool(source, output)

    assert result["status"] == "PASS"
    assert {value.name for value in output.iterdir()} == {
        "checksums.sha256",
        "source-references.json",
        "index.html",
        "inventory.json",
        "performance-records.js",
        "performance-summary.json",
        "summary.txt",
    }
    page = (output / "index.html").read_text()
    assert "performance-records.js" in page
    assert "Raw invocation records" in page
    assert "Rollups overlap their child stages" in page
    assert "RSS, input, output" in page
    browser = (output / "performance-records.js").read_text()
    assert "__SKYSEED_EXECUTION_OBSERVATIONS__=" in browser
    assert "__SKYSEED_EXECUTION_OBSERVATIONS__=" not in page
    assert '"duration_seconds":2.0' in browser
    for line in (output / "checksums.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        assert sha256((output / relative).read_bytes()).hexdigest() == digest
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    write_performance_tool(source, output)
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}


def test_page_exposes_detached_scope_filters_and_non_additive_duration_ux(
    tmp_path,
) -> None:
    output = tmp_path / "output"
    write_performance_tool(_records(tmp_path / "records.jsonl"), output)
    page = (output / "index.html").read_text(encoding="utf-8")

    for identifier in (
        'id="component-filter"',
        'id="role-filter"',
        'id="raw-count"',
        'id="raw-records"',
    ):
        assert identifier in page
    for heading in (
        "Detached execution evidence",
        "Formal progression",
        "Actual pipeline groups",
        "Independent top-level timing series",
        "GroundStation timing decomposition",
        "Stage timing, work, and resources",
        "Raw invocation records",
    ):
        assert heading in page
    assert "Rollups overlap their child stages and are never added to them" in page
    assert "Samples carry resource evidence with zero duration" in page
    assert '<option value="stage">stage</option>' in page
    assert '<option value="rollup">rollup</option>' in page
    assert '<option value="sample">sample</option>' in page
    assert "source.records.filter" in page
    assert 'recordScript.src="performance-records.js"' in page
    assert "Unable to load raw records" in page
    assert "exact detached rows" in page
    assert 'id="timeline-reset"' in page
    assert 'id="timeline-focus-selected"' in page
    assert "cross-component overlap ratio" in page
    assert "raw span overlap including nested rollups" in page
    assert "wheel: cursor zoom" in page
    assert '<details class="card"><summary><strong>Advanced timing statistics' in page
    assert '<details class="card" id="raw-invocation-evidence">' in page


@pytest.mark.parametrize(
    ("row_index", "field", "value", "message"),
    (
        (0, "format_version", 3, "format version"),
        (1, "sequence", 10, "sequence"),
        (1, "duration_role", "aggregate", "duration_role"),
    ),
)
def test_reader_rejects_format_sequence_or_role_drift(
    tmp_path,
    row_index,
    field,
    value,
    message,
) -> None:
    source = _records(tmp_path / "records.jsonl")
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    rows[row_index][field] = value
    source.write_text("\n".join(json.dumps(value) for value in rows) + "\n")
    with pytest.raises(ValueError, match=message):
        read_execution_observations(source)


def test_observation_sources_do_not_import_or_execute_domain_components() -> None:
    combined = "\n".join(path.read_text() for path in TOOL_SOURCES)
    assert "skyseed_v2.simulator" not in combined
    assert "compute_observation(" not in combined
    assert "plan_route_outcomes(" not in combined
    assert "compile_bpf(" not in combined


def test_real_pipeline_and_runtime_wrapper_emit_owned_observation_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "output"
    observation_path = (
        output / "results/experiment/generation-performance.jsonl"
    )
    simulator = create_complete_static_simulator(build_emulator())
    _bind_observer(simulator, {
            "scenario_identity": "round-b-observation-proof",
            "formal_interval_seconds": 10,
        })
    plans, realizations, _snapshot = run_complete_static_chain(simulator)
    simulator.get_planner()._get_execution_observer().write_jsonl(observation_path)
    manifest = _write_runner_artifact(
        realizations,
        output / "results/compiler/runner-artifact",
    )
    write_traffic_probe_plan(
        TrafficProbePlanBuilder().build(plans, realizations),
        output / "results/traffic/probe-plan.json",
    )

    class _Runner:
        def __init__(self, **facts) -> None:
            self._artifact = facts["runtime_artifact"]
            self._state = "new"

        def _enable_event_history(self, _path) -> None:
            pass

        def _enable_traffic_telemetry(self, _path) -> None:
            pass

        def prepare(self) -> None:
            self._state = "prepared"

        def run(self) -> None:
            self._state = "completed"

        def cleanup(self) -> None:
            self._state = "cleaned"

        def inspection_snapshot(self):
            return {
                "runner": {
                    "lifecycle_state": self._state,
                    "artifact": {"boundary_count": 6},
                }
            }

    class _Traffic:
        def __init__(self, **_facts) -> None:
            pass

        def start(self, _runner) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(execution, "Runner", _Runner)
    monkeypatch.setattr("tools.traffic.active.probe_execution.TrafficProbeExecutor", _Traffic)
    monkeypatch.setattr(
        execution,
        "_read_and_verify_execution",
        lambda _output: ({
            "format_version": 3,
            "runner": {"execution_contract": "sealed-precomputed-only"},
            "runner_artifact": {
                "manifest": str(manifest.relative_to(output)),
            },
            "generated_output_identity": "observation-runtime-proof",
        }, manifest, {"realization_count": 2, "boundary_count": 6}),
    )

    invocation = output / "runs/observation-test"
    execution._run_generated_bpf_experiment(output, invocation_directory=invocation)

    # Phase 2 stores merged runtime observations under their owning invocation.
    observation_path = invocation / "performance/execution-observations.jsonl"
    observations = read_execution_observations(observation_path)
    by_component = {
        component: tuple(
            value
            for value in observations.records
            if value["component"] == component
        )
        for component in (
            "physical-world",
            "physical-link",
            "planner",
            "compiler",
            "realization-producer",
            "runner",
            "traffic",
        )
    }
    assert all(by_component.values())
    assert {
        "position-compute",
        "physical-frame-publication",
        "physical-projection",
    } <= {value["stage"] for value in by_component["physical-world"]}
    assert {value["stage"] for value in by_component["physical-link"]} == {
        "physical-link-observation", "candidate-search", "candidate-search-total"
    }
    assert {
        "physical-input-validation",
        "isl-candidate-selection",
        "acquisition/lifecycle-advancement",
        "ground-station-planning",
        "zone-planning",
        "parameter-planning",
        "route-planning",
        "coordination",
        "state-advancement",
        "plan-creation",
        "publication-total",
        "planning-total",
    } <= {value["stage"] for value in by_component["planner"]}
    assert {value["stage"] for value in by_component["compiler"]} == {
        "plan-lineage",
        "event-and-world-indexing",
        "transition-lowering",
        "constraint-folding-and-materialization",
        "backend-state-freeze",
        "semantic-to-bpf-lowering",
        "compile-total",
    }
    assert {value["stage"] for value in by_component["realization-producer"]} == {
        "producer-total"
    }
    assert {
        (value["component"], value["stage"], value["phase"], value["duration_role"])
        for component in ("runner", "traffic")
        for value in by_component[component]
    } == {
        ("runner", "runner-total", "one-shot", "rollup"),
        ("traffic", "traffic-total", "one-shot", "rollup"),
    }
    data = collect_performance_data(observation_path)
    assert data["timeline"]["formal_transition_unit_count"] == 4
    assert data["timeline"]["physical_frame_unit_count"] == 4
    assert data["timeline"]["plan_publication_unit_count"] == len(plans) == 1
    assert data["timeline"]["compiler_realization_unit_count"] == (
        len(realizations)
    ) == 1
    handoff = json.loads(
        (invocation / "runner/runner-invocation.json").read_text(
            encoding="utf-8"
        )
    )
    assert handoff["runtime_outcome"]["status"] == "completed"
    assert handoff["runner_snapshot"]["runner"]["lifecycle_state"] == "cleaned"
    assert handoff["runner_snapshot"]["runner"]["artifact"]["boundary_count"] == 6


def test_compiler_observation_is_one_shot_and_semantically_inert() -> None:
    simulator = create_complete_static_simulator(build_emulator())
    plans, expected, _snapshot = run_complete_static_chain(simulator)
    observer = _ExecutionObserver({"scenario": "compiler-observation-proof"})
    observed = []
    previous = None
    for compile_index, plan in enumerate(plans):
        realization = _compile_bpf_observed(
            plan,
            simulator.get_physical_world(),
            previous,
            execution_observer=observer,
            compile_index=compile_index,
        )
        observed.append(realization)
        previous = realization

    assert tuple(observed) == expected
    records = tuple(
        value
        for value in observer.snapshot()["records"]
        if value["component"] == "compiler"
    )
    assert len(records) == len(plans) * 7
    assert {value["phase"] for value in records} == {"one-shot"}
    assert {value["scope"]["compile_index"] for value in records} == set(range(len(plans)))
    assert {value["stage"] for value in records} == {
        "plan-lineage",
        "event-and-world-indexing",
        "transition-lowering",
        "constraint-folding-and-materialization",
        "backend-state-freeze",
        "semantic-to-bpf-lowering",
        "compile-total",
    }
    totals = tuple(
        value for value in records if value["stage"] == "compile-total"
    )
    assert len(totals) == len(plans)
    assert all(value["duration_role"] == "rollup" for value in totals)
    assert all(value["work"]["activation_boundaries"] > 0 for value in totals)


@pytest.mark.parametrize("poison_observer", (False, True))
def test_execution_observation_does_not_change_formal_results(
    tmp_path,
    poison_observer: bool,
) -> None:
    baseline = create_complete_static_simulator(build_emulator())
    observed = create_complete_static_simulator(build_emulator())
    path = tmp_path / f"observed-{poison_observer}.jsonl"
    observer = _bind_observer(observed, None)
    if poison_observer:
        observer.record(
            component="",
            stage="forced-recording-failure",
            phase="steady",
            duration_seconds=0.0,
        )

    baseline_plans, baseline_realizations, baseline_snapshot = (
        run_complete_static_chain(baseline)
    )
    observed_plans, observed_realizations, observed_snapshot = (
        run_complete_static_chain(observed)
    )

    assert observed_plans == baseline_plans
    assert observed_realizations == baseline_realizations
    assert observed_snapshot == baseline_snapshot
    if poison_observer:
        assert observer._get_recording_failure() is not None
        with pytest.raises(RuntimeError, match="observation is incomplete"):
            observer.write_jsonl(path)
        assert not path.exists()
    else:
        observer.write_jsonl(path)
        assert read_execution_observations(path).records


def _bind_observer(simulator, metadata=None):
    from skyseed_v2.experiment.output.observation import _ExecutionObserver
    observer = _ExecutionObserver(metadata)
    simulator._set_execution_observer(observer)
    return observer


def test_generation_runtime_and_reset_keep_independent_time_bases(tmp_path):
    from tools.performance import project_performance
    generation = tmp_path/'generation.jsonl'
    runtime = tmp_path/'runtime.jsonl'
    reset = tmp_path/'reset.jsonl'
    def record(path, metadata, component, start, end):
        header = {'record_type': 'execution_observation_header', 'format_version': 2, 'metadata': metadata}
        row = {'record_type':'execution_observation', 'sequence':0, 'component':component,
               'stage':'test', 'phase':'one-shot', 'duration_role':'stage', 'duration_seconds':end-start,
               'run_relative_start_seconds':start, 'run_relative_end_seconds':end,
               'span_source':'measured-monotonic', 'scope':{}, 'work':{}, 'resources':{}}
        path.write_text(json.dumps(header)+'\n'+json.dumps(row)+'\n')
    record(generation, {}, 'compiler', 0, 100)
    record(runtime, {'generation_reference':'generation.jsonl','invocation_id':'first'}, 'runner', 0, 5)
    record(reset, {'generation_reference':'generation.jsonl','invocation_id':'reset'}, 'runner', 0, 2)
    before = generation.read_bytes()
    for path, duration in ((runtime, 5), (reset, 2)):
        read = read_execution_observations(path)
        data = project_performance(read)
        assert not data['execution_timeline']['available']
        timelines = data['execution_timelines']
        assert [v['span_seconds'] for v in timelines] == [100, duration]
        assert all(v['statistics']['cross_component_overlap_seconds'] == 0 for v in timelines)
        assert {r['source_id'] for r in read.records} == {str(generation), str(path)}
        output = tmp_path/path.stem
        write_performance_tool(path, output)
        assert not (output/'execution-observations.jsonl').exists()
        assert len(json.loads((output/'source-references.json').read_text())['sources']) == 2
        assert generation.read_bytes() == before
    assert read_execution_observations(runtime).sources[-1]['source_id'] != read_execution_observations(reset).sources[-1]['source_id']
