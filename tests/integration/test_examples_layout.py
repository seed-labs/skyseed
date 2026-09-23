"""Each maintained entry is readable, directly executable and delegates generation."""
import ast
from importlib import import_module
from pathlib import Path
from subprocess import run
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = {
    "Sky06_s4_native_ospf": "s4_native_ospf.py",
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
    local_models = {"Sky03_s3_ospf": {"ospf_model.py"}, "Sky01_s5_custom_models": {"model.py"},
                    "Sky06_s4_native_ospf": {"edge_routes.py", "config_helpers.py", "readiness.py"}}
    assert {path.name for path in root.glob("*.py")} == {filename} | local_models.get(directory, set())
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


@pytest.mark.parametrize("directory,filename", [
    (directory, filename) for directory, filename in ENTRIES.items()
    if directory != "Sky00_tiny_network"
])
def test_file_driven_entry_inherits_backend_and_preserves_workload(tmp_path, monkeypatch, directory, filename):
    from skyseed import experiment

    entry = import_module(f"examples.{directory}.{Path(filename).stem}")
    controllers = []
    original = experiment.ExperimentController
    projections = []
    if directory == 'Sky06_s4_native_ospf':
        project = entry.adapters.create_simulator_static_input
        def capture_projection(*args, **kwargs):
            value = project(*args, **kwargs)
            projections.append(value)
            return value
        monkeypatch.setattr(entry.adapters, 'create_simulator_static_input', capture_projection)

    class InspectController(original):
        def __init__(self, network, simulation, output, **options):
            # A conflicting construction/Controller selection would fail in the
            # real constructor. There is no second backend choice in the entry.
            assert "forwarding_backend" not in options
            super().__init__(network, simulation, output, **options)
            controllers.append(self)

        def generate(self):
            return self._output  # Inspect assembly only; no output or deployment.

    monkeypatch.setattr(experiment, "ExperimentController", InspectController)
    try:
        assert entry.main(["--operation", "generate", "--no-tools", "--output", str(tmp_path)]) == 0
        controller, = controllers
        native = directory == "Sky06_s4_native_ospf"
        assert controller._forwarding_backend == ("linux" if native else "bpf")
        assert controller._traffic_enabled is True
        assert controller._traffic_telemetry_enabled is (not native)
        assert controller._internet_map_enabled is True
        timeline = controller._simulator.get_timeline()
        assert timeline.start_time.isoformat() == "2026-01-01T00:00:00+00:00"
        assert (timeline.end_time - timeline.start_time).total_seconds() == 600
        assert timeline.observation_interval.total_seconds() == 5
        assert {entry.SHELLS_FILE, entry.GROUND_STATIONS_FILE} <= set(controller._input_files)
        if native:
            assert len(projections) == 1
            assert controller._simulator.get_definition().static_input is projections[0]
            # Fixed GS/Zone selection with a five-second sender cadence.
            assert controller._traffic_options == {"interval_seconds": 5.0}
            from tools.generation.traffic.workload import select_workload
            workload = select_workload(controller._simulator.get_physical_world(),
                duration_seconds=600, **controller._traffic_options)
            assert workload.interval_seconds == 5.0
            assert workload.duration_seconds == 600
            assert len(workload.communications) == 4
            pairs = {(c.ground_station_id, c.network_scope, c.zone_id) for c in workload.communications}
            assert len(pairs) == 2
            for pair in pairs:
                assert {c.direction for c in workload.communications
                        if (c.ground_station_id, c.network_scope, c.zone_id) == pair} == {"ground-to-zone", "zone-to-ground"}
            assert len({c.communication_id for c in workload.communications}) == 4
        assert not list(tmp_path.iterdir())
    finally:
        for controller in controllers:
            controller._simulator.shutdown()


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
