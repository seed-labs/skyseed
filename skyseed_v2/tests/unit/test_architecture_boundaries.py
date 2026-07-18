import ast
import importlib.util
import inspect
import os
from pathlib import Path
from subprocess import run
import sys

import skyseed_v2
from skyseed_v2 import simulator_input
from skyseed_v2.entities import GroundStation, Satellite
from skyseed_v2.identifier import validate_identifier
from skyseed_v2.seedemu_integration import SkyseedDockerCompiler
from skyseed_v2.seedemu_integration.metadata import (
    SEED_METADATA_PREFIX,
    SKYSEED_METADATA_PREFIX,
)


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


def test_peer_entities_do_not_depend_on_each_other_or_simulator_input() -> None:
    from skyseed_v2.entities import ground_station, satellite

    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    assert "skyseed_v2.entities.ground_station" not in satellite_imports
    assert "skyseed_v2.entities.satellite" not in ground_station_imports
    assert not any("simulator_input" in module for module in satellite_imports)
    assert not any("simulator_input" in module for module in ground_station_imports)
    assert not hasattr(Satellite, "runtime_reference")
    assert not hasattr(GroundStation, "runtime_reference")


def test_skyseed_as_owns_entities_but_not_simulator_input() -> None:
    from skyseed_v2.seedemu_integration import skyseed_autonomous_system

    imports = imported_modules(skyseed_autonomous_system)
    assert not any("simulator_input" in module for module in imports)
    assert not hasattr(
        skyseed_autonomous_system.SkyseedAutonomousSystem,
        "simulator_input",
    )
    assert not hasattr(
        skyseed_autonomous_system.SkyseedAutonomousSystem,
        "simulator_static_input",
    )


def test_simulator_input_has_only_data_layer_dependencies() -> None:
    from skyseed_v2.simulator_input import (
        ground_station,
        satellite,
        static_input,
    )
    from skyseed_v2 import node_id

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
            "skyseed_v2.entities",
            "skyseed_v2.seedemu_integration",
            "seedemu",
        ):
            assert not any(
                module == forbidden_prefix
                or module.startswith(f"{forbidden_prefix}.")
                for module in imports
            )
    assert "skyseed_v2.identifier" in node_id_imports
    assert ".ground_station" not in satellite_imports
    assert ".satellite" not in ground_station_imports
    assert {name for name in static_input_imports if name.startswith(".")} == {
        ".ground_station",
        ".satellite",
    }
    assert "skyseed_v2.node_id" in static_input_imports


