#!/usr/bin/env python3
"""Validate the authoritative Round-B documentation contract."""

from __future__ import annotations

import ast
import argparse
from dataclasses import fields, is_dataclass
from enum import Enum
import importlib
import inspect
import re
from pathlib import Path
from textwrap import dedent
from urllib.parse import unquote

from check_import_dependencies import collapsed_import_edges


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT
SOURCE_ROOT = ROOT / "src"
PRODUCTION_ROOT = SOURCE_ROOT / "skyseed_v2"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
FENCE = re.compile(r"^```", re.MULTILINE)
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
IMPORT_GRAPH_EDGE = re.compile(
    r"^\s*(?P<source>[A-Z][A-Z_]*)\s+-->\s+"
    r"(?P<target>[A-Z][A-Z_]*)\s*$",
    re.MULTILINE,
)


def _module_name_for_source(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT)
    parts = (
        relative.parts[:-1]
        if path.name == "__init__.py"
        else relative.with_suffix("").parts
    )
    return ".".join(parts)


PUBLIC_PACKAGE_INITS = tuple(sorted(PRODUCTION_ROOT.rglob("__init__.py")))
DISCOVERED_PUBLIC_PACKAGES = tuple(
    sorted(
        _module_name_for_source(path)
        for path in PUBLIC_PACKAGE_INITS
        if re.search(
            r"^__all__\b",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
)
PUBLIC_MODULE_NAMES = tuple(
    sorted((*DISCOVERED_PUBLIC_PACKAGES, "skyseed_v2.emulator_to_simulator"))
)
PUBLIC_PACKAGE_READMES = tuple(
    SOURCE_ROOT / Path(*module_name.split(".")) / "README.md"
    for module_name in DISCOVERED_PUBLIC_PACKAGES
)

REQUIRED_DOCUMENTS = (
    "README.md",
    "docs/README.md",
    "docs/CURRENT.md",
    "docs/accepted-design.md",
    "docs/codex-reference/README.md",
    "docs/codex-reference/physical-world-zone-contracts.md",
    "docs/codex-reference/operational-values.md",
    "docs/codex-reference/model-contracts.md",
    "docs/codex-reference/planning-contracts.md",
    "docs/codex-reference/timeline-plan-contracts.md",
    "docs/codex-reference/routing-path-contracts.md",
    "docs/codex-reference/bpf-compiler.md",
    "docs/codex-reference/producer-runtime-contracts.md",
    "docs/codex-reference/ownership-lifecycle.md",
    "docs/codex-reference/public-imports.md",
    "docs/codex-reference/inspection-tools.md",
    "docs/human-readable/system-overview.md",
    "docs/human-readable/simulator-chain.md",
    "docs/human-readable/object-model.md",
    "docs/human-readable/terminology.md",
    "docs/human-readable/traceability-map.md",
    "docs/user-guides/getting-started.md",
    "docs/user-guides/inspection-tools.md",
    "examples/README.md",
    "examples/complete_static_emulator/README.md",
    "examples/mini_satellite_internet/README.md",
    "examples/starlink_gen1_satellite_internet/README.md",
    "src/skyseed_v2/README.md",
    "src/skyseed_v2/simulator/README.md",
    "src/skyseed_v2/simulator/physical/README.md",
    "src/skyseed_v2/simulator/planning/README.md",
    "src/skyseed_v2/backend/bpf/README.md",
    "tools/README.md",
    "tools/planner/README.md",
    "tools/bpf_compiler/README.md",
    "tools/traffic/README.md",
)

REMOVED_DOCUMENTS = (
    "docs/codex-reference/dynamic-" + "sli" + "cing-contracts.md",
    "docs/codex-reference/timeline-" + "slice-difference-contracts.md",
    "docs/codex-reference/position-" + "connection-values.md",
    "docs/codex-reference/" + "topology-zone-contracts.md",
)

REMOVED_DIRECTORIES = (
    "src/skyseed_v2/simulator/" + "sli" + "cing",
    "src/skyseed_v2/simulator/" + "topology",
    "tools/" + "slice",
    "tools/" + "topology",
)

RETIRED_TEXT = (
    "Sli" + "cer",
    "Topology" + "Slice",
    "Topology" + "Difference",
    "Connection" + "Slice",
    "Route" + "Slice",
    "simulator." + "sli" + "cing",
    "tools." + "slice",
    "--through " + "slicer",
)

EXPECTED_SIMULATOR_EXPORTS = {
    "PhysicalWorld",
    "PhysicalFrame",
    "PositionModel",
    "PhysicalLinkModel",
    "PhysicalLinkRequest",
    "PhysicalLinkObservation",
    "Planner",
    "PlannedOperationalState",
    "PlanTransition",
    "Plan",
    "InterSatelliteConnectionPlanningModel",
    "GroundStationAccessPlanningModel",
    "ZoneServicePlanningModel",
    "ParameterPlanningModel",
    "RoutePlanningModel",
    "CoordinationPolicy",
    "PlanningWindowPolicy",
    "FrameBatchPlanningWindowPolicy",
    "apply_plan_transition",
    "apply_plan",
}

EXPECTED_PLAN_FIELDS = (
    "plan_id",
    "previous_plan_id",
    "physical_world_fingerprint",
    "input_state_fingerprint",
    "output_state_fingerprint",
    "observation_interval",
    "start_timestamp",
    "end_timestamp",
    "transitions",
    "semantic_event_precedences",
)

EXPECTED_TRANSITION_FIELDS = (
    "timestamp",
    "established_connections",
    "withdrawn_connection_ids",
    "parameter_updated_connections",
    "zone_service_transitions",
    "route_outcome_transitions",
)


OWNER_READMES = tuple(sorted((ROOT / "src").rglob("README.md")))


def _current_documents() -> tuple[Path, ...]:
    """Return current repository architecture documents for visual checking."""

    return tuple(
        path
        for path in _authoritative_markdown()
        if path == ROOT / "README.md" or "docs" in path.relative_to(ROOT).parts
    )


def _full_repository_documents() -> tuple[Path, ...]:
    """Return every repository Markdown document for the full visual gate."""

    return tuple(
        sorted(
            path
            for path in ROOT.rglob("*.md")
            if ".git" not in path.parts and "build" not in path.parts
        )
    )


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _authoritative_markdown() -> tuple[Path, ...]:
    values = {ROOT / "README.md"}
    for pattern in (
        "docs/*.md",
        "docs/codex-reference/*.md",
        "docs/human-readable/*.md",
        "docs/user-guides/*.md",
        "examples/**/*.md",
        "src/**/*.md",
        "tools/**/*.md",
    ):
        values.update(ROOT.glob(pattern))
    return tuple(
        sorted(
            value
            for value in values
            if value.is_file()
            and "docs/provenance" not in value.relative_to(ROOT).as_posix()
            and "docs/candidates" not in value.relative_to(ROOT).as_posix()
        )
    )


def _require_files() -> None:
    missing = [value for value in REQUIRED_DOCUMENTS if not (ROOT / value).is_file()]
    if missing:
        raise AssertionError(f"required documentation missing: {missing}")
    unexpected = [value for value in REMOVED_DOCUMENTS if (ROOT / value).exists()]
    if unexpected:
        raise AssertionError(f"removed documentation remains: {unexpected}")
    directories = [value for value in REMOVED_DIRECTORIES if (ROOT / value).exists()]
    if directories:
        raise AssertionError(f"removed package/Tool directory remains: {directories}")


def _require_current_architecture_text() -> None:
    accepted = _read("docs/accepted-design.md")
    current = _read("docs/CURRENT.md")
    planning = _read("docs/codex-reference/planning-contracts.md")
    compiler = _read("docs/codex-reference/bpf-compiler.md")
    producer = _read("docs/codex-reference/producer-runtime-contracts.md")
    inspection = _read("docs/codex-reference/inspection-tools.md")

    required_by_document = {
        "accepted-design": (
            "SimulationTimeline",
            "PhysicalWorld.frame_at(timestamp)",
            "PhysicalLinkObservation",
            "PlannedOperationalState",
            "BpfCompiledRealization",
            "frames_per_batch=32",
            "independent publication lookahead",
            "121 formal transitions",
            "4 Plans",
            "persisted-only",
            "Before `BpfRunner` construction",
            "sealed Runner artifact as the semantic payload owner",
            "`runtime/execution.json` anchors",
            "`runner-total`",
            "`traffic-total`",
        ),
        "CURRENT": (
            "PhysicalWorld / PhysicalFrame",
            "PhysicalLinkObservation",
            "Planner / PlannedOperationalState",
            "BPF Compiler / BpfCompiledRealization",
            "120 formal Main activations",
        ),
        "planning": (
            "apply_plan_transition",
            "apply_plan",
            "frames_per_batch = 32",
            "no independent lookahead wait",
            "121 disjoint formal transitions across 4 Plans",
        ),
        "compiler": (
            "compile_bpf(plan, physical_world, previous_realization)",
            "output_planned_state",
            "BpfCompiledActivationBoundary",
            "does not rerun",
        ),
        "producer": (
            "produce_next_realization()",
            "physical-frames.jsonl",
            "planning.jsonl",
            "compiler.jsonl",
            "precomputed",
            "Before `BpfRunner` construction",
            "mmap",
            "one-shot Preparation",
            "semantic_payload_owner",
            "manifest_sha256",
            "successful runtime outcome",
        ),
        "inspection": (
            "PHYSICAL CANDIDATE",
            "SELECTED / ACQUIRING",
            "OPERATIONAL CONNECTION",
            "ROUTED / UNREACHABLE",
            "COMPILED",
            "REALIZED",
            "`runner-total`",
            "`traffic-total`",
            "combined, invocation-owned stream",
            "successful runtime outcome",
        ),
    }
    contents = {
        "accepted-design": accepted,
        "CURRENT": current,
        "planning": planning,
        "compiler": compiler,
        "producer": producer,
        "inspection": inspection,
    }
    normalized_contents = {
        document: re.sub(r"\s+", " ", text)
        for document, text in contents.items()
    }
    for document, needles in required_by_document.items():
        absent = [
            needle
            for needle in needles
            if re.sub(r"\s+", " ", needle) not in normalized_contents[document]
        ]
        if absent:
            raise AssertionError(f"{document} missing required contract text: {absent}")

    stale_artifact_claims = {
        "accepted-design": ("Plan/realization lineage and compiled facts",),
        "producer": ("activation and intended-state facts",),
    }
    for document, needles in stale_artifact_claims.items():
        present = [
            needle
            for needle in needles
            if re.sub(r"\s+", " ", needle) in normalized_contents[document]
        ]
        if present:
            raise AssertionError(
                f"{document} retains overbroad artifact claims: {present}"
            )


def _reject_retired_text(markdown_files: tuple[Path, ...]) -> None:
    failures: list[str] = []
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        for token in RETIRED_TEXT:
            if token in text:
                failures.append(f"{path.relative_to(ROOT)}: {token}")
    if failures:
        raise AssertionError("retired current-document text found:\n" + "\n".join(failures))


def _reject_retired_physical_frame_tool_claims(
    markdown_files: tuple[Path, ...],
) -> None:
    """Reject maintained-Tool claims without banning domain PhysicalFrame prose."""

    patterns = (
        re.compile(
            r"\bPhysical ?Frame Tool (?:remains|is maintained|is available)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bmaintained inspection surfaces are[^\n]*\bPhysical Frame\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bDetached Tools render[^\n]*\bPhysical Frame\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bmap-first Physical World/Physical ?Frame/Planner views\b",
            re.IGNORECASE,
        ),
    )
    failures: list[str] = []
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            match = pattern.search(text)
            if match is not None:
                failures.append(
                    f"{path.relative_to(ROOT)}: {match.group(0)}"
                )
    if failures:
        raise AssertionError(
            "retired standalone PhysicalFrame Tool claim found:\n"
            + "\n".join(failures)
        )


def _validate_links(markdown_files: tuple[Path, ...]) -> None:
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    failures: list[str] = []
    for path in markdown_files:
        for raw_target in link_pattern.findall(path.read_text(encoding="utf-8")):
            target = raw_target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / unquote(target)).resolve()
            if not resolved.exists():
                failures.append(f"{path.relative_to(ROOT)} -> {raw_target}")
    if failures:
        raise AssertionError("broken documentation links:\n" + "\n".join(failures))


def _parse(path: str) -> ast.Module:
    return ast.parse(_read(path), filename=path)


def _literal_all(path: str) -> tuple[str, ...]:
    module = _parse(path)
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, (list, tuple)) or not all(
                isinstance(item, str) for item in value
            ):
                raise AssertionError(f"{path} __all__ is not a literal string sequence")
            return tuple(value)
    raise AssertionError(f"{path} has no literal __all__")


