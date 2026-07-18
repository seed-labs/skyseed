from pathlib import Path
from subprocess import run

import skyseed_v2
from examples.complete_static_emulator import main as complete
from examples.isolated_satellites import main as isolated
from examples.large_static_network import main as large


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


def test_formal_examples_own_data_output_scripts_and_unique_projects() -> None:
    project_names = []
    for name, (module, expected_project) in EXPERIMENTS.items():
        root = EXAMPLES_ROOT / name
        assert module.EXAMPLE_ROOT == root
        assert module.DEFAULT_OUTPUT == root / "output"
        assert module.COMPOSE_PROJECT_NAME == expected_project
        project_names.append(module.COMPOSE_PROJECT_NAME)
        for required in ("main.py", "README.md", "start.sh", "cleanup.sh"):
            assert (root / required).is_file()

        readme = (root / "README.md").read_text(encoding="utf-8")
        assert f"examples/{name}/start.sh" in readme
        assert f"examples/{name}/cleanup.sh" in readme
        assert expected_project in readme

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


def test_example_shell_scripts_are_thin_scoped_and_syntax_valid() -> None:
    for name, (_, project_name) in EXPERIMENTS.items():
        root = EXAMPLES_ROOT / name
        start = root / "start.sh"
        cleanup = root / "cleanup.sh"
        for script in (start, cleanup):
            result = run(
                ("bash", "-n", str(script)),
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr

        start_text = start.read_text(encoding="utf-8")
        cleanup_text = cleanup.read_text(encoding="utf-8")
        assert "python3 -m" in start_text
        assert "docker" not in start_text
        assert "scenario.yaml" not in start_text
        assert project_name in cleanup_text
        assert 'OUTPUT_DIR="$SCRIPT_DIR/output"' in cleanup_text
        assert "down --remove-orphans" in cleanup_text
        assert "prune" not in cleanup_text
        assert "docker image" not in cleanup_text


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
    complete_source = Path(complete.__file__).read_text(encoding="utf-8")

    for source in (isolated_source, complete_source):
        assert "StaticEmulatorScenarioLoader" not in source
        assert "StaticEmulatorSpecificationApplier" not in source
        assert "scenario.yaml" not in source
        assert ".create_shell(" in source
        assert ".create_orbit(" in source
        assert ".create_satellite(" in source

    assert ".create_ground_station(" in complete_source
    assert ".createAutonomousSystem(" in complete_source
    assert ".createNetwork(" in complete_source
    assert ".createRouter(" in complete_source
    assert "SimulatorStaticInput" in complete_source
    assert "create_simulator_static_input" in complete_source
    assert "from skyseed_v2.simulator_input import SimulatorStaticInput" in (
        complete_source
    )
    assert "(skyseed_as,)" in complete_source
    assert "ground_as)" not in complete_source
    assert "catalog_builder" not in complete_source
    assert "SkyseedDockerCompiler" in complete_source


def test_large_example_has_one_thin_file_application_path() -> None:
    source = Path(large.__file__).read_text(encoding="utf-8")

    assert "StaticEmulatorScenarioLoader().load" in source
    assert "StaticEmulatorSpecificationApplier().apply" in source
    assert "from skyseed_v2.serialization import" in source
    assert "from skyseed_v2.application import" in source
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
        "not an Emulator entity",
        "immutable Simulator input snapshot",
        "scenario, a SEED Registry",
        "container inventory",
    ):
        assert required_explanation in readme
