from __future__ import annotations

import inspect
from pathlib import Path
from subprocess import run
import sys

import pytest

from examples.complete_static_emulator import main as complete
from examples.mini_satellite_internet import main as mini
from examples.mini_satellite_internet import simu as mini_simu
from examples.starlink_gen1_satellite_internet import main as starlink
from examples.starlink_gen1_satellite_internet import simu as starlink_simu
from skyseed_v2.compose.launcher import (
    _prepare_generated_output,
    _write_compose_project_scripts,
)
from skyseed_v2.experiment.execution import _write_generated_run_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = PROJECT_ROOT / "examples"
FORMAL_EXAMPLES = (
    "complete_static_emulator",
    "mini_satellite_internet",
    "starlink_gen1_satellite_internet",
)
RETIRED_SYMBOLS = (
    "Slicer",
    "TopologySlice",
    "TopologyDifference",
    "ConnectionSlice",
    "RouteSlice",
    "simulator.slicing",
    "tools.slice",
    "--through slicer",
)


def test_formal_examples_have_one_visible_output_root_and_current_readme() -> None:
    readme = (EXAMPLES_ROOT / "README.md").read_text(encoding="utf-8")
    for name in FORMAL_EXAMPLES:
        directory = EXAMPLES_ROOT / name
        assert directory.is_dir()
        assert (directory / "main.py").is_file()
        assert (directory / "simu.py").is_file()
        assert f"`{name}`" in readme
    for phrase in (
        "PhysicalWorld",
        "PhysicalLinkObservation",
        "Planner",
        "Plan",
        "Compiler",
    ):
        assert phrase in readme


