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
    "compose", "experiment/workflow.py", "experiment/_realization_producer.py",
    "experiment/traffic", "shared/_domain_history.py", "shared/_execution_observation.py",
    "backend/bpf/compiler.py",
    "backend/bpf/compiled_realization.py",
    "backend/bpf/runner.py",
    "backend/bpf/_runner_artifact.py",
    "backend/bpf/_runner",
    "simulator/planning",
    "simulator/planner/models.py",
    "simulator/_ground_station_allocation.py",
    "simulator/_gateway_candidates.py",
    "simulator/_route_computation.py",
    "simulator/_routing_core.py",
    "simulator/planner/_route_demands.py",
    "emulator_to_simulator.py",
    "emulator/seedemu/skyseed_autonomous_system.py",
    "configuration/emulator/satellite.py",
    "configuration/emulator/yaml.py",
    "configuration/simulator/zone.py",
    "configuration/simulator/yaml.py",
    "configuration/simulator/global_layout.py",
    "simulator/models",
    "simulator/physical/world.py",
    "simulator/physical/zone.py",
    "simulator/physical/capability.py",
    "simulator/physical/position.py",
    "simulator/physical/physical_frame.py",
    "simulator/physical/physical_link.py",
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
    "simulator/_history_codec.py",
    "simulator/models/gateway_hop_count_route.py",
    "simulator/models/geometric_connection.py",
    "simulator/models/hop_count_route.py",
    "simulator/planner/_difference_time.py",
    "simulator/planner/_network_changes.py",
    "simulator/planner/_network_coordination.py",
    "simulator/planner/_network_core.py",
    "simulator/planner/_network_models.py",
    "simulator/planner/_network_values.py",
    "simulator/planner/algorithms.py",
    "simulator/slicing",
    "simulator/topology.py",
    "simulator/topology",
    "simulator/zone.py",
)


def _collapsed_group(module_name: str) -> str:
    if module_name == "skyseed_v2":
        return "ROOT"
    if module_name == "skyseed_v2.experiment" or module_name.startswith(
        "skyseed_v2.experiment."
    ):
        return "EXPERIMENT"
    if module_name == "skyseed_v2.adapters" or module_name.startswith("skyseed_v2.adapters."):
        return "BRIDGE"
    if module_name == "skyseed_v2.backend" or module_name.startswith(
        "skyseed_v2.backend."
    ):
        return "BPF"
    for prefix, group in (
        ("skyseed_v2.configuration.emulator", "CONFIG_EMU"),
        ("skyseed_v2.configuration.simulator", "CONFIG_SIM"),
        ("skyseed_v2.configuration", "CONFIG"),
        ("skyseed_v2.emulator.entities", "ENTITIES"),
        ("skyseed_v2.emulator.seedemu", "SEED"),
        ("skyseed_v2.emulator", "EMU"),
        ("skyseed_v2.simulator.input", "INPUT"),
        ("skyseed_v2.simulator.geometry", "GEOMETRY"),
        ("skyseed_v2.simulator.physical", "PHYSICAL"),
        ("skyseed_v2.simulator.planner", "PLANNING"),
        ("skyseed_v2.simulator", "SIM"),
        ("skyseed_v2.shared", "SHARED"),
        ("skyseed_v2.deployment", "DEPLOYMENT"),
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
    *,
    include_type_checking: bool = True,
) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = (
        module_name
        if path.name == "__init__.py"
        else module_name.rpartition(".")[0]
    )
    internal: set[str] = set()
    external: set[str] = set()
    type_checking_imports = (
        set()
        if include_type_checking
        else {
            nested
            for guard in ast.walk(tree)
            if isinstance(guard, ast.If)
            and (
                isinstance(guard.test, ast.Name)
                and guard.test.id == "TYPE_CHECKING"
                or isinstance(guard.test, ast.Attribute)
                and guard.test.attr == "TYPE_CHECKING"
            )
            for statement in guard.body
            for nested in ast.walk(statement)
            if isinstance(nested, (ast.Import, ast.ImportFrom))
        }
    )
    for node in ast.walk(tree):
        if node in type_checking_imports:
            continue
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
    runtime_graph: dict[str, set[str]] = {}
    external: dict[str, set[str]] = {}
    for module_name, path in modules.items():
        graph[module_name], external[module_name] = _imports(
            module_name, path, known
        )
        runtime_graph[module_name], _ = _imports(
            module_name,
            path,
            known,
            include_type_checking=False,
        )

    edges = tuple(
        (source, target)
        for source in sorted(graph)
        for target in sorted(graph[source])
    )
    components = _strongly_connected_components(runtime_graph)
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
        # The root exports selectable modules but performs no cross-domain adaptation.
        if has_emulator and has_simulator and module_name != PACKAGE_NAME:
            bridge_modules.append(module_name)
        backend = "skyseed_v2.backend.bpf."
        if module_name.startswith(backend + "compiler") and any(
            target.startswith(backend + "runner") for target in targets
        ):
            raise AssertionError(f"Compiler depends on runtime: {module_name} -> {targets}")
        if module_name.startswith(backend + "execution_materials"):
            if any(target.startswith(backend + "runner") for target in targets):
                raise AssertionError(f"Pure materials depend on runtime: {module_name} -> {targets}")
            if not module_name.endswith(".sealing") and any(
                target.startswith(backend + "compiler") for target in targets
            ):
                raise AssertionError(f"Material reading/rules depend on Compiler: {module_name} -> {targets}")
        if module_name.startswith(backend + "runner") and any(
            target.startswith(backend + "compiler") or target == backend + "execution_materials.sealing"
            for target in targets
        ):
            raise AssertionError(f"Runner depends on compilation: {module_name} -> {targets}")
        if module_name.startswith("skyseed_v2.simulator") and any(
            target.startswith("skyseed_v2.emulator")
            or target == "skyseed_v2.adapters"
            or target.startswith("skyseed_v2.adapters.")
            or target.startswith("skyseed_v2.backend")
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
                "skyseed_v2.deployment",
                "tools",
                "examples",
            )
        ):
            raise AssertionError(
                f"SEED boundary reverse dependency: {module_name} -> {targets}"
            )

    if bridge_modules != ["skyseed_v2.adapters.emulator_to_simulator"]:
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
                allowed = {"experiment/controller.py", "experiment/execution.py"}
                if path.relative_to(PACKAGE_ROOT).as_posix() not in allowed or not all(
                    name in {"tools.traffic.active.probe_plan", "tools.traffic.active.probe_execution"}
                    for name in names if name == "tools" or name.startswith("tools.")):
                    raise AssertionError(f"non-boundary production imports Tools: {path}")
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
    print("cross-layer bridge: skyseed_v2.adapters.emulator_to_simulator")
    print("SEED direct internal dependencies:")
    for value in seed_direct_dependencies:
        print(f"  {value}")
    print("SEED direct internal consumers:")
    for value in seed_direct_consumers:
        print(f"  {value}")
    print("retired paths: absent")
    print("core -> Tools: absent; experiment boundary -> explicitly enabled Traffic only")
    print("Simulator -> Emulator/SEED: absent")
    print("Simulator -> backend: absent")
    print("SEED -> Simulator/Tools/Deployment/examples: absent")
    print("compatibility forwarding: absent")
    print("import dependency result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
