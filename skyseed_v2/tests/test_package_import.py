import importlib


def test_package_can_be_imported() -> None:
    package = importlib.import_module("skyseed_v2")

    assert package.__name__ == "skyseed_v2"


def test_package_exposes_no_retired_satellite_node_alias() -> None:
    package = importlib.import_module("skyseed_v2")
    seed_integration = importlib.import_module("skyseed_v2.emulator.seedemu")

    assert not hasattr(package, "SatelliteNode")
    assert not hasattr(seed_integration, "SatelliteNode")
    assert set(seed_integration.__all__) == {
        "SkyseedAutonomousSystem",
        "get_skyseed_autonomous_systems",
    }


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
        "NodeId",
        "SimulationNodeId",
        "SimulatorStaticInput",
        "EmulatorStaticInput",
        "StaticEmulatorCreationResult",
        "load_static_emulator_config",
        "StaticEmulatorConfig",
        "create_static_emulator",
        "Zone",
        "ZoneLayout",
        "ZoneMembershipModel",
        "DefaultZoneMembershipModel",
        "LatitudeLongitudeRectangle",
    ):
        assert not hasattr(package, module_owned_name)


def test_identity_and_generation_exports_stay_with_their_owner_modules() -> None:
    bpf = importlib.import_module("skyseed_v2.backend.bpf")
    node_id = importlib.import_module("skyseed_v2.shared")
    simulator = importlib.import_module("skyseed_v2.simulator")
    simulator_static_input = importlib.import_module(
        "skyseed_v2.simulator.input"
    )
    generation = importlib.import_module(
        "skyseed_v2.emulator_to_simulator"
    )
    emulator = importlib.import_module("skyseed_v2.emulator")
    simulator_configuration = importlib.import_module(
        "skyseed_v2.configuration.simulator"
    )

    assert node_id.__all__ == ["NodeId"]
    assert bpf.__all__ == ["BpfCompiledRealization", "compile_bpf"]
    assert set(simulator.__all__) == {
        "AccumulatedDeadbandParameterPlanningModel",
        "AllAvailablePlanningHorizonPolicy",
        "CircularOrbitPositionModel",
        "Connection",
        "ConnectionDirection",
        "ConnectionId",
        "ConnectionKind",
        "ConnectionModel",
        "ConnectionPlanningModel",
        "ConnectionSlice",
        "ConnectionTransition",
        "ConnectionTransitionOperation",
        "CoordinationPolicy",
        "DefaultCoordinationPolicy",
        "DependencyAwareForwardingPlanningModel",
        "DirectionalConnectionParameters",
        "DirectionalConnectionParametersPatch",
        "DifferenceOperation",
        "EarthFixedPosition",
        "ForwardingPlanningModel",
        "ForwardingTransition",
        "ForwardingTransitionPrecedence",
        "GeometricConnectionModel",
        "GroundStation",
        "GroundStationRouteTarget",
        "HopCountRouteModel",
        "DefaultZoneMembershipModel",
        "LatitudeLongitudeRectangle",
        "MidpointConnectionPlanningModel",
        "Orbit",
        "ParameterPlanningModel",
        "ParameterTransition",
        "Plan",
        "Planner",
        "PlanningDecision",
        "PlanningHorizonPolicy",
        "PositionModel",
        "RouteKey",
        "RouteModel",
        "RoutePatch",
        "RouteSlice",
        "RouteTarget",
        "Satellite",
        "Shell",
        "SimulationDefinition",
        "SimulationTimeline",
        "Simulator",
        "Slicer",
        "SampleBoundaryConnectionPlanningModel",
        "SphericalCap",
        "SkyseedAutonomousSystem",
        "Topology",
        "TopologyDifference",
        "TopologySlice",
        "Zone",
        "ZoneLayout",
        "ZoneMembershipModel",
        "ZoneRouteTarget",
        "ZoneServiceAssignmentPatch",
    }
    assert "NodeId" not in simulator_static_input.__all__
    assert "SimulationNodeId" not in simulator_static_input.__all__
    assert set(simulator_static_input.__all__) == {
        "GroundStationSimulationInput",
        "SatelliteSimulationInput",
        "SimulatorStaticInput",
    }
    assert set(generation.__all__) == {
        "create_ground_station_simulation_input",
        "create_satellite_simulation_input",
        "create_simulator_static_input",
    }
    assert set(emulator.__all__) == {
        "create_static_emulator",
        "entities",
        "seedemu",
    }
    assert "ZoneLayoutDefinitionLoadError" in simulator_configuration.__all__
    assert not hasattr(generation, "create_emulator_node_index")
    assert not hasattr(simulator_static_input, "EmulatorStaticInput")
    assert not hasattr(generation, "create_emulator_static_input")

    package = importlib.import_module("skyseed_v2")
    for simulator_owned_name in (
        "SimulationDefinition",
        "SimulationTimeline",
        "Simulator",
        "SkyseedAutonomousSystem",
        "Topology",
        "TopologySlice",
        "Slicer",
        "Planner",
        "Plan",
        "BpfCompiledRealization",
        "compile_bpf",
    ):
        assert not hasattr(package, simulator_owned_name)


def test_configuration_exports_are_split_by_construction_owner() -> None:
    configuration = importlib.import_module("skyseed_v2.configuration")
    emulator_configuration = importlib.import_module(
        "skyseed_v2.configuration.emulator"
    )
    simulator_configuration = importlib.import_module(
        "skyseed_v2.configuration.simulator"
    )

    expected_emulator_names = {
        "EbgpPeeringConfig",
        "EbgpRouterReference",
        "GeneratedOrbitLayout",
        "GroundStationConfig",
        "InOrbitPhaseDistribution",
        "InternetExchangeAttachment",
        "InternetExchangeConfig",
        "LocalNetworkAttachment",
        "NetworkConfig",
        "OrbitConfig",
        "OrdinaryAutonomousSystemConfig",
        "RightAscensionDistribution",
        "RouterConfig",
        "SatelliteConfig",
        "ShellConfig",
        "SkyseedAutonomousSystemConfig",
        "StaticEmulatorConfig",
    }
    expected_simulator_names = {
        "LatitudeLongitudeRectangleDefinition",
        "ZoneDefinition",
        "ZoneLayoutDefinition",
        "load_zone_layout_definition",
    }
    assert configuration.__all__ == ["emulator", "simulator"]
    assert expected_emulator_names <= set(emulator_configuration.__all__)
    assert expected_simulator_names <= set(simulator_configuration.__all__)
    assert not expected_emulator_names & set(simulator_configuration.__all__)
    assert not expected_simulator_names & set(emulator_configuration.__all__)
    for retired_name in (
        "EbgpRouterReferenceConfig",
        "GroundStationSpecification",
        "InOrbitPhaseDistributionConfig",
        "InternetExchangeAttachmentConfig",
        "LocalNetworkAttachmentConfig",
        "OrbitGenerationConfig",
        "OrbitSpecification",
        "RaanDistributionConfig",
        "SatelliteSpecification",
        "ShellSpecification",
        "StaticEmulatorScenarioSpecification",
        "StaticEmulatorScenarioLoader",
    ):
        assert not hasattr(emulator_configuration, retired_name)