def _check_public_import_mapping() -> int:
    """Require the documented table to equal every public ``__all__`` exactly."""

    mapping = _read("docs/codex-reference/public-imports.md")
    rows: dict[str, tuple[str, ...]] = {}
    for match in PUBLIC_IMPORT_ROW.finditer(mapping):
        module_name = match.group("module")
        if module_name in rows:
            raise AssertionError(f"duplicate public import row: {module_name}")
        rows[module_name] = tuple(
            BACKTICK_NAME.findall(match.group("exports"))
        )
    checked = 0
    for module_name in PUBLIC_MODULE_NAMES:
        module = importlib.import_module(module_name)
        exports = tuple(getattr(module, "__all__", ()))
        documented = rows.get(module_name)
        if documented is None:
            raise AssertionError(
                f"public module missing from exact mapping: {module_name}"
            )
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


def _public_owner_package(contract: object) -> str | None:
    module_name = getattr(contract, "__module__", "")
    candidates = tuple(
        package
        for package in DISCOVERED_PUBLIC_PACKAGES
        if module_name == package or module_name.startswith(f"{package}.")
    )
    return max(candidates, key=len) if candidates else None


def _owned_public_classes(module_name: str) -> tuple[type, ...]:
    module = importlib.import_module(module_name)
    return tuple(
        value
        for export_name in getattr(module, "__all__", ())
        if inspect.isclass(value := getattr(module, export_name))
        and _public_owner_package(value) == module_name
    )


