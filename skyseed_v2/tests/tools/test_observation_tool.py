from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_simulator,
    prepare_mini_satellite_internet_planning,
)
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.backend.bpf.compiler import _compile_bpf_observed
from skyseed_v2.shared._execution_observation import _ExecutionObserver
from tools.observation import (
    collect_observation_data,
    read_execution_observations,
    write_observation_tool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCES = tuple(sorted((PROJECT_ROOT / "tools/observation").glob("*.py")))
EXPECTED_FILES = {
    "checksums.sha256",
    "execution-observations.jsonl",
    "index.html",
    "inventory.json",
    "observation-records.js",
    "observation-summary.json",
    "summary.txt",
}


def _records(path: Path) -> Path:
    observer = _ExecutionObserver({"scenario": "observation-tool-test"})
    timestamps = {
        1: "2026-01-01T00:00:00+00:00",
        2: "2026-01-01T00:00:10+00:00",
        3: "2026-01-01T00:00:20+00:00",
    }
    for phase, duration, slice_id, routes in (
        ("bootstrap", 100.0, 1, 5),
        ("steady", 1.0, 2, 10),
        ("steady", 3.0, 3, 30),
    ):
        observer.record(
            component="slicer",
            stage="route-compute",
            phase=phase,
            duration_seconds=duration,
            scope={"slice_id": slice_id, "timestamp": timestamps[slice_id]},
            work={"route_entries": routes},
        )
    for phase, duration, slice_id in (
        ("bootstrap", 120.0, 1),
        ("steady", 2.0, 2),
        ("steady", 4.0, 3),
    ):
        observer.record(
            component="slicer",
            stage="complete-slice",
            phase=phase,
            duration_seconds=duration,
            duration_role="rollup",
            scope={"slice_id": slice_id, "timestamp": timestamps[slice_id]},
            work={"slices_published": 1},
        )
    observer.record(
        component="slicer",
        stage="difference",
        phase="steady",
        duration_seconds=0.5,
        scope={
            "previous_slice_id": 2,
            "current_slice_id": 3,
            "timestamp": timestamps[3],
        },
        work={"difference_operations": 4},
    )
    observer.record(
        component="slicer",
        stage="resource-sample",
        phase="steady",
        duration_seconds=0.0,
        duration_role="sample",
        scope={"slice_id": 3, "timestamp": timestamps[3]},
        work={"sample_units": 1},
        resources={"rss_bytes": 123_456},
    )
    observer.record(
        component="planner",
        stage="total-synchronize",
        phase="bootstrap",
        duration_seconds=7.0,
        duration_role="rollup",
        scope={
            "publication_index": 0,
            "planning_start_slice_id": 0,
            "planning_end_slice_id": 3,
            "publication_start_slice_id": 0,
            "publication_end_slice_id": 2,
            "start_timestamp": None,
            "end_timestamp": timestamps[3],
        },
        work={"planning_window_differences": 3},
    )
    return observer.write_jsonl(path)


def test_projection_separates_bootstrap_from_steady_percentiles(tmp_path) -> None:
    data = collect_observation_data(_records(tmp_path / "source.jsonl"))
    slicer = next(
        row for row in data["components"] if row["component"] == "slicer"
    )
    stage = next(
        row for row in slicer["stages"] if row["label"] == "route-compute"
    )

    assert stage["duration_role"] == "stage"
    assert stage["bootstrap_run_seconds"] == 100.0
    assert stage["steady_count"] == 2
    assert stage["steady_p50_seconds"] == 2.0
    assert stage["steady_p95_seconds"] == 3.0
    assert stage["steady_max_seconds"] == 3.0
    assert stage["run_seconds"] == 104.0
    assert stage["work_totals"] == {"route_entries": 45}
    sample = next(
        row
        for row in slicer["stages"]
        if row["label"] == "resource-sample"
    )
    assert sample["duration_role"] == "sample"
    assert sample["steady_count"] == 1
    assert sample["bootstrap_run_seconds"] is None
    assert sample["steady_p50_seconds"] is None
    assert sample["run_seconds"] is None
    assert "recorded_duration_seconds" not in data
    assert "total_seconds" not in slicer
    assert [
        (row["component"], row["label"], row["duration_role"])
        for row in data["top_level_timing_series"]
    ] == [
        ("planner", "total-synchronize", "rollup"),
        ("slicer", "complete-slice", "rollup"),
        ("slicer", "difference", "stage"),
    ]
    assert data["stage_work_measurement_names"] == [
        "difference_operations",
        "route_entries",
    ]
    assert data["timeline"] == {
        "simulation_start_timestamp": "2026-01-01T00:00:00+00:00",
        "simulation_end_timestamp": "2026-01-01T00:00:20+00:00",
        "slice_interval_seconds": 10.0,
        "slicer_unit_count": 3,
        "planner_unit_count": 1,
        "compiler_unit_count": 0,
        "x_axis_semantics": (
            "simulation timestamp aligned by Slice identity, Planner "
            "publication coverage, and its 1:1 Compiler realization"
        ),
    }
    assert data["inventory"] == {
        "observations": 9,
        "components": 2,
        "stages": 5,
        "bootstrap_observations": 3,
        "steady_observations": 6,
        "idle_observations": 0,
        "one_shot_observations": 0,
        "work_measurements": 5,
        "resource_measurements": 1,
    }


def test_writer_uses_external_records_and_deterministic_checksums(tmp_path) -> None:
    source = _records(tmp_path / "source.jsonl")
    output = tmp_path / "inspection"
    result = write_observation_tool(source, output)

    assert result["status"] == "PASS"
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES
    page = (output / "index.html").read_text()
    assert "observation-records.js" in page
    assert "__SKYSEED_EXECUTION_OBSERVATIONS__=" not in page
    assert '"duration_seconds":100.0' not in page
    assert "Slicer, Planner, Compiler, or Runner" in page
    browser_records = (output / "observation-records.js").read_text()
    assert "__SKYSEED_EXECUTION_OBSERVATIONS__=" in browser_records
    assert '"duration_seconds":100.0' in browser_records
    assert "Rows are never added into a component or pipeline total" in page
    assert 'record.duration_role==="stage"' in page
    assert '<option value="stage" selected>Exclusive leaf stages</option>' in page
    assert "Rollups overlap their child stages" in page
    assert "Slicer Difference work occurs outside complete-slice" in page
    assert "Samples carry resource evidence and have zero duration" in page
    assert '<option value="route_entries">route_entries</option>' in page
    assert '<option value="slices_published">' not in page
    assert "recorded_duration_seconds" not in (
        output / "summary.txt"
    ).read_text()


def test_page_uses_unified_logical_scope_and_non_additive_duration_ux(
    tmp_path,
) -> None:
    source = _records(tmp_path / "source.jsonl")
    output = tmp_path / "inspection"
    write_observation_tool(source, output)
    page = (output / "index.html").read_text()

    for identifier in (
        "mode-progression",
        "mode-unit",
        "unit-component",
        "unit-selector",
        "unit-summary",
        "unit-decomposition",
        "unit-rollups",
        "unit-independent",
        "fit-selection",
        "role-filter",
        "clear-annotation",
        "persistent-annotation",
    ):
        assert identifier in page
    assert "Stage progression" in page
    assert "Per-Slice / Plan breakdown" in page
    assert "unit-scrubber-track" in page
    assert "unit-heatmap" in page
    assert "HEATMAP_MAX_COLUMNS=64" in page
    assert "Missing means no observation record" in page
    assert "exact measured zero" in page
    assert "Unattributed / gap" in page
    assert "publication (" in page
    assert "Difference S" in page
    assert "formal Slice interval" in page
    assert "shared Slicer/Planner duration scale" in page
    assert "niceStep" in page
    assert "recordPosition(record)" in page
    assert "record.sequence" not in page.split(
        "function recordPosition(record)", 1
    )[1].split("function filteredRecords", 1)[0]
    assert "isIndependent(record)" in page
    assert "The subtotal excludes all nested/overlapping rollups" in page
    assert "They are never summed with child stages" in page
    assert "function stageHue(stage)" in page
    assert "heatColor(stage,duration" in page
    assert "stageColor(record.stage,1)" in page
    assert "share>=9" in page
    assert "Short segments intentionally omit internal text" in page
    assert "decomposition-legend" in page

    checksums = (output / "checksums.sha256").read_text().splitlines()
    for line in checksums:
        expected, relative = line.split("  ", 1)
        assert sha256((output / relative).read_bytes()).hexdigest() == expected
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    write_observation_tool(source, output)
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}


