from __future__ import annotations

from pathlib import Path

from examples.complete_static_emulator import main as complete
from examples.complete_static_emulator.emu import build_emulator
from skyseed_v2.simulator import Simulator
from tools.topology import write_topology_tool as real_write_topology_tool
from tools.zone import write_zone_tool as real_write_zone_tool


EXPECTED_ARTIFACTS = {
    "index.html",
    "topology/index.html",
    "topology/topology.json",
    "topology/summary.txt",
    "zone/index.html",
    "zone/probes.json",
    "zone/summary.txt",
}


def test_main_builds_once_creates_one_topology_and_shares_it_after_compile(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"
    build_calls = 0
    topology_calls = 0
    received = []
    original_create_topology = Simulator.create_topology

    def build_spy():
        nonlocal build_calls
        build_calls += 1
        return build_emulator()

    def create_topology_spy(self, **kwargs):
        nonlocal topology_calls
        topology_calls += 1
        return original_create_topology(self, **kwargs)

    def topology_writer(topology, directory):
        assert (output / "docker-compose.yml").is_file()
        received.append(topology)
        return real_write_topology_tool(topology, directory)

    def zone_writer(topology, directory):
        assert (output / "docker-compose.yml").is_file()
        received.append(topology)
        return real_write_zone_tool(topology, directory)

    monkeypatch.setattr(complete, "build_emulator", build_spy)
    monkeypatch.setattr(Simulator, "create_topology", create_topology_spy)
    monkeypatch.setattr(complete, "write_topology_tool", topology_writer)
    monkeypatch.setattr(complete, "write_zone_tool", zone_writer)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--output", str(output), "--no-internet-map"],
    )

    assert complete.main() == 0

    assert build_calls == 1
    assert topology_calls == 1
    assert len(received) == 2
    assert received[0] is received[1]
    artifacts = {
        path.relative_to(output / "tools").as_posix()
        for path in (output / "tools").rglob("*")
        if path.is_file()
    }
    assert artifacts == EXPECTED_ARTIFACTS
    terminal = capsys.readouterr().out
    assert str(output / "tools/index.html") in terminal
    assert str(output / "tools/topology/index.html") in terminal
    assert str(output / "tools/zone/index.html") in terminal
    assert "Topology Tool: PASS" in terminal
    assert "Zone Tool: PASS" in terminal
    assert "Tooling overall: PASS" in terminal


def test_current_snapshot_replaces_previous_snapshot_without_history(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    topology = complete._create_simulator_topology(build_emulator())

    first, first_aggregate = complete._write_tool_snapshot(topology, output)
    stale = output / "tools/topology/stale-success.txt"
    stale.write_text("old", encoding="utf-8")
    second, second_aggregate = complete._write_tool_snapshot(topology, output)

    assert first_aggregate and second_aggregate
    assert all(result["status"] == "PASS" for result in first.values())
    assert all(result["status"] == "PASS" for result in second.values())
    assert not stale.exists()
    assert not (output / "tools/latest").exists()
    assert not any(
        path.is_dir() and path.name.startswith("run")
        for path in (output / "tools").iterdir()
    )


def test_tool_failure_writes_current_failed_artifact_continues_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"
    stale = output / "tools/topology/topology.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"stale":true}', encoding="utf-8")
    zone_calls = 0

    def fail_topology(_topology, _directory):
        raise RuntimeError("forced topology failure")

    def observe_zone(topology, directory):
        nonlocal zone_calls
        zone_calls += 1
        return real_write_zone_tool(topology, directory)

    monkeypatch.setattr(complete, "write_topology_tool", fail_topology)
    monkeypatch.setattr(complete, "write_zone_tool", observe_zone)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--output", str(output), "--no-internet-map"],
    )

    assert complete.main() == 1

    assert zone_calls == 1
    assert not stale.exists()
    topology_failure = (
        output / "tools/topology/index.html"
    ).read_text(encoding="utf-8")
    aggregate = (output / "tools/index.html").read_text(encoding="utf-8")
    assert "FAILED" in topology_failure
    assert "forced topology failure" in topology_failure
    assert "FAIL" in aggregate
    assert (output / "tools/zone/probes.json").is_file()
    terminal = capsys.readouterr().out
    assert "Topology Tool: FAILED" in terminal
    assert "Zone Tool: PASS" in terminal
    assert "Tooling overall: FAIL" in terminal
