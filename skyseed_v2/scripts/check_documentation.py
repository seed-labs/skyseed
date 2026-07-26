#!/usr/bin/env python3
"""Check Skyseed documentation by explicit audience/path identity."""

from __future__ import annotations

import argparse
import ast
from dataclasses import fields, is_dataclass
from enum import Enum
import importlib
import inspect
from pathlib import Path
import re
from textwrap import dedent
from urllib.parse import unquote

from check_import_dependencies import collapsed_import_edges


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
SOURCE_ROOT = PROJECT_ROOT / "src"
PRODUCTION_ROOT = SOURCE_ROOT / "skyseed_v2"


def _module_name_for_source(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT)
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.with_suffix("").parts
    return ".".join(parts)


PUBLIC_PACKAGE_INITS = tuple(sorted(PRODUCTION_ROOT.rglob("__init__.py")))
DISCOVERED_PUBLIC_PACKAGES = tuple(
    sorted(
        _module_name_for_source(path)
        for path in PUBLIC_PACKAGE_INITS
        if "__all__" in path.read_text(encoding="utf-8")
    )
)
APPROVED_PUBLIC_PACKAGES = (
    "skyseed_v2",
    "skyseed_v2.compose",
    "skyseed_v2.configuration",
    "skyseed_v2.configuration.emulator",
    "skyseed_v2.configuration.simulator",
    "skyseed_v2.emulator",
    "skyseed_v2.emulator.entities",
    "skyseed_v2.emulator.seedemu",
    "skyseed_v2.shared",
    "skyseed_v2.simulator",
    "skyseed_v2.simulator.geometry",
    "skyseed_v2.simulator.input",
    "skyseed_v2.simulator.models",
    "skyseed_v2.simulator.slicing",
    "skyseed_v2.simulator.topology",
)
PUBLIC_MODULE_NAMES = tuple(
    sorted((*DISCOVERED_PUBLIC_PACKAGES, "skyseed_v2.emulator_to_simulator"))
)
PUBLIC_PACKAGE_READMES = tuple(
    SOURCE_ROOT / Path(*module_name.split(".")) / "README.md"
    for module_name in DISCOVERED_PUBLIC_PACKAGES
)
TOOL_READMES = (
    PROJECT_ROOT / "tools/README.md",
    PROJECT_ROOT / "tools/topology/README.md",
    PROJECT_ROOT / "tools/zone/README.md",
    PROJECT_ROOT / "tools/dynamic_slice/README.md",
)

AUTHORITATIVE_ROOT_FILES = (
    DOCS_ROOT / "README.md",
    DOCS_ROOT / "CURRENT.md",
    DOCS_ROOT / "accepted-design.md",
)
CURRENT_DIRECTORIES = (
    DOCS_ROOT / "human-readable",
    DOCS_ROOT / "codex-reference",
    DOCS_ROOT / "user-guides",
)
ISOLATED_DIRECTORIES = (
    DOCS_ROOT / "candidates",
    DOCS_ROOT / "provenance",
)
OWNER_READMES = tuple(sorted((*PUBLIC_PACKAGE_READMES, *TOOL_READMES)))
EXPECTED_DOC_DIRECTORIES = {
    "candidates",
    "codex-reference",
    "human-readable",
    "provenance",
    "user-guides",
}
EXPECTED_DOC_ROOT_FILES = {"README.md", "CURRENT.md", "accepted-design.md"}

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
MERMAID_BLOCK = re.compile(r"```mermaid[ \t]*\n(.*?)```", re.DOTALL)
FENCE = re.compile(r"^```", re.MULTILINE)
MERMAID_TYPES = {
    "classDiagram",
    "erDiagram",
    "flowchart",
    "gitGraph",
    "mindmap",
    "pie",
    "sequenceDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "timeline",
}
PUBLIC_IMPORT_ROW = re.compile(
    r"^\| `(?P<module>skyseed_v2(?:\.[^`]+)?)` "
    r"\| (?P<exports>.*?) \|$",
    re.MULTILINE,
)
BACKTICK_NAME = re.compile(r"`([^`]+)`")
OBJECT_CONTRACT_ROW = re.compile(
    r"^\| `(?P<alias>[^`]+)` "
    r"\| `(?P<contract>skyseed_v2\.[^`]+)` \|$",
    re.MULTILINE,
)
DETAIL_CONTRACT_ROW = re.compile(
    r"^\| `(?P<alias>[^`]+)` "
    r"\| `(?P<contract>skyseed_v2\.[^`]+)` "
    r"\| (?P<members>.*?) \|$",
    re.MULTILINE,
)
PUBLIC_PACKAGE_ROW = re.compile(
    r"^\| `(?P<module>skyseed_v2(?:\.[^`]+)?)` "
    r"\| [^|]+ \| `(?P<readme>src/skyseed_v2/[^`]+/README\.md"
    r"|src/skyseed_v2/README\.md)` \|$",
    re.MULTILINE,
)
CLASS_BLOCK = re.compile(
    r"^\s*class\s+(?P<alias>[A-Za-z_]\w*)"
    r"(?:\s*\[[^\]]+\])?\s*\{(?P<body>.*?)^\s*\}",
    re.MULTILINE | re.DOTALL,
)
CLASS_RELATION_LINE = re.compile(
    r"^\s*(?P<source>[A-Za-z_]\w*)"
    r'(?:\s+"(?P<source_cardinality>[^"]+)")?\s+'
    r"(?P<relation>\*--|o--|-->|<--|\.\.>|\.\.\|>)\s+"
    r'(?:"(?P<target_cardinality>[^"]+)"\s+)?'
    r"(?P<target>[A-Za-z_]\w*)"
    r"(?:\s*:\s*.+)?\s*$"
)
FLOW_RELATION_LINE = re.compile(
    r"^\s*(?P<source>[A-Za-z_]\w*)"
    r'(?:\["(?P<source_label>[^"]+)"\])?\s+'
    r"(?P<relation>-->|-\.->)\s+"
    r"(?P<target>[A-Za-z_]\w*)"
    r'(?:\["(?P<target_label>[^"]+)"\])?\s*$'
)
IMPORT_GRAPH_EDGE = re.compile(
    r"^\s*(?P<source>[A-Z][A-Z_]*)\s+-->\s+"
    r"(?P<target>[A-Z][A-Z_]*)\s*$",
    re.MULTILINE,
)


