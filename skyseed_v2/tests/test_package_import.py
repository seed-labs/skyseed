import importlib


def test_package_can_be_imported() -> None:
    package = importlib.import_module("skyseed_v2")

    assert package.__name__ == "skyseed_v2"


def test_package_exposes_no_retired_satellite_node_alias() -> None:
    package = importlib.import_module("skyseed_v2")
    seed_integration = importlib.import_module("skyseed_v2.seed")

    assert not hasattr(package, "SatelliteNode")
    assert not hasattr(seed_integration, "SatelliteNode")


def test_root_exports_only_stable_domain_values_and_contracts() -> None:
    package = importlib.import_module("skyseed_v2")

    assert set(package.__all__) == {
        "GroundStation",
        "GroundStationDescriptor",
        "GroundStationLocation",
        "Orbit",
        "OrbitParameters",
        "Satellite",
        "SatelliteDescriptor",
        "Shell",
        "SimulationEntityCatalog",
        "SimulationNodeId",
    }
    for module_owned_name in (
        "GroundStationSpecification",
        "SimulationEntityCatalogBuilder",
        "StaticEmulatorApplyResult",
        "StaticEmulatorScenarioLoader",
        "StaticEmulatorScenarioSpecification",
        "StaticEmulatorSpecificationApplier",
    ):
        assert not hasattr(package, module_owned_name)