@pytest.mark.parametrize("name", FORMAL_EXAMPLES)
def test_formal_example_main_supports_direct_help(name: str) -> None:
    completed = run(
        (
            sys.executable,
            str(EXAMPLES_ROOT / name / "main.py"),
            "--help",
        ),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "usage:" in completed.stdout


@pytest.mark.parametrize("module", (complete, mini, starlink))
def test_default_output_is_owned_by_the_example_source_directory(module) -> None:
    assert module.DEFAULT_OUTPUT == module.EXAMPLE_ROOT / "output"
    assert module.DEFAULT_OUTPUT.is_absolute()


@pytest.mark.parametrize("name", FORMAL_EXAMPLES)
def test_formal_example_supports_help_from_its_own_directory(name: str) -> None:
    completed = run(
        (sys.executable, "main.py", "--help"),
        cwd=EXAMPLES_ROOT / name,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "--output" in completed.stdout


def test_starlink_cli_exposes_only_final_domain_boundaries() -> None:
    parser = starlink._parser()
    for stage in ("physical-world", "planner", "compiler"):
        assert parser.parse_args(["--through", stage]).through == stage
    with pytest.raises(SystemExit):
        parser.parse_args(["--through", "slicer"])
    source = inspect.getsource(starlink)
    assert "_IncrementalRealizationProducer(" in source
    assert "produce_next_plan()" in source
    assert "iter_timestamps(" not in source
    assert "_prime_planner" not in source
    assert "compile_bpf(" not in source


def test_mini_and_starlink_policy_inputs_remain_explicit() -> None:
    assert mini_simu.DEFAULT_OBSERVATION_INTERVAL.total_seconds() == 10
    assert mini_simu.PLAN_BATCH_FRAMES == 32
    assert mini_simu.ACQUISITION_DURATION.total_seconds() == 5
    assert mini_simu.FORMAL_BOUNDARY_COUNT == 64

    assert starlink_simu.STARLINK_GEN1_OBSERVATION_INTERVAL.total_seconds() == 5
    assert starlink_simu.STARLINK_GEN1_PLAN_BATCH_FRAMES == 32
    assert starlink_simu.STARLINK_GEN1_ACQUISITION_DURATION.total_seconds() == 5
    assert starlink_simu.STARLINK_GEN1_FORMAL_BOUNDARY_COUNT == 121


def test_maintained_example_sources_contain_no_retired_architecture() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for name in FORMAL_EXAMPLES
        for path in sorted((EXAMPLES_ROOT / name).glob("*.py"))
    )
    for retired in RETIRED_SYMBOLS:
        assert retired not in sources
    assert "slice_interval" not in sources
    assert "slice_id" not in sources


def test_precomputed_examples_use_the_one_incremental_producer() -> None:
    complete_source = (
        EXAMPLES_ROOT / "complete_static_emulator/simu.py"
    ).read_text(encoding="utf-8")
    mini_source = (
        EXAMPLES_ROOT / "mini_satellite_internet/simu.py"
    ).read_text(encoding="utf-8")
    starlink_source = inspect.getsource(starlink)
    for source in (complete_source, mini_source, starlink_source):
        assert "_IncrementalRealizationProducer(" in source
    assert "_RollingRealizationSupplier" not in starlink_source


def test_runner_artifact_precedes_best_effort_observation_and_tools() -> None:
    # Inspect the compiler branch rather than the earlier cutoff branches.
    mini_source = inspect.getsource(mini.main)
    start = mini_source.index("_write_runner_artifact(")
    ready = mini_source.index("readiness('MATERIALS_READY'", start)
    assert start < mini_source.index("_write_generated_run_script(", start) < ready
    assert ready < mini_source.index("_write_inspection_tools(", ready)
    starlink_source = inspect.getsource(starlink.main)
    package = starlink_source.index("_write_runtime_package(")
    ready = starlink_source.index("status='MATERIALS_READY'", package)
    assert package < ready < starlink_source.index("_run_dynamic_tools(", ready)


def test_exact_frame_facts_are_inspected_only_through_physical_world() -> None:
    for module in (complete, mini, starlink):
        source = inspect.getsource(module)
        assert "write_physical_frame_tool(" not in source
        assert "write_physical_world_tool(" in source
        assert "physical_link_observation" in source
        assert "first_formal_input()" in source
        assert "compute_observation(" not in source


def test_mini_generated_run_script_is_persisted_only(tmp_path: Path) -> None:
    assert mini._write_generated_run_script is _write_generated_run_script
    assert not hasattr(mini, "_write_execution_scripts")
    assert "_write_generated_run_script(" in inspect.getsource(mini.main)
    mini._write_generated_run_script(
        tmp_path,
        python_executable=sys.executable,
        compiler_tool_directory="bpf-compiler",
        tool_package_parent=PROJECT_ROOT,
        required_input_paths=(
            "inputs/scenario.yaml",
            "inputs/zones.yaml",
        ),
        traffic_workload_path="inputs/traffic-workload.json",
    )
    source = (tmp_path / "run.sh").read_text(encoding="utf-8")
    assert "runtime/runner-artifact/manifest.json" in source
    assert "runtime/runner-artifact/runner-runtime.bin" in source
    assert "domain-history/physical-frames.jsonl" in source
    assert "domain-history/planning.jsonl.gz" in source
    assert "bpf-compiler-inspection.json" in source
    assert "inspection/runtime-runs/$INVOCATION_ID" in source
    assert "--invocation-directory \"$INVOCATION_DIR\"" in source
    assert '"$INVOCATION_DIR/runner/runner-events.jsonl"' in source
    assert '"$INVOCATION_DIR/traffic/probe-execution.jsonl"' in source
    assert '"$INVOCATION_DIR/observation/execution-observations.jsonl"' in source
    assert "inputs/traffic-workload.json" in source
    assert "--physical-frames" in source
    assert "--planning" in source
    assert "--slice" not in source
    for name in ("run.sh", "launch.sh"):
        assert run(("bash", "-n", str(tmp_path / name))).returncode == 0


def test_starlink_generated_run_script_is_persisted_only(tmp_path: Path) -> None:
    _write_generated_run_script(
        tmp_path,
        python_executable=sys.executable,
        compiler_tool_directory="bpf-compiler",
        tool_package_parent=PROJECT_ROOT,
    )
    source = (tmp_path / "run.sh").read_text(encoding="utf-8")
    for required in (
        "runtime/runner-artifact/manifest.json",
        "domain-history/physical-frames.jsonl",
        "domain-history/planning.jsonl.gz",
        "--bpf-compiler",
    ):
        assert required in source
    assert "--slice" not in source
    assert run(("bash", "-n", str(tmp_path / "run.sh"))).returncode == 0


def test_compose_controls_are_scoped_and_path_independent(tmp_path: Path) -> None:
    output = tmp_path / "output"
    runtime = _prepare_generated_output(output, replace=True)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    _write_compose_project_scripts(output, "skyseed_v2_example_test")
    for name in ("start.sh", "cleanup.sh"):
        path = output / name
        assert path.stat().st_mode & 0o111
        assert run(("bash", "-n", str(path))).returncode == 0
        assert "BASH_SOURCE[0]" in path.read_text(encoding="utf-8")


def test_output_preparation_replaces_only_the_owned_root(tmp_path: Path) -> None:
    output = tmp_path / "owned-output"
    outside = tmp_path / "preserved.txt"
    outside.write_text("preserved", encoding="utf-8")
    runtime = _prepare_generated_output(output, replace=True)
    (output / "stale.txt").write_text("stale", encoding="utf-8")
    assert runtime == output / "runtime"
    replacement = _prepare_generated_output(output, replace=True)
    assert replacement == output / "runtime"
    assert not (output / "stale.txt").exists()
    assert outside.read_text(encoding="utf-8") == "preserved"


def test_complete_example_keeps_static_tool_composition_explicit() -> None:
    source = inspect.getsource(complete)
    assert complete.ENABLED_TOOLS == (
        "physical-world",
        "zone",
        "planner",
        "bpf-compiler",
        "observation",
    )
    assert "write_physical_world_tool(" in source
    assert "write_zone_tool(" in source
    assert "write_physical_frame_tool(" not in source
    assert "write_planner_tool(" in source
    assert "write_bpf_compiler_tool(" in source
    assert "write_observation_tool(" in source


def test_example_package_has_no_v1_or_constellation_path() -> None:
    assert not (EXAMPLES_ROOT / "v1").exists()
    assert not (EXAMPLES_ROOT / "constellation").exists()