def _markdown_files(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(directory.rglob("*.md")))


def _current_documents() -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                *AUTHORITATIVE_ROOT_FILES,
                *(
                    path
                    for directory in CURRENT_DIRECTORIES
                    for path in _markdown_files(directory)
                ),
            }
        )
    )


def _isolated_documents() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for directory in ISOLATED_DIRECTORIES
            for path in _markdown_files(directory)
        )
    )


def _full_repository_documents() -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                PROJECT_ROOT / "README.md",
                PROJECT_ROOT / "AGENTS.md",
                *_current_documents(),
                *_isolated_documents(),
                *OWNER_READMES,
                *_markdown_files(PROJECT_ROOT / "examples"),
            }
        )
    )


def _check_identity_layout() -> None:
    actual_directories = {
        path.name for path in DOCS_ROOT.iterdir() if path.is_dir()
    }
    actual_root_files = {
        path.name for path in DOCS_ROOT.iterdir() if path.is_file()
    }
    if actual_directories != EXPECTED_DOC_DIRECTORIES:
        raise AssertionError(
            "docs directory identity mismatch: "
            f"{sorted(actual_directories)}"
        )
    if actual_root_files != EXPECTED_DOC_ROOT_FILES:
        raise AssertionError(
            f"docs root file identity mismatch: {sorted(actual_root_files)}"
        )


def _check_basic_markdown(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if not source.strip():
        raise AssertionError(f"empty Markdown: {path}")
    if not source.startswith("#"):
        raise AssertionError(f"Markdown must start with a heading: {path}")
    if len(FENCE.findall(source)) % 2:
        raise AssertionError(f"unclosed fenced block: {path}")


def _link_target(path: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0]
    if (
        not target
        or target.startswith("#")
        or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)
    ):
        return None
    target = unquote(target.split("#", 1)[0])
    resolved = (path.parent / target).resolve()
    if resolved.is_dir():
        resolved = resolved / "README.md"
    return resolved


