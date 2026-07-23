import inspect
import re
from pathlib import Path

from seedemu.core import Emulator

import skyseed_v2
import skyseed_v2.simulator as simulator_core
from skyseed_v2 import (
    configuration,
    entities,
    materialization,
    node_id,
    seedemu_integration,
)
from skyseed_v2.materialization import ComposeProjectLauncher
from skyseed_v2.simulator import static_input as simulator_static_input
from skyseed_v2 import static_emulator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_DOCUMENT = (
    PROJECT_ROOT / "docs/architecture/emulator-public-construction-api.md"
)
TERMINOLOGY = PROJECT_ROOT / "docs/architecture/terminology.md"
ACCEPTED_DESIGN = PROJECT_ROOT / "docs/architecture/accepted-design.md"
DYNAMIC_SLICE_CONTRACT = (
    PROJECT_ROOT / "docs/architecture/dynamic-slice-public-contract.md"
)
CURRENT = PROJECT_ROOT / "docs/CURRENT.md"
PUBLIC_MODULES = (
    entities,
    materialization,
    node_id,
    seedemu_integration,
    simulator_static_input,
    configuration,
    static_emulator,
    simulator_core,
)


APPROVED_DYNAMIC_TYPES = (
    "EarthFixedPosition",
    "SphericalCap",
    "PositionModel",
    "ConnectionModel",
    "RouteModel",
    "CircularOrbitPositionModel",
    "GeometricConnectionModel",
    "HopCountRouteModel",
    "ConnectionKind",
    "ConnectionId",
    "DirectionalConnectionParameters",
    "Connection",
    "ConnectionSlice",
    "GroundStationRouteTarget",
    "ZoneRouteTarget",
    "RouteTarget",
    "RouteKey",
    "RouteSlice",
    "TopologySlice",
    "Slicer",
    "TopologyDifference",
)

F_IMPLEMENTED_DYNAMIC_TYPES = (
    "ConnectionDirection",
    "DirectionalConnectionParametersPatch",
    "DifferenceOperation",
    "RoutePatch",
    "ZoneServiceAssignmentPatch",
)

CURRENT_EXPORTED_DYNAMIC_TYPES = (
    APPROVED_DYNAMIC_TYPES + F_IMPLEMENTED_DYNAMIC_TYPES
)

ANCHOR_DYNAMIC_CONCEPTS = (
    "SphericalCap",
    "PositionModel",
    "ConnectionModel",
    "RouteModel",
    "TopologySlice",
    "Slicer",
    "TopologyDifference",
)


def public_classes(module) -> tuple[type, ...]:
    return tuple(
        value
        for name in module.__all__
        if inspect.isclass(value := getattr(module, name))
    )


def test_formal_public_classes_declare_layer_boundary_and_public_reason() -> None:
    for module in PUBLIC_MODULES:
        for class_type in public_classes(module):
            docstring = inspect.getdoc(class_type)
            assert docstring is not None, class_type
            assert "Layer:" in docstring, class_type
            assert re.search(r"\bnot\b", docstring), class_type
            assert "public" in docstring.lower(), class_type


def test_current_api_mermaid_names_are_real_and_classified() -> None:
    document = API_DOCUMENT.read_text(encoding="utf-8")
    current_map = document.split("## Implemented class and API map", 1)[1].split(
        "### Retained public-type proof", 1
    )[0]
    class_blocks = re.findall(
        r"^\s*class\s+(\w+)\s*\{(.*?)^\s*\}",
        current_map,
        flags=re.MULTILINE | re.DOTALL,
    )
    names = {name for name, _ in class_blocks}
    expected_types = {
        "ComposeProjectLauncher": ComposeProjectLauncher,
        "Emulator": Emulator,
        "GroundStation": entities.GroundStation,
        "GroundStationSimulationInput": (
            simulator_static_input.GroundStationSimulationInput
        ),
        "GroundStationLocation": entities.GroundStationLocation,
        "Orbit": entities.Orbit,
        "OrbitParameters": entities.OrbitParameters,
        "Satellite": entities.Satellite,
        "SatelliteBase": seedemu_integration.SatelliteBase,
        "SatelliteSimulationInput": (
            simulator_static_input.SatelliteSimulationInput
        ),
        "Shell": entities.Shell,
        "NodeId": node_id.NodeId,
        "SimulatorStaticInput": simulator_static_input.SimulatorStaticInput,
        "SkyseedAutonomousSystem": seedemu_integration.SkyseedAutonomousSystem,
        "SkyseedDockerCompiler": seedemu_integration.SkyseedDockerCompiler,
        "StaticEmulatorConfig": configuration.StaticEmulatorConfig,
    }
    assert names == set(expected_types)
    assert all(inspect.isclass(value) for value in expected_types.values())
    assert all("<<" in body and ">>" in body for _, body in class_blocks)
    for retired in (
        "StaticEmulatorScenarioBuilder",
        "ShellPopulationBuilder",
        "GroundStationBuilder",
        "Constellation",
    ):
        assert retired not in current_map