def test_tool_sources_do_not_import_or_execute_domain_components() -> None:
    combined = "\n".join(path.read_text() for path in TOOL_SOURCES)
    assert "skyseed_v2.simulator" not in combined
    assert ".create_next_slice(" not in combined
    assert ".create_difference(" not in combined
    assert ".synchronize(" not in combined
    assert "compile_bpf(" not in combined


def test_reader_rejects_sequence_or_format_drift(tmp_path) -> None:
    source = _records(tmp_path / "source.jsonl")
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    rows[1]["sequence"] = 9
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="sequence"):
        read_execution_observations(source)


def test_mini_execution_emits_slicer_and_planner_stage_and_work_evidence(
    tmp_path,
) -> None:
    simulator = create_mini_satellite_internet_simulator(
        build_mini_satellite_internet_emulator()
    )
    path = tmp_path / "mini-observations.jsonl"
    simulator._enable_execution_observation(
        path,
        metadata={"scenario_identity": "mini-observation-test"},
    )
    slicer = simulator.get_slicer()
    slicer.create_all_slices()
    planner, plans, _ = prepare_mini_satellite_internet_planning(simulator)
    simulator._write_execution_observations()
    observations = read_execution_observations(path)

    by_component = {
        component: [
            record
            for record in observations.records
            if record["component"] == component
        ]
        for component in ("slicer", "planner")
    }
    slicer_stages = {record["stage"] for record in by_component["slicer"]}
    planner_stages = {record["stage"] for record in by_component["planner"]}
    assert {
        "position-compute",
        "position-validation",
        "connection-preparation",
        "connection-compute",
        "connection-validation",
        "route-compute",
        "route-validation",
        "slice-publication",
        "complete-slice",
        "difference",
    } <= slicer_stages
    assert {
        "difference-acquisition",
        "window-selection",
        "route-context",
        "connection-planning",
        "connection-output-validation",
        "parameter-planning",
        "parameter-output-validation",
        "forwarding-planning",
        "forwarding-output-validation",
        "suppression-candidate-discovery",
        "coordination-policy",
        "coordination-output-validation",
        "coordination",
        "forwarding-precedence",
        "state-advancement",
        "validation",
        "publication-plan-construction",
        "publication-state-advancement",
        "publication-evidence-persistence",
        "publication-state-commit",
        "publication",
        "total-synchronize",
    } <= planner_stages
    complete_slices = [
        record
        for record in by_component["slicer"]
        if record["stage"] == "complete-slice"
    ]
    total_synchronizations = [
        record
        for record in by_component["planner"]
        if record["stage"] == "total-synchronize"
    ]
    assert len(complete_slices) == 64
    assert [record["phase"] for record in complete_slices].count(
        "bootstrap"
    ) == 1
    assert len(total_synchronizations) == len(plans) == 16
    assert total_synchronizations[0]["phase"] == "bootstrap"
    assert all(
        record["phase"] == "steady"
        for record in total_synchronizations[1:]
    )
    rollups = complete_slices + total_synchronizations + [
        record
        for record in by_component["planner"]
        if record["stage"] in {"coordination", "validation", "publication"}
    ]
    assert all(
        record["duration_role"] == "rollup"
        for record in rollups
    )
    assert all(
        record["duration_role"] == "stage"
        for records in by_component.values()
        for record in records
        if record["stage"]
        not in {
            "complete-slice",
            "coordination",
            "validation",
            "publication",
            "total-synchronize",
            "resource-sample",
            "retained-state-sample",
        }
    )
    candidate_discovery = next(
        record
        for record in by_component["planner"]
        if record["stage"] == "suppression-candidate-discovery"
    )
    assert "suppression_candidates" in candidate_discovery["work"]
    assert sum(
        record["work"]["published_differences"]
        for record in total_synchronizations
    ) == len(planner.get_consumed_differences()) == 64
    precedence = next(
        record
        for record in by_component["planner"]
        if record["stage"] == "forwarding-precedence"
    )
    assert precedence["work"]["precedence_candidate_pairs"] >= (
        precedence["work"]["emitted_precedences"]
    )
    route_context = next(
        record
        for record in by_component["planner"]
        if record["stage"] == "route-context"
    )
    assert route_context["work"]["route_entries_processed"] > 10_000
    assert route_context["work"]["path_edges_processed"] > 10_000


