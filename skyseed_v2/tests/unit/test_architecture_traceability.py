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
BPF_PACKAGE_ROOT = PROJECT_ROOT / "src/skyseed_v2/backend/bpf"
GENERATED_BPF_EXECUTION = (
    PROJECT_ROOT / "src/skyseed_v2/_generated_bpf_execution.py"
)
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
    "AllAvailablePlanningWindowPolicy",
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
    "PlanningWindowPolicy",
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


def test_bpf_package_layout_and_private_residue_are_exact() -> None:
    assert {
        path.name
        for path in (PROJECT_ROOT / "src/skyseed_v2").glob("*generated*execution.py")
    } == {"_generated_bpf_execution.py"}
    assert {
        path.name
        for path in BPF_PACKAGE_ROOT.iterdir()
        if path.is_file() and path.suffix in {".py", ".md"}
    } == {
        "README.md",
        "__init__.py",
        "compiled_realization.py",
        "compiler.py",
        "runner.py",
    }
    runner_private = BPF_PACKAGE_ROOT / "_runner"
    assert {
        path.name for path in runner_private.iterdir() if path.suffix == ".py"
    } == {
        "__init__.py",
        "privileged_runtime_command.py",
        "runtime_realization.py",
        "traffic_telemetry.py",
        "traffic_telemetry_abi.py",
        "traffic_telemetry_collector.py",
    }

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(BPF_PACKAGE_ROOT.rglob("*.py"))
    )
    for retired in (
        "_PrivilegedWorker",
        "_wait_for_maintenance_idle",
        "_probe_representative_packet_path",
        "_probe_ground_station_access_peer",
        "_probe_zone_ground_round_trip",
        "_probe_next_future_resource_inactive",
        "_inspect_resource",
        "_probe_next_forwarding_transition",
        "_probe_activated_forwarding_transition",
        "_runtime_snapshot",
        "_evidence_snapshot",
        "_resolve_first_nominal_boundary",
        "_working_set_snapshot",
        "_retained_slice_peak",
        "_retained_difference_peak",
        "_retained_route_context_peak",
        "_retained_decision_peak",
        "_observe_current_working_set",
        "_observe_working_set",
        "simulator._durable_history",
        "_traffic_telemetry_protocol",
    ):
        assert retired not in source

    generated_source = GENERATED_BPF_EXECUTION.read_text(encoding="utf-8")
    for required in (
        "class _RollingRealizationSupplier:",
        ".create_next_slice()",
        ".synchronize()",
        "compile_bpf(",
        "BpfRunner(",
        "runner.prepare()",
        "runner.run()",
        "runner.cleanup()",
    ):
        assert required in generated_source
    for forbidden in (
        "_RollingRealizationSupplier",
        "create_next_slice",
        ".synchronize(",
        "progression_builder",
    ):
        assert forbidden not in source
    assert "_generated_bpf_execution" not in skyseed_v2.__all__


def test_simulator_history_codec_and_jsonl_mechanics_have_distinct_owners() -> None:
    codec = (
        PROJECT_ROOT / "src/skyseed_v2/simulator/_history_codec.py"
    ).read_text(encoding="utf-8")
    planner = (
        PROJECT_ROOT / "src/skyseed_v2/simulator/planning/planner.py"
    ).read_text(encoding="utf-8")

    assert "shared._durable_evidence" not in codec
    for generic_helper in (
        "_append_jsonl",
        "_read_jsonl",
        "_reset_jsonl",
        "_semantic_value",
    ):
        assert generic_helper not in codec
        assert generic_helper in planner
    assert "from skyseed_v2.shared._durable_evidence import" in planner
    assert "from .._history_codec import" in planner
    assert not (
        PROJECT_ROOT / "src/skyseed_v2/simulator/_durable_history.py"
    ).exists()


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
    assert "Slice / Difference semantics: **implemented and accepted**" in current
    for implemented in (
        "Runner milestone: **implemented, runtime-validated, and manually accepted**",
        "rolling Slicer →",
        "Preparation binds",
        "Main alone",
        "Maintenance prepares",
        "inputs/execution.json",
        "append-only JSONL",
        "60-second preparation lookahead",
        "eight-boundary future depth",
        "K = boundary depth + 1",
        "64 Slices / 64 Differences",
        "16 Plans / 16",
        "75 nominal boundaries / 75 Main",
        "Final canonical Mini runtime: **PASS**",
        "eight formal repository-local",
        "three maintained runnable examples",
        "Manual Tool acceptance is complete",
        "S4 Planner and BPF Compiler scale milestone: **implemented, qualified, and",
        "The **S4 Planner + BPF Compiler scale stage** is accepted",
        "181 real Slices / 181 Differences",
        "90 `BpfCompiledRealization` values",
        "next scale stage is S4 Runner/runtime qualification",
        "final S4-specific Zone-service minimum is 25 degrees",
        "Slice 0 plus 20 real Slices/20 Differences",
        "Traffic / Path Replay",
        "traffic-telemetry.jsonl",
        "Normal generated `run.sh` neither reads nor starts that",
    ):
        assert implemented in current
    for deferred in (
        "automatic rollback",
        "public dataplane plugin API",
        "large-network performance",
        "realtime/live traffic visualization",
    ):
        assert deferred in current
    assert "EmulatorStaticInput" not in PUBLIC_IMPORTS.read_text(
        encoding="utf-8"
    )
    assert "`compile_bpf(...)` is a one-shot, stateless lowering" in current
    assert "`skyseed_v2.backend.bpf`" in PUBLIC_IMPORTS.read_text(
        encoding="utf-8"
    )
    assert "`BpfRunner`" in PUBLIC_IMPORTS.read_text(encoding="utf-8")


def test_current_reference_has_no_accepted_g_candidate_status() -> None:
    authoritative = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(DOCS_ROOT.rglob("*.md"))
        if "candidates" not in path.parts and "provenance" not in path.parts
    )
    for stale in (
        "G candidate",
        "G final review",
        "Dynamic" + " Slice A–G: candidate",
        "pending " + "acceptance",
        "BPF Runner " + "Vertical Slice",
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
