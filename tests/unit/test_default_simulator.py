"""Behavioral contracts for the eager default assembly, using small inputs."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed.configuration.simulator import (
    build_global_zone_layout, load_zone_layout_definition, LatitudeLongitudeRectangleDefinition,
    ZoneDefinition, ZoneLayoutDefinition,
)
from skyseed.simulator import (
    AccumulatedDeadbandParameterPlanningModel, CircularOrbitPositionModel,
    CoverageFirstGroundStationAccessPlanningModel,
    DefaultSimulatorConfiguration, PlannerConfiguration,
    GatewayHopCountRoutePlanningModel, GeometricPhysicalLinkModel,
    HighestElevationZoneServicePlanningModel, OperationalConnectionKind,
    ParameterKindConfiguration, RetainFirstInterSatelliteConfiguration,
    RetainFirstInterSatelliteConnectionPlanningModel, Simulator, create_default_simulator,
)
from skyseed.simulator import defaults
from tests._support import drain_simulator


@pytest.fixture
def definition():
    from skyseed.adapters.emulator_to_simulator import create_simulator_static_input
    from skyseed.emulator.seedemu import get_skyseed_autonomous_systems
    from skyseed.simulator import SimulationDefinition, SimulationTimeline
    return SimulationDefinition(
        static_input=create_simulator_static_input(get_skyseed_autonomous_systems(tiny.build_emulator().getLayer("Base"))),
        timeline=SimulationTimeline(start_time=tiny.DEFAULT_START_TIME, end_time=tiny.DEFAULT_END_TIME,
                                    observation_interval=timedelta(seconds=10)))


def test_default_and_explicit_composition_publish_identical_small_plans(definition):
    zones = load_zone_layout_definition(tiny.ZONE_LAYOUT_FILE)
    configured = create_default_simulator(definition,
        configuration=DefaultSimulatorConfiguration(zone_layout_definition=zones))
    explicit = Simulator(definition)
    explicit.create_physical_world(position_model=CircularOrbitPositionModel(), zone_layout_definition=zones)
    explicit.create_planner(
        physical_link_model=GeometricPhysicalLinkModel(maximum_isl_distance_m=6_000_000,
            ground_station_minimum_elevation_deg=5, ground_station_maximum_distance_m=3_000_000,
            zone_coverage_minimum_elevation_deg=25, propagation_speed_m_per_s=299_792_458),
        inter_satellite_model=RetainFirstInterSatelliteConnectionPlanningModel(RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=2, desired_cross_plane_links=2, maximum_cross_plane_separation=1,
            same_plane_neighbor_span=1, cross_plane_neighbor_count=4,
             minimum_service_duration=timedelta(seconds=180))),
        ground_station_model=CoverageFirstGroundStationAccessPlanningModel(default_ground_station_limit=4,
            initial_candidate_count=32,  minimum_service_duration=timedelta(seconds=60)),
        zone_service_model=HighestElevationZoneServicePlanningModel(),
        parameter_model=AccumulatedDeadbandParameterPlanningModel({
            kind: ParameterKindConfiguration(bandwidth, loss, .10, timedelta(seconds=120))
            for kind, bandwidth, loss in ((OperationalConnectionKind.INTER_SATELLITE, 1_000_000_000, 100),
                (OperationalConnectionKind.GROUND_STATION_ACCESS, 500_000_000, 200))}),
        route_model=GatewayHopCountRoutePlanningModel(gateway_candidate_count=3, gateway_candidate_max_distance_m=None),
        planner_configuration=PlannerConfiguration(32, inter_satellite_acquisition_duration=timedelta(seconds=5), ground_station_acquisition_duration=timedelta(seconds=5)),
    )
    actual = drain_simulator(configured)
    expected = drain_simulator(explicit)
    assert actual[:2] == expected[:2]
    assert actual[2]["transition_count"] == 4


def test_models_are_fresh_even_when_configuration_is_reused(definition):
    config = DefaultSimulatorConfiguration(zone_layout_definition=None)
    first = create_default_simulator(definition, configuration=config)
    second = create_default_simulator(definition, configuration=config)
    assert first.get_physical_world().get_position_model() is not second.get_physical_world().get_position_model()
    assert first.get_physical_link_model() is not second.get_physical_link_model()
    for name in ("get_inter_satellite_model", "get_ground_station_model", "get_zone_service_model",
                 "get_parameter_model", "get_route_model"):
        assert getattr(first.get_planner(), name)() is not getattr(second.get_planner(), name)()
    assert first.get_planner().configuration == second.get_planner().configuration
    drain_simulator(first)
    assert second.get_planner()._physical_observation_frontier is None
    second.get_planner().shutdown()


def test_replacement_never_constructs_unused_default_or_mutates_injected_role(definition, monkeypatch):
    route = GatewayHopCountRoutePlanningModel(gateway_candidate_count=1)
    original = route._gateway_candidate_count
    def forbidden(*a, **kw):
        raise AssertionError("unused default was constructed")
    monkeypatch.setattr(defaults, "GatewayHopCountRoutePlanningModel", forbidden)
    sim = create_default_simulator(definition, configuration=DefaultSimulatorConfiguration(
        zone_layout_definition=None, gateway_candidate_count=-1), route_model=route)
    assert sim.get_planner()._route_model is route
    assert route._gateway_candidate_count == original
    sim.get_planner().shutdown()


def test_change_one_parameter_preserves_other_roles_and_low_level_defaults(definition):
    config = DefaultSimulatorConfiguration(zone_layout_definition=None)
    sim = create_default_simulator(definition, configuration=replace(config, maximum_isl_distance_m=4_000_000))
    assert sim.get_physical_link_model()._maximum_isl_distance_m == 4_000_000
    assert sim.get_planner()._ground_station_model.initial_candidate_count == 32
    assert RetainFirstInterSatelliteConfiguration().maximum_cross_plane_separation == 3
    assert ParameterKindConfiguration(1, 0).max_staleness is None
    sim.get_planner().shutdown()


def test_generated_file_manual_and_disabled_zone_choices(definition):
    generated = build_global_zone_layout()
    loaded = load_zone_layout_definition(Path('data/global_zones.yaml'))
    assert generated == loaded
    assert len(generated.zones) == 732
    manual = ZoneLayoutDefinition(zones=(ZoneDefinition(zone_id="handwritten",
        geometry=LatitudeLongitudeRectangleDefinition(0, 20, 100, 120)),), allow_overlap=False)
    for config, count in ((DefaultSimulatorConfiguration(), 732),
                          (DefaultSimulatorConfiguration(zone_layout_definition=loaded), 732),
                          (DefaultSimulatorConfiguration(zone_layout_definition=manual), len(manual.zones))):
        sim = create_default_simulator(definition, configuration=config)
        assert len(sim.get_physical_world().get_zone_layout().zones) == count
        sim.get_planner().shutdown()
    disabled = create_default_simulator(definition, configuration=DefaultSimulatorConfiguration(zone_layout_definition=None))
    with pytest.raises(RuntimeError):
        disabled.get_physical_world().get_zone_layout()
    disabled.get_planner().shutdown()
    bare = Simulator(definition)
    bare.create_physical_world(position_model=CircularOrbitPositionModel(), zone_layout_definition=None)
    with pytest.raises(RuntimeError):
        bare.get_physical_world().get_zone_layout()