def test_compiler_joins_unified_observation_as_one_shot_plan_units() -> None:
    emulator = build_mini_satellite_internet_emulator()
    simulator = create_mini_satellite_internet_simulator(emulator)
    slicer = simulator.get_slicer()
    slicer.create_all_slices()
    _, plans, _ = prepare_mini_satellite_internet_planning(simulator)
    selected_plans = plans[:2]
    observer = _ExecutionObserver({"scenario": "mini-compiler-observation"})
    observed = []
    previous = None
    for compile_index, plan in enumerate(selected_plans):
        result = _compile_bpf_observed(
            plan,
            slicer=slicer,
            previous=previous,
            execution_observer=observer,
            compile_index=compile_index,
        )
        observed.append(result)
        previous = result

    plain = []
    previous = None
    for plan in selected_plans:
        result = compile_bpf(plan, slicer=slicer, previous=previous)
        plain.append(result)
        previous = result
    assert tuple(observed) == tuple(plain)

    compiler_records = tuple(
        value
        for value in observer.snapshot()["records"]
        if value["component"] == "compiler"
    )
    assert {value["phase"] for value in compiler_records} == {"one-shot"}
    assert {
        value["scope"]["compile_index"] for value in compiler_records
    } == {0, 1}
    assert {
        "input-validation",
        "slice-coverage",
        "temporal-source-indexing",
        "topology-preparation",
        "baseline-initialization",
        "plan-transition-validation",
        "resource-parameter-lowering",
        "forwarding-lowering",
        "zone-edge-lowering",
        "constraint-compilation",
        "end-state-validation",
        "realization-freeze",
        "total-compile",
    } <= {value["stage"] for value in compiler_records}
    rollups = [
        value for value in compiler_records if value["stage"] == "total-compile"
    ]
    assert len(rollups) == 2
    assert all(value["duration_role"] == "rollup" for value in rollups)
    assert all(value["work"]["baseline_records"] > 0 for value in rollups)


