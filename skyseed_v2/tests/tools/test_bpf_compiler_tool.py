from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from examples.complete_static_emulator.emu import build_emulator
from examples.complete_static_emulator.simu import (
    create_complete_static_simulator,
    run_complete_static_chain,
)
from tools.bpf_compiler import (
    collect_bpf_compiler_data,
    read_bpf_compiler_history,
    write_bpf_compiler_tool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCES = tuple(
    sorted((PROJECT_ROOT / "tools/bpf_compiler").glob("*.py"))
)


@pytest.fixture(scope="module")
def complete_chain(tmp_path_factory):
    root = tmp_path_factory.mktemp("compiler-tool")
    simulator = create_complete_static_simulator(build_emulator())
    plans, realizations, snapshot = run_complete_static_chain(
        simulator, evidence_path=root / "history"
    )
    return root, plans, realizations, snapshot


def test_projection_preserves_lineage_and_compact_compiler_statistics(
    complete_chain,
) -> None:
    _, plans, realizations, snapshot = complete_chain
    data = collect_bpf_compiler_data(plans, realizations)

    assert data["inventory"]["plans"] == len(plans) == 2
    assert data["inventory"]["realizations"] == len(realizations)
    assert data["inventory"]["activation_boundaries"] == snapshot[
        "formal_frame_count"
    ] == 4
    for plan, realization, record in zip(
        plans, realizations, data["realizations"], strict=True
    ):
        assert record["lineage"]["source_plan_id"] == plan.plan_id
        assert record["lineage"]["output_state_fingerprint"] == (
            plan.output_state_fingerprint
        )
        assert len(record["boundary_workloads"]) == len(plan.transitions)
        assert record["intended_state_timestamp"] == (
            realization.end_timestamp.isoformat()
        )
        assert record["summary"]["planned_routes_reachable"] == 2
        assert record["summary"]["planned_routes_unreachable"] == 1
    assert data["realizations"][0]["summary"]["constraints"] > 0
    assert data["realizations"][0]["operation_counts"]
    assert data["format_version"] == 3
    assert "source_plan" not in data["realizations"][0]
    assert "compiled_intents" not in data["realizations"][0]
    assert "intended_state" not in data["realizations"][0]
    assert data["interpretation"]["realized"].startswith("not claimed")


def test_optional_compile_evidence_is_aligned_and_retained(complete_chain) -> None:
    _, plans, realizations, _ = complete_chain
    evidence = tuple(
        {"compile_index": index, "compile_seconds": 0.1 + index}
        for index in range(len(plans))
    )
    data = collect_bpf_compiler_data(
        plans, realizations, compile_evidence=evidence
    )
    assert tuple(
        value["compile_evidence"] for value in data["realizations"]
    ) == evidence
    with pytest.raises(ValueError, match="continuous"):
        collect_bpf_compiler_data(
            plans,
            realizations,
            compile_evidence=({"compile_index": 1},),
        )


def test_reader_rejects_misaligned_source_plan(complete_chain) -> None:
    _, plans, realizations, _ = complete_chain
    with pytest.raises(ValueError, match="source Plan"):
        collect_bpf_compiler_data(plans, tuple(reversed(realizations)))


def test_compact_producer_history_is_continuous(complete_chain) -> None:
    root, plans, _, _ = complete_chain
    history = read_bpf_compiler_history(root / "history/compiler.jsonl")
    assert len(history["realizations"]) == len(plans)
    assert tuple(
        value["realization_index"] for value in history["realizations"]
    ) == tuple(range(len(plans)))


def test_compact_history_reader_rejects_state_lineage_drift(
    tmp_path, complete_chain
) -> None:
    root, _, _, _ = complete_chain
    history = Path(
        shutil.copytree(root / "history", tmp_path / "state-lineage")
    )
    rows = [
        json.loads(line)
        for line in (history / "compiler.jsonl").read_text().splitlines()
    ]
    rows[2]["input_state_fingerprint"] = "wrong"
    path = history / "compiler.jsonl"
    path.write_text(
        "".join(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            for value in rows
        ),
        encoding="utf-8",
    )
    manifest_path = history / "domain-history-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = path.read_bytes()
    manifest["streams"]["compiler"].update(
        {
            "sha256": sha256(payload).hexdigest(),
            "bytes": len(payload),
            "record_count": len(rows) - 1,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="input_state_fingerprint differs"):
        read_bpf_compiler_history(path)


def test_compiler_reader_requires_compiler_terminal_seal(
    tmp_path, complete_chain
) -> None:
    root, _, _, _ = complete_chain
    history = Path(shutil.copytree(root / "history", tmp_path / "planner-only"))
    compiler_path = history / "compiler.jsonl"
    header = compiler_path.read_text(encoding="utf-8").splitlines()[0]
    compiler_path.write_text(header + "\n", encoding="utf-8")
    manifest_path = history / "domain-history-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = compiler_path.read_bytes()
    manifest["through"] = "planner"
    manifest["counts"]["realizations"] = 0
    manifest["lineage"]["last_realization_plan_id"] = None
    manifest["lineage"]["last_realization_output_state_fingerprint"] = None
    manifest["streams"]["compiler"].update(
        {
            "sha256": sha256(payload).hexdigest(),
            "bytes": len(payload),
            "record_count": 0,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not sealed through compiler"):
        read_bpf_compiler_history(compiler_path)


def test_writer_emits_exact_json_html_and_accounting(
    tmp_path, complete_chain
) -> None:
    _, plans, realizations, _ = complete_chain
    result = write_bpf_compiler_tool(plans, realizations, tmp_path)

    assert result["status"] == "PASS"
    assert result["artifact_metrics"]["input_bytes"] > 0
    assert {value.name for value in tmp_path.iterdir()} == {
        "bpf-compiler-inspection.json",
        "checksums.sha256",
        "index.html",
        "inventory.json",
        "summary.txt",
    }
    payload = json.loads(
        (tmp_path / "bpf-compiler-inspection.json").read_text()
    )
    assert payload["tool"] == "bpf-compiler-inspection"
    page = (tmp_path / "index.html").read_text()
    assert "Plan → compiled intended realization" in page
    assert "Intent workload by realization" in page
    assert 'id="workload-chart"' in page
    assert 'id="delta-chart"' in page
    assert 'id="folding-chart"' in page
    assert 'id="timing-chart"' in page
    assert 'id="compiler-boundary"' not in page
    assert 'aria-label="Jump to realization"' in page
    assert "select.dataset.globalBoundary" in page
    assert "workload.dataset.globalBoundary" in page
    assert "focusedRealization" in page
    assert "<pre" not in page
    for line in (tmp_path / "checksums.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        assert sha256((tmp_path / relative).read_bytes()).hexdigest() == digest


def test_tool_sources_do_not_compile_or_query_planner() -> None:
    combined = "\n".join(path.read_text() for path in TOOL_SOURCES)
    assert "compile_bpf(" not in combined
    assert ".get_published_evidence(" not in combined
    assert ".get_state(" not in combined
