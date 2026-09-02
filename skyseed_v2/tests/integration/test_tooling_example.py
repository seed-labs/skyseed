from __future__ import annotations

from pathlib import Path

from examples.complete_static_emulator import main as complete
from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import DEFAULT_START_TIME
from skyseed_v2.simulator import Simulator
from tools.physical_world import write_physical_world_tool as real_write_physical_world_tool
from tools.zone import write_zone_tool as real_write_zone_tool


EXPECTED_ARTIFACTS = {
    "index.html",
    "physical-world/index.html",
    "physical-world/physical-world.json",
    "physical-world/summary.txt",
    "zone/index.html",
    "zone/zones.json",
    "zone/summary.txt",
    "planner/index.html",
    "planner/planner-inspection.json",
    "planner/summary.txt",
    "planner/inventory.json",
    "planner/checksums.sha256",
    "bpf-compiler/index.html",
    "bpf-compiler/bpf-compiler-inspection.json",
    "bpf-compiler/summary.txt",
    "bpf-compiler/inventory.json",
    "bpf-compiler/checksums.sha256",
    "observation/index.html",
    "observation/execution-observations.jsonl",
    "observation/observation-records.js",
    "observation/observation-summary.json",
    "observation/summary.txt",
    "observation/inventory.json",
    "observation/checksums.sha256",
}