def _runtime_public_members(contract: object) -> set[str]:
    members = {
        name
        for owner in getattr(contract, "__mro__", (contract,))
        for name in getattr(owner, "__dict__", {})
        if not name.startswith("_")
    }
    members.update(
        name
        for owner in getattr(contract, "__mro__", (contract,))
        for name in getattr(owner, "__annotations__", {})
        if not name.startswith("_")
    )
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
    required: set[str] = {
        name for name in getattr(contract, "__annotations__", {})
        if not name.startswith("_")
    }
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


def _public_contract(contract: str) -> object:
    module_name, attribute = contract.rsplit(".", 1)
    module = importlib.import_module(module_name)
    if attribute not in getattr(module, "__all__", ()):
        raise AssertionError(f"documented class is not public: {contract}")
    if not hasattr(module, attribute):
        raise AssertionError(f"documented public class is absent: {contract}")
    return getattr(module, attribute)


def _heading_section(source: str, heading: str) -> str:
    marker = f"## {heading}"
    if source.count(marker) != 1:
        raise AssertionError(f"README requires exactly one {marker!r}")
    start = source.index(marker) + len(marker)
    following = re.search(r"^##\s+", source[start:], re.MULTILINE)
    end = len(source) if following is None else start + following.start()
    return source[start:end]


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
            members.setdefault(alias, set()).update({
                line.strip()[1:].strip()
                for line in body.splitlines()
                if line.strip().startswith("+")
            })
    return members, count


