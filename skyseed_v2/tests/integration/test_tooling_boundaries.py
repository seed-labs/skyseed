from __future__ import annotations

from pathlib import Path

import skyseed_v2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PROJECT_ROOT / "tools"
COMPLETE_MAIN = (
    PROJECT_ROOT / "examples/complete_static_emulator/main.py"
)


def test_tools_are_repository_local_and_dependency_direction_is_one_way() -> None:
    assert (TOOLS_ROOT / "__init__.py").is_file()
    assert 'where = ["src"]' in (
        PROJECT_ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert "tools" not in skyseed_v2.__all__
    assert not hasattr(skyseed_v2, "Tool")
    assert not hasattr(skyseed_v2, "TopologyTool")
    assert not hasattr(skyseed_v2, "ZoneTool")

    for path in (PROJECT_ROOT / "src/skyseed_v2").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from tools" not in source, path
        assert "import tools" not in source, path


def test_tooling_has_no_unapproved_general_framework() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TOOLS_ROOT.glob("*.py")
    )
    for forbidden in (
        "class Tool",
        "ToolManager",
        "ToolContext",
        "ToolRegistry",
        "BaseInspector",
        "VisualizerFactory",
        "plugin discovery",
        "artifact manager",
    ):
        assert forbidden not in source


def test_dynamic_tool_boundary_allows_readonly_results_without_generation() -> None:
    contract = (
        PROJECT_ROOT / "docs/architecture/dynamic-slice-public-contract.md"
    ).read_text(encoding="utf-8")

    for readable_result in (
        "TopologySlice",
        "ConnectionSlice",
        "RouteSlice",
        "TopologyDifference",
    ):
        assert readable_result in contract
    assert "may read frozen Position" in contract
    assert "never reruns a Model" in contract
    assert "non-authoritative design analysis" in contract


def test_general_tooling_has_no_canonical_example_identity_control_logic() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*sorted(TOOLS_ROOT.glob("*.py")), COMPLETE_MAIN)
    )
    for canonical_value in (
        "65001",
        "shell-1",
        "orbit-1",
        "americas",
        "new-york-metro",
        "satellite-0",
        "satellite-1",
        "gs-new-york",
    ):
        assert canonical_value not in source


def test_accepted_design_anchor_keeps_the_tooling_boundary() -> None:
    anchor = (
        PROJECT_ROOT / "docs/architecture/accepted-design.md"
    ).read_text(encoding="utf-8")
    normalized_anchor = " ".join(anchor.split())

    for accepted in (
        "dynamic-slice-public-contract.md",
        "Tools may read formal dynamic results",
        "Tools do not independently propagate Position",
        "Tools do not recompute authoritative Connection or Route results",
        "never Model fallbacks",
        "second authoritative computation path",
        "Candidate and historical records retain provenance",
    ):
        assert accepted in normalized_anchor

    for retired in (
        "Topology\n→ Slicer",
        "position prerequisite computation",
        "tools` a formal computation path or fallback",
    ):
        assert retired not in anchor
