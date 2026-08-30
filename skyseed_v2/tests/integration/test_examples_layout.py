import os
import inspect
from pathlib import Path
from subprocess import run
import sys

import pytest
import skyseed_v2
from examples.complete_static_emulator import emu
from examples.complete_static_emulator import main as complete
from examples.complete_static_emulator import simu
from examples.mini_satellite_internet import main as mini
from examples.starlink_gen1_satellite_internet import main as starlink_gen1
from seedemu.core import Emulator
from seedemu.layers import Base
from skyseed_v2.compose.launcher import (
    _prepare_generated_output,
    _write_compose_project_scripts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = PROJECT_ROOT / "examples"
EXPERIMENTS = {
    "complete_static_emulator": (
        complete,
        "skyseed_v2_complete_static_emulator",
    ),
}
FORMAL_EXAMPLES = (
    *EXPERIMENTS,
    "mini_satellite_internet",
    "starlink_gen1_satellite_internet",
)


def test_formal_examples_generate_controls_into_unique_project_outputs() -> None:
    project_names = []
    for name, (module, expected_project) in EXPERIMENTS.items():
        root = EXAMPLES_ROOT / name
        assert module.EXAMPLE_ROOT == root
        assert module.DEFAULT_OUTPUT == root / "output"
        assert module.COMPOSE_PROJECT_NAME == expected_project
        project_names.append(module.COMPOSE_PROJECT_NAME)
        required_files = ["main.py", "README.md", "emu.py", "simu.py"]
        for required in required_files:
            assert (root / required).is_file()
        assert not (root / "start.sh").exists()
        assert not (root / "cleanup.sh").exists()

        readme = (root / "README.md").read_text(encoding="utf-8")
        assert f"python3 -m examples.{name}.main" in readme
        assert "./output/start.sh" in readme
        assert "./output/cleanup.sh" in readme
        assert expected_project in readme
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "_write_compose_project_scripts(" in source

    assert len(set(project_names)) == len(project_names)
    assert not hasattr(complete, "DEFAULT_SCENARIO")
    assert not (EXAMPLES_ROOT / "complete_static_emulator/scenario.yaml").exists()
    assert "examples/*/output/" in (PROJECT_ROOT / ".gitignore").read_text()


def test_examples_readme_indexes_current_long_term_roles_and_outputs() -> None:
    readme = (EXAMPLES_ROOT / "README.md").read_text(encoding="utf-8")

    for name in FORMAL_EXAMPLES:
        assert f"`{name}`" in readme
    assert "Minimal static/canonical example kept deliberately small" in readme
    assert "Small but reasonably rich reusable satellite Internet" in readme
    assert "a 121-Slice formal Timeline and rolling" in readme
    assert "Topology, Zone, Slice / Difference, Planner, BPF Compiler" in readme
    assert mini.DEFAULT_OUTPUT == (
        EXAMPLES_ROOT / "mini_satellite_internet/output"
    )
    assert mini.COMPOSE_PROJECT_PREFIX == "skyseed_v2_mini_satellite_internet"
    assert starlink_gen1.DEFAULT_OUTPUT == (
        EXAMPLES_ROOT / "starlink_gen1_satellite_internet/output"
    )


@pytest.mark.parametrize("name", tuple(EXPERIMENTS))
def test_formal_examples_support_direct_file_execution(name: str) -> None:
    root = EXAMPLES_ROOT / name
    result = run(
        (sys.executable, "main.py", "--help"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--no-override" in result.stdout


def test_generated_shell_scripts_are_scoped_executable_and_syntax_valid(
    tmp_path: Path,
) -> None:
    for name, (_, project_name) in EXPERIMENTS.items():
        output = tmp_path / name / "output"
        runtime = _prepare_generated_output(output, replace=False)
        runtime.mkdir()
        (runtime / "docker-compose.yml").write_text(
            "services: {}\n",
            encoding="utf-8",
        )
        _write_compose_project_scripts(output, project_name)
        start = output / "start.sh"
        cleanup = output / "cleanup.sh"
        for script in (start, cleanup):
            assert script.stat().st_mode & 0o111
            result = run(
                ("bash", "-n", str(script)),
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr

        start_text = start.read_text(encoding="utf-8")
        cleanup_text = cleanup.read_text(encoding="utf-8")
        assert "config --quiet" in start_text
        assert "up -d --build" in start_text
        assert (
            'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"'
            in start_text
        )
        assert (
            'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"'
            in cleanup_text
        )
        assert project_name in start_text
        assert project_name in cleanup_text
        assert "down --remove-orphans" in cleanup_text
        assert "prune" not in cleanup_text
        assert "docker image" not in cleanup_text
        assert "find " not in cleanup_text


def test_compose_owner_script_generation_remains_deployment_only() -> None:
    source = inspect.getsource(_write_compose_project_scripts)
    for forbidden in (
        "BpfRunner",
        "runtime.py",
        "run.sh",
        "launch.sh",
        "mini_satellite_internet",
    ):
        assert forbidden not in source


def test_compose_startup_evidence_is_optional_bounded_shell_output(
    tmp_path: Path,
) -> None:
    runtime = _prepare_generated_output(tmp_path / "output", replace=False)
    runtime.mkdir()
    (runtime / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    output = runtime.parent
    build_plan = runtime / "build-plan.txt"
    build_plan.write_text(
        "prerequisite base-image\n"
        "alias node-a node-a\n"
        "alias node-a node-b\n",
        encoding="utf-8",
    )
    _write_compose_project_scripts(
        output,
        "skyseed_v2_startup_evidence",
        startup_evidence_path="runtime/deployment-startup.json",
        build_plan_path="runtime/build-plan.txt",
        python_executable=sys.executable,
    )

    start = (output / "start.sh").read_text(encoding="utf-8")
    assert "compose-startup-evidence" in start
    assert "duration_seconds" in start
    assert "deployment-startup.json" in start
    assert '"${COMPOSE[@]}" build "$source"' in start
    assert 'docker image tag "$PROJECT_NAME-$source"' in start
    assert "required_images_match" in start
    assert "required generated images already match; skipping build plan" in start
    assert "missing or stale while project containers exist" in start
    assert '"${COMPOSE[@]}" up -d --no-build' in start
    assert "--parallel" not in start
    assert "skyseed_v2.experiment.execution" not in start
    assert "tools.runner" not in start
    assert "run.sh" not in start
    assert run(("bash", "-n", str(output / "start.sh"))).returncode == 0


def test_planned_compose_start_reuses_matching_images_without_build(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    runtime = _prepare_generated_output(output, replace=False)
    runtime.mkdir()
    (runtime / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (runtime / "build-plan.txt").write_text(
        "prerequisite base-image\n"
        "alias node-a node-a\n"
        "alias node-a node-b\n",
        encoding="utf-8",
    )
    _write_compose_project_scripts(
        output,
        "skyseed_v2_image_reuse",
        startup_evidence_path="runtime/deployment-startup.json",
        build_plan_path="runtime/build-plan.txt",
        python_executable=sys.executable,
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [[ "$1" == "compose" ]]; then
    for argument in "$@"; do
        case "$argument" in
            config)
                if [[ " $* " == *" --images "* ]]; then
                    printf '%s\n' \
                        base-image \
                        skyseed_v2_image_reuse-node-a \
                        skyseed_v2_image_reuse-node-b
                fi
                exit 0
                ;;
            ps)
                [[ "${FAKE_PROJECT_CONTAINERS:-0}" == "1" ]] && echo existing
                exit 0
                ;;
            build)
                exit 0
                ;;
            up)
                exit 0
                ;;
        esac
    done
elif [[ "$1 $2" == "image inspect" ]]; then
    [[ "${FAKE_IMAGES_MATCH:-1}" == "1" ]] || exit 1
    shift 2
    while (( $# )); do
        if [[ "$1" == "--format" ]]; then
            shift 2
            continue
        fi
        printf '%s\n' "sha256:matching skyseed_v2_image_reuse"
        shift
    done
    exit 0
elif [[ "$1 $2" == "image tag" ]]; then
    exit 0
fi
exit 99
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    docker_log = tmp_path / "docker.log"
    environment["DOCKER_LOG"] = str(docker_log)

    result = run(
        (str(output / "start.sh"),),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "required generated images already match; skipping build plan" in (
        result.stdout
    )
    calls = docker_log.read_text(encoding="utf-8").splitlines()
    assert not any(" build " in f" {call} " for call in calls)
    assert not any(call.startswith("image tag ") for call in calls)
    assert any(" up -d --no-build" in call for call in calls)
    assert not any("--parallel" in call for call in calls)

    docker_log.write_text("", encoding="utf-8")
    environment["FAKE_IMAGES_MATCH"] = "0"
    environment["FAKE_PROJECT_CONTAINERS"] = "1"
    failed = run(
        (str(output / "start.sh"),),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert failed.returncode == 1
    assert "missing or stale while project containers exist" in failed.stderr
    failed_calls = docker_log.read_text(encoding="utf-8").splitlines()
    assert not any(" build " in f" {call} " for call in failed_calls)
    assert not any(" up -d " in f" {call} " for call in failed_calls)


def test_generated_shell_scripts_resolve_runtime_from_any_working_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scenario-output"
    runtime = _prepare_generated_output(output, replace=False)
    runtime.mkdir()
    compose_file = runtime / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    _write_compose_project_scripts(output, "skyseed_v2_path_test")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOCKER_LOG"
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    unrelated_working_directory = tmp_path / "unrelated/working/directory"
    unrelated_working_directory.mkdir(parents=True)
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    docker_log = tmp_path / "docker.log"
    environment["DOCKER_LOG"] = str(docker_log)

    for script_name in ("start.sh", "cleanup.sh"):
        result = run(
            (str(output / script_name),),
            cwd=unrelated_working_directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    calls = docker_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    assert all(
        f"-f {compose_file}" in call
        and "-p skyseed_v2_path_test" in call
        for call in calls
    )
    assert calls[0].endswith("config --quiet")
    assert calls[1].endswith("up -d --build")
    assert calls[2].endswith("down --remove-orphans")


def test_output_script_generation_fails_closed_on_incomplete_output(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="docker-compose.yml"):
        _write_compose_project_scripts(tmp_path, "skyseed_v2_test")

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "docker-compose.yml").write_text(
        "services: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="project_name"):
        _write_compose_project_scripts(tmp_path, "invalid project")


def test_example_output_preparation_replaces_only_the_owned_root(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "emulator").mkdir()
    (output / "simulator").mkdir()
    (output / "slice").mkdir()
    outside = tmp_path / "outside"
    outside.write_text("preserved", encoding="utf-8")

    runtime = _prepare_generated_output(output, replace=True)

    assert runtime == output / "runtime"
    assert output.is_dir()
    assert tuple(output.iterdir()) == ()
    assert outside.read_text(encoding="utf-8") == "preserved"
    with pytest.raises(FileExistsError, match="already exists"):
        _prepare_generated_output(output, replace=False)


@pytest.mark.parametrize(
    ("name", "module", "inspection_tools"),
    (
        ("complete_static_emulator", complete, ("topology", "zone")),
        (
            "mini_satellite_internet",
            mini,
            (
                "topology",
                "zone",
                "slice",
                "planner",
                "observation",
                "bpf-compiler",
            ),
        ),
    ),
)
def test_formal_examples_emit_one_user_facing_output_tree(
    name: str,
    module,
    inspection_tools: tuple[str, ...],
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / name / "output"

    def render_spy(self):
        return self

    def compile_spy(self, _compiler, directory, override=False):
        assert not override
        runtime = Path(directory)
        assert runtime == output / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "docker-compose.yml").write_text(
            "services: {}\n",
            encoding="utf-8",
        )
        (runtime / "compiler-output.txt").write_text(
            "runtime-only",
            encoding="utf-8",
        )
        return self

    monkeypatch.setattr(Emulator, "render", render_spy)
    monkeypatch.setattr(Emulator, "compile", compile_spy)
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--output",
            str(output),
            "--no-internet-map",
        ],
    )

    result = module.main()
    assert result in (None, 0)
    expected_root = {"start.sh", "cleanup.sh", "runtime"}
    if name == "mini_satellite_internet":
        expected_root.update(
            {
                "inputs",
                "run.sh",
                "launch.sh",
            }
        )
    if inspection_tools:
        expected_root.add("inspection")
    assert {path.name for path in output.iterdir()} == expected_root
    assert not any(
        (output / forbidden).exists()
        for forbidden in ("emulator", "simulator", "slice", "tools")
    )
    expected_runtime = {"docker-compose.yml", "compiler-output.txt"}
    if name == "mini_satellite_internet":
        expected_runtime.update({"compose-project-name", "execution.json"})
    assert {path.name for path in (output / "runtime").iterdir()} == expected_runtime
    script_names = ["start.sh", "cleanup.sh"]
    if name == "mini_satellite_internet":
        script_names.extend(("run.sh", "launch.sh"))
        assert {
            path.name for path in (output / "inputs").iterdir()
        } == {
            "scenario.yaml",
            "traffic-workload.json",
            "zones.yaml",
        }
    for script_name in script_names:
        script = output / script_name
        assert script.is_file()
        assert script.stat().st_mode & 0o111
        if script_name in {"start.sh", "cleanup.sh"}:
            assert (
                'COMPOSE_FILE="$SCRIPT_DIR/runtime/docker-compose.yml"'
                in script.read_text(encoding="utf-8")
            )

    if not inspection_tools:
        return
    inspection = output / "inspection"
    tool_directories = {
        path.name for path in inspection.iterdir() if path.is_dir()
    }
    expected_directories = set(inspection_tools)
    if name == "mini_satellite_internet":
        assert (output / "inputs/traffic-workload.json").is_file()
        expected_directories.add("traffic")
        assert (inspection / "traffic/probe-plan.json").is_file()
    assert tool_directories == expected_directories
    for tool_name in inspection_tools:
        assert (inspection / tool_name / "index.html").is_file()
    assert not any(
        path.name in {
            "topology.json",
            "zones.json",
            "slice-manifest.json",
            "planner-inspection.json",
            "bpf-compiler-inspection.json",
        }
        for path in output.iterdir()
    )


def test_formal_examples_and_package_have_no_v1_or_constellation_path() -> None:
    forbidden = (
        "V1StaticInputAdapter",
        "default_reference_root",
        "--reference-root",
        "reference_shell_label",
        "skyseed_v1",
    )
    for name in EXPERIMENTS:
        root = EXAMPLES_ROOT / name
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix in {".py", ".yaml", ".md", ".sh"}
                and "output" not in path.parts
            ):
                text = path.read_text(encoding="utf-8")
                assert not any(value in text for value in forbidden), path

    assert not hasattr(skyseed_v2, "ConstellationBuilder")
    assert not hasattr(skyseed_v2, "ConstellationBuildResult")
    assert not (PROJECT_ROOT / "src/skyseed_v2/constellation_builder.py").exists()


def test_small_examples_show_public_construction_without_file_facades() -> None:
    emu_source = Path(emu.__file__).read_text(encoding="utf-8")
    complete_source = Path(complete.__file__).read_text(encoding="utf-8")

    for source in (emu_source,):
        assert "load_static_emulator_config" not in source
        assert "create_static_emulator" not in source
        assert "scenario.yaml" not in source
        assert ".create_shell(" in source
        assert ".create_orbit(" in source
        assert ".create_satellite(" in source

    assert ".create_ground_station(" in emu_source
    assert ".createAutonomousSystem(" in emu_source
    assert ".createNetwork(" in emu_source
    assert ".createRouter(" in emu_source
    assert "create_simulator_static_input" in complete_source
    assert "SimulationTimeline(" in complete_source
    assert "SimulationDefinition(" in complete_source
    assert "Simulator(" in complete_source
    assert ".create_topology(" in complete_source
    assert "create_emulator_node_index" not in complete_source
    assert "emulator_node_index" not in complete_source
    assert "catalog_builder" not in complete_source
    assert "Docker(" in complete_source
    assert "SkyseedDockerCompiler" not in complete_source
    assert 'ENABLED_TOOLS = ("topology", "zone")' in complete_source
    assert "write_topology_tool(" in complete_source
    assert "write_zone_tool(" in complete_source
    assert (
        "from examples.complete_static_emulator.emu import build_emulator"
        in complete_source
    )
    assert complete.build_emulator is emu.build_emulator
    assert (
        EXAMPLES_ROOT / "complete_static_emulator/simu.py"
    ).is_file()
    complete_readme = (
        EXAMPLES_ROOT / "complete_static_emulator/README.md"
    ).read_text(encoding="utf-8")
    assert "python3 -m examples.complete_static_emulator.simu" in complete_readme


def test_canonical_simu_path_is_explicit_deterministic_and_executable() -> None:
    source = Path(simu.__file__).read_text(encoding="utf-8")

    assert simu.DEFAULT_SLICE_INTERVAL.total_seconds() == 10
    assert simu.DEFAULT_START_TIME.tzinfo is not None
    assert "datetime.now" not in source
    assert (
        "from examples.complete_static_emulator.emu import build_emulator"
        in source
    )
    assert "complete_static_emulator.main" not in source
    assert ".getLayer(\"Base\")" in source
    assert "get_skyseed_autonomous_systems(base)" in source
    assert "create_simulator_static_input" in source
    assert "SimulationTimeline(" in source
    assert "SimulationDefinition(" in source
    assert "Simulator(" in source
    assert ".create_topology(" in source
    assert "load_zone_layout_definition" in source
    assert "zones.yaml" in source
    assert "build_everything" not in source
    assert "CircularOrbitPositionModel" in source
    assert "GeometricConnectionModel" in source
    assert "HopCountRouteModel" in source
    assert ".create_slicer(" in source
    assert ".create_slices(2)" in source
    assert ".create_difference(0, 1)" in source
    assert ".create_difference(1, 2)" in source
    assert ".resolve_path(" in source
    assert simu.build_emulator is emu.build_emulator

    result = run(
        (sys.executable, "simu.py"),
        cwd=EXAMPLES_ROOT / "complete_static_emulator",
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "created Simulator static topology" in result.stdout
    assert "single physical matches: americas" in result.stdout
    assert "overlap physical matches: americas, new-york-metro" in result.stdout
    assert "authoritative Zone: new-york-metro" in result.stdout
    assert "2026-01-01T00:00:30+00:00" in result.stdout
    assert "slice 0: id=0, timestamp=None" in result.stdout
    assert "effective Connections: ISL=" in result.stdout
    assert "GS access=" in result.stdout
    assert "legally unserved: americas" in result.stdout
    assert "next hops: G=" in result.stdout
    assert "resolved path:" in result.stdout
    assert "Difference 0->1: added_connections=" in result.stdout
    assert "Difference 0->1 samples: Connection=" in result.stdout
    assert "Zone=new-york-metro->" in result.stdout
    assert "Route=RouteKey(" in result.stdout
    assert "Difference 1->2: empty=" in result.stdout
    assert "evidence=" in result.stdout


def test_complete_emulator_helper_has_one_emulator_only_responsibility() -> None:
    source = Path(emu.__file__).read_text(encoding="utf-8")

    emulator = emu.build_emulator()
    assert isinstance(emulator, Emulator)
    assert isinstance(emulator.getLayer("Base"), Base)
    assert "SimulatorStaticInput" not in source
    assert "create_simulator_static_input" not in source
    assert "SimulationDefinition" not in source
    assert ".render(" not in source
    assert ".compile(" not in source
    assert "if __name__" not in source


def test_canonical_simu_bounds_unbounded_timeline_display(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(simu, "DEFAULT_END_TIME", None)

    simu.main()

    timestamp_line = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("timeline timestamps:")
    )
    assert timestamp_line.count("2026-01-01T") == (
        simu.UNBOUNDED_TIMESTAMPS_TO_DISPLAY
    )