def _check_class_details() -> tuple[int, int, dict[int, tuple[str, set[str]]]]:
    detail_objects: dict[int, tuple[str, set[str]]] = {}
    detail_contracts = 0
    detail_blocks = 0
    for module_name, path in zip(
        DISCOVERED_PUBLIC_PACKAGES, PUBLIC_PACKAGE_READMES, strict=True
    ):
        expected = {
            id(value): (export_name, value)
            for export_name in getattr(
                importlib.import_module(module_name), "__all__", ()
            )
            if inspect.isclass(value := getattr(
                importlib.import_module(module_name), export_name
            ))
            and _public_owner_package(value) == module_name
        }
        if not expected:
            continue
        source = path.read_text(encoding="utf-8")
        section = _heading_section(
            source, "Detailed public class/member diagrams"
        )
        block_members, block_count = _mermaid_class_members(section)
        detail_blocks += block_count
        matches = tuple(DETAIL_CONTRACT_ROW.finditer(section))
        aliases = [match.group("alias") for match in matches]
        if len(aliases) != len(set(aliases)):
            raise AssertionError(f"duplicate class-detail row in {path}")
        rows = {
            match.group("alias"): (
                match.group("contract"),
                set(BACKTICK_NAME.findall(match.group("members"))),
            )
            for match in matches
        }
        if set(block_members) != set(rows):
            raise AssertionError(
                f"class detail diagram/table mismatch in {path}: "
                f"diagram={sorted(block_members)}, rows={sorted(rows)}"
            )
        represented: set[int] = set()
        for alias, (contract_name, documented) in rows.items():
            if block_members[alias] != documented:
                raise AssertionError(
                    f"class detail member/table mismatch in {path}: {alias}: "
                    f"diagram={sorted(block_members[alias])}, "
                    f"table={sorted(documented)}"
                )
            contract = _public_contract(contract_name)
            contract_id = id(contract)
            if contract_id not in expected:
                raise AssertionError(
                    f"{path} documents a class not owned by {module_name}: "
                    f"{contract_name}"
                )
            if contract_id in detail_objects:
                raise AssertionError(
                    f"one public class has multiple detailed owner sources: "
                    f"{contract_name}"
                )
            required = _direct_required_public_members(contract)
            if documented != required:
                raise AssertionError(
                    f"{path} public member inventory differs for {alias}: "
                    f"required={sorted(required)}, docs={sorted(documented)}"
                )
            runtime = _runtime_public_members(contract)
            absent = {
                member
                for member in documented
                if member.removesuffix("()") not in runtime
                and not hasattr(contract, member.removesuffix("()"))
            }
            if absent:
                raise AssertionError(
                    f"documented public members are absent for {alias}: "
                    f"{sorted(absent)}"
                )
            represented.add(contract_id)
            detail_objects[contract_id] = (alias, documented)
            detail_contracts += 1
        if represented != set(expected):
            missing = sorted(
                export_name
                for contract_id, (export_name, _) in expected.items()
                if contract_id not in represented
            )
            raise AssertionError(
                f"{path} omits owned public classes: {missing}"
            )
    return detail_contracts, detail_blocks, detail_objects


