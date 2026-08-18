from __future__ import annotations

import inspect
from pathlib import Path
import re
from subprocess import run
import sys

from seedemu.core import Emulator

import skyseed_v2
from skyseed_v2.backend import bpf
import skyseed_v2.simulator as simulator_core
from skyseed_v2 import compose
from skyseed_v2 import shared
from skyseed_v2.configuration import emulator as configuration
from skyseed_v2.configuration import simulator as simulator_configuration
from skyseed_v2 import emulator as emulator_facade
from skyseed_v2.emulator import construction
from skyseed_v2.emulator import entities
from skyseed_v2.emulator import seedemu
from skyseed_v2.simulator import input as simulator_input
from skyseed_v2.simulator import planning as simulator_planning


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = PROJECT_ROOT / "docs"
ACCEPTED_DESIGN = DOCS_ROOT / "accepted-design.md"
CURRENT = DOCS_ROOT / "CURRENT.md"
PUBLIC_IMPORTS = DOCS_ROOT / "codex-reference/public-imports.md"
PUBLIC_MODULES = (
    entities,
    compose,
    shared,
    seedemu,
    simulator_input,
    configuration,
    simulator_configuration,
    emulator_facade,
    construction,
    simulator_core,
    simulator_planning,
    bpf,
)
DYNAMIC_TYPES = (
    "AccumulatedDeadbandParameterPlanningModel",
    "AllAvailablePlanningHorizonPolicy",
    "CircularOrbitPositionModel",
    "Connection",
    "ConnectionDirection",
    "ConnectionId",
    "ConnectionKind",
    "ConnectionModel",
    "ConnectionPlanningModel",
    "ConnectionSlice",
    "ConnectionTransition",
    "ConnectionTransitionOperation",
    "CoordinationPolicy",
    "DefaultCoordinationPolicy",
    "DependencyAwareForwardingPlanningModel",
    "DirectionalConnectionParameters",
    "DirectionalConnectionParametersPatch",
    "DifferenceOperation",
    "EarthFixedPosition",
    "ForwardingPlanningModel",
    "ForwardingTransition",
    "ForwardingTransitionPrecedence",
    "GeometricConnectionModel",
    "GroundStationRouteTarget",
    "HopCountRouteModel",
    "MidpointConnectionPlanningModel",
    "ParameterPlanningModel",
    "ParameterTransition",
    "Plan",
    "Planner",
    "PlanningDecision",
    "PlanningHorizonPolicy",
    "PositionModel",
    "RouteKey",
    "RouteModel",
    "RoutePatch",
    "RouteSlice",
    "RouteTarget",
    "Slicer",
    "SampleBoundaryConnectionPlanningModel",
    "SphericalCap",
    "TopologyDifference",
    "TopologySlice",
    "ZoneRouteTarget",
    "ZoneServiceAssignmentPatch",
)


def _public_classes(module) -> tuple[type, ...]:
    return tuple(
        value
        for name in module.__all__
        if inspect.isclass(value := getattr(module, name))
    )


def test_formal_public_classes_declare_layer_boundary_and_public_reason() -> None:
    for module in PUBLIC_MODULES:
        for class_type in _public_classes(module):
            docstring = inspect.getdoc(class_type)
            assert docstring is not None, class_type
            assert "Layer:" in docstring, class_type
            assert re.search(r"\bnot\b", docstring), class_type
            assert "public" in docstring.lower(), class_type


def test_document_identity_is_explicit_in_paths() -> None:
    assert {
        path.name for path in DOCS_ROOT.iterdir() if path.is_dir()
    } == {
        "candidates",
        "codex-reference",
        "human-readable",
        "provenance",
        "user-guides",
    }
    assert {
        path.name for path in DOCS_ROOT.iterdir() if path.is_file()
    } == {"README.md", "CURRENT.md", "accepted-design.md"}

    documentation_index = (DOCS_ROOT / "README.md").read_text(
        encoding="utf-8"
    )
    for audience in (
        "human-readable/",
        "codex-reference/",
        "user-guides/",
        "candidates/",
        "provenance/",
    ):
        assert audience in documentation_index


def test_accepted_anchor_and_current_have_distinct_roles() -> None:
    accepted = ACCEPTED_DESIGN.read_text(encoding="utf-8")
    current = CURRENT.read_text(encoding="utf-8")

    for required in (
        "SimulatorStaticInput",
        "create_simulator_static_input",
        "Position → Connection → Route",
        "second authoritative computation path",
    ):
        assert required in accepted
    assert "Dynamic Slice A–G: **implemented and accepted**" in current
    for deferred in (
        "runtime binding",
        "BPF preparation/load/activation/maintenance",
        "dynamic execution",
        "future names, owners, packages, inputs, outputs",
        "execution chains are not frozen",
    ):
        assert deferred in current
    assert "EmulatorStaticInput" not in PUBLIC_IMPORTS.read_text(
        encoding="utf-8"
    )
    assert (
        "BPF Compiler minimal vertical slice: **implemented and accepted**"
        in current
    )
    assert "`skyseed_v2.backend.bpf`" in PUBLIC_IMPORTS.read_text(
        encoding="utf-8"
    )


def test_current_reference_has_no_accepted_g_candidate_status() -> None:
    authoritative = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(DOCS_ROOT.rglob("*.md"))
        if "candidates" not in path.parts and "provenance" not in path.parts
    )
    for stale in (
        "G candidate",
        "G final review",
        "Dynamic Slice A–G: candidate",
    ):
        assert stale not in authoritative


def test_dynamic_types_export_only_from_simulator_contract_owner() -> None:
    mapping = PUBLIC_IMPORTS.read_text(encoding="utf-8")
    for type_name in DYNAMIC_TYPES:
        assert hasattr(simulator_core, type_name)
        assert not hasattr(skyseed_v2, type_name)
        assert f"`{type_name}`" in mapping


def test_static_emulator_operation_returns_upstream_emulator_directly() -> None:
    function = construction.create_static_emulator
    assert function.__module__ == "skyseed_v2.emulator.construction"
    assert inspect.get_annotations(function, eval_str=True)["return"] is Emulator
    assert not hasattr(construction, "StaticEmulatorCreationResult")
    assert emulator_facade.create_static_emulator is function
    assert not hasattr(skyseed_v2, "create_static_emulator")


def test_isolated_documents_cannot_present_current_authority() -> None:
    for directory in ("candidates", "provenance"):
        for path in (DOCS_ROOT / directory).glob("*.md"):
            source = path.read_text(encoding="utf-8")
            assert "Non-authoritative" in source, path


def test_focused_documentation_checker_passes() -> None:
    completed = run(
        (
            sys.executable,
            "scripts/check_documentation.py",
            "--scope",
            "focused",
        ),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "documentation result: PASS" in completed.stdout
