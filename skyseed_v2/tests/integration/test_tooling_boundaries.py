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


def test_tooling_has_no_general_framework_or_unapproved_runtime_concepts() -> None:
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
        "TopologySlice",
        "ConnectionSlice",
        "RouteSlice",
    ):
        assert forbidden not in source


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


def test_accepted_design_anchor_contains_the_frozen_direction_and_boundaries() -> None:
    anchor = (
        PROJECT_ROOT / "docs/architecture/accepted-design.md"
    ).read_text(encoding="utf-8")

    for accepted in (
        "Simulator",
        "SimulationTimeline",
        "SimulationDefinition",
        "Topology",
        "ZoneLayout",
        "LatitudeLongitudeRectangle",
        "DefaultZoneMembershipModel",
        "Topology\n→ Slicer",
        "position prerequisite computation",
        "tools` a formal computation path or fallback",
        "Repository facts",
        "Design candidates",
        "Historical/reference facts",
    ):
        assert accepted in anchor