def _check_object_model_contracts(
    detail_objects: dict[int, tuple[str, set[str]]],
) -> int:
    path = ROOT / "docs/human-readable/object-model.md"
    source = path.read_text(encoding="utf-8")
    global_members, _ = _mermaid_class_members(source)
    matches = tuple(OBJECT_CONTRACT_ROW.finditer(source))
    aliases = [match.group("alias") for match in matches]
    if not aliases:
        raise AssertionError("object model has no public contract mapping")
    if len(aliases) != len(set(aliases)):
        raise AssertionError("object model has duplicate public contract aliases")
    rows = {
        match.group("alias"): match.group("contract") for match in matches
    }
    if set(global_members) != set(rows):
        raise AssertionError(
            "object-model class blocks and public mapping differ: "
            f"diagram={sorted(global_members)}, rows={sorted(rows)}"
        )
    represented: set[int] = set()
    for alias, contract_name in rows.items():
        contract = _public_contract(contract_name)
        contract_id = id(contract)
        if contract_id in represented:
            raise AssertionError(
                f"object model maps one public class more than once: {contract_name}"
            )
        represented.add(contract_id)
        detail = detail_objects.get(contract_id)
        if detail is None:
            raise AssertionError(
                f"global object has no owner README detail: {alias}: "
                f"{contract_name}"
            )
        key_members = global_members[alias]
        if not 1 <= len(key_members) <= 3:
            raise AssertionError(
                f"global object must show 1-3 key members: {alias}: "
                f"{sorted(key_members)}"
            )
        _, owner_members = detail
        if not key_members <= owner_members:
            raise AssertionError(
                f"global members differ from owner README for {alias}: "
                f"{sorted(key_members - owner_members)}"
            )

    diagrams = "\n".join(MERMAID_BLOCK.findall(source))
    required_relations = (
        'SimulationDefinition "1" *-- "1" SimulationTimeline',
        'Simulator "1" *-- "1" SimulationDefinition',
        'Simulator "1" *-- "1" PhysicalWorld',
        'Simulator "1" *-- "1" Planner',
        "PhysicalWorld ..> PhysicalFrame",
        'PhysicalLinkObservation "1" *-- "0..*" InterSatelliteLinkCandidate',
        'PlannedOperationalState "1" *-- "0..*" OperationalConnection',
        'Plan "1" *-- "1..*" PlanTransition',
        'Plan "1" *-- "0..*" SemanticEventPrecedence',
        'BpfCompiledRealization "1" *-- "1..*" BpfCompiledActivationBoundary',
        'BpfCompiledRealization "1" *-- "1" PlannedOperationalState',
        "BpfCompiledActivationBoundary ..> PlanTransition : fingerprint provenance",
        "BpfCompiledRealization ..> BpfRunner",
        "BpfRunner ..> RealizedRuntimeState",
    )
    missing = [value for value in required_relations if value not in diagrams]
    if missing:
        raise AssertionError(f"object-model relations missing: {missing}")
    return len(rows)


