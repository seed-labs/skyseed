import importlib


def test_package_can_be_imported() -> None:
    package = importlib.import_module("skyseed_v2")

    assert package.__name__ == "skyseed_v2"


def test_package_exposes_no_retired_satellite_node_alias() -> None:
    package = importlib.import_module("skyseed_v2")
    seed_integration = importlib.import_module("skyseed_v2.seedemu_integration")

    assert not hasattr(package, "SatelliteNode")
    assert not hasattr(seed_integration, "SatelliteNode")


def test_root_exports_only_high_frequency_entities_and_values() -> None:
    package = importlib.import_module("skyseed_v2")

    assert set(package.__all__) == {
        "GroundStation",
        "GroundStationLocation",
        "Orbit",
        "OrbitParameters",
        "Satellite",
        "Shell",
    }
    for module_owned_name in (
        "GroundStationConfig",
        "GroundStationSimulationInput",
        "SatelliteSimulationInput",
        "SimulationNodeId",
        "SimulatorStaticInput",
        "StaticEmulatorCreationResult",
        "load_static_emulator_config",
        "StaticEmulatorConfig",
        "create_static_emulator",
    ):
        assert not hasattr(package, module_owned_name)


def test_configuration_exports_only_config_names_without_old_type_aliases() -> None:
    configuration = importlib.import_module("skyseed_v2.configuration")

    expected_config_names = {
        "EbgpPeeringConfig",
        "EbgpRouterReferenceConfig",
        "GroundStationConfig",
        "InOrbitPhaseDistributionConfig",
        "InternetExchangeAttachmentConfig",
        "InternetExchangeConfig",
        "LocalNetworkAttachmentConfig",
        "NetworkConfig",
        "OrbitConfig",
        "OrbitGenerationConfig",
        "OrdinaryAutonomousSystemConfig",
        "RaanDistributionConfig",
        "RouterConfig",
        "SatelliteConfig",
        "ShellConfig",
        "SkyseedAutonomousSystemConfig",
        "StaticEmulatorConfig",
    }
    assert expected_config_names <= set(configuration.__all__)
    for retired_name in (
        "GroundStationSpecification",
        "OrbitSpecification",
        "SatelliteSpecification",
        "ShellSpecification",
        "StaticEmulatorScenarioSpecification",
        "StaticEmulatorScenarioLoader",
    ):
        assert not hasattr(configuration, retired_name)
