import ast
import importlib.util
import inspect
from pathlib import Path

import skyseed_v2
from skyseed_v2 import contracts
from skyseed_v2 import catalog_builder
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


def test_domain_entities_do_not_depend_on_each_other_or_contracts() -> None:
    from skyseed_v2.domain import ground_station, satellite

    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    assert "skyseed_v2.domain.ground_station" not in satellite_imports
    assert "skyseed_v2.domain.satellite" not in ground_station_imports
    assert not any("contracts" in module for module in satellite_imports)
    assert not any("contracts" in module for module in ground_station_imports)
    assert not hasattr(Satellite, "runtime_reference")
    assert not hasattr(GroundStation, "runtime_reference")


def test_contract_modules_have_only_the_approved_internal_dependencies() -> None:
    from skyseed_v2.contracts import catalog, ground_station, identity, satellite

    catalog_imports = imported_modules(catalog)
    identity_imports = imported_modules(identity)
    satellite_imports = imported_modules(satellite)
    ground_station_imports = imported_modules(ground_station)

    for module_imports in (
        catalog_imports,
        identity_imports,
        satellite_imports,
        ground_station_imports,
    ):
        assert not any(
            module_name == "skyseed_v2.seed"
            or module_name.startswith("skyseed_v2.seed.")
            or module_name == "seedemu"
            or module_name.startswith("seedemu.")
            for module_name in module_imports
        )

    assert {name for name in satellite_imports if name.startswith(".")} == {
        ".identity"
    }
    assert {
        name for name in ground_station_imports if name.startswith(".")
    } == {".identity"}
    assert ".ground_station" not in satellite_imports
    assert ".satellite" not in ground_station_imports
    assert {name for name in catalog_imports if name.startswith(".")} == {
        ".ground_station",
        ".identity",
        ".satellite",
    }


def test_catalog_builder_has_no_registry_compiler_or_output_dependency() -> None:
    builder_imports = imported_modules(catalog_builder)

    assert "skyseed_v2.seed.autonomous_system" in builder_imports
    for forbidden_dependency in (
        "skyseed_v2.seed.compiler",
        "skyseed_v2.seed.metadata",
        "seedemu.compiler",
        "docker",
    ):
        assert not any(
            module_name == forbidden_dependency
            or module_name.startswith(f"{forbidden_dependency}.")
            for module_name in builder_imports
        )


def test_removed_and_deferred_contract_apis_are_not_exported() -> None:
    for forbidden_name in (
        "RuntimeNodeReference",
        "SeedRegistryLocator",
        "RuntimeNodeResolver",
        "ExecutionTargetRef",
    ):
        assert not hasattr(contracts, forbidden_name)
        assert not hasattr(skyseed_v2, forbidden_name)

    assert importlib.util.find_spec("skyseed_v2.contracts.runtime") is None


def test_catalog_contract_and_builder_are_public_without_domain_backlinks() -> None:
    from skyseed_v2.contracts import SimulationEntityCatalog
    from skyseed_v2.domain import ground_station, satellite

    assert skyseed_v2.SimulationEntityCatalog is SimulationEntityCatalog
    assert skyseed_v2.SimulationEntityCatalogBuilder.__module__ == (
        "skyseed_v2.catalog_builder"
    )
    assert not any(
        "catalog" in module_name for module_name in imported_modules(satellite)
    )
    assert not any(
        "catalog" in module_name
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
