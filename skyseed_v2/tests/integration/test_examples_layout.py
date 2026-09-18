"""Each maintained entry is readable, directly executable and delegates generation."""
import ast
from pathlib import Path
from subprocess import run
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = {
    "Sky03_s3_ospf": "s3_ospf.py",
    "Sky02_s4_default": "s4.py",
    "Sky00_tiny_network": "tiny_network.py",
    "Sky01_s5_custom_models": "s5.py",
    "Sky04_single_shell_continuation": "single_shell.py",
    "Sky05_five_shells": "five_shells.py",
}


@pytest.mark.parametrize("directory,filename", ENTRIES.items())
def test_single_entry_uses_public_assembly_and_generation(directory, filename):
    root = ROOT / "examples" / directory
    local_models = {"Sky03_s3_ospf": "ospf_model.py", "Sky01_s5_custom_models": "model.py"}
    assert {path.name for path in root.glob("*.py")} == {filename} | ({local_models[directory]} if directory in local_models else set())
    source = (root / filename).read_text()
    assert "SimulationTimeline(" in source
    assert "SimulationDefinition(" in source
    assert "create_default_simulator(" in source
    assert "ExperimentController(" in source and ".generate()" in source
    assert "tools.write_experiment_tools(" in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("examples."):
                assert node.module.startswith(f"examples.{directory}.")
    assert "Producer" not in source
    assert "produce_next_" not in source
    assert "Runner(" not in source


@pytest.mark.parametrize("directory,filename", ENTRIES.items())
@pytest.mark.parametrize("cwd", [ROOT, Path('/tmp')])
def test_direct_help_resolves_paths_without_selected_working_directory(directory, filename, cwd):
    result = run([sys.executable, str(ROOT / "examples" / directory / filename), "--help"],
                 cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for flag in ("--operation", "--batch-seconds", "--duration-seconds", "--through", "--output", "--override"):
        assert flag in result.stdout
    assert "--generate-only" not in result.stdout
    source = (ROOT / "examples" / directory / filename).read_text()
    assert 'DEFAULT_OPERATION = "serve"' in source
    assert 'on_completed=None if args.no_tools else present' in source
