import inspect
import re
from pathlib import Path

from seedemu.core import Emulator

import skyseed_v2
from skyseed_v2 import (
    configuration,
    entities,
    materialization,
    node_id,
    seedemu_integration,
    simulator_input,
)
from skyseed_v2.materialization import ComposeProjectLauncher
from skyseed_v2 import static_emulator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_DOCUMENT = (
    PROJECT_ROOT / "docs/architecture/emulator-public-construction-api.md"
)
TERMINOLOGY = PROJECT_ROOT / "docs/architecture/terminology.md"
PUBLIC_MODULES = (
    entities,
    materialization,
    node_id,
    seedemu_integration,
    simulator_input,
    configuration,
    static_emulator,
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
            simulator_input.GroundStationSimulationInput
        ),
        "GroundStationLocation": entities.GroundStationLocation,
        "Orbit": entities.Orbit,
        "OrbitParameters": entities.OrbitParameters,
        "Satellite": entities.Satellite,
        "SatelliteBase": seedemu_integration.SatelliteBase,
        "SatelliteSimulationInput": simulator_input.SatelliteSimulationInput,
        "Shell": entities.Shell,
        "NodeId": node_id.NodeId,
        "SimulatorStaticInput": simulator_input.SimulatorStaticInput,
        "SkyseedAutonomousSystem": seedemu_integration.SkyseedAutonomousSystem,
        "SkyseedDockerCompiler": seedemu_integration.SkyseedDockerCompiler,
        "StaticEmulatorCreationResult": (
            static_emulator.StaticEmulatorCreationResult
        ),
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
        "Compiler",
        "Launcher",
        "StaticEmulatorCreationResult",
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


def test_creation_result_is_operation_module_only() -> None:
    result_type = static_emulator.StaticEmulatorCreationResult
    assert result_type.__module__ == "skyseed_v2.static_emulator"
    assert not hasattr(skyseed_v2, result_type.__name__)
    assert not hasattr(entities, result_type.__name__)
    assert not hasattr(simulator_input, result_type.__name__)


def test_direct_file_driven_call_chain_is_documented() -> None:
    document = API_DOCUMENT.read_text(encoding="utf-8")
    for operation in (
        "load_static_emulator_config(path)",
        "create_static_emulator(config)",
        "create_simulator_static_input(creation.skyseed_autonomous_systems)",
        "create_emulator_node_index(creation.skyseed_autonomous_systems)",
    ):
        assert operation in document
    for retired in (
        "StaticEmulatorScenarioLoader",
        "StaticScenarioApplier",
        "StaticEmulatorSpecificationApplier",
        "StaticEmulatorApplyResult",
    ):
        assert retired not in document