def _check_links(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    checked = 0
    for match in MARKDOWN_LINK.finditer(source):
        target = _link_target(path, match.group(1))
        if target is None:
            continue
        checked += 1
        if not target.exists():
            relative = path.relative_to(PROJECT_ROOT)
            raise AssertionError(
                f"broken local link in {relative}: {match.group(1)}"
            )
    return checked


def _check_mermaid_static(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    matches = tuple(MERMAID_BLOCK.finditer(source))
    for index, match in enumerate(matches, start=1):
        block = match.group(1)
        preceding = source[max(0, match.start() - 500) : match.start()]
        preceding_paragraph = preceding.rstrip().rsplit("\n\n", 1)[-1]
        if "Legend:" not in preceding_paragraph:
            raise AssertionError(
                f"Mermaid block needs an explicit adjacent legend: {path}:{index}"
            )
        first = next(
            (line.strip().split(maxsplit=1)[0] for line in block.splitlines()
             if line.strip()),
            "",
        )
        if first not in MERMAID_TYPES:
            raise AssertionError(
                f"unsupported Mermaid type in {path}:{index}: {first!r}"
            )
        if first == "flowchart" and "-->" not in block and "-.->" not in block:
            raise AssertionError(f"empty Mermaid flowchart in {path}:{index}")
        if first == "sequenceDiagram" and "->>" not in block:
            raise AssertionError(
                f"empty Mermaid sequenceDiagram in {path}:{index}"
            )
        relation_patterns = {
            "composition": r"\*--",
            "aggregation": r"o--",
            "realization": r"\.\.\|>",
            "dependency": r"\.\.>",
            "dashed-flow": r"-\.->",
            "sequence-return": r"-->>",
            "sequence-call": r"(?<!-)->>",
            "solid-flow/reference": r"(?<![.*-])-->(?!>)",
        }
        relation_kinds = {
            name
            for name, pattern in relation_patterns.items()
            if re.search(pattern, block)
        }
        if "sequence-return" in relation_kinds:
            relation_kinds.discard("sequence-call")
            if re.search(r"(?<!-)->>", block):
                relation_kinds.add("sequence-call")
        if len(relation_kinds) > 1 or (
            not relation_kinds and first != "classDiagram"
        ):
            raise AssertionError(
                f"Mermaid block must express at most one relation kind in "
                f"{path}:{index}: {sorted(relation_kinds)}"
            )
    return len(matches)


def _check_anchor_consistency() -> None:
    accepted = (DOCS_ROOT / "accepted-design.md").read_text(encoding="utf-8")
    current = (DOCS_ROOT / "CURRENT.md").read_text(encoding="utf-8")
    imports = (
        DOCS_ROOT / "codex-reference/public-imports.md"
    ).read_text(encoding="utf-8")
    for required in (
        "SimulatorStaticInput",
        "create_simulator_static_input",
        "Position → Connection → Route",
        "second authoritative computation path",
    ):
        if required not in accepted:
            raise AssertionError(f"accepted anchor missing: {required}")
    if "Dynamic Slice A–G" not in current:
        raise AssertionError("Dynamic Slice A–G status missing from CURRENT")
    if "implemented and accepted" not in current:
        raise AssertionError("accepted Dynamic Slice status missing from CURRENT")
    for deferred in (
        "Planning",
        "backend preparation/compilation",
        "dynamic execution",
        "future names, owners, packages, inputs, outputs",
        "execution chains are not frozen",
    ):
        if deferred not in current:
            raise AssertionError(f"CURRENT deferred status missing: {deferred}")
    combined_current = "\n".join(
        path.read_text(encoding="utf-8") for path in _current_documents()
    )
    for stale in (
        "G candidate",
        "G final review",
        "Dynamic Slice A–G: candidate",
    ):
        if stale in combined_current:
            raise AssertionError(f"stale accepted status: {stale}")
    if "EmulatorStaticInput" in imports:
        raise AssertionError("retired EmulatorStaticInput in public imports")
    current_status_heading = "# Current implementation status"
    status_owners = [
        path
        for path in _current_documents()
        if current_status_heading
        in path.read_text(encoding="utf-8")
    ]
    if status_owners != [DOCS_ROOT / "CURRENT.md"]:
        raise AssertionError(
            f"CURRENT status ownership mismatch: {status_owners}"
        )
    for path in _markdown_files(DOCS_ROOT / "codex-reference"):
        if path.name == "README.md":
            continue
        source = path.read_text(encoding="utf-8")
        if "## Status" in source:
            raise AssertionError(f"Codex reference retains Status heading: {path}")
        if "## Authority" not in source:
            raise AssertionError(f"Codex reference lacks Authority: {path}")
        for duplicate_state in (
            "implemented and accepted",
            "remains deferred",
            "are deferred",
            "Accepted Dynamic Slice",
            "Accepted current",
            "Accepted implemented",
        ):
            if duplicate_state in source:
                raise AssertionError(
                    f"implementation state duplicated outside CURRENT: "
                    f"{path}: {duplicate_state}"
                )
    for directory in (
        DOCS_ROOT / "human-readable",
        DOCS_ROOT / "user-guides",
    ):
        for path in _markdown_files(directory):
            source = path.read_text(encoding="utf-8")
            for duplicate_state in (
                "implemented and accepted",
                "remains deferred",
                "are deferred",
                "Accepted Dynamic Slice",
            ):
                if duplicate_state in source:
                    raise AssertionError(
                        f"implementation state duplicated outside CURRENT: "
                        f"{path}: {duplicate_state}"
                    )
    for path in _full_repository_documents():
        if (
            path == DOCS_ROOT / "CURRENT.md"
            or DOCS_ROOT / "candidates" in path.parents
            or DOCS_ROOT / "provenance" in path.parents
        ):
            continue
        source = path.read_text(encoding="utf-8")
        for duplicate_state in (
            "implemented and accepted",
            "remains deferred",
            "are deferred",
            "Accepted Dynamic Slice",
        ):
            if duplicate_state in source:
                raise AssertionError(
                    f"implementation state duplicated outside CURRENT: "
                    f"{path}: {duplicate_state}"
                )

    critical_contracts = {
        "model-contracts.md": (
            "previous `ConnectionSlice`",
            "previous `RouteSlice`",
            "unique ascending RAAN",
            "maximum_reference_distance",
            "ConnectionModel never invokes PositionModel",
        ),
        "routing-path-contracts.md": (
            "GroundStations get no G-to-G routes",
            "Satellite → GroundStation → Satellite",
            "known but currently unserved Zone",
            "currently unreachable source-target",
        ),
        "timeline-slice-difference-contracts.md": (
            "`iter_timestamps()` is the only authority",
            "consumes neither ID nor pending timestamp",
            "Generated ID/time",
            "synthetic Slice 0",
            "`N → N+1`",
            "Position motion",
        ),
    }
    for filename, required_values in critical_contracts.items():
        source = (
            DOCS_ROOT / "codex-reference" / filename
        ).read_text(encoding="utf-8")
        for required in required_values:
            if required not in source:
                raise AssertionError(
                    f"{filename} missing critical contract: {required}"
                )


def _check_public_import_mapping() -> int:
    mapping = (
        DOCS_ROOT / "codex-reference/public-imports.md"
    ).read_text(encoding="utf-8")
    rows: dict[str, tuple[str, ...]] = {}
    for match in PUBLIC_IMPORT_ROW.finditer(mapping):
        module_name = match.group("module")
        if module_name in rows:
            raise AssertionError(
                f"duplicate public import row: {module_name}"
            )
        rows[module_name] = tuple(
            BACKTICK_NAME.findall(match.group("exports"))
        )
    checked = 0
    for module_name in PUBLIC_MODULE_NAMES:
        module = importlib.import_module(module_name)
        exports = tuple(getattr(module, "__all__", ()))
        if module_name not in rows:
            raise AssertionError(f"public module missing from mapping: {module_name}")
        documented = rows[module_name]
        if len(documented) != len(set(documented)):
            raise AssertionError(
                f"duplicate export in public import row: {module_name}"
            )
        if documented != exports:
            raise AssertionError(
                f"public import row mismatch for {module_name}: "
                f"code={exports}, docs={documented}"
            )
        for name in exports:
            checked += 1
            if not hasattr(module, name):
                raise AssertionError(
                    f"{module_name} exports absent attribute {name}"
                )
    extra_rows = set(rows) - set(PUBLIC_MODULE_NAMES)
    if extra_rows:
        raise AssertionError(
            f"unregistered public import rows: {sorted(extra_rows)}"
        )
    return checked


def _check_object_model_contracts() -> int:
    path = DOCS_ROOT / "human-readable/object-model.md"
    source = path.read_text(encoding="utf-8")
    diagram = "\n".join(MERMAID_BLOCK.findall(source))
    rows = OBJECT_CONTRACT_ROW.findall(source)
    if not rows:
        raise AssertionError("object model has no public contract mapping")
    seen: set[str] = set()
    for alias, contract in rows:
        if alias in seen:
            raise AssertionError(f"duplicate object-model alias: {alias}")
        seen.add(alias)
        if f"class {alias}" not in diagram:
            raise AssertionError(
                f"object-model mapping absent from diagram: {alias}"
            )
        module_name, attribute = contract.rsplit(".", 1)
        module = importlib.import_module(module_name)
        if attribute not in getattr(module, "__all__", ()):
            raise AssertionError(
                f"object model is not a public contract: {contract}"
            )
        if not hasattr(module, attribute):
            raise AssertionError(
                f"object model target is absent: {contract}"
            )
    for required_relation in (
        'SimulationDefinition "1" *-- "1" SimulatorStaticInput',
        'SimulationDefinition "1" *-- "1" SimulationTimeline',
        'Simulator "1" *-- "1" SimulationDefinition',
        'Simulator "1" *-- "1" Topology',
        'Simulator "1" *-- "1" Slicer',
        'Slicer "1" --> "1" Topology',
        'Slicer "1" --> "1" SimulationTimeline',
        'TopologySlice "1" *-- "0..*" EarthFixedPosition',
        'TopologySlice "1" *-- "1" ConnectionSlice',
        'TopologySlice "1" *-- "1" RouteSlice',
        'UpstreamEmulator ..> SimulatorStaticInput',
        "Slicer ..> PositionModel",
        "Slicer ..> ConnectionModel",
        "Slicer ..> RouteModel",
        "CircularOrbitPositionModel ..|> PositionModel",
        "GeometricConnectionModel ..|> ConnectionModel",
        "HopCountRouteModel ..|> RouteModel",
    ):
        if required_relation not in diagram:
            raise AssertionError(
                f"object model relation missing: {required_relation}"
            )
    return len(rows)


def _public_contract(contract: str) -> object:
    module_name, attribute = contract.rsplit(".", 1)
    module = importlib.import_module(module_name)
    if attribute not in getattr(module, "__all__", ()):
        raise AssertionError(f"class detail is not a public contract: {contract}")
    if not hasattr(module, attribute):
        raise AssertionError(f"class detail target is absent: {contract}")
    return getattr(module, attribute)


def _runtime_public_members(contract: object) -> set[str]:
    members = {
        name
        for owner in getattr(contract, "__mro__", (contract,))
        for name in getattr(owner, "__dict__", {})
        if not name.startswith("_")
    }
    annotations = {
        name
        for owner in getattr(contract, "__mro__", (contract,))
        for name in getattr(owner, "__annotations__", {})
        if not name.startswith("_")
    }
    members.update(annotations)
    if is_dataclass(contract):
        members.update(field.name for field in fields(contract))
    try:
        tree = ast.parse(dedent(inspect.getsource(contract)))
    except (OSError, TypeError, IndentationError):
        return members
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Store)
            and not node.attr.startswith("_")
        ):
            members.add(node.attr)
    return members