def test_all_emulator_class_diagrams_classify_every_declared_type() -> None:
    for relative in (
        "docs/architecture/conceptual-class-diagrams.md",
        "docs/architecture/diagrams.md",
        "docs/architecture/emulator-public-construction-api.md",
        "docs/design/static-ground-station-emulator.md",
    ):
        document = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for diagram in re.findall(
            r"```mermaid\s+classDiagram(.*?)```",
            document,
            flags=re.DOTALL,
        ):
            blocks = re.findall(
                r"^\s*class\s+(\w+)\s*\{(.*?)^\s*\}",
                diagram,
                flags=re.MULTILINE | re.DOTALL,
            )
            assert blocks, relative
            for name, body in blocks:
                assert "<<" in body and ">>" in body, (relative, name)


def test_terminology_and_project_rules_cover_the_governance_contract() -> None:
    terminology = TERMINOLOGY.read_text(encoding="utf-8")
    for term in (
        "Shell",
        "Orbit",
        "Satellite",
        "GroundStation",
        "StaticEmulatorConfig",
        "load_static_emulator_config",
        "create_static_emulator",
        "SatelliteSimulationInput",
        "GroundStationSimulationInput",
        "SimulatorStaticInput",
        "NodeId",
        "SkyseedDockerCompiler",
        "SimulationPlanCompiler",
        "ComposeProjectLauncher",
    ):
        assert f"| {term} |" in terminology

    rules = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for required_rule in (
        "标准纵向开发流程",
        "已批准范围内的实现自主权",
        "公共架构变更必须先申请",
        "架构变更提案格式",
        "Architecture Delta",
        "Example 是人类审核入口",
        "命名与概念治理",
        "历史实现调查边界",
        "切片接受条件",
        "本轮批准新增或修改",
        "本轮明确不包含",
    ):
        assert required_rule in rules
    assert "Do not start Simulator" not in rules
    assert "simulator_input_generation.py" not in rules


def test_accepted_dynamic_contract_is_linked_and_status_classified() -> None:
    accepted_source = ACCEPTED_DESIGN.read_text(encoding="utf-8")
    accepted = " ".join(accepted_source.split()).lower()
    contract = " ".join(
        DYNAMIC_SLICE_CONTRACT.read_text(encoding="utf-8").split()
    ).lower()
    current = " ".join(
        CURRENT.read_text(encoding="utf-8").split()
    ).lower()

    assert DYNAMIC_SLICE_CONTRACT.is_file()
    assert "(dynamic-slice-public-contract.md)" in accepted_source
    for status_record in (accepted, contract, current):
        assert "a–e" in status_record
        assert "implemented and accepted" in status_record
    for status_record in (accepted, contract, current):
        assert "f" in status_record
        assert "topologydifference" in status_record
    assert "a–e and f are implemented and accepted" in accepted
    assert "a–e and f implemented and accepted" in contract
    assert "topologydifference: **implemented and accepted**" in current
    assert "g" in contract and "not implemented" in contract
    assert "g remains unimplemented" in accepted
    assert "g dynamic tool" in current
    assert "full dynamic slice stage is not complete" in current

    for type_name in ANCHOR_DYNAMIC_CONCEPTS:
        assert type_name.lower() in accepted
    for type_name in APPROVED_DYNAMIC_TYPES:
        assert type_name.lower() in contract


