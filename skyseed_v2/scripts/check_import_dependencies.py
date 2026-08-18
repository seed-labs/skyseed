#!/usr/bin/env python3
"""Print and validate the actual internal production import graph."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src/skyseed_v2"
PACKAGE_NAME = "skyseed_v2"
RETIRED_PATHS = (
    "_time.py",
    "entities",
    "identifier.py",
    "materialization",
    "node_id.py",
    "seedemu_integration",
    "simulator_input_generation.py",
    "static_emulator.py",
    "simulator/connection.py",
    "simulator/coordinates.py",
    "simulator/difference.py",
    "simulator/position.py",
    "simulator/route.py",
    "simulator/slice.py",
    "simulator/slicer.py",
    "simulator/static_input",
    "simulator/topology.py",
    "simulator/zone.py",
)


def _collapsed_group(module_name: str) -> str:
    if module_name == "skyseed_v2":
        return "ROOT"
    if module_name == "skyseed_v2.emulator_to_simulator":
        return "BRIDGE"
    for prefix, group in (
        ("skyseed_v2.configuration.emulator", "CONFIG_EMU"),
        ("skyseed_v2.configuration.simulator", "CONFIG_SIM"),
        ("skyseed_v2.configuration", "CONFIG"),
        ("skyseed_v2.emulator.entities", "ENTITIES"),
        ("skyseed_v2.emulator.seedemu", "SEED"),
        ("skyseed_v2.emulator", "EMU"),
        ("skyseed_v2.simulator.input", "INPUT"),
        ("skyseed_v2.simulator.geometry", "GEOMETRY"),
        ("skyseed_v2.simulator.topology", "TOPOLOGY"),
        ("skyseed_v2.simulator.slicing", "SLICING"),
        ("skyseed_v2.simulator.models", "MODELS"),
        ("skyseed_v2.simulator.planning", "PLANNING"),
        ("skyseed_v2.simulator", "SIM"),
        ("skyseed_v2.shared", "SHARED"),
        ("skyseed_v2.compose", "COMPOSE"),
    ):
        if module_name == prefix or module_name.startswith(prefix + "."):
            return group
    raise AssertionError(f"production module has no documented group: {module_name}")


def collapsed_import_edges() -> frozenset[tuple[str, str]]:
    """Return the exact direct production imports collapsed by owner package."""

    modules = _known_modules()
    known = set(modules)
    edges: set[tuple[str, str]] = set()
    for module_name, path in modules.items():
        internal, _ = _imports(module_name, path, known)
        source = _collapsed_group(module_name)
        for imported in internal:
            target = _collapsed_group(imported)
            if source != target:
                edges.add((source, target))
    return frozenset(edges)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((PACKAGE_NAME, *parts))


def _known_modules() -> dict[str, Path]:
    return {
        _module_name(path): path
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _internal_target(name: str, known: set[str]) -> str | None:
    candidate = name
    while candidate.startswith(f"{PACKAGE_NAME}.") or candidate == PACKAGE_NAME:
        if candidate in known:
            return candidate
        if "." not in candidate:
            break
        candidate = candidate.rpartition(".")[0]
    return None


def _imports(
    module_name: str,
    path: Path,
    known: set[str],
) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = (
        module_name
        if path.name == "__init__.py"
        else module_name.rpartition(".")[0]
    )
    internal: set[str] = set()
    external: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative = "." * node.level + (node.module or "")
                resolved = resolve_name(relative, package)
                if node.module is None:
                    names.extend(f"{resolved}.{alias.name}" for alias in node.names)
                else:
                    names.append(resolved)
            elif node.module is not None:
                names.append(node.module)
        for imported in names:
            target = _internal_target(imported, known)
            if target is not None:
                if target != module_name:
                    internal.add(target)
            else:
                external.add(imported)
    return internal, external


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> tuple[tuple[str, ...], ...]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return tuple(sorted(components))


def main() -> int:
    modules = _known_modules()
    known = set(modules)
    graph: dict[str, set[str]] = {}
    external: dict[str, set[str]] = {}
    for module_name, path in modules.items():
        graph[module_name], external[module_name] = _imports(
            module_name, path, known
        )

    edges = tuple(
        (source, target)
        for source in sorted(graph)
        for target in sorted(graph[source])
    )
    components = _strongly_connected_components(graph)
    if components:
        raise AssertionError(f"production import SCCs: {components}")

    bridge_modules = []
    for module_name, targets in graph.items():
        has_emulator = any(
            target.startswith("skyseed_v2.emulator") for target in targets
        )
        has_simulator = any(
            target.startswith("skyseed_v2.simulator") for target in targets
        )
        if has_emulator and has_simulator:
            bridge_modules.append(module_name)
        if module_name.startswith("skyseed_v2.simulator") and any(
            target.startswith("skyseed_v2.emulator")
            or target == "skyseed_v2.emulator_to_simulator"
            for target in targets
        ):
            raise AssertionError(
                f"Simulator reverse dependency: {module_name} -> {targets}"
            )
        if module_name.startswith("skyseed_v2.emulator.seedemu") and any(
            target.startswith(prefix)
            for target in (*targets, *external[module_name])
            for prefix in (
                "skyseed_v2.simulator",
                "skyseed_v2.compose",
                "tools",
                "examples",
            )
        ):
            raise AssertionError(
                f"SEED boundary reverse dependency: {module_name} -> {targets}"
            )

    if bridge_modules != ["skyseed_v2.emulator_to_simulator"]:
        raise AssertionError(f"cross-layer bridges: {bridge_modules}")

    for path in PACKAGE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "tools" or name.startswith("tools.") for name in names):
                raise AssertionError(f"production imports Tools: {path}")
        if "sys.modules" in source:
            raise AssertionError(f"compatibility module alias found: {path}")

    for relative in RETIRED_PATHS:
        if (PACKAGE_ROOT / relative).exists():
            raise AssertionError(f"retired production path exists: {relative}")

    seed_prefix = "skyseed_v2.emulator.seedemu"
    seed_modules = {
        name for name in modules if name == seed_prefix or name.startswith(seed_prefix + ".")
    }
    seed_direct_dependencies = sorted(
        {
            target
            for source in seed_modules
            for target in graph[source]
            if target not in seed_modules
        }
    )
    seed_direct_consumers = sorted(
        {
            source
            for source, targets in graph.items()
            if source not in seed_modules and targets & seed_modules
        }
    )

    print(f"production modules: {len(modules)}")
    print(f"internal edges: {len(edges)}")
    print("internal import edges:")
    for source, target in edges:
        print(f"  {source} -> {target}")
    print(f"SCCs with more than one module: {len(components)}")
    print("cross-layer bridge: skyseed_v2.emulator_to_simulator")
    print("SEED direct internal dependencies:")
    for value in seed_direct_dependencies:
        print(f"  {value}")
    print("SEED direct internal consumers:")
    for value in seed_direct_consumers:
        print(f"  {value}")
    print("retired paths: absent")
    print("production -> Tools: absent")
    print("Simulator -> Emulator/SEED: absent")
    print("SEED -> Simulator/Tools/Compose/examples: absent")
    print("compatibility forwarding: absent")
    print("import dependency result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