def _direct_required_public_members(contract: object) -> set[str]:
    required: set[str] = set()
    if is_dataclass(contract):
        required.update(
            field.name for field in fields(contract)
            if not field.name.startswith("_")
        )
    if inspect.isclass(contract) and issubclass(contract, Enum):
        required.update(contract.__members__)

    seedemu_extension = getattr(contract, "__module__", "").startswith(
        "skyseed_v2.emulator.seedemu"
    )
    for name, value in getattr(contract, "__dict__", {}).items():
        if name.startswith("_"):
            continue
        is_public_callable = (
            isinstance(value, property)
            or inspect.isfunction(value)
            or isinstance(value, (classmethod, staticmethod))
        )
        if not is_public_callable:
            continue
        if seedemu_extension and any(
            name in getattr(base, "__dict__", {})
            and not getattr(base, "__module__", "").startswith("skyseed_v2.")
            for base in getattr(contract, "__mro__", ())[1:]
        ):
            # Broad upstream compatibility overrides are real but are not
            # Skyseed's local architecture contract. A README may still list
            # a deliberately important hook such as render() or getName().
            continue
        required.add(name if isinstance(value, property) else f"{name}()")

    try:
        tree = ast.parse(dedent(inspect.getsource(contract)))
    except (OSError, TypeError, IndentationError):
        return required
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Store)
            and not node.attr.startswith("_")
        ):
            required.add(node.attr)
    return required


def _public_owner_package(contract: object) -> str | None:
    module_name = getattr(contract, "__module__", "")
    candidates = tuple(
        package
        for package in DISCOVERED_PUBLIC_PACKAGES
        if module_name == package or module_name.startswith(f"{package}.")
    )
    return max(candidates, key=len) if candidates else None


def _mermaid_class_members(source: str) -> tuple[dict[str, set[str]], int]:
    members: dict[str, set[str]] = {}
    count = 0
    for mermaid in MERMAID_BLOCK.findall(source):
        for block in CLASS_BLOCK.finditer(mermaid):
            count += 1
            alias = block.group("alias")
            body = block.group("body")
            if not re.search(r"<<[^<>]+>>", body):
                raise AssertionError(
                    f"detailed class lacks role stereotype: {alias}"
                )
            members.setdefault(alias, set()).update(
                line.strip()[1:].strip()
                for line in body.splitlines()
                if line.strip().startswith("+")
            )
    return members, count


