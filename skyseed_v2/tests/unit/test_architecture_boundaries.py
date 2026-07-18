import ast
import importlib.util
import inspect
from pathlib import Path

import skyseed_v2
from skyseed_v2 import simulator_input
from skyseed_v2.domain import GroundStation, Satellite, validate_identifier
from skyseed_v2.seed import SkyseedDockerCompiler
from skyseed_v2.seed.metadata import (
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
    from skyseed_v2.domain import ground_station, satellite

    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    assert "skyseed_v2.domain.ground_station" not in satellite_imports
    assert "skyseed_v2.domain.satellite" not in ground_station_imports
    assert not any("simulator_input" in module for module in satellite_imports)
    assert not any("simulator_input" in module for module in ground_station_imports)
    assert not hasattr(Satellite, "runtime_reference")
    assert not hasattr(GroundStation, "runtime_reference")


def test_skyseed_as_owns_entities_but_not_simulator_input() -> None:
    from skyseed_v2.seed import autonomous_system

    imports = imported_modules(autonomous_system)
    assert not any("simulator_input" in module for module in imports)
    assert not hasattr(autonomous_system.SkyseedAutonomousSystem, "simulator_input")
    assert not hasattr(
        autonomous_system.SkyseedAutonomousSystem,
        "simulator_static_input",
    )


def test_simulator_input_dependencies_point_toward_emulator_entities() -> None:
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

    assert "skyseed_v2.domain" in satellite_imports
    assert "skyseed_v2.domain" in ground_station_imports
    assert "skyseed_v2.domain.validation" in identity_imports
    assert ".ground_station" not in satellite_imports
    assert ".satellite" not in ground_station_imports
    assert {name for name in static_input_imports if name.startswith(".")} == {
        ".ground_station",
        ".identity",
        ".satellite",
    }
    assert "seedemu.core" in static_input_imports
    assert "skyseed_v2.seed.autonomous_system" in static_input_imports


def test_static_input_has_no_registry_compiler_or_output_dependency() -> None:
    from skyseed_v2.simulator_input import static_input

    static_input_imports = imported_modules(static_input)

    assert "skyseed_v2.seed.autonomous_system" in static_input_imports
    for forbidden_dependency in (
        "skyseed_v2.seed.compiler",
        "skyseed_v2.seed.metadata",
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


def test_simulator_static_input_is_root_public_with_no_entity_back_dependency() -> None:
    from skyseed_v2.simulator_input import SimulatorStaticInput
    from skyseed_v2.domain import ground_station, satellite

    assert skyseed_v2.SimulatorStaticInput is SimulatorStaticInput
    assert "SimulatorStaticInput" in simulator_input.__all__
    assert not any(
        "simulator_input" in module_name
        for module_name in imported_modules(satellite)
    )
    assert not any(
        "simulator_input" in module_name
        for module_name in imported_modules(ground_station)
    )


def test_shared_validation_and_seed_metadata_have_explicit_owners() -> None:
    assert validate_identifier.__module__ == "skyseed_v2.domain.validation"
    assert SEED_METADATA_PREFIX == "org.seedsecuritylabs.seedemu.meta."
    assert SKYSEED_METADATA_PREFIX == "skyseed."


def test_retired_as_and_compiler_names_have_no_aliases() -> None:
    import skyseed_v2.seed as seed_integration

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
    from skyseed_v2 import specifications

    imports = imported_modules(specifications)
    assert not any(
        module == "seedemu"
        or module.startswith("seedemu.")
        or module == "skyseed_v1"
        or module.startswith("skyseed_v1.")
        for module in imports
    )
    source = Path(inspect.getsourcefile(specifications)).read_text()
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
        "skyseed_v2.domain",
        "skyseed_v2.seed",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden_prefixes
    )


def test_scenario_loader_and_formal_examples_respect_application_boundaries() -> None:
    from skyseed_v2 import scenario_application, scenario_loader
    from skyseed_v2.domain import ground_station, satellite

    loader_imports = imported_modules(scenario_loader)
    assert "skyseed_v2.specifications" in loader_imports
    for forbidden_prefix in (
        "seedemu",
        "skyseed_v2.seed",
        "skyseed_v2.domain",
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
            "scenario_loader" in module
            for module in imported_modules(input_module)
        )
    for domain_module in (satellite, ground_station):
        assert not any(
            "scenario" in module or "yaml" in module
            for module in imported_modules(domain_module)
        )

    application_imports = imported_modules(scenario_application)
    assert "skyseed_v2.specifications" in application_imports
    assert "skyseed_v2.seed" in application_imports
    assert "skyseed_v2.simulator_input" in application_imports
    for forbidden_prefix in (
        "skyseed_v2.scenario_loader",
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
        inspect.getsourcefile(scenario_application)
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

    from skyseed_v2.scenario_application import (
        StaticEmulatorApplyResult,
        StaticEmulatorSpecificationApplier,
    )

    assert StaticEmulatorSpecificationApplier.__module__ == (
        "skyseed_v2.scenario_application"
    )
    assert StaticEmulatorApplyResult.__module__ == (
        "skyseed_v2.scenario_application"
    )
    assert not hasattr(skyseed_v2, "StaticEmulatorSpecificationApplier")
    assert not hasattr(skyseed_v2, "StaticEmulatorApplyResult")
    assert not hasattr(simulator_input, "StaticEmulatorApplyResult")
