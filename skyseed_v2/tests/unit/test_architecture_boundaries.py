import ast
import importlib.util
import inspect
import os
from pathlib import Path
from subprocess import run
import sys

import skyseed_v2
from skyseed_v2.emulator.entities import GroundStation, Satellite
from skyseed_v2.shared.identifier import validate_identifier
from skyseed_v2.simulator import input as simulator_static_input


def imported_modules(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            if node.module is not None:
                modules.add(f"{prefix}{node.module}")
            else:
                modules.update(f"{prefix}{alias.name}" for alias in node.names)
    return modules


def test_peer_entities_do_not_depend_on_simulator_static_input() -> None:
    from skyseed_v2.emulator.entities import ground_station, satellite

    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    assert "skyseed_v2.emulator.entities.ground_station" not in satellite_imports
    assert "skyseed_v2.emulator.entities.satellite" not in ground_station_imports
    assert not any(
        "skyseed_v2.simulator" in module for module in satellite_imports
    )
    assert not any(
        "skyseed_v2.simulator" in module for module in ground_station_imports
    )
    assert not hasattr(Satellite, "runtime_reference")
    assert not hasattr(GroundStation, "runtime_reference")


def test_skyseed_as_owns_entities_but_not_simulator_static_input() -> None:
    from skyseed_v2.emulator.seedemu import skyseed_autonomous_system

    imports = imported_modules(skyseed_autonomous_system)
    assert not any("skyseed_v2.simulator" in module for module in imports)
    for boundary_attribute in (
        "emulator_input",
        "simulator_input",
        "simulator_static_input",
    ):
        assert not hasattr(
            skyseed_autonomous_system.SkyseedAutonomousSystem,
            boundary_attribute,
        )


def test_seedemu_extension_is_an_extractable_upstream_boundary() -> None:
    from skyseed_v2.emulator import seedemu

    seedemu_root = Path(inspect.getsourcefile(seedemu)).parent.resolve()
    files = {
        path.relative_to(seedemu_root).as_posix()
        for path in seedemu_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert files == {
        "README.md",
        "__init__.py",
        "skyseed_autonomous_system.py",
    }

    for path in seedemu_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for forbidden_prefix in (
            "skyseed_v2.simulator",
            "skyseed_v2.configuration",
            "skyseed_v2.compose",
            "skyseed_v2.emulator_to_simulator",
            "tools",
            "examples",
        ):
            assert not any(
                imported == forbidden_prefix
                or imported.startswith(f"{forbidden_prefix}.")
                for imported in imports
            ), (path, forbidden_prefix)


def test_retired_seedemu_compiler_assets_are_not_packaged() -> None:
    project_root = Path(__file__).resolve().parents[2]
    package_configuration = (project_root / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"skyseed_v2.emulator.seedemu" = ["assets/*.sh"]' not in (
        package_configuration
    )
    assert '"skyseed_v2.seedemu_integration"' not in package_configuration
    seedemu_root = project_root / "src/skyseed_v2/emulator/seedemu"
    assert not any((seedemu_root / "assets").glob("*"))
    assert not (seedemu_root / "metadata.py").exists()
    assert not (seedemu_root / "satellite_base.py").exists()
    assert not (seedemu_root / "skyseed_docker_compiler.py").exists()


def test_simulator_static_input_has_only_data_layer_dependencies() -> None:
    from skyseed_v2.simulator.input import (
        ground_station,
        satellite,
        static_input,
    )
    from skyseed_v2.shared import node_id

    static_input_imports = imported_modules(static_input)
    node_id_imports = imported_modules(node_id)
    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    for imports in (
        static_input_imports,
        node_id_imports,
        satellite_imports,
        ground_station_imports,
    ):
        for forbidden_prefix in (
            "skyseed_v2.emulator.entities",
            "skyseed_v2.emulator.seedemu",
            "seedemu",
        ):
            assert not any(
                module == forbidden_prefix
                or module.startswith(f"{forbidden_prefix}.")
                for module in imports
            )
    assert "skyseed_v2.shared.identifier" in node_id_imports
    assert ".ground_station" not in satellite_imports
    assert ".satellite" not in ground_station_imports
    assert {name for name in static_input_imports if name.startswith(".")} == {
        ".ground_station",
        ".satellite",
    }
    assert "skyseed_v2.shared" in static_input_imports


def test_simulator_static_input_import_does_not_load_upstream_seedemu() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'seedemu' or name.startswith('seedemu.'):
        raise AssertionError(f'unexpected seedemu import: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator.input import SimulatorStaticInput
assert NodeId(65001, 'satellite', 'satellite-0').value == (
    '65001/satellite/satellite-0'
)
assert SimulatorStaticInput().list_nodes() == ()
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root / "src")
    result = run(
        (sys.executable, "-c", script),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_static_input_has_no_registry_compilation_or_output_dependency() -> None:
    from skyseed_v2.simulator.input import static_input

    static_input_imports = imported_modules(static_input)

    for forbidden_dependency in (
        "skyseed_v2.emulator.entities",
        "skyseed_v2.emulator.seedemu",
        "seedemu",
        "skyseed_v2.emulator.seedemu.skyseed_docker_compiler",
        "skyseed_v2.emulator.seedemu.metadata",
        "seedemu.compiler",
        "docker",
    ):
        assert not any(
            module_name == forbidden_dependency
            or module_name.startswith(f"{forbidden_dependency}.")
            for module_name in static_input_imports
        )
    source = Path(inspect.getsourcefile(static_input)).read_text(encoding="utf-8")
    for forbidden in (
        "getRegistry",
        "create_shell",
        "create_orbit",
        "create_satellite",
        "create_ground_station",
        "load_static_emulator_config",
        "SkyseedDockerCompiler",
        "SimulationPlanCompiler",
    ):
        assert forbidden not in source


def test_removed_and_undesignated_future_boundary_apis_are_not_exported() -> None:
    for forbidden_name in (
        "RuntimeNodeReference",
        "SeedRegistryLocator",
        "RuntimeNodeResolver",
        "ExecutionTargetRef",
        "BoundaryResult",
        "NodeBindingRegistry",
        "NodeResolverManager",
        "GlobalNodeRegistry",
    ):
        assert not hasattr(simulator_static_input, forbidden_name)
        assert not hasattr(skyseed_v2, forbidden_name)

    import skyseed_v2.simulator as simulator

    for implemented_name in (
        "SimulationDefinition",
        "SimulationTimeline",
        "Simulator",
        "Topology",
    ):
        assert hasattr(simulator, implemented_name)
        assert not hasattr(skyseed_v2, implemented_name)

    future_capability_tokens = (
        "backend",
        "executioncompiler",
        "preparer",
        "simulationrunner",
    )
    for module in (simulator, skyseed_v2):
        public_names = tuple(getattr(module, "__all__", ()))
        assert not any(
            token in public_name.lower()
            for public_name in public_names
            for token in future_capability_tokens
        )

    project_package = Path(inspect.getsourcefile(skyseed_v2)).parent
    assert not any(
        future_component in path.parts
        for path in project_package.rglob("*")
        for future_component in (
            "backend",
            "execution",
            "preparer",
            "runner",
            "runtime",
        )
    )
    assert not any(
        isinstance(node, ast.ClassDef)
        and any(
            token in node.name.lower()
            for token in future_capability_tokens
        )
        for path in project_package.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert importlib.util.find_spec("skyseed_v2.simulator.slicing.difference") is not None
    assert importlib.util.find_spec("skyseed_v2.simulator.planning") is not None
    assert (
        importlib.util.find_spec("skyseed_v2.simulator.input.identity")
        is None
    )
    assert importlib.util.find_spec("skyseed_v2.emulator_input") is None
    assert importlib.util.find_spec("skyseed_v2.simulator_input") is None
    for removed_module in (
        "skyseed_v2.contracts",
        "skyseed_v2.projection",
        "skyseed_v2.catalog_builder",
    ):
        assert importlib.util.find_spec(removed_module) is None


def test_simulator_static_input_is_public_without_entity_back_dependency() -> None:
    from skyseed_v2.simulator.input import SimulatorStaticInput
    from skyseed_v2.emulator.entities import ground_station, satellite

    assert "SimulatorStaticInput" in simulator_static_input.__all__
    assert (
        SimulatorStaticInput.__module__
        == "skyseed_v2.simulator.input.static_input"
    )
    for input_name in simulator_static_input.__all__:
        assert not hasattr(skyseed_v2, input_name)
    assert not any(
        "skyseed_v2.simulator" in module_name
        for module_name in imported_modules(satellite)
    )
    assert not any(
        "skyseed_v2.simulator" in module_name
        for module_name in imported_modules(ground_station)
    )


def test_simulator_core_has_no_emulator_seed_or_generation_dependency() -> None:
    from skyseed_v2.simulator import definition, simulator, timeline
    from skyseed_v2.simulator.geometry import coordinates
    from skyseed_v2.simulator.slicing import (
        connection,
        difference,
        position,
        route,
        slice,
        slicer,
    )
    from skyseed_v2.simulator.topology import topology, zone
    from skyseed_v2.simulator.models import (
        circular_orbit,
        geometric_connection,
        hop_count_route,
    )

    for module in (
        connection,
        coordinates,
        definition,
        difference,
        position,
        route,
        simulator,
        slice,
        slicer,
        timeline,
        topology,
        zone,
        circular_orbit,
        geometric_connection,
        hop_count_route,
    ):
        imports = imported_modules(module)
        for forbidden_prefix in (
            "skyseed_v2.emulator.entities",
            "skyseed_v2.emulator.seedemu",
            "skyseed_v2.emulator_to_simulator",
            "seedemu",
        ):
            assert not any(
                imported == forbidden_prefix
                or imported.startswith(f"{forbidden_prefix}.")
                for imported in imports
            )


def test_shared_validation_has_an_explicit_owner() -> None:
    assert validate_identifier.__module__ == "skyseed_v2.shared.identifier"


def test_retired_as_and_compiler_names_have_no_aliases() -> None:
    import skyseed_v2.emulator.seedemu as seed_integration

    for retired_name in (
        "SatelliteAutonomousSystem",
        "GroundStationAutonomousSystem",
        "SatelliteDockerCompiler",
        "SkyseedDockerCompiler",
        "SatelliteBase",
    ):
        assert not hasattr(skyseed_v2, retired_name)
        assert not hasattr(seed_integration, retired_name)

def test_skyseed_has_no_private_docker_accumulator_access() -> None:
    package_root = Path(inspect.getsourcefile(skyseed_v2)).parent
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package_root.rglob("*.py")
    )
    assert "_Docker__services" not in production_source


def test_static_configuration_is_backend_and_v1_independent() -> None:
    from skyseed_v2.configuration.emulator import (
        _validation,
        attachments,
        ground_network,
        ground_station,
        satellite,
        static_emulator_config,
    )

    for config_module in (
        _validation,
        attachments,
        ground_network,
        ground_station,
        satellite,
        static_emulator_config,
    ):
        imports = imported_modules(config_module)
        assert not any(
            module == "seedemu"
            or module.startswith("seedemu.")
            or module == "skyseed_v1"
            or module.startswith("skyseed_v1.")
            for module in imports
        )
        source = Path(inspect.getsourcefile(config_module)).read_text()
        for forbidden in (
            "simulation_start_time",
            "current_position",
            "visibility",
            "RuntimeNodeReference",
            "resource_locator",
        ):
            assert forbidden not in source


def test_compose_materialization_is_outside_emulator_and_compilation_models() -> None:
    from skyseed_v2.compose import launcher

    imports = imported_modules(launcher)
    forbidden_prefixes = (
        "seedemu",
        "skyseed_v2.simulator",
        "skyseed_v2.emulator.entities",
        "skyseed_v2.emulator.seedemu",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden_prefixes
    )


def test_configuration_creation_and_input_generation_respect_boundaries() -> None:
    from skyseed_v2 import emulator_to_simulator
    from skyseed_v2.configuration.emulator import yaml as config_yaml
    from skyseed_v2.emulator import construction
    from skyseed_v2.emulator.entities import ground_station, satellite

    yaml_imports = imported_modules(config_yaml)
    assert ".static_emulator_config" in yaml_imports
    for forbidden_prefix in (
        "seedemu",
        "skyseed_v2.emulator.seedemu",
        "skyseed_v2.emulator.entities",
        "skyseed_v2.compose",
        "skyseed_v2.simulator",
    ):
        assert not any(
            module == forbidden_prefix
            or module.startswith(f"{forbidden_prefix}.")
            for module in yaml_imports
        )

    from skyseed_v2.simulator.input import (
        ground_station as ground_station_input,
        satellite as satellite_input,
        static_input,
    )
    from skyseed_v2.shared import node_id

    for input_module in (
        static_input,
        node_id,
        satellite_input,
        ground_station_input,
    ):
        assert not any(
            "configuration" in module or "yaml" in module
            for module in imported_modules(input_module)
        )
    for entity_module in (satellite, ground_station):
        assert not any(
            "scenario" in module or "yaml" in module
            for module in imported_modules(entity_module)
        )

    creation_imports = imported_modules(construction)
    assert "skyseed_v2.configuration.emulator" in creation_imports
    assert "skyseed_v2.emulator.entities" in creation_imports
    assert "skyseed_v2.emulator.seedemu" in creation_imports
    for forbidden_prefix in (
        "skyseed_v2.simulator",
        "skyseed_v2.emulator_to_simulator",
        "skyseed_v2.compose",
        "yaml",
        "docker",
    ):
        assert not any(
            module == forbidden_prefix
            or module.startswith(f"{forbidden_prefix}.")
            for module in creation_imports
        )
    creation_source = Path(
        inspect.getsourcefile(construction)
    ).read_text(encoding="utf-8")
    for forbidden in (
        "getRegistry",
        "registry.register",
        "validate_identifier",
        "SkyseedDockerCompiler",
        "duplicate Orbits",
        "duplicate Satellites",
    ):
        assert forbidden not in creation_source

    generation_imports = imported_modules(emulator_to_simulator)
    for required_dependency in (
        "skyseed_v2.emulator.entities",
        "skyseed_v2.shared",
        "skyseed_v2.emulator.seedemu",
        "skyseed_v2.simulator.input",
    ):
        assert required_dependency in generation_imports
    for forbidden_dependency in (
        "skyseed_v2.configuration",
        "skyseed_v2.emulator.construction",
        "skyseed_v2.compose",
        "yaml",
        "docker",
    ):
        assert not any(
            module == forbidden_dependency
            or module.startswith(f"{forbidden_dependency}.")
            for module in generation_imports
        )
    generation_source = Path(
        inspect.getsourcefile(emulator_to_simulator)
    ).read_text(encoding="utf-8")
    assert not any(
        isinstance(node, ast.ClassDef)
        for node in ast.walk(ast.parse(generation_source))
    )
    assert "def _derive_node_id(" in generation_source
    assert "create_emulator_node_index" not in generation_source
    assert "MappingProxyType" not in generation_source
    assert ".split(" not in generation_source
    assert "getName(" not in generation_source


def test_constellation_helper_never_became_a_domain_or_public_api() -> None:
    for forbidden_name in (
        "Constellation",
        "ConstellationBuilder",
        "ConstellationBuildResult",
        "SatellitePopulation",
        "GroundStationPopulation",
        "ShellPopulationBuilder",
        "ShellPopulationBuildResult",
        "GroundStationBuilder",
        "GroundStationBuildResult",
        "StaticEmulatorScenarioBuilder",
        "StaticEmulatorBuildResult",
    ):
        assert not hasattr(skyseed_v2, forbidden_name)

    for retired_module in (
        "skyseed_v2.shell_population_builder",
        "skyseed_v2.ground_station_builder",
        "skyseed_v2.static_scenario_builder",
    ):
        assert importlib.util.find_spec(retired_module) is None

    from seedemu.core import Emulator
    from skyseed_v2.emulator import create_static_emulator

    assert create_static_emulator.__module__ == "skyseed_v2.emulator.construction"
    assert inspect.get_annotations(create_static_emulator, eval_str=True)[
        "return"
    ] is Emulator
    assert not hasattr(skyseed_v2, "create_static_emulator")
    assert not hasattr(skyseed_v2, "StaticEmulatorCreationResult")
    assert not hasattr(simulator_static_input, "StaticEmulatorCreationResult")


def test_orbit_parameters_have_one_entity_module_owner() -> None:
    from skyseed_v2.emulator.entities import orbit, satellite

    assert inspect.getsourcefile(orbit.OrbitParameters) == inspect.getsourcefile(
        orbit.Orbit
    )
    assert not hasattr(satellite.Satellite, "orbit_parameters")
    assert "OrbitParameters" not in imported_modules(satellite)
    assert importlib.util.find_spec("skyseed_v2.emulator.entities.orbit_parameters") is None


def test_retired_file_boundary_packages_and_public_classes_are_absent() -> None:
    project_package = Path(inspect.getsourcefile(skyseed_v2)).parent
    for retired_package in ("application", "serialization", "specifications"):
        assert not (project_package / retired_package).exists()
    assert importlib.util.find_spec("skyseed_v2.emulator.construction") is not None
    assert importlib.util.find_spec("skyseed_v2.emulator.construction_creation") is None
    assert (
        importlib.util.find_spec(
            "skyseed_v2.configuration.emulator.static_emulator_config"
        )
        is not None
    )
    assert (
        importlib.util.find_spec("skyseed_v2.configuration.static_emulator")
        is None
    )
    assert importlib.util.find_spec("skyseed_v2.emulator") is not None
    for removed_module in (
        "skyseed_v2.static_emulator",
        "skyseed_v2.simulator_input_generation",
        "skyseed_v2.materialization",
        "skyseed_v2.entities",
        "skyseed_v2.seedemu_integration",
        "skyseed_v2.node_id",
        "skyseed_v2.identifier",
        "skyseed_v2.simulator.static_input",
        "skyseed_v2.simulator.position",
        "skyseed_v2.simulator.coordinates",
    ):
        assert importlib.util.find_spec(removed_module) is None

    import skyseed_v2.configuration.emulator as configuration
    import skyseed_v2.emulator.construction as static_emulator

    for retired_name in (
        "StaticEmulatorScenarioLoader",
        "StaticScenarioApplier",
        "StaticScenarioApplyResult",
    ):
        assert not hasattr(configuration, retired_name)
        assert not hasattr(static_emulator, retired_name)


def test_file_input_convergence_has_no_forbidden_parallel_api() -> None:
    import skyseed_v2.configuration.emulator as configuration
    import skyseed_v2.emulator.construction as static_emulator

    for forbidden_name in (
        "ConstructionInputManager",
        "SkyseedEmulator",
        "StaticEmulatorCreationResult",
        "load_shell_config",
        "load_ground_station_config",
    ):
        assert not hasattr(configuration, forbidden_name)
        assert not hasattr(static_emulator, forbidden_name)
        assert not hasattr(skyseed_v2, forbidden_name)
    assert not any(
        name.endswith("ConstructionInput") for name in configuration.__all__
    )
    assert set(static_emulator.__all__) == {"create_static_emulator"}


def test_one_shot_migration_has_no_old_modules_or_forwarders() -> None:
    removed_modules = (
        "skyseed_v2._time",
        "skyseed_v2.identifier",
        "skyseed_v2.node_id",
        "skyseed_v2.entities",
        "skyseed_v2.materialization",
        "skyseed_v2.seedemu_integration",
        "skyseed_v2.static_emulator",
        "skyseed_v2.simulator_input_generation",
        "skyseed_v2.configuration.attachments",
        "skyseed_v2.configuration.ground_network",
        "skyseed_v2.configuration.ground_station",
        "skyseed_v2.configuration.satellite",
        "skyseed_v2.configuration.static_emulator_config",
        "skyseed_v2.configuration.yaml",
        "skyseed_v2.configuration.zone",
        "skyseed_v2.simulator._routing_policy",
        "skyseed_v2.simulator.connection",
        "skyseed_v2.simulator.coordinates",
        "skyseed_v2.simulator.difference",
        "skyseed_v2.simulator.position",
        "skyseed_v2.simulator.route",
        "skyseed_v2.simulator.slice",
        "skyseed_v2.simulator.slicer",
        "skyseed_v2.simulator.static_input",
        "skyseed_v2.simulator.zone",
    )
    for module_name in removed_modules:
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            spec = None
        assert spec is None, module_name


def test_production_dependency_direction_has_one_cross_layer_bridge() -> None:
    package_root = Path(inspect.getsourcefile(skyseed_v2)).parent
    cross_layer_modules = []
    for path in package_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(
            imported == "tools" or imported.startswith("tools.")
            for imported in imports
        ), path
        imports_emulator = any(
            imported == "skyseed_v2.emulator"
            or imported.startswith("skyseed_v2.emulator.")
            for imported in imports
        )
        imports_simulator = any(
            imported == "skyseed_v2.simulator"
            or imported.startswith("skyseed_v2.simulator.")
            for imported in imports
        )
        if imports_emulator and imports_simulator:
            cross_layer_modules.append(path.relative_to(package_root).as_posix())
    assert cross_layer_modules == ["emulator_to_simulator.py"]


def test_geometry_module_cycle_is_eliminated() -> None:
    from skyseed_v2.simulator.geometry import coordinates, position

    coordinate_imports = imported_modules(coordinates)
    position_imports = imported_modules(position)
    assert ".position" in coordinate_imports
    assert ".coordinates" not in position_imports


def test_complete_production_import_graph_has_no_scc_or_reverse_dependency() -> None:
    project_root = Path(__file__).resolve().parents[2]
    completed = run(
        (
            sys.executable,
            "scripts/check_import_dependencies.py",
        ),
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SCCs with more than one module: 0" in completed.stdout
    assert "import dependency result: PASS" in completed.stdout