def _check_class_details() -> tuple[int, int]:
    owner_rows = dict(
        OBJECT_CONTRACT_ROW.findall(
            (DOCS_ROOT / "human-readable/object-model.md").read_text(
                encoding="utf-8"
            )
        )
    )
    detail_contracts: set[str] = set()
    detail_objects: dict[int, tuple[str, str, Path]] = {}
    documented_members: dict[str, set[str]] = {}
    class_blocks = 0
    for facade_name in DISCOVERED_PUBLIC_PACKAGES:
        path = (
            SOURCE_ROOT / Path(*facade_name.split(".")) / "README.md"
        )
        source = path.read_text(encoding="utf-8")
        block_members, block_count = _mermaid_class_members(source)
        class_blocks += block_count
        row_matches = tuple(DETAIL_CONTRACT_ROW.finditer(source))
        row_aliases = [match.group("alias") for match in row_matches]
        if len(row_aliases) != len(set(row_aliases)):
            raise AssertionError(f"duplicate class-detail row in {path}")
        rows = {
            match.group("alias"): (
                match.group("contract"),
                set(BACKTICK_NAME.findall(match.group("members"))),
            )
            for match in row_matches
        }
        if set(block_members) != set(rows):
            raise AssertionError(
                f"class detail diagram/table mismatch in {path}: "
                f"diagram={sorted(block_members)}, rows={sorted(rows)}"
            )

        facade = importlib.import_module(facade_name)
        expected_objects = {
            id(value): export_name
            for export_name in getattr(facade, "__all__", ())
            if inspect.isclass(value := getattr(facade, export_name))
            and _public_owner_package(value) == facade_name
        }
        represented_objects: set[int] = set()
        for alias, (contract_name, required) in rows.items():
            if block_members.get(alias, set()) != required:
                raise AssertionError(
                    f"class detail member/table mismatch in {path}: {alias}: "
                    f"diagram={sorted(block_members.get(alias, set()))}, "
                    f"table={sorted(required)}"
                )
            detail_contracts.add(contract_name)
            contract = _public_contract(contract_name)
            contract_id = id(contract)
            if contract_id not in expected_objects:
                raise AssertionError(
                    f"{path} documents a class not owned by {facade_name}: "
                    f"{contract_name}"
                )
            if contract_id in detail_objects:
                previous_alias, previous_contract, previous_path = (
                    detail_objects[contract_id]
                )
                raise AssertionError(
                    "one public class has multiple detailed maintenance "
                    f"sources: {previous_alias}/{previous_contract} in "
                    f"{previous_path}, {alias}/{contract_name} in {path}"
                )
            detail_objects[contract_id] = (alias, contract_name, path)
            represented_objects.add(contract_id)
            documented_members[contract_name] = required
            runtime = _runtime_public_members(contract)
            for member in required:
                runtime_name = member.removesuffix("()")
                if runtime_name not in runtime and not hasattr(
                    contract, runtime_name
                ):
                    raise AssertionError(
                        f"documented public member is absent: "
                        f"{contract_name}.{member}"
                    )
            omitted = _direct_required_public_members(contract) - required
            if omitted:
                raise AssertionError(
                    f"{path} omits derived public members for {alias}: "
                    f"{sorted(omitted)}"
                )
        if represented_objects != set(expected_objects):
            missing = [
                export_name
                for object_id, export_name in expected_objects.items()
                if object_id not in represented_objects
            ]
            raise AssertionError(
                f"{path} omits public owner/value/Protocol classes: "
                f"{sorted(missing)}"
            )

    object_source = (
        DOCS_ROOT / "human-readable/object-model.md"
    ).read_text(encoding="utf-8")
    global_members, _ = _mermaid_class_members(object_source)
    for alias, contract_name in owner_rows.items():
        contract = _public_contract(contract_name)
        detail = detail_objects.get(id(contract))
        if detail is None:
            raise AssertionError(
                f"global object has no local detailed source: {alias}: "
                f"{contract_name}"
            )
        detail_alias, detail_contract, _ = detail
        if detail_contract != contract_name:
            raise AssertionError(
                f"owner/detail class name mismatch: {alias}: "
                f"owner={contract_name}, "
                f"detail={detail_alias}/{detail_contract}"
            )
        if alias not in global_members:
            raise AssertionError(
                f"global mapped object lacks role/member class block: {alias}"
            )
        if not 1 <= len(global_members[alias]) <= 3:
            raise AssertionError(
                f"global object must show 1-3 key members: {alias}: "
                f"{sorted(global_members[alias])}"
            )
        if not global_members[alias] <= documented_members[detail_contract]:
            raise AssertionError(
                f"global members differ from local source for {alias}: "
                f"{sorted(global_members[alias] - documented_members[detail_contract])}"
            )
    return len(detail_contracts), class_blocks


def _check_import_graph_mapping() -> int:
    path = DOCS_ROOT / "human-readable/repository-map.md"
    source = path.read_text(encoding="utf-8")
    documented = frozenset(
        (match.group("source"), match.group("target"))
        for match in IMPORT_GRAPH_EDGE.finditer(source)
    )
    actual = collapsed_import_edges()
    if documented != actual:
        raise AssertionError(
            "package/import diagram mismatch: "
            f"missing={sorted(actual - documented)}, "
            f"extra={sorted(documented - actual)}"
        )
    return len(actual)


def _section(source: str, start: str, end: str) -> str:
    start_marker = f"## {start}"
    end_marker = f"## {end}"
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        raise AssertionError(
            f"README requires one {start_marker!r} and one {end_marker!r}"
        )
    start_index = source.index(start_marker)
    end_index = source.index(end_marker)
    if start_index >= end_index:
        raise AssertionError(
            f"README section order invalid: {start_marker}, {end_marker}"
        )
    return source[start_index:end_index]


def _owned_public_classes(module_name: str) -> tuple[type, ...]:
    module = importlib.import_module(module_name)
    return tuple(
        value
        for export_name in getattr(module, "__all__", ())
        if inspect.isclass(value := getattr(module, export_name))
        and _public_owner_package(value) == module_name
    )


def _normalized_node_label(label: str) -> str:
    without_html = re.sub(r"<br\s*/?>", " ", label, flags=re.IGNORECASE)
    return " ".join(without_html.split())


def _normalized_relations(
    source: str,
) -> set[tuple[str, str, str, str, str]]:
    relation_kinds = {
        "*--": "composition",
        "o--": "aggregation",
        "-->": "reference",
        "<--": "reference",
        "..>": "dependency",
        "..|>": "realization",
    }
    relations: set[tuple[str, str, str, str, str]] = set()
    for block in MERMAID_BLOCK.findall(source):
        first = next(
            (line.strip() for line in block.splitlines() if line.strip()),
            "",
        )
        if first.startswith("classDiagram"):
            for line in block.splitlines():
                match = CLASS_RELATION_LINE.match(line)
                if match is None:
                    continue
                source_name = match.group("source")
                target_name = match.group("target")
                if match.group("relation") == "<--":
                    source_name, target_name = target_name, source_name
                relations.add(
                    (
                        relation_kinds[match.group("relation")],
                        source_name,
                        target_name,
                        match.group("source_cardinality") or "",
                        match.group("target_cardinality") or "",
                    )
                )
        elif first.startswith("flowchart"):
            labels: dict[str, str] = {}
            pending: list[tuple[str, str, str]] = []
            for line in block.splitlines():
                match = FLOW_RELATION_LINE.match(line)
                if match is None:
                    continue
                source_id = match.group("source")
                target_id = match.group("target")
                if source_label := match.group("source_label"):
                    labels[source_id] = _normalized_node_label(source_label)
                if target_label := match.group("target_label"):
                    labels[target_id] = _normalized_node_label(target_label)
                pending.append(
                    (source_id, match.group("relation"), target_id)
                )
            for source_id, relation, target_id in pending:
                relations.add(
                    (
                        "handoff" if relation == "-->" else "injection",
                        labels.get(source_id, source_id),
                        labels.get(target_id, target_id),
                        "",
                        "",
                    )
                )
    return relations


