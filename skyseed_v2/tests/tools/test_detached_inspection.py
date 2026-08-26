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
    evidence = tmp_path / "qualification"
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


def test_s4_qualification_schedules_planner_tool_before_compiler_domain() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "examples/s4_satellite_internet/qualification.py"
    ).read_text(encoding="utf-8")

    planner_submit = source.index('inspection_executor.submit(\n                    "planner"')
    compiler_start = source.index('_execution_event("compiler", "start"')
    compiler_submit = source.index('inspection_executor.submit(\n                    "compiler"')
    final_join = source.index("inspection_executor.join()")

    assert planner_submit < compiler_start < compiler_submit < final_join
    assert '"status": domain_status' in source
    assert '"planner_tool_building_when_compiler_started"' in source