def _check_import_graph_mapping() -> int:
    path = ROOT / "docs/human-readable/repository-map.md"
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


LOCAL_ASSEMBLY_REQUIRED = {
    "skyseed_v2.backend.bpf": (
        "Plan",
        "PhysicalWorld",
        "compile_bpf",
        "BpfCompiledRealization",
        "BpfRunner",
    ),
    "skyseed_v2.shared": ("NodeId",),
    "skyseed_v2.simulator": (
        "SimulationDefinition",
        "SimulationTimeline",
        "Simulator",
        "PhysicalWorld",
        "Planner",
    ),
    "skyseed_v2.simulator.geometry": ("EarthFixedPosition",),
    "skyseed_v2.simulator.models": (
        "CircularOrbitPositionModel",
        "GeometricPhysicalLinkModel",
    ),
    "skyseed_v2.simulator.physical": (
        "PhysicalWorld",
        "PositionModel",
        "PhysicalFrame",
        "PhysicalLinkModel",
        "PhysicalLinkRequest",
        "PhysicalLinkObservation",
        "ZoneLayout",
    ),
    "skyseed_v2.simulator.planning": (
        "PhysicalWorld",
        "PhysicalFrame",
        "PhysicalLinkObservation",
        "Planner",
        "PlannedOperationalState",
        "PlanTransition",
        "Plan",
    ),
}


def _check_local_assembly_maps() -> int:
    checked = 0
    for module_name, path in zip(
        DISCOVERED_PUBLIC_PACKAGES, PUBLIC_PACKAGE_READMES, strict=True
    ):
        owned = _owned_public_classes(module_name)
        source = path.read_text(encoding="utf-8")
        if not owned:
            if "mermaid" not in source:
                raise AssertionError(f"public package README needs Mermaid: {path}")
            continue
        local = _heading_section(source, "Local assembly / ownership map")
        if not MERMAID_BLOCK.search(local):
            raise AssertionError(f"local assembly map lacks Mermaid: {path}")
        missing = [
            name
            for name in LOCAL_ASSEMBLY_REQUIRED.get(module_name, ())
            if name not in local
        ]
        if missing:
            raise AssertionError(
                f"local assembly map omits core contracts in {path}: {missing}"
            )
        checked += 1
    return checked


def _check_maintenance_mapping() -> None:
    maintenance_path = ROOT / "docs/codex-reference/maintenance-map.md"
    maintenance = maintenance_path.read_text(encoding="utf-8")
    rows = {
        match.group("module"): match.group("readme")
        for match in PUBLIC_PACKAGE_ROW.finditer(maintenance)
    }
    discovered = set(DISCOVERED_PUBLIC_PACKAGES)
    if set(rows) != discovered:
        raise AssertionError(
            "public package maintenance classification mismatch: "
            f"missing={sorted(discovered - set(rows))}, "
            f"extra={sorted(set(rows) - discovered)}"
        )
    for module_name, readme in rows.items():
        expected = (
            SOURCE_ROOT / Path(*module_name.split(".")) / "README.md"
        ).relative_to(ROOT).as_posix()
        if readme != expected:
            raise AssertionError(
                f"public package README mapping mismatch: {module_name}: "
                f"{readme} != {expected}"
            )
        if not (ROOT / readme).is_file():
            raise AssertionError(f"public package README is absent: {readme}")
    normalized_maintenance = " ".join(maintenance.split())
    for required in (
        "simulator/physical/",
        "simulator/planning/",
        "backend/bpf/",
        "Runner artifact/execution",
        "Traffic",
        "public `__init__.py`",
        "current architecture docs",
        "Do not create empty future directories",
        "forwarding modules",
        "duplicate public imports",
    ):
        if required not in normalized_maintenance:
            raise AssertionError(
                f"maintenance map missing current governance rule: {required}"
            )


def _class_fields(module: ast.Module, class_name: str) -> tuple[str, ...]:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return tuple(
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and not child.target.id.startswith("_")
            )
    raise AssertionError(f"class {class_name} not found")


