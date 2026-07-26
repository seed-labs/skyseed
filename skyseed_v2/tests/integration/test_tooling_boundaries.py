from __future__ import annotations

import ast
from pathlib import Path

import skyseed_v2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PROJECT_ROOT / "tools"
COMPLETE_MAIN = (
    PROJECT_ROOT / "examples/complete_static_emulator/main.py"
)
DYNAMIC_TOOL = TOOLS_ROOT / "dynamic_slice/inspection.py"
DYNAMIC_TOOL_UI = TOOLS_ROOT / "dynamic_slice/renderer.py"
DYNAMIC_TOOL_SOURCES = tuple(
    sorted((TOOLS_ROOT / "dynamic_slice").glob("*.py"))
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
    assert not hasattr(skyseed_v2, "DynamicSliceTool")

    for path in (PROJECT_ROOT / "src/skyseed_v2").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from tools" not in source, path
        assert "import tools" not in source, path


def test_tooling_has_no_unapproved_general_framework() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TOOLS_ROOT.rglob("*.py")
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


def test_browser_smoke_remains_outside_tool_responsibilities() -> None:
    browser_source = (
        PROJECT_ROOT / "scripts/check_dynamic_slice_tool_browser.py"
    ).read_text(encoding="utf-8")
    assert "geckodriver" in browser_source
    assert "geckodriver" not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in TOOLS_ROOT.rglob("*.py")
    )


def _called_attributes(source: str) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }


def _defined_functions(source: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(source: str) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }


def _owns_detached_snapshot(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "snapshot"
        for node in ast.walk(tree)
    )


def test_tool_files_are_shaped_by_responsibility_not_a_uniform_template() -> None:
    formal_calls = (
        "get_autonomous_system",
        "get_shell",
        "get_orbit",
        "get_node",
        "get_satellite",
        "get_ground_station",
        "get_zone_layout",
        "get_zone",
        "find_zones",
        "resolve_zone",
        "get_retained_slice_ids",
        "get_slice",
        "get_topology",
        "get_timeline",
        "resolve_path",
        "create_difference",
    )
    for tool_name in ("topology", "zone", "dynamic_slice"):
        root = TOOLS_ROOT / tool_name
        sources = {
            path: path.read_text(encoding="utf-8")
            for path in root.glob("*.py")
        }
        formal_readers = {
            path
            for path, source in sources.items()
            if _called_attributes(source) & set(formal_calls)
        }
        assert formal_readers, tool_name
        for path in formal_readers:
            source = sources[path]
            assert _owns_detached_snapshot(source), path
            assert any(
                name.startswith("read_")
                for name in _defined_functions(source)
            ), path
            assert "display_analysis" not in source, path
            assert "standalone_page" not in source, path
            assert "write_text(" not in source, path

        projections = {
            path
            for path, source in sources.items()
            if ".snapshot" in source and path not in formal_readers
        }
        assert projections, tool_name
        for path in projections:
            source = sources[path]
            assert ".snapshot" in source, path
            assert "skyseed_v2" not in source, path
            assert not (_called_attributes(source) & set(formal_calls)), path
            assert "standalone_page" not in source, path
            assert "write_text(" not in source, path

        renderers = {
            path
            for path, source in sources.items()
            if "standalone_page(" in source
        }
        writers = {
            path
            for path, source in sources.items()
            if _called_names(source) & {"write_json", "write_text"}
        }
        assert renderers and writers, tool_name
        for path in renderers:
            source = sources[path]
            assert "skyseed_v2" not in source, path
            assert not (_called_attributes(source) & set(formal_calls)), path
            assert "write_text(" not in source, path
        for path in writers:
            source = sources[path]
            assert "skyseed_v2" not in source, path
            assert not (_called_attributes(source) & set(formal_calls)), path
            assert "standalone_page(" not in source, path

    display_only_helpers = {
        "_fit_origin_plane": "dynamic_slice",
        "_symmetric_eigendecomposition": "dynamic_slice",
        "sample_rectangle_boundary": "zone",
        "_annotate_focus_visibility": "zone",
    }
    all_sources = {
        (tool_name, path): path.read_text(encoding="utf-8")
        for tool_name in ("topology", "zone", "dynamic_slice")
        for path in (TOOLS_ROOT / tool_name).glob("*.py")
    }
    for helper, owner_tool in display_only_helpers.items():
        definitions = {
            (tool_name, path)
            for (tool_name, path), source in all_sources.items()
            if helper in _defined_functions(source)
        }
        assert len(definitions) == 1
        tool_name, path = next(iter(definitions))
        assert tool_name == owner_tool
        source = all_sources[(tool_name, path)]
        module_docstring = ast.get_docstring(ast.parse(source)) or ""
        assert "display" in module_docstring.lower(), path
        assert "non-authoritative" in module_docstring.lower(), path
        assert "skyseed_v2.simulator.topology" not in source, path
        assert "skyseed_v2.simulator.slicing" not in source, path
        assert not (_called_attributes(source) & set(formal_calls)), path
        assert "standalone_page" not in source, path
        assert "write_text(" not in source, path


def test_dynamic_tool_boundary_allows_readonly_results_without_generation() -> None:
    contract = (
        PROJECT_ROOT / "docs/codex-reference/dynamic-slicing-contracts.md"
    ).read_text(encoding="utf-8")

    for readable_result in (
        "TopologySlice",
        "ConnectionSlice",
        "RouteSlice",
        "TopologyDifference",
    ):
        assert readable_result in contract
    assert "read-only Tools consume exact retained values" in " ".join(
        contract.split()
    )
    assert "No Model calls outside Slicer" in contract
    assert "Tool-side comparator" in contract

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in DYNAMIC_TOOL_SOURCES
    )
    for required_read in (
        ".get_topology(",
        ".get_slice(",
        ".resolve_path(",
        ".create_difference(",
    ):
        assert required_read in source
    for forbidden_generation in (
        "CircularOrbitPositionModel",
        "GeometricConnectionModel",
        "HopCountRouteModel",
        "compute_positions(",
        "compute_connection_slice(",
        "compute_route_slice(",
        "_compare_topology_slices",
    ):
        assert forbidden_generation not in source


def test_tools_do_not_read_domain_private_members() -> None:
    findings = []
    for path in sorted(TOOLS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("_")
                and not node.attr.startswith("__")
            ):
                findings.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}:"
                    f".{node.attr}"
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value.startswith("_")
            ):
                findings.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}:"
                    f"{node.args[1].value}"
                )
    assert findings == []


def test_general_tooling_has_no_canonical_example_identity_control_logic() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*sorted(TOOLS_ROOT.rglob("*.py")), COMPLETE_MAIN)
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
        PROJECT_ROOT / "docs/accepted-design.md"
    ).read_text(encoding="utf-8")
    normalized_anchor = " ".join(anchor.split())

    for accepted in (
        "codex-reference/dynamic-slicing-contracts.md",
        "Tools are repository-local, read-only, and non-authoritative",
        "no Tool-side Model execution",
        "Connection/Route recomputation",
        "no second authoritative computation path in Tools",
        "second authoritative computation path",
        "candidate and provenance material is non-authoritative",
    ):
        assert accepted.lower() in normalized_anchor.lower()

    for retired in (
        "position prerequisite computation",
        "tools` a formal computation path or fallback",
        "Topology creates TopologySlice",
    ):
        assert retired not in anchor
