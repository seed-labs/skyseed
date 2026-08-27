from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from examples.mini_satellite_internet.emu import (
    build_mini_satellite_internet_emulator,
)
from examples.mini_satellite_internet.simu import (
    create_mini_satellite_internet_slicer,
)
from tools._detached_inspection import (
    _DetachedInspectionExecutor,
    rebuild_from_handoff,
)


def test_slice_tool_runs_in_an_independent_process_and_rebuilds_from_handoff(
    tmp_path: Path,
) -> None:
    slicer = create_mini_satellite_internet_slicer(
        build_mini_satellite_internet_emulator()
    )
    slicer.create_all_slices()
    evidence = tmp_path / "inspection" / "_execution"
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
    handoff_path = evidence / "tool-handoffs" / "slice.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff["snapshot"]["status"] == "COMPLETE"
    assert handoff["snapshot"]["encoding"] == "gzip-pickle-v5"
    assert handoff["source"] == {
        "detached_before_projection": True,
        "domain_reexecution": False,
        "kind": "immutable-domain-fork-snapshot",
    }
    expected_checksums = (output / "checksums.sha256").read_bytes()

    shutil.rmtree(output)
    rebuilt = rebuild_from_handoff(handoff_path)

    assert rebuilt["status"] == "PASS"
    assert (output / "checksums.sha256").read_bytes() == expected_checksums
    assert not tuple(output.parent.glob(".slice.staging-*"))


def test_s4_main_uses_neutral_detached_handoffs_around_public_compilation() -> None:
    example = (
        Path(__file__).resolve().parents[2] / "examples/s4_satellite_internet"
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
    assert 'inspection / "_execution"' in source
    assert "_compile_bpf_observed" not in source
    assert "_record_public_compile" not in source
    assert "qualification" not in source
    assert not (example / "qualification.py").exists()
