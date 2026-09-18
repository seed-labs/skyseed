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
        "EmulatorAutonomousSystem",
        "get_skyseed_autonomous_systems",
    }


def test_root_exports_responsibility_modules_and_retained_entities() -> None:
    package = importlib.import_module("skyseed_v2")

    assert set(package.__all__) == {
        "emulator", "simulator", "experiment", "configuration",
        "adapters", "backend", "deployment",
        "EmulatorGroundStation",
        "GroundStationLocation",
        "EmulatorOrbit",
        "OrbitParameters",
        "EmulatorSatellite",
        "EmulatorShell",
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
        "skyseed_v2.adapters.emulator_to_simulator"
    )
    emulator = importlib.import_module("skyseed_v2.emulator")
    simulator_configuration = importlib.import_module(
        "skyseed_v2.configuration.simulator"
    )

    assert node_id.__all__ == ["NodeId"]
    assert bpf.__all__ == [
        "BpfCompiledActivationBoundary",
        "BpfCompilationResult",
        "Runner",
        "compile_bpf",
        "compiler",
        "runner",
    ]
    assert set(simulator.__all__) == {
        "physical", "planner",
        "AccumulatedDeadbandParameterPlanningModel",
        "CandidateDecision",
        "CandidateDecisionKind",
        "CircularOrbitPositionModel",
        "ConnectionParameterObservation",
        "DefaultSimulatorConfiguration",
        "create_default_simulator",
        "DefaultZoneMembershipModel",
        "EarthFixedPosition",
        "EffectiveConnectionParameters",
        "GatewayHopCountRoutePlanningModel",
        "GeometricPhysicalLinkModel",
        "PhysicalGroundStation",
        "GroundStationAccessCandidate",
        "GroundStationAccessPlanningModel",
        "GroundStationCandidateSearch",
        "GroundStationCandidateSearchModel",
        "GroundStationRouteDemandTarget",
        "HighestElevationZoneServicePlanningModel",
        "InterSatelliteConnectionPlanningModel",
        "InterSatelliteCandidateSearchModel",
        "InterSatelliteLifecycleEvidence",
        "GroundStationLifecycleEvidence",
        "InterSatelliteLinkCapability",
        "InterSatelliteLinkCandidate",
        "InterSatelliteSelection",
        "LatitudeLongitudeRectangle",
        "OperationalConnection",
        "OperationalConnectionId",
        "OperationalConnectionKind",
        "PhysicalOrbit",
        "ParameterKindConfiguration",
        "DeadbandConnectionMemory",
        "ParameterPlanningModel",
        "ParameterPlanningResult",
        "PhysicalPositionSnapshot",
        "PhysicalLinkModel",
        "PhysicalLinkQueryResult",
        "PhysicalContactWindow",
        "PhysicalLinkRequest",
        "PhysicalLinkWorkCounts",
        "PhysicalWorld",
        "Plan",
        "Planner",
        "PlanningCandidateHistogram",
        "PlanningCandidateSummary",
        "PlanningFrameEvidence",
        "PlanningFrontiers",
        "PlanningHistogramBin",
        "PlanningInspection",
        "PlannerConfiguration",
        "PlanTransition",
        "PlannedOperationalState",
        "PositionModel",
        "PublishedPlanningEvidence",
        "CoverageFirstGroundStationAccessPlanningModel",
        "ReachableRouteOutcome",
        "RetainFirstInterSatelliteConfiguration",
        "RetainFirstInterSatelliteConnectionPlanningModel",
        "RouteDemand",
        "RouteDemandTarget",
        "RouteOutcome",
        "RouteOutcomeTransition",
        "RoutePlanningModel",
        "RoutePlanningResult",
        "RouteUnreachableReason",
        "PhysicalSatellite",
        "SemanticEventKind",
        "SemanticEventPrecedence",
        "SemanticEventReference",
        "PhysicalShell",
        "SimulationDefinition",
        "SimulationTimeline",
        "Simulator",
        "PhysicalAutonomousSystem",
        "SphericalCap",
        "PlannerConfiguration",
        "UnreachableRouteOutcome",
        "Zone",
        "ZoneLayout",
        "ZoneMembershipModel",
        "ZoneRouteDemandTarget",
        "ZoneServiceAssignment",
        "ZoneServiceCandidate",
        "ZoneServicePlanningModel",
        "ZoneServicePlanningResult",
        "ZoneServiceTransition",
        "apply_plan",
        "apply_plan_transition",
        "create_plan",
        "plan_from_data",
        "plan_to_data",
        "plan_transition_from_data",
        "plan_transition_fingerprint",
        "plan_transition_to_data",
        "planned_operational_state_from_data",
        "planned_operational_state_fingerprint",
        "planned_operational_state_to_data",
        "published_planning_evidence_from_data",
        "published_planning_evidence_to_data",
        "semantic_event_references",
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
        "PhysicalWorld",
        "TopologySlice",
        "Slicer",
        "Planner",
        "Plan",
        "BpfCompilationResult",
        "Runner",
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