def _require_public_contracts() -> None:
    exports = set(_literal_all("src/skyseed_v2/simulator/__init__.py"))
    missing = sorted(EXPECTED_SIMULATOR_EXPORTS - exports)
    if missing:
        raise AssertionError(f"Simulator public exports missing: {missing}")
    forbidden_exports = {
        "Sli" + "cer",
        "Topology" + "Slice",
        "Topology" + "Difference",
        "Connection" + "Slice",
        "Route" + "Slice",
    }
    present = sorted(forbidden_exports & exports)
    if present:
        raise AssertionError(f"retired Simulator exports remain: {present}")

    values = _parse("src/skyseed_v2/simulator/planning/values.py")
    if _class_fields(values, "Plan") != EXPECTED_PLAN_FIELDS:
        raise AssertionError("Plan field inventory differs from documentation")
    if _class_fields(values, "PlanTransition") != EXPECTED_TRANSITION_FIELDS:
        raise AssertionError("PlanTransition field inventory differs from documentation")

    public_imports = _read("docs/codex-reference/public-imports.md")
    undocumented = sorted(
        name for name in EXPECTED_SIMULATOR_EXPORTS if name not in public_imports
    )
    if undocumented:
        raise AssertionError(f"public imports missing documented names: {undocumented}")


def _require_public_package_readmes() -> None:
    missing: list[str] = []
    for init in sorted((ROOT / "src/skyseed_v2").rglob("__init__.py")):
        source = init.read_text(encoding="utf-8")
        if not re.search(r"^__all__\b", source, re.MULTILINE):
            continue
        readme = init.parent / "README.md"
        if not readme.is_file():
            missing.append(readme.relative_to(ROOT).as_posix())
    if missing:
        raise AssertionError(f"public package README missing: {missing}")


def _function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def _require_compiler_signature() -> None:
    function = _function(
        _parse("src/skyseed_v2/backend/bpf/compiler.py"), "compile_bpf"
    )
    parameters = tuple(value.arg for value in function.args.args)
    if parameters != ("plan", "physical_world", "previous_realization"):
        raise AssertionError(f"compile_bpf signature differs: {parameters}")
    if function.args.vararg or function.args.kwarg or function.args.kwonlyargs:
        raise AssertionError("compile_bpf has an undocumented alternate argument seam")


def _require_simulator_lifecycle() -> None:
    module = _parse("src/skyseed_v2/simulator/simulator.py")
    simulator = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Simulator"
    )
    methods = {
        node.name for node in simulator.body if isinstance(node, ast.FunctionDef)
    }
    expected = {
        "create_physical_world",
        "get_physical_world",
        "create_planner",
        "get_physical_link_model",
        "get_planner",
    }
    if not expected <= methods:
        raise AssertionError(f"Simulator lifecycle missing: {sorted(expected - methods)}")
    retired = {"create_" + "slicer", "get_" + "slicer"}
    if methods & retired:
        raise AssertionError("retired Simulator lifecycle remains")


def _require_production_traceability() -> None:
    timeline = _parse("src/skyseed_v2/simulator/timeline.py")
    timeline_class = next(
        node
        for node in timeline.body
        if isinstance(node, ast.ClassDef) and node.name == "SimulationTimeline"
    )
    timeline_members = {
        node.name
        for node in timeline_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    timeline_members.update(
        node.target.id
        for node in timeline_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    )
    if "observation_interval" not in timeline_members:
        raise AssertionError("Timeline observation_interval contract is absent")
    if ("slice" + "_interval") in timeline_members:
        raise AssertionError("retired Timeline interval contract remains")

    starlink = _parse("examples/starlink_gen1_satellite_internet/main.py")
    stages = None
    for node in starlink.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_THROUGH_STAGES"
            for target in node.targets
        ):
            stages = ast.literal_eval(node.value)
            break
    if stages != ("physical-world", "planner", "compiler"):
        raise AssertionError(f"canonical stage inventory differs: {stages!r}")

    producer = _parse("src/skyseed_v2/experiment/_realization_producer.py")
    producer_class = next(
        node
        for node in producer.body
        if isinstance(node, ast.ClassDef)
        and node.name == "_IncrementalRealizationProducer"
    )
    producer_methods = {
        node.name
        for node in producer_class.body
        if isinstance(node, ast.FunctionDef)
    }
    required_methods = {"produce_next_realization", "snapshot", "close"}
    if not required_methods <= producer_methods:
        raise AssertionError(
            f"incremental producer methods missing: "
            f"{sorted(required_methods - producer_methods)}"
        )

    execution = _read("src/skyseed_v2/experiment/execution.py")
    if "_read_runner_artifact_manifest_snapshot" not in execution:
        raise AssertionError("generated execution is not Runner-artifact based")
    if "future_realization_supplier" in execution:
        raise AssertionError("runtime realization supplier remains")