def _require_normalized_relations(
    *,
    relative: str,
    source: str,
    required: tuple[tuple[str, str, str, str | None, str | None], ...],
) -> set[tuple[str, str, str, str, str]]:
    actual = _normalized_relations(source)
    missing = []
    for kind, relation_source, target, source_cardinality, target_cardinality in required:
        matches = {
            relation
            for relation in actual
            if relation[:3] == (kind, relation_source, target)
            and (
                source_cardinality is None
                or relation[3] == source_cardinality
            )
            and (
                target_cardinality is None
                or relation[4] == target_cardinality
            )
        }
        if not matches:
            missing.append(
                (
                    kind,
                    relation_source,
                    target,
                    source_cardinality,
                    target_cardinality,
                )
            )
    if missing:
        raise AssertionError(
            f"critical normalized relations missing in {relative}: {missing}"
        )
    return actual


def _check_local_assembly_maps() -> int:
    required_heading_order = (
        "Local assembly / ownership map",
        "Detailed public class/member diagrams",
        "Entry points",
        "Dependencies",
        "Examples and tests",
        "Maintenance checklist",
    )
    checked = 0
    for module_name, path in zip(
        DISCOVERED_PUBLIC_PACKAGES, PUBLIC_PACKAGE_READMES, strict=True
    ):
        source = path.read_text(encoding="utf-8")
        positions = []
        for heading in required_heading_order:
            marker = f"## {heading}"
            if source.count(marker) != 1:
                raise AssertionError(
                    f"{path} requires one {marker!r} heading"
                )
            positions.append(source.index(marker))
        if positions != sorted(positions):
            raise AssertionError(f"owner README headings out of order: {path}")

        local = _section(
            source,
            "Local assembly / ownership map",
            "Detailed public class/member diagrams",
        )
        if not MERMAID_BLOCK.search(local):
            raise AssertionError(f"local assembly map lacks Mermaid: {path}")
        for contract in _owned_public_classes(module_name):
            if contract.__name__ not in local:
                raise AssertionError(
                    f"local assembly map omits owned public class or explicit "
                    f"standalone value/helper: {path}: {contract.__name__}"
                )
        checked += 1

    critical_readmes = {
        "src/skyseed_v2/simulator/topology/README.md": (
            ("composition", "Topology", "SkyseedAutonomousSystem", "1", "0..*"),
            ("composition", "Topology", "ZoneLayout", "1", "0..1"),
            ("composition", "SkyseedAutonomousSystem", "Shell", "1", "0..*"),
            ("composition", "SkyseedAutonomousSystem", "GroundStation", "1", "0..*"),
            ("composition", "Shell", "Orbit", "1", "0..*"),
            ("composition", "Orbit", "Satellite", "1", "0..*"),
            ("composition", "ZoneLayout", "Zone", "1", "1..*"),
            ("composition", "Zone", "LatitudeLongitudeRectangle", "1", "1"),
            ("dependency", "ZoneLayout", "SphericalCap", None, None),
            ("reference", "ZoneLayout", "ZoneMembershipModel", "1", "1"),
            ("realization", "DefaultZoneMembershipModel", "ZoneMembershipModel", None, None),
        ),
        "src/skyseed_v2/simulator/slicing/README.md": (
            ("composition", "Slicer", "TopologySlice", "1", "1..*"),
            ("composition", "TopologySlice", "EarthFixedPosition", "1", "0..*"),
            ("composition", "TopologySlice", "ConnectionSlice", "1", "1"),
            ("composition", "TopologySlice", "RouteSlice", "1", "1"),
            ("reference", "Slicer", "Topology", "1", "1"),
            ("reference", "Slicer", "SimulationTimeline", "1", "1"),
            ("dependency", "Slicer", "PositionModel", None, None),
            ("dependency", "Slicer", "ConnectionModel", None, None),
            ("dependency", "Slicer", "RouteModel", None, None),
            ("dependency", "Slicer", "TopologySlice", None, None),
            ("dependency", "Slicer", "TopologyDifference", None, None),
        ),
        "src/skyseed_v2/simulator/README.md": (
            ("composition", "SimulationDefinition", "SimulatorStaticInput", "1", "1"),
            ("composition", "SimulationDefinition", "SimulationTimeline", "1", "1"),
            ("composition", "Simulator", "SimulationDefinition", "1", "1"),
            ("composition", "Simulator", "Topology", "1", "1"),
            ("composition", "Simulator", "Slicer", "1", "1"),
        ),
        "src/skyseed_v2/simulator/input/README.md": (
            ("composition", "SimulatorStaticInput", "SatelliteSimulationInput", "1", "0..*"),
            ("composition", "SimulatorStaticInput", "GroundStationSimulationInput", "1", "0..*"),
            ("handoff", "emulator_to_simulator", "SimulatorStaticInput", None, None),
            ("handoff", "SimulatorStaticInput", "SimulationDefinition", None, None),
            ("handoff", "SimulationDefinition", "Simulator", None, None),
            ("handoff", "Simulator", "create_topology()", None, None),
            ("handoff", "create_topology()", "Topology", None, None),
        ),
        "src/skyseed_v2/simulator/models/README.md": (
            ("realization", "CircularOrbitPositionModel", "PositionModel", None, None),
            ("realization", "GeometricConnectionModel", "ConnectionModel", None, None),
            ("realization", "HopCountRouteModel", "RouteModel", None, None),
        ),
        "src/skyseed_v2/emulator/entities/README.md": (
            ("composition", "SkyseedAutonomousSystem", "GroundStation", "1", "0..*"),
            ("composition", "SkyseedAutonomousSystem", "Shell", "1", "0..*"),
            ("composition", "Shell", "Orbit", "1", "0..*"),
            ("composition", "Orbit", "Satellite", "1", "0..*"),
        ),
        "src/skyseed_v2/emulator/seedemu/README.md": (
            ("composition", "SatelliteBase", "SkyseedAutonomousSystem", "1", "0..*"),
            ("composition", "SkyseedAutonomousSystem", "GroundStation", "1", "0..*"),
            ("composition", "SkyseedAutonomousSystem", "Shell", "1", "0..*"),
            ("dependency", "SkyseedDockerCompiler", "Emulator", None, None),
        ),
        "src/skyseed_v2/configuration/emulator/README.md": (
            ("handoff", "YAML / handwritten declarations", "StaticEmulatorConfig", None, None),
            ("handoff", "StaticEmulatorConfig", "create_static_emulator()", None, None),
            ("handoff", "create_static_emulator()", "upstream Emulator object graph", None, None),
        ),
        "src/skyseed_v2/configuration/simulator/README.md": (
            ("handoff", "YAML / handwritten Zone definitions", "ZoneLayoutDefinition", None, None),
            ("handoff", "ZoneLayoutDefinition", "Simulator.create_topology()", None, None),
            ("handoff", "Simulator.create_topology()", "Topology", None, None),
            ("handoff", "Topology", "ZoneLayout", None, None),
        ),
        "src/skyseed_v2/compose/README.md": (
            ("handoff", "compiled Compose project", "ComposeProjectLauncher", None, None),
            ("handoff", "ComposeProjectLauncher", "launch / verify", None, None),
            ("handoff", "launch / verify", "ComposeMaterializationResult", None, None),
            ("composition", "ComposeMaterializationResult", "ComposeBatchResult", "1", "0..*"),
        ),
        "src/skyseed_v2/simulator/geometry/README.md": (
            ("handoff", "EarthFixedPosition", "coordinate conversion functions", None, None),
            ("handoff", "coordinate conversion functions", "EarthFixedPosition", None, None),
            ("handoff", "coordinate conversion functions", "distance / elevation / LOS calculations", None, None),
            ("handoff", "EarthFixedPosition + geometry functions", "PositionModel implementations", None, None),
            ("handoff", "EarthFixedPosition + geometry functions", "ConnectionModel implementations", None, None),
            ("handoff", "EarthFixedPosition + geometry functions", "Inspection display analysis", None, None),
        ),
        "src/skyseed_v2/shared/README.md": (
            ("handoff", "NodeId", "SatelliteSimulationInput", None, None),
            ("handoff", "NodeId", "GroundStationSimulationInput", None, None),
            ("handoff", "NodeId", "Topology node identities", None, None),
            ("handoff", "NodeId", "Connection / Route / Slice values", None, None),
        ),
    }
    relation_sets: dict[
        str, set[tuple[str, str, str, str, str]]
    ] = {}
    for relative, required_relations in critical_readmes.items():
        path = PROJECT_ROOT / relative
        source = path.read_text(encoding="utf-8")
        local = _section(
            source,
            "Local assembly / ownership map",
            "Detailed public class/member diagrams",
        )
        relation_sets[relative] = _require_normalized_relations(
            relative=relative,
            source=local,
            required=required_relations,
        )

    forbidden_role_classes = (
        "PreviousRetainedTopologySlice",
        "CurrentRetainedTopologySlice",
    )
    owner_text = "\n".join(
        path.read_text(encoding="utf-8") for path in PUBLIC_PACKAGE_READMES
    )
    for forbidden in forbidden_role_classes:
        if forbidden in owner_text:
            raise AssertionError(
                f"owner README uses fictional role class: {forbidden}"
            )

    models_source = (
        PRODUCTION_ROOT / "simulator/models/README.md"
    ).read_text(encoding="utf-8")
    for mermaid in MERMAID_BLOCK.findall(models_source):
        if "Slicer" in mermaid:
            raise AssertionError(
                "Models README must not make Slicer depend on built-in classes"
            )

    topology_path = PRODUCTION_ROOT / "simulator/topology/README.md"
    topology_source = topology_path.read_text(encoding="utf-8")
    topology_relations = _normalized_relations(topology_source)
    if any(
        kind == "composition"
        and source_name == "Zone"
        and target == "SphericalCap"
        for kind, source_name, target, _, _ in topology_relations
    ):
        raise AssertionError("Zone must not own or compose SphericalCap")
    rectangle_relations = {
        relation
        for relation in topology_relations
        if relation[:3]
        == ("composition", "Zone", "LatitudeLongitudeRectangle")
    }
    if not rectangle_relations or any(
        source_cardinality != "1" or target_cardinality != "1"
        for _, _, _, source_cardinality, target_cardinality
        in rectangle_relations
    ):
        raise AssertionError(
            "Zone must compose exactly one LatitudeLongitudeRectangle"
        )

    compose_source = (
        PRODUCTION_ROOT / "compose/README.md"
    ).read_text(encoding="utf-8")
    if "teardown evidence" in compose_source.casefold():
        raise AssertionError(
            "Compose README must not claim teardown evidence"
        )

    simulator_source = (
        PRODUCTION_ROOT / "simulator/README.md"
    ).read_text(encoding="utf-8")
    simulator_local = _section(
        simulator_source,
        "Local assembly / ownership map",
        "Detailed public class/member diagrams",
    )
    if len(MERMAID_BLOCK.findall(simulator_local)) != 1:
        raise AssertionError(
            "Simulator README requires one local assembly diagram"
        )
    simulator_details = _section(
        simulator_source,
        "Detailed public class/member diagrams",
        "Entry points",
    )
    duplicated_creation_relations = {
        relation
        for relation in _normalized_relations(simulator_details)
        if relation[1] == "Simulator"
        and relation[2] in {"Topology", "Slicer"}
    }
    if duplicated_creation_relations:
        raise AssertionError(
            "Simulator detailed members duplicate local creation relations: "
            f"{sorted(duplicated_creation_relations)}"
        )

    root_local = _section(
        (PRODUCTION_ROOT / "README.md").read_text(encoding="utf-8"),
        "Local assembly / ownership map",
        "Detailed public class/member diagrams",
    )
    root_relations = _normalized_relations(root_local)
    if any(
        kind not in {"handoff"}
        or source_name in {
            "PositionModel",
            "ConnectionModel",
            "RouteModel",
            "Topology",
            "Slicer",
        }
        or target in {
            "PositionModel",
            "ConnectionModel",
            "RouteModel",
            "Topology",
            "Slicer",
        }
        for kind, source_name, target, _, _ in root_relations
    ):
        raise AssertionError(
            "production root README must remain a top-level data-handoff map"
        )
    return checked