def test_simulator_input_import_does_not_load_upstream_seedemu() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'seedemu' or name.startswith('seedemu.'):
        raise AssertionError(f'unexpected seedemu import: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from skyseed_v2.node_id import NodeId
from skyseed_v2.simulator_input import SimulatorStaticInput
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


def test_static_input_has_no_registry_compiler_or_output_dependency() -> None:
    from skyseed_v2.simulator_input import static_input

    static_input_imports = imported_modules(static_input)

    for forbidden_dependency in (
        "skyseed_v2.entities",
        "skyseed_v2.seedemu_integration",
        "seedemu",
        "skyseed_v2.seedemu_integration.skyseed_docker_compiler",
        "skyseed_v2.seedemu_integration.metadata",
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
    ):
        assert forbidden not in source


def test_removed_and_deferred_boundary_apis_are_not_exported() -> None:
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
        assert not hasattr(simulator_input, forbidden_name)
        assert not hasattr(skyseed_v2, forbidden_name)

    assert importlib.util.find_spec("skyseed_v2.simulator_input.runtime") is None
    assert importlib.util.find_spec("skyseed_v2.simulator") is None
    assert importlib.util.find_spec("skyseed_v2.simulator_input.identity") is None
    for removed_module in (
        "skyseed_v2.contracts",
        "skyseed_v2.projection",
        "skyseed_v2.catalog_builder",
    ):
        assert importlib.util.find_spec(removed_module) is None


def test_simulator_static_input_is_subpackage_public_with_no_entity_back_dependency() -> None:
    from skyseed_v2.simulator_input import SimulatorStaticInput
    from skyseed_v2.entities import ground_station, satellite

    assert "SimulatorStaticInput" in simulator_input.__all__
    for input_name in simulator_input.__all__:
        assert not hasattr(skyseed_v2, input_name)
    assert not any(
        "simulator_input" in module_name
        for module_name in imported_modules(satellite)
    )
    assert not any(
        "simulator_input" in module_name
        for module_name in imported_modules(ground_station)
    )


def test_shared_validation_and_seed_metadata_have_explicit_owners() -> None:
    assert validate_identifier.__module__ == "skyseed_v2.identifier"
    assert SEED_METADATA_PREFIX == "org.seedsecuritylabs.seedemu.meta."
    assert SKYSEED_METADATA_PREFIX == "skyseed."


def test_retired_as_and_compiler_names_have_no_aliases() -> None:
    import skyseed_v2.seedemu_integration as seed_integration

    for retired_name in (
        "SatelliteAutonomousSystem",
        "GroundStationAutonomousSystem",
        "SatelliteDockerCompiler",
    ):
        assert not hasattr(skyseed_v2, retired_name)
        assert not hasattr(seed_integration, retired_name)

    compiler = SkyseedDockerCompiler(internet_map_enabled=False)
    assert compiler.getName() == "SkyseedDocker"


def test_private_upstream_accumulator_has_one_source_access_point() -> None:
    compiler_source = Path(inspect.getsourcefile(SkyseedDockerCompiler)).read_text()
    assert compiler_source.count('"_Docker__services"') == 1


def test_static_configuration_is_backend_and_v1_independent() -> None:
    from skyseed_v2.configuration import (
        _validation,
        ground_station,
        network,
        satellite,
        static_emulator,
    )

    for config_module in (
        _validation,
        ground_station,
        network,
        satellite,
        static_emulator,
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
            "epoch_utc",
            "simulation_start_time",
            "current_position",
            "visibility",
            "RuntimeNodeReference",
            "resource_locator",
        ):
            assert forbidden not in source


def test_compose_materialization_is_outside_emulator_and_compiler_models() -> None:
    from skyseed_v2.materialization import compose_project_launcher

    imports = imported_modules(compose_project_launcher)
    forbidden_prefixes = (
        "seedemu",
        "skyseed_v2.simulator_input",
        "skyseed_v2.entities",
        "skyseed_v2.seedemu_integration",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden_prefixes
    )


def test_configuration_creation_and_input_generation_respect_boundaries() -> None:
    from skyseed_v2 import simulator_input_generation, static_emulator
    from skyseed_v2.configuration import yaml as config_yaml
    from skyseed_v2.entities import ground_station, satellite

    yaml_imports = imported_modules(config_yaml)
    assert ".static_emulator" in yaml_imports
    for forbidden_prefix in (
        "seedemu",
        "skyseed_v2.seedemu_integration",
        "skyseed_v2.entities",
        "skyseed_v2.materialization",
        "skyseed_v2.simulator_input",
    ):
        assert not any(
            module == forbidden_prefix
            or module.startswith(f"{forbidden_prefix}.")
            for module in yaml_imports
        )

    from skyseed_v2.simulator_input import (
        ground_station as ground_station_input,
        satellite as satellite_input,
        static_input,
    )
    from skyseed_v2 import node_id

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

    creation_imports = imported_modules(static_emulator)
    assert "skyseed_v2.configuration" in creation_imports
    assert "skyseed_v2.entities" in creation_imports
    assert "skyseed_v2.seedemu_integration" in creation_imports
    for forbidden_prefix in (
        "skyseed_v2.simulator_input",
        "skyseed_v2.simulator_input_generation",
        "skyseed_v2.materialization",
        "yaml",
        "docker",
    ):
        assert not any(
            module == forbidden_prefix
            or module.startswith(f"{forbidden_prefix}.")
            for module in creation_imports
        )
    creation_source = Path(
        inspect.getsourcefile(static_emulator)
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

    generation_imports = imported_modules(simulator_input_generation)
    for required_dependency in (
        "skyseed_v2.entities",
        "skyseed_v2.node_id",
        "skyseed_v2.seedemu_integration",
        "skyseed_v2.simulator_input",
    ):
        assert required_dependency in generation_imports
    for forbidden_dependency in (
        "skyseed_v2.configuration",
        "skyseed_v2.static_emulator",
        "skyseed_v2.materialization",
        "yaml",
        "docker",
    ):
        assert not any(
            module == forbidden_dependency
            or module.startswith(f"{forbidden_dependency}.")
            for module in generation_imports
        )
    generation_source = Path(
        inspect.getsourcefile(simulator_input_generation)
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

    from skyseed_v2.static_emulator import (
        StaticEmulatorCreationResult,
        create_static_emulator,
    )

    assert create_static_emulator.__module__ == "skyseed_v2.static_emulator"
    assert StaticEmulatorCreationResult.__module__ == "skyseed_v2.static_emulator"
    assert not hasattr(skyseed_v2, "create_static_emulator")
    assert not hasattr(skyseed_v2, "StaticEmulatorCreationResult")
    assert not hasattr(simulator_input, "StaticEmulatorCreationResult")


def test_orbit_parameters_have_one_entity_module_owner() -> None:
    from skyseed_v2.entities import orbit, satellite

    assert inspect.getsourcefile(orbit.OrbitParameters) == inspect.getsourcefile(
        orbit.Orbit
    )
    assert not hasattr(satellite.Satellite, "orbit_parameters")
    assert "OrbitParameters" not in imported_modules(satellite)
    assert importlib.util.find_spec("skyseed_v2.entities.orbit_parameters") is None


def test_retired_file_boundary_packages_and_public_classes_are_absent() -> None:
    project_package = Path(inspect.getsourcefile(skyseed_v2)).parent
    for retired_package in ("application", "serialization", "specifications"):
        assert not (project_package / retired_package).exists()

    import skyseed_v2.configuration as configuration
    import skyseed_v2.static_emulator as static_emulator

    for retired_name in (
        "StaticEmulatorScenarioLoader",
        "StaticScenarioApplier",
        "StaticScenarioApplyResult",
    ):
        assert not hasattr(configuration, retired_name)
        assert not hasattr(static_emulator, retired_name)
