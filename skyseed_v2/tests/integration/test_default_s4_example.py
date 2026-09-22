"""The introductory entry assembles real defaults and makes execution explicit."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from examples.Sky02_s4_default import s4


def test_s4_construction_preserves_configured_scene():
    inputs = s4.configuration.emulator.load_static_emulator_config(s4.SHELLS_FILE.parent / "scenario.yaml")
    system, = inputs.skyseed_autonomous_systems
    selected, = (shell for shell in system.shells if shell.shell_id == "S4")
    reference = s4.emulator.create_static_emulator(replace(
        inputs, skyseed_autonomous_systems=(replace(system, shells=(selected,)),),
    ))

    actual = s4.build_emulator()
    assert isinstance(actual, s4.seed.Emulator)
    assert [layer.getName() for layer in actual.getLayers()] == ["Base"]
    base = actual.getLayer("Base")
    assert base.getAsns() == [65040]
    assert base.getInternetExchangeIds() == []
    actual_as = base.getAutonomousSystem(system.asn)
    reference_as = reference.getLayer("Base").getAutonomousSystem(system.asn)
    assert actual_as.getIbgpMode() == reference_as.getIbgpMode() == "disabled"
    actual_shell, = actual_as.shells
    reference_shell, = reference_as.shells
    assert actual_shell.shell_id == reference_shell.shell_id == "S4"
    assert len(actual_shell.orbits) == 6
    assert selected.epoch_utc == datetime(2015, 1, 1, tzinfo=timezone.utc)
    assert actual_shell.orbits[0].parameters.right_ascension_deg == 63.7
    for orbit, expected in zip(actual_shell.orbits, reference_shell.orbits, strict=True):
        assert orbit.orbit_id == expected.orbit_id
        assert orbit.parameters == expected.parameters
        assert orbit.parameters.epoch_utc == selected.epoch_utc
        assert len(orbit.satellites) == 58
        for satellite, other in zip(orbit.satellites, expected.satellites, strict=True):
            for field in (
                "satellite_id", "shell_id", "orbit_id", "orbit_index",
                "mean_anomaly_deg", "payload", "max_simultaneous_ground_station_accesses",
                "maximum_simultaneous_inter_satellite_links",
            ):
                assert getattr(satellite, field) == getattr(other, field)
            assert actual_as.getExtensionNode(satellite.getName()) is satellite
    assert len(actual_as.getExtensionNodes()) == 348
    assert len(actual_as.ground_stations()) == len(actual_as.getRouters()) == 355
    for station, other in zip(actual_as.ground_stations(), reference_as.ground_stations(), strict=True):
        for field in (
            "ground_station_id", "location", "station_type", "operator",
            "max_simultaneous_satellite_accesses", "service_config",
        ):
            assert getattr(station, field) == getattr(other, field)
        assert station.getDisplayName() == other.getDisplayName()
        assert actual_as.getRouter(station.getName()) is station
    adapt = s4.adapters.create_simulator_static_input
    assert adapt((actual_as,)) == adapt((reference_as,))


@pytest.mark.parametrize("operation", ["serve", "generate"])
def test_s4_default_scene_and_entry_order(tmp_path, monkeypatch, operation):
    calls = []
    assembled = []
    create = s4.simulator.create_default_simulator
    build = s4.build_emulator

    def build_scene():
        calls.append("emulator")
        return build()

    def assemble(definition, **options):
        assert options == {}  # Entry uses defaults without restating them.
        simulation = create(definition)
        assembled.append(simulation)
        calls.append("assembly")
        return simulation

    original = s4.experiment.ExperimentController
    class Workflow:
        add_arguments = staticmethod(original.add_arguments)
        def __init__(self, network, simulation, output, **options):
            assert simulation is assembled[0]
            assert options == {
                "entry_path": Path(s4.__file__),
                "input_files": (s4.SHELLS_FILE, s4.GROUND_STATIONS_FILE),
                "replace": True, "traffic_enabled": True,
                "traffic_telemetry_enabled": True,
                "through": "compiler", "internet_map_enabled": True,
                "deploy_parallel": None, "runtime_workers": None, "route_workers": None,
            }
            assert output == tmp_path
            self.output = output
        def generate(self):
            calls.append("generation")
            return self.output

        def serve(self, **options):
            assert options['run'] is False
            calls.append("retained")
            options['on_completed'](self.output, {})
            return self.output

    def tools(output, *, physical_world, publication=None):
        assert output == tmp_path
        assert physical_world is assembled[0].get_physical_world()
        calls.append("tools")

    def execute(output, **options):
        assert output == tmp_path and options == {}
        calls.append("execution")

    monkeypatch.setattr(s4, "build_emulator", build_scene)
    monkeypatch.setattr(s4.simulator, "create_default_simulator", assemble)
    monkeypatch.setattr(s4.experiment, "ExperimentController", Workflow)
    monkeypatch.setattr(s4.tools, "write_experiment_tools", tools)
    monkeypatch.setattr(s4.experiment, "run_generated_experiment", execute)
    args = ["--output", str(tmp_path), "--override"]
    if operation == "generate":
        args.extend(["--operation", "generate"])
    try:
        assert s4.main(args) == 0
        simulation, = assembled
        world = simulation.get_physical_world()
        static_input = simulation.get_definition().static_input
        satellites = static_input.list_satellites()
        assert len(satellites) == 348
        assert {sat.shell_id for sat in satellites} == {"S4"}
        assert sorted(Counter(sat.orbit_id for sat in satellites).values()) == [58] * 6
        assert len(static_input.list_ground_stations()) == 355
        assert len(world.get_zone_layout().zones) == 732
        timeline = simulation.get_timeline()
        assert timeline.start_time == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert timeline.end_time - timeline.start_time == timedelta(seconds=600)
        assert timeline.observation_interval == timedelta(seconds=5)
        assert len(tuple(timeline.iter_timestamps())) == 121
        assert simulation.get_planner()._physical_observation_frontier is None
        assert calls == ["emulator", "assembly", "generation" if operation == "generate" else "retained", "tools"]
    finally:
        for simulation in assembled:
            simulation.shutdown()