def _check_repository_growth() -> None:
    discovered = set(DISCOVERED_PUBLIC_PACKAGES)
    approved = set(APPROVED_PUBLIC_PACKAGES)
    if discovered != approved:
        raise AssertionError(
            "public package approval mismatch: "
            f"unapproved={sorted(discovered - approved)}, "
            f"retired={sorted(approved - discovered)}"
        )

    maintenance_path = DOCS_ROOT / "codex-reference/maintenance-map.md"
    maintenance = maintenance_path.read_text(encoding="utf-8")
    rows = {
        match.group("module"): match.group("readme")
        for match in PUBLIC_PACKAGE_ROW.finditer(maintenance)
    }
    if set(rows) != discovered:
        raise AssertionError(
            "public package classification mismatch: "
            f"missing={sorted(discovered - set(rows))}, "
            f"extra={sorted(set(rows) - discovered)}"
        )
    for module_name, readme in rows.items():
        expected = (
            SOURCE_ROOT / Path(*module_name.split(".")) / "README.md"
        ).relative_to(PROJECT_ROOT).as_posix()
        if readme != expected:
            raise AssertionError(
                f"public package README mapping mismatch: {module_name}: "
                f"{readme} != {expected}"
            )
        if not (PROJECT_ROOT / readme).is_file():
            raise AssertionError(f"public package README is absent: {readme}")

    forbidden_future_package_parts = {
        "backend",
        "execution",
        "planner",
        "planning",
        "preparer",
        "runner",
        "runtime",
    }
    for init_path in PRODUCTION_ROOT.rglob("__init__.py"):
        overlap = forbidden_future_package_parts.intersection(init_path.parts)
        if overlap:
            raise AssertionError(
                f"unapproved future capability package exists: "
                f"{init_path}: {sorted(overlap)}"
            )
    for required in (
        "New production code first belongs to an existing owner package",
        "File count and single-file line count are not",
        "prior user/GPT approval",
        "Do not create empty directories for future capabilities",
        "Compatibility forwarding",
        "simultaneous old/new import paths",
        "A package split based only on adding two modules is prohibited",
    ):
        if required not in maintenance:
            raise AssertionError(
                f"repository growth rule missing: {required}"
            )


