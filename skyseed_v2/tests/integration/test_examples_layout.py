from pathlib import Path
from subprocess import run
import sys

import pytest
import skyseed_v2
from examples import _direct_execution
from examples._compose_project_scripts import write_compose_project_scripts
from examples.complete_static_emulator import emu
from examples.complete_static_emulator import main as complete
from examples.complete_static_emulator import simu
from examples.isolated_satellites import main as isolated
from examples.large_static_network import main as large
from seedemu.core import Emulator
from skyseed_v2.seedemu_integration import SatelliteBase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = PROJECT_ROOT / "examples"
EXPERIMENTS = {
    "isolated_satellites": (
        isolated,
        "skyseed_v2_isolated_satellites",
    ),
    "complete_static_emulator": (
        complete,
        "skyseed_v2_complete_static_emulator",
    ),
    "large_static_network": (
        large,
        "skyseed_v2_large_static_network",
    ),
}


def test_formal_examples_generate_controls_into_unique_project_outputs() -> None:
    project_names = []
    for name, (module, expected_project) in EXPERIMENTS.items():
        root = EXAMPLES_ROOT / name
        assert module.EXAMPLE_ROOT == root
        assert module.DEFAULT_OUTPUT == root / "output"
        assert module.COMPOSE_PROJECT_NAME == expected_project
        project_names.append(module.COMPOSE_PROJECT_NAME)
        required_files = ["main.py", "README.md"]
        if name == "complete_static_emulator":
            required_files.extend(("emu.py", "simu.py"))
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
        assert "write_compose_project_scripts(" in source

    assert len(set(project_names)) == len(project_names)
    for module, name in (
        (isolated, "isolated_satellites"),
        (complete, "complete_static_emulator"),
    ):
        assert not hasattr(module, "DEFAULT_SCENARIO")
        assert not (EXAMPLES_ROOT / name / "scenario.yaml").exists()
    assert large.DEFAULT_SCENARIO == (
        EXAMPLES_ROOT / "large_static_network/scenario.yaml"
    )
    assert (EXAMPLES_ROOT / "large_static_network/ground_stations.yaml").is_file()
    assert "examples/*/output/" in (PROJECT_ROOT / ".gitignore").read_text()


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


def test_direct_execution_never_restarts_through_another_interpreter(
    monkeypatch,
) -> None:
    source = Path(_direct_execution.__file__).read_text(encoding="utf-8")
    assert ".venv" not in source
    assert "execv" not in source
    assert "subprocess" not in source

    monkeypatch.setattr(_direct_execution, "find_spec", lambda _name: None)
    with pytest.raises(SystemExit) as error:
        _direct_execution.prepare_direct_file_execution("example/main.py")

    message = str(error.value)
    assert "skyseed_dev" in message
    assert "scripts/setup_dev_environment.sh" in message
    assert "conda activate skyseed_dev" in message
    assert "correctly configured venv or CI" in message


def test_generated_shell_scripts_are_scoped_executable_and_syntax_valid(
    tmp_path: Path,
) -> None:
    for name, (_, project_name) in EXPERIMENTS.items():
        output = tmp_path / name / "output"
        output.mkdir(parents=True)
        (output / "docker-compose.yml").write_text(
            "services: {}\n",
            encoding="utf-8",
        )
        write_compose_project_scripts(output, project_name)
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
        assert 'COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"' in start_text
        assert project_name in start_text
        assert project_name in cleanup_text
        assert "down --remove-orphans" in cleanup_text
        assert "prune" not in cleanup_text
        assert "docker image" not in cleanup_text
        assert "find " not in cleanup_text


def test_output_script_generation_fails_closed_on_incomplete_output(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="docker-compose.yml"):
        write_compose_project_scripts(tmp_path, "skyseed_v2_test")

    (tmp_path / "docker-compose.yml").write_text(
        "services: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="project_name"):
        write_compose_project_scripts(tmp_path, "invalid project")


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
    isolated_source = Path(isolated.__file__).read_text(encoding="utf-8")
    emu_source = Path(emu.__file__).read_text(encoding="utf-8")
    complete_source = Path(complete.__file__).read_text(encoding="utf-8")

    for forbidden_boundary in (
        "NodeId",
        "SimulatorStaticInput",
        "create_simulator_static_input",
        "create_emulator_node_index",
    ):
        assert forbidden_boundary not in isolated_source

    for source in (isolated_source, emu_source):
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
    assert "SkyseedDockerCompiler" in complete_source
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
    assert ".get_skyseed_autonomous_systems()" in source
    assert "create_simulator_static_input" in source
    assert "SimulationTimeline(" in source
    assert "SimulationDefinition(" in source
    assert "Simulator(" in source
    assert ".create_topology(" in source
    assert "load_zone_layout_definition" in source
    assert "zones.yaml" in source
    assert "build_everything" not in source
    assert "TopologySlice" not in source
    assert "static slice" not in source.lower()
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


def test_complete_emulator_helper_has_one_emulator_only_responsibility() -> None:
    source = Path(emu.__file__).read_text(encoding="utf-8")

    emulator = emu.build_emulator()
    assert isinstance(emulator, Emulator)
    assert isinstance(emulator.getLayer("Base"), SatelliteBase)
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


def test_large_example_shows_load_create_and_input_generation_directly() -> None:
    source = Path(large.__file__).read_text(encoding="utf-8")

    assert "load_static_emulator_config" in source
    assert "create_static_emulator" in source
    assert "create_simulator_static_input" in source
    assert "create_emulator_node_index" not in source
    assert "emulator_node_index" not in source
    assert "from skyseed_v2.configuration import" in source
    assert "from skyseed_v2.static_emulator import" in source
    assert "from skyseed_v2.simulator_input_generation import" in source
    assert "from skyseed_v2.application import" not in source
    assert "from skyseed_v2.serialization import" not in source
    assert "from skyseed_v2 import" not in source
    assert "@dataclass" not in source
    assert "safe_load" not in source
    assert "scenario-summary.json" not in source
    assert "LargeStaticScenario" not in source
    assert "LargeStaticSummary" not in source

    readme = (EXAMPLES_ROOT / "large_static_network/README.md").read_text(
        encoding="utf-8"
    )
    for required_explanation in (
        "only YAML example",
        "not an Emulator subclass",
        "result wrapper",
        "immutable static input consumed by Simulator",
        "Config, a SEED Registry",
        "container inventory",
    ):
        assert required_explanation in readme