def _check_basic_markdown(markdown_files: tuple[Path, ...]) -> None:
    for path in markdown_files:
        source = path.read_text(encoding="utf-8")
        if not source.strip():
            raise AssertionError(f"empty Markdown: {path.relative_to(ROOT)}")
        if not source.startswith("#"):
            raise AssertionError(
                f"Markdown must start with a heading: {path.relative_to(ROOT)}"
            )
        if len(FENCE.findall(source)) % 2:
            raise AssertionError(
                f"unclosed fenced block: {path.relative_to(ROOT)}"
            )


def _validate_mermaid(markdown_files: tuple[Path, ...]) -> int:
    diagrams = 0
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        for index, match in enumerate(MERMAID_BLOCK.finditer(text), start=1):
            block = match.group(1)
            diagrams += 1
            first = next(
                (
                    line.strip().split(maxsplit=1)[0]
                    for line in block.splitlines()
                    if line.strip()
                ),
                "",
            )
            if first not in {"flowchart", "classDiagram", "sequenceDiagram"}:
                raise AssertionError(
                    f"unsupported Mermaid root in {path.relative_to(ROOT)}: "
                    f"{first!r}"
                )
            preceding = text[max(0, match.start() - 600) : match.start()]
            adjacent = preceding.rstrip().rsplit("\n\n", 1)[-1]
            if "Legend:" not in adjacent:
                raise AssertionError(
                    f"Mermaid block needs an adjacent Legend: "
                    f"{path.relative_to(ROOT)}:{index}"
                )
            if block.count("[") != block.count("]"):
                raise AssertionError(
                    f"unbalanced Mermaid brackets in {path.relative_to(ROOT)}"
                )
            if block.count("{") != block.count("}"):
                raise AssertionError(
                    f"unbalanced Mermaid braces in {path.relative_to(ROOT)}"
                )
            if first == "flowchart" and "-->" not in block and "-.->" not in block:
                raise AssertionError(
                    f"empty Mermaid flowchart in {path.relative_to(ROOT)}:{index}"
                )
            if first == "sequenceDiagram" and "->>" not in block:
                raise AssertionError(
                    f"empty Mermaid sequence in {path.relative_to(ROOT)}:{index}"
                )
    if diagrams < 10:
        raise AssertionError(f"expected at least 10 maintained Mermaid diagrams, got {diagrams}")
    return diagrams


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("focused", "full"),
        default="full",
        help="validation label; both scopes enforce the current architecture",
    )
    arguments = parser.parse_args(argv)
    _require_files()
    current_markdown = _authoritative_markdown()
    markdown_files = (
        current_markdown
        if arguments.scope == "focused"
        else _full_repository_documents()
    )
    _check_basic_markdown(markdown_files)
    _require_current_architecture_text()
    _reject_retired_text(current_markdown)
    _reject_retired_physical_frame_tool_claims(current_markdown)
    _validate_links(markdown_files)
    _require_public_contracts()
    _require_public_package_readmes()
    public_exports = _check_public_import_mapping()
    detail_contracts, detail_blocks, detail_objects = _check_class_details()
    object_contracts = _check_object_model_contracts(detail_objects)
    import_edges = _check_import_graph_mapping()
    local_maps = _check_local_assembly_maps()
    _check_maintenance_mapping()
    _require_compiler_signature()
    _require_simulator_lifecycle()
    _require_production_traceability()
    diagrams = _validate_mermaid(markdown_files)
    print(
        "documentation result: PASS\n"
        f"scope: {arguments.scope}\n"
        "Documentation checks passed: "
        f"{len(markdown_files)} authoritative Markdown files, "
        f"{diagrams} Mermaid diagrams, "
        f"{public_exports} exact public exports, "
        f"{detail_contracts} owner class contracts / {detail_blocks} blocks, "
        f"{object_contracts} object-model mappings, "
        f"{local_maps} local assembly maps, "
        f"{import_edges} collapsed import edges verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