def test_main_builds_once_creates_one_physical_world_and_shares_it_after_compile(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"
    build_calls = 0
    physical_world_calls = 0
    received = []
    original_create_physical_world = Simulator.create_physical_world

    def build_spy():
        nonlocal build_calls
        build_calls += 1
        return build_emulator()

    def create_physical_world_spy(self, **kwargs):
        nonlocal physical_world_calls
        physical_world_calls += 1
        return original_create_physical_world(self, **kwargs)

    def physical_world_writer(physical_world, directory, **kwargs):
        assert (output / "runtime/docker-compose.yml").is_file()
        received.append(physical_world)
        return real_write_physical_world_tool(
            physical_world, directory, **kwargs
        )

    def zone_writer(physical_world, directory):
        assert (output / "runtime/docker-compose.yml").is_file()
        received.append(physical_world)
        return real_write_zone_tool(physical_world, directory)

    monkeypatch.setattr(complete, "build_emulator", build_spy)
    monkeypatch.setattr(Simulator, "create_physical_world", create_physical_world_spy)
    monkeypatch.setattr(complete, "write_physical_world_tool", physical_world_writer)
    monkeypatch.setattr(complete, "write_zone_tool", zone_writer)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--output", str(output), "--no-internet-map"],
    )

    assert complete.main() == 0

    assert build_calls == 1
    assert physical_world_calls == 1
    assert len(received) == 2
    assert received[0] is received[1]
    artifacts = {
        path.relative_to(output / "inspection").as_posix()
        for path in (output / "inspection").rglob("*")
        if path.is_file()
    }
    assert EXPECTED_ARTIFACTS <= artifacts
    chunk_artifacts = artifacts - EXPECTED_ARTIFACTS
    assert chunk_artifacts
    assert all(
        artifact.startswith("physical-world/data/frame-")
        or artifact.startswith("planner/data/frame-")
        or artifact.startswith("planner/data/routes-")
        for artifact in chunk_artifacts
    )
    assert {
        artifact
        for artifact in chunk_artifacts
        if artifact.startswith("physical-world/data/frame-")
    } == {
        f"physical-world/data/frame-{index:03d}.js" for index in range(4)
    }
    assert {
        artifact
        for artifact in chunk_artifacts
        if artifact.startswith("planner/data/frame-")
    } == {f"planner/data/frame-{index:03d}.js" for index in range(4)}
    assert any(
        artifact.startswith("planner/data/routes-")
        for artifact in chunk_artifacts
    )
    aggregate = (output / "inspection/index.html").read_text(encoding="utf-8")
    physical_world_page = (
        output / "inspection/physical-world/index.html"
    ).read_text(encoding="utf-8")
    zone_page = (output / "inspection/zone/index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="physical-world/index.html"' in aggregate
    assert 'href="zone/index.html"' in aggregate
    assert 'href="physical-frame/index.html"' not in aggregate
    assert "直接选择静态 Zone 边界与几何信息" in aggregate
    assert "探针" not in aggregate
    assert 'href="../index.html"' in physical_world_page
    assert 'href="../index.html"' in zone_page
    terminal = capsys.readouterr().out
    assert str(output / "inspection/index.html") in terminal
    assert str(output / "inspection/physical-world/index.html") in terminal
    assert str(output / "inspection/zone/index.html") in terminal
    assert "Physical World Tool: PASS" in terminal
    assert "Zone Tool: PASS" in terminal
    assert "Tooling overall: PASS" in terminal


def test_current_snapshot_replaces_previous_snapshot_without_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    simulator = complete._create_simulator(build_emulator())
    physical_world = simulator.get_physical_world()
    planner = complete.create_complete_static_planner(simulator)
    physical_frame = physical_world.frame_at(DEFAULT_START_TIME)
    observation = simulator.get_physical_link_model().compute_observation(
        physical_world,
        physical_frame,
        planner.create_physical_link_request(),
    )
    monkeypatch.setattr(
        complete,
        "ENABLED_TOOLS",
        ("physical-world", "zone"),
    )

    first, first_aggregate = complete._write_tool_snapshot(
        physical_world,
        output,
        physical_link_model=simulator.get_physical_link_model(),
        physical_frame=physical_frame,
        physical_link_observation=observation,
        plans=(),
        realizations=(),
        observation_path=output / "unused-observations.jsonl",
    )
    stale = output / "inspection/physical-world/stale-success.txt"
    stale.write_text("old", encoding="utf-8")
    second, second_aggregate = complete._write_tool_snapshot(
        physical_world,
        output,
        physical_link_model=simulator.get_physical_link_model(),
        physical_frame=physical_frame,
        physical_link_observation=observation,
        plans=(),
        realizations=(),
        observation_path=output / "unused-observations.jsonl",
    )

    assert first_aggregate and second_aggregate
    assert all(result["status"] == "PASS" for result in first.values())
    assert all(result["status"] == "PASS" for result in second.values())
    assert not stale.exists()
    assert not (output / "inspection/latest").exists()
    assert not any(
        path.is_dir() and path.name.startswith("run")
        for path in (output / "inspection").iterdir()
    )


def test_tool_failure_is_recorded_without_invalidating_domain_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"
    stale = output / "inspection/physical-world/physical-world.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"stale":true}', encoding="utf-8")
    zone_calls = 0

    def fail_physical_world(_physical_world, _directory, **_kwargs):
        raise RuntimeError("forced Physical World failure")

    def observe_zone(physical_world, directory):
        nonlocal zone_calls
        zone_calls += 1
        return real_write_zone_tool(physical_world, directory)

    monkeypatch.setattr(complete, "write_physical_world_tool", fail_physical_world)
    monkeypatch.setattr(complete, "write_zone_tool", observe_zone)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--output", str(output), "--no-internet-map"],
    )

    assert complete.main() == 0

    assert zone_calls == 1
    assert not stale.exists()
    physical_world_failure = (
        output / "inspection/physical-world/index.html"
    ).read_text(encoding="utf-8")
    aggregate = (output / "inspection/index.html").read_text(encoding="utf-8")
    assert "FAILED" in physical_world_failure
    assert "forced Physical World failure" in physical_world_failure
    assert "FAIL" in aggregate
    assert (output / "inspection/zone/zones.json").is_file()
    assert (output / "domain-history/domain-history-manifest.json").is_file()
    capture = capsys.readouterr()
    terminal = capture.out
    assert "Physical World Tool: FAILED" in terminal
    assert "Zone Tool: PASS" in terminal
    assert "Tooling overall: FAIL" in terminal
    assert "did not invalidate" in capture.err


def test_tool_batch_exception_does_not_invalidate_completed_domain_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "output"

    def fail_tool_batch(*_args, **_kwargs):
        raise RuntimeError("forced Tool batch failure")

    monkeypatch.setattr(complete, "_write_tool_snapshot", fail_tool_batch)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--output", str(output), "--no-internet-map"],
    )

    assert complete.main() == 0

    assert (output / "runtime/docker-compose.yml").is_file()
    assert (output / "domain-history/domain-history-manifest.json").is_file()
    capture = capsys.readouterr()
    assert "Tooling overall: FAIL" in capture.out
    assert "did not invalidate" in capture.err
