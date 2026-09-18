from __future__ import annotations

import ast
import json
from pathlib import Path

from tools._presentation.html import finalize_runtime_aggregate_index, write_aggregate_index


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PROJECT_ROOT / "tools"
CURRENT_TOOL_PACKAGES = {
    "physical_world",
    "zone",
    "planner",
    "bpf_compiler",
    "runner",
    "performance",
    "traffic",
}
RETIRED_SYMBOLS = (
    "Slicer",
    "TopologySlice",
    "TopologyDifference",
    "ConnectionSlice",
    "RouteSlice",
    "simulator.slicing",
    "tools.slice",
)


def _python_source(directory: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*.py"))
        if "__pycache__" not in path.parts and "active" not in path.relative_to(directory).parts
    )


def _called_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                names.add(function.id)
            elif isinstance(function, ast.Attribute):
                names.add(function.attr)
    return names


def test_tools_are_repository_local_and_slice_tool_is_absent() -> None:
    assert (TOOLS_ROOT / "__init__.py").is_file()
    assert {
        path.name
        for path in TOOLS_ROOT.iterdir()
        if path.is_dir()
        and path.name not in {"__pycache__", "_presentation"}
        and (path / "__init__.py").is_file()
    } == CURRENT_TOOL_PACKAGES
    assert not (TOOLS_ROOT / "slice").exists()
    assert not (TOOLS_ROOT / "physical_frame/__init__.py").exists()
    assert not (PROJECT_ROOT / "scripts/check_slice_tool_browser.py").exists()


def test_tool_sources_have_no_retired_domain_contract() -> None:
    source = _python_source(TOOLS_ROOT)
    for retired in RETIRED_SYMBOLS:
        assert retired not in source
    assert "slice-manifest.json" not in source
    assert "slice-difference.json" not in source


def test_tools_do_not_advance_or_recompute_domain_state() -> None:
    forbidden_calls = {
        "frame_at",
        "compute_observation",
        "observe",
        "prime",
        "finish",
        "compile_bpf",
        "prepare",
        "run",
    }
    for package in CURRENT_TOOL_PACKAGES:
        source = _python_source(TOOLS_ROOT / package)
        assert not (_called_names(source) & forbidden_calls), package


def test_planner_and_compiler_tools_read_public_detached_values() -> None:
    planner = _python_source(TOOLS_ROOT / "planner")
    compiler = _python_source(TOOLS_ROOT / "bpf_compiler")
    assert "PublishedPlanningEvidence" in planner
    assert "read_planner_history" in planner
    assert "Plan" in compiler
    assert "BpfCompilationResult" in compiler
    assert "read_bpf_compiler_history" in compiler
    for private in (
        "_selected_by_pair",
        "_lifecycle_by_pair",
        "_unpublished",
        "_current_state",
    ):
        assert private not in planner
        assert private not in compiler


def test_observation_groups_describe_the_actual_round_b_pipeline() -> None:
    source = _python_source(TOOLS_ROOT / "performance")
    lowered = source.lower()
    for required in (
        "physical projection",
        "physical-link observation",
        "isl selection",
        "acquisition / lifecycle",
        "route planning",
        "plan publication",
        "compiler",
        "realization producer",
        "runner",
        "traffic",
    ):
        assert required in lowered
    for retired in ("slicer", "difference"):
        assert retired not in source.lower()


def test_aggregate_index_links_only_current_tools(tmp_path: Path) -> None:
    results = {
        "physical-world": {"status": "PASS"},
        "zone": {"status": "PASS"},
        "planner": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "bpf-compiler": {"status": "PASS"},
        "runner": {"status": "PASS"},
        "traffic": {"status": "PASS"},
    }
    write_aggregate_index(tmp_path, results, {"satellites": 1})
    source = (tmp_path / "index.html").read_text(encoding="utf-8")
    for path in (
        "physical-world/index.html",
        "zone/index.html",
        "planner/index.html",
        "performance/index.html",
        "bpf-compiler/index.html",
        "runner/index.html",
        "traffic/index.html",
    ):
        assert path in source
    assert "slice/index.html" not in source


def test_runtime_aggregate_finalization_preserves_static_results(
    tmp_path: Path,
) -> None:
    physical = tmp_path / "physical-world"
    physical.mkdir()
    (physical / "physical-world.json").write_text(
        json.dumps({"counts": {"satellites": 4}}), encoding="utf-8"
    )
    for name in ("planner", "bpf-compiler", "runner", "traffic"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "summary.txt").write_text("status: PASS\n", encoding="utf-8")
    finalize_runtime_aggregate_index(tmp_path)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Satellite" in page
    assert "slice/index.html" not in page


def test_detached_executor_supports_only_semantic_side_work() -> None:
    source = (TOOLS_ROOT / "_detached_inspection.py").read_text(
        encoding="utf-8"
    )
    assert '_SUPPORTED_TASKS = frozenset({"planner", "compiler", "performance"})' in source
    for forbidden in ("physical-world", "physical-frame", "zone", "traffic", "slice"):
        assert f'"{forbidden}"' not in source.split("_SUPPORTED_TASKS", 1)[1].split(")", 1)[0]


def test_tool_readmes_describe_observation_not_domain_ownership() -> None:
    for package in CURRENT_TOOL_PACKAGES:
        readme = (TOOLS_ROOT / package / "README.md").read_text(encoding="utf-8")
        assert "Tool" in readme
        assert any(
            word in readme.lower()
            for word in ("read", "inspection", "inspect", "parse")
        )
        for retired in RETIRED_SYMBOLS:
            assert retired not in readme


def test_concise_namespace_reexports_canonical_capabilities():
    import tools
    from tools.planner.export import write_network_export
    from tools.traffic.export import write_traffic_export
    from tools.experiment import write_experiment_tools
    assert tools.write_network_export is write_network_export
    assert tools.write_traffic_export is write_traffic_export
    assert tools.write_experiment_tools is write_experiment_tools
    assert tools.finalize_runtime_aggregate_index is finalize_runtime_aggregate_index
    for name in CURRENT_TOOL_PACKAGES:
        assert getattr(tools, name).__name__ == 'tools.' + name


def test_active_traffic_consumes_existing_facts_without_computation():
    source = _python_source(TOOLS_ROOT / "traffic/active")
    assert not (_called_names(source) & {"frame_at", "compute_observation", "observe", "compile_bpf"})
