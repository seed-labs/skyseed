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


def test_domain_entities_do_not_depend_on_each_other_or_simulator_input() -> None:
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
        identity,
        satellite,
        static_input,
    )

    static_input_imports = imported_modules(static_input)
    identity_imports = imported_modules(identity)
    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    for imports in (
        static_input_imports,
        identity_imports,
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
    assert "skyseed_v2.identifier" in identity_imports
    assert ".ground_station" not in satellite_imports
    assert ".satellite" not in ground_station_imports
    assert {name for name in static_input_imports if name.startswith(".")} == {
        ".ground_station",
        ".identity",
        ".satellite",
    }


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
from skyseed_v2.simulator_input import SimulatorStaticInput
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
        "StaticEmulatorScenarioLoader",
        "SkyseedDockerCompiler",
    ):
        assert forbidden not in source


def test_removed_and_deferred_boundary_apis_are_not_exported() -> None:
    for forbidden_name in (
        "RuntimeNodeReference",
        "SeedRegistryLocator",
        "RuntimeNodeResolver",
        "ExecutionTargetRef",
    ):
        assert not hasattr(simulator_input, forbidden_name)
        assert not hasattr(skyseed_v2, forbidden_name)

    assert importlib.util.find_spec("skyseed_v2.simulator_input.runtime") is None
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


def test_generic_static_specifications_are_backend_and_v1_independent() -> None:
    from skyseed_v2.specifications import (
        _validation,
        ground_station,
        network,
        satellite,
        scenario,
    )

    for specification_module in (
        _validation,
        ground_station,
        network,
        satellite,
        scenario,
    ):
        imports = imported_modules(specification_module)
        assert not any(
            module == "seedemu"
            or module.startswith("seedemu.")
            or module == "skyseed_v1"
            or module.startswith("skyseed_v1.")
            for module in imports
        )
        source = Path(inspect.getsourcefile(specification_module)).read_text()
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


def test_serialization_and_application_respect_package_boundaries() -> None:
    from skyseed_v2.application import (
        simulator_static_input as simulator_static_input_application,
        static_scenario_application,
    )
    from skyseed_v2.entities import ground_station, satellite
    from skyseed_v2.serialization import static_emulator_yaml_loader

    loader_imports = imported_modules(static_emulator_yaml_loader)
    assert "skyseed_v2.specifications" in loader_imports
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
            for module in loader_imports
        )

    from skyseed_v2.simulator_input import (
        ground_station as ground_station_input,
        identity,
        satellite as satellite_input,
        static_input,
    )

    for input_module in (
        static_input,
        identity,
        satellite_input,
        ground_station_input,
    ):
        assert not any(
            "serialization" in module
            for module in imported_modules(input_module)
        )
    for domain_module in (satellite, ground_station):
        assert not any(
            "scenario" in module or "yaml" in module
            for module in imported_modules(domain_module)
        )

    application_imports = imported_modules(static_scenario_application)
    assert "skyseed_v2.specifications" in application_imports
    assert "skyseed_v2.seedemu_integration" in application_imports
    assert "skyseed_v2.simulator_input" in application_imports
    for forbidden_prefix in (
        "skyseed_v2.serialization",
        "skyseed_v2.materialization",
        "yaml",
        "docker",
    ):
        assert not any(
            module == forbidden_prefix
            or module.startswith(f"{forbidden_prefix}.")
            for module in application_imports
        )
    application_source = Path(
        inspect.getsourcefile(static_scenario_application)
    ).read_text(encoding="utf-8")
    for forbidden in (
        "getRegistry",
        "registry.register",
        "validate_identifier",
        "SkyseedDockerCompiler",
        "duplicate Orbits",
        "duplicate Satellites",
    ):
        assert forbidden not in application_source

    conversion_imports = imported_modules(simulator_static_input_application)
    for required_dependency in (
        "skyseed_v2.entities",
        "skyseed_v2.seedemu_integration",
        "skyseed_v2.simulator_input",
    ):
        assert required_dependency in conversion_imports
    conversion_source = Path(
        inspect.getsourcefile(simulator_static_input_application)
    ).read_text(encoding="utf-8")
    assert not any(
        isinstance(node, ast.ClassDef)
        for node in ast.walk(ast.parse(conversion_source))
    )


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

    from skyseed_v2.application import (
        StaticEmulatorApplyResult,
        StaticEmulatorSpecificationApplier,
    )

    assert StaticEmulatorSpecificationApplier.__module__ == (
        "skyseed_v2.application.static_scenario_application"
    )
    assert StaticEmulatorApplyResult.__module__ == (
        "skyseed_v2.application.static_scenario_application"
    )
    assert not hasattr(skyseed_v2, "StaticEmulatorSpecificationApplier")
    assert not hasattr(skyseed_v2, "StaticEmulatorApplyResult")
    assert not hasattr(simulator_input, "StaticEmulatorApplyResult")
