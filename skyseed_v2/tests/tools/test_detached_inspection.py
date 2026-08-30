from __future__ import annotations

import os
from pathlib import Path

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_slicer,
)
from tools._detached_inspection import _DetachedInspectionExecutor


def test_slice_tool_runs_in_an_independent_process_and_discards_control_state(
    tmp_path: Path,
) -> None:
    slicer = create_mini_satellite_internet_slicer(
        build_mini_satellite_internet_emulator()
    )
    slicer.create_all_slices()
    evidence = tmp_path / ".inspection-control"
    output = tmp_path / "inspection" / "slice"
    executor = _DetachedInspectionExecutor(evidence, max_workers=2)

    scheduled = executor.submit("slice", {"slicer": slicer}, output)
    records = executor.join()

    assert scheduled["status"] == "BUILDING"
    assert scheduled["pid"] != os.getpid()
    assert len(records) == 1
    assert records[0]["status"] == "COMPLETE"
    assert records[0]["pid"] == scheduled["pid"]
    assert records[0]["peak_rss_bytes"] > 0
    assert (output / "checksums.sha256").is_file()
    assert not evidence.exists()
    assert not tuple(output.parent.glob(".slice.staging-*"))


def test_s4_main_uses_neutral_detached_handoffs_around_public_compilation() -> None:
    example = (
        Path(__file__).resolve().parents[2]
        / "examples/starlink_gen1_satellite_internet"
    )
    source = (example / "main.py").read_text(encoding="utf-8")

    planner_boundary = source.index(
        "if plan.end_slice_id == final_slice_id:"
    )
    observation_binding = source.index("with _bind_compile_observation(")
    public_compile = source.index("realization = compile_bpf(")
    compiler_submit = source.index(
        'schedule(\n                "compiler"',
        public_compile,
    )
    final_join = source.index("tasks = executor.join()")

    assert (
        planner_boundary
        < observation_binding
        < public_compile
        < compiler_submit
        < final_join
    )
    assert 'output / ".inspection-control"' in source
    assert "_compile_bpf_observed" not in source
    assert "_record_public_compile" not in source
    assert "qualification" not in source
    assert not (example / "qualification.py").exists()
