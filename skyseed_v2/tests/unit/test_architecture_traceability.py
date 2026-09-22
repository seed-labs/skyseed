from __future__ import annotations

import inspect
from pathlib import Path
from subprocess import run
import sys

from seedemu.core import Emulator

import skyseed_v2
from skyseed_v2.backend import bpf
import skyseed_v2.simulator as simulator_core
from skyseed_v2 import experiment
from skyseed_v2 import deployment
from skyseed_v2 import shared
from skyseed_v2.configuration import emulator as configuration
from skyseed_v2.configuration import simulator as simulator_configuration
from skyseed_v2 import emulator as emulator_facade
from skyseed_v2.emulator import construction
from skyseed_v2.emulator import entities
from skyseed_v2.emulator import seedemu
from skyseed_v2.simulator import input as simulator_input
from skyseed_v2.simulator import planner as simulator_planning


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = PROJECT_ROOT / "docs"
BPF_PACKAGE_ROOT = PROJECT_ROOT / "src/skyseed_v2/backend/bpf"
GENERATED_BPF_EXECUTION = (
    PROJECT_ROOT / "src/skyseed_v2/experiment/execution.py"
)
DYNAMIC_TYPES = (
    "AccumulatedDeadbandParameterPlanningModel",
    "CircularOrbitPositionModel",
    "EarthFixedPosition",
    "GatewayHopCountRoutePlanningModel",
    "GeometricPhysicalLinkModel",
    "OperationalConnection",
    "ParameterPlanningModel",
    "PhysicalPositionSnapshot",
    "PhysicalLinkQueryResult",
    "PhysicalWorld",
    "Plan",
    "PlanTransition",
    "PlannedOperationalState",
    "Planner",
    "PlannerConfiguration",
    "PositionModel",
    "PublishedPlanningEvidence",
    "RoutePlanningModel",
    "SphericalCap",
)




def test_bpf_package_layout_and_private_residue_are_exact() -> None:
    experiment = PROJECT_ROOT / "src/skyseed_v2/experiment"
    assert not (PROJECT_ROOT / "src/skyseed_v2/_traffic").exists()
    assert {
        path.name
        for path in experiment.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    } == {
        "output"
    }
    assert {
        path.name
        for path in (PROJECT_ROOT / "tools/generation/traffic").iterdir()
        if path.suffix == ".py"
    } == {"__init__.py", "__main__.py", "probe_plan.py", "probe_execution.py", "preview.py"}
    assert not tuple(
        (PROJECT_ROOT / "src/skyseed_v2").glob("*generated*execution.py")
    )
    assert {
        path.name for path in experiment.iterdir() if path.suffix == ".py"
    } == {
        "__init__.py",
        "execution.py", "preparation.py",
        "producer.py", "controller.py", "_control.py",
    }
    assert {
        path.name
        for path in BPF_PACKAGE_ROOT.iterdir()
        if path.is_file() and path.suffix in {".py", ".md"}
    } == {
        "README.md",
        "__init__.py",
    }
    assert {p.name for p in (BPF_PACKAGE_ROOT / "compiler").glob("*.py")} == {
        "__init__.py", "result.py",
    }
    assert {p.name for p in (BPF_PACKAGE_ROOT.parent / "execution_materials").glob("*.py")} == {
        "__init__.py", "actions.py", "boundaries.py", "codec.py", "sealing.py", "mechanics.py",
    }
    assert {p.name for p in (BPF_PACKAGE_ROOT / "dataplane").glob("*.py")} == {
        "__init__.py", "program.py", "translation.py", "materials.py", "object_contract.py",
        "runtime.py", "runtime_command.py", "telemetry_abi.py", "data.py", "scopes.py", "local_ingress.py",
    }
    assert {p.name for p in (BPF_PACKAGE_ROOT.parent / "linux").glob("*.py")} == {
        "__init__.py", "bpf.py", "netlink.py", "elf.py", "environment.py",
        "materials.py", "translation.py", "runtime.py", "runtime_command.py", "network.py",
    }
    runner_private = BPF_PACKAGE_ROOT.parent / "runner"
    assert {
        path.name for path in runner_private.iterdir() if path.suffix == ".py"
    } == {
            "__init__.py",
            "lifecycle.py",
            "deployment.py",
            "command_channel.py",
            "event_history.py", "connection_observer.py",
            "process_incarnation.py",
        "runtime_command.py",
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
    producer_source = (
        experiment / "producer.py"
    ).read_text(encoding="utf-8")
    for required in (
        "class Producer:",
        ".frame_at(",
        ".compute_observation(",
        ".observe(",
        "compile_bpf(",
        "_compile_bpf_observed(",
    ):
        assert required in producer_source
    for required in (
        "runner._bind_execution_input(",
        "Runner(",
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
    assert "experiment" in skyseed_v2.__all__
    assert skyseed_v2.experiment.__name__ == "skyseed_v2.experiment"


def test_plan_is_the_only_planner_to_compiler_semantic_handoff() -> None:
    source_root = PROJECT_ROOT / "src/skyseed_v2"
    planner_source = (
        source_root / "simulator/planner/planner.py"
    ).read_text(encoding="utf-8")
    producer_source = (
        source_root / "experiment/producer.py"
    ).read_text(encoding="utf-8")
    compiler_source = (
        source_root / "backend/compiler/__init__.py"
    ).read_text(encoding="utf-8")
    boundary_source = planner_source + producer_source + compiler_source

    for forbidden in (
        "_published_output_states",
        "_get_published_output_state",
        "_latest_plan_output_state",
        "_compile_bpf_published",
        "authoritative_output_state",
    ):
        assert forbidden not in boundary_source
    assert "_create_plan_with_output_state(" in planner_source
    assert "self._last_published_state = published_state" in planner_source
    assert "next_semantic_state = apply_plan_transition(" in compiler_source
    assert "self._previous_result" in producer_source


def test_simulator_history_codec_and_jsonl_mechanics_have_distinct_owners() -> None:
    assert not (
        PROJECT_ROOT / "src/skyseed_v2/simulator/_history_codec.py"
    ).exists()
    assert not (
        PROJECT_ROOT / "src/skyseed_v2/simulator/_durable_history.py"
    ).exists()
    producer = (
        PROJECT_ROOT / "src/skyseed_v2/experiment/producer.py"
    ).read_text(encoding="utf-8")
    planning_evidence = (
        PROJECT_ROOT / "src/skyseed_v2/simulator/planner/evidence.py"
    ).read_text(encoding="utf-8")
    writer = (PROJECT_ROOT / "src/skyseed_v2/experiment/output/history_writer.py").read_text()
    assert "shared._durable_evidence" in writer
    assert "published_planning_evidence_to_data" in writer
    assert "_GenerationHistory" in producer
    assert "plan_to_data" in planning_evidence
    for retired in ("Difference", "Slice", "_history_codec"):
        assert retired not in producer
        assert retired not in planning_evidence




def test_dynamic_types_export_only_from_simulator_contract_owner() -> None:
    for type_name in DYNAMIC_TYPES:
        assert hasattr(simulator_core, type_name)
        assert not hasattr(skyseed_v2, type_name)


def test_static_emulator_operation_returns_upstream_emulator_directly() -> None:
    function = construction.create_static_emulator
    assert function.__module__ == "skyseed_v2.emulator.construction"
    assert inspect.get_annotations(function, eval_str=True)["return"] is Emulator
    assert not hasattr(construction, "StaticEmulatorCreationResult")
    assert emulator_facade.create_static_emulator is function
    assert not hasattr(skyseed_v2, "create_static_emulator")


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