def test_dynamic_contract_preserves_the_accepted_owner_direction() -> None:
    accepted = " ".join(ACCEPTED_DESIGN.read_text(encoding="utf-8").split())
    accepted_semantics = accepted.replace("`", "")
    contract = DYNAMIC_SLICE_CONTRACT.read_text(encoding="utf-8")

    for required in (
        "Simulator",
        "Topology",
        "Slicer",
        "PositionModel",
        "ConnectionModel",
        "RouteModel",
        "TopologySlice",
        "TopologyDifference",
    ):
        assert required in accepted
        assert required in contract
    for owner_semantic in (
        "Simulator remains the composition root",
        "Topology owns the immutable static world",
        "Slicer owns the sole dynamic generation order",
    ):
        assert owner_semantic in accepted_semantics

    for retired in (
        "Topology.create_slice(",
        "Topology.compare_slices(",
        "Topology creates TopologySlice",
    ):
        assert retired not in accepted
        assert retired not in contract


def test_dynamic_terms_classify_accepted_and_deferred_boundaries() -> None:
    terminology = " ".join(TERMINOLOGY.read_text(encoding="utf-8").split())

    for term in CURRENT_EXPORTED_DYNAMIC_TYPES:
        assert term in terminology
    assert "implemented and accepted" in terminology
    assert "adjacent TopologyDifference | **implemented and accepted**" in terminology


def test_provenance_cannot_override_the_accepted_contract() -> None:
    accepted = " ".join(ACCEPTED_DESIGN.read_text(encoding="utf-8").split())
    provenance = " ".join(
        (
            PROJECT_ROOT / "docs/design/simulator-conceptual-design.md"
        ).read_text(encoding="utf-8").split()
    )

    assert "cannot override this anchor" in accepted
    assert "retired/non-authoritative" in provenance.lower()
    assert "cannot override" in provenance


def test_dynamic_accepted_types_export_only_from_their_contract_owner() -> None:
    for type_name in CURRENT_EXPORTED_DYNAMIC_TYPES:
        assert hasattr(simulator_core, type_name)
        assert not hasattr(skyseed_v2, type_name)


def test_compilation_stages_have_distinct_current_architecture_terms() -> None:
    terminology = TERMINOLOGY.read_text(encoding="utf-8")
    assert "| SkyseedDockerCompiler |" in terminology
    assert "| SimulationPlanCompiler |" in terminology
    assert "| Compiler |" not in terminology

    for relative in (
        "docs/CURRENT.md",
        "docs/architecture/diagrams.md",
        "docs/architecture/emulator-public-construction-api.md",
    ):
        document = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert "SkyseedDockerCompiler" in document
        assert "SimulationPlanCompiler" in document


def test_current_emulator_documents_have_no_known_stale_implementation_names() -> None:
    documents = tuple(
        (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "docs/architecture/conceptual-class-diagrams.md",
            "docs/architecture/diagrams.md",
            "docs/design/complete-static-emulator.md",
            "docs/design/isolated-satellite-emulator.md",
            "docs/design/static-ground-station-emulator.md",
        )
    )
    for document in documents:
        assert "create_ground_station(config)" not in document
        assert "satellite_descriptors()" not in document
        assert "ground_station_descriptors()" not in document
        assert "loads its adjacent v2 `scenario.yaml`" not in document
        assert "using its adjacent versioned" not in document

    api_document = API_DOCUMENT.read_text(encoding="utf-8")
    for required_section in (
        "Package ownership",
        "Simulator static-input design",
        "Public construction paths",
        "Rename and removal record",
        "API change summary",
        "Review checklist",
    ):
        assert required_section in api_document


def test_static_emulator_operation_returns_upstream_emulator_directly() -> None:
    function = static_emulator.create_static_emulator
    assert function.__module__ == "skyseed_v2.static_emulator"
    assert inspect.get_annotations(function, eval_str=True)["return"] is Emulator
    assert not hasattr(static_emulator, "StaticEmulatorCreationResult")
    assert not hasattr(skyseed_v2, "create_static_emulator")


def test_direct_file_driven_call_chain_is_documented() -> None:
    document = API_DOCUMENT.read_text(encoding="utf-8")
    for operation in (
        "load_static_emulator_config(path)",
        "create_static_emulator(config)",
        "base.get_skyseed_autonomous_systems()",
    ):
        assert operation in document
    for retired in (
        "StaticEmulatorScenarioLoader",
        "StaticScenarioApplier",
        "StaticEmulatorSpecificationApplier",
        "StaticEmulatorApplyResult",
    ):
        assert retired not in document