@pytest.mark.parametrize("poison_observer", (False, True))
def test_execution_observation_does_not_change_mini_formal_results(
    tmp_path,
    poison_observer: bool,
) -> None:
    baseline = create_mini_satellite_internet_simulator(
        build_mini_satellite_internet_emulator()
    )
    observed = create_mini_satellite_internet_simulator(
        build_mini_satellite_internet_emulator()
    )
    path = tmp_path / f"observed-{poison_observer}.jsonl"
    observer = observed._enable_execution_observation(path)
    if poison_observer:
        observer.record(
            component="slicer",
            stage="forced-recording-failure",
            phase="steady",
            duration_seconds=-1.0,
        )

    baseline_slices = baseline.get_slicer().create_all_slices()
    observed_slices = observed.get_slicer().create_all_slices()
    baseline_planner, baseline_plans, _ = (
        prepare_mini_satellite_internet_planning(baseline)
    )
    observed_planner, observed_plans, _ = (
        prepare_mini_satellite_internet_planning(observed)
    )

    assert observed_slices == baseline_slices
    assert observed_plans == baseline_plans
    assert (
        observed_planner.get_consumed_differences()
        == baseline_planner.get_consumed_differences()
    )
    assert (
        observed_planner.get_planning_decisions()
        == baseline_planner.get_planning_decisions()
    )
    if poison_observer:
        assert observer._get_recording_failure() is not None
        with pytest.raises(RuntimeError, match="observation is incomplete"):
            observed._write_execution_observations()
    else:
        observed._write_execution_observations()
        assert read_execution_observations(path).records