def _check_maintenance_mapping() -> None:
    maintenance = (
        DOCS_ROOT / "codex-reference/maintenance-map.md"
    ).read_text(encoding="utf-8")
    for path in OWNER_READMES:
        if not path.is_file():
            raise AssertionError(f"owner README missing: {path}")
        source = path.read_text(encoding="utf-8")
        if "mermaid" not in source:
            raise AssertionError(f"owner README needs Mermaid: {path}")
    for required in (
        "configuration/emulator/",
        "emulator/seedemu/",
        "emulator_to_simulator.py",
        "simulator/topology/",
        "simulator/slicing/",
        "tools/dynamic_slice/",
        "src/skyseed_v2/simulator/models/README.md",
        "src/skyseed_v2/shared/README.md",
    ):
        if required not in maintenance:
            raise AssertionError(f"maintenance path missing: {required}")


def _check_uniform_contracts() -> None:
    headings = (
        "Authority",
        "Scope",
        "Owners and consumers",
        "Contract",
        "Invariants",
        "Failure semantics",
        "Prohibited behavior",
        "Extension points",
        "Maintenance",
    )
    for filename in (
        "emulator-construction.md",
        "compose-launcher.md",
        "inspection-tools.md",
    ):
        source = (DOCS_ROOT / "codex-reference" / filename).read_text(
            encoding="utf-8"
        )
        positions = []
        for heading in headings:
            marker = f"## {heading}"
            if source.count(marker) != 1:
                raise AssertionError(
                    f"{filename} requires one {marker!r} heading"
                )
            positions.append(source.index(marker))
        if positions != sorted(positions):
            raise AssertionError(f"{filename} contract headings are out of order")


def run(scope: str) -> None:
    _check_identity_layout()
    current = _current_documents()
    documents = (
        tuple(sorted({*current, *OWNER_READMES}))
        if scope == "focused"
        else _full_repository_documents()
    )
    links = 0
    mermaid = 0
    for path in documents:
        _check_basic_markdown(path)
        links += _check_links(path)
        mermaid += _check_mermaid_static(path)
    _check_anchor_consistency()
    imports = _check_public_import_mapping()
    object_contracts = _check_object_model_contracts()
    detail_contracts, detail_blocks = _check_class_details()
    import_edges = _check_import_graph_mapping()
    local_maps = _check_local_assembly_maps()
    _check_repository_growth()
    _check_maintenance_mapping()
    _check_uniform_contracts()
    print(f"documentation scope: {scope}")
    print(f"Markdown files: {len(documents)}")
    print(f"local links: {links}")
    print(f"Mermaid blocks: {mermaid}")
    print("Mermaid validation: structural precheck only")
    print(
        "Mermaid visual rendering: run scripts/check_mermaid_render.py "
        "with pinned mermaid.min.js"
    )
    print(f"public export entries: {imports}")
    print(f"object-model public contracts: {object_contracts}")
    print(f"detailed class public contracts: {detail_contracts}")
    print(f"detailed class blocks: {detail_blocks}")
    print(f"public package local assembly maps: {local_maps}")
    print(f"documented collapsed import edges: {import_edges}")
    print("documentation result: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("focused", "full"), required=True)
    arguments = parser.parse_args()
    run(arguments.scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
