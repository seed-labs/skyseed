"""Eager assembly of the existing built-in Simulator composition."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType

from skyseed.configuration.forwarding import ForwardingBackend, _backend
from skyseed.configuration.simulator import ZoneLayoutDefinition, build_global_zone_layout
from skyseed.shared import NodeId
from skyseed.shared._shutdown import _shutdown_owned

from .definition import SimulationDefinition
from skyseed.simulator.physical.models import CircularOrbitPositionModel, GeometricPhysicalLinkModel
from .physical import PhysicalLinkModel, PositionModel, ZoneMembershipModel
from .planner import (
    AccumulatedDeadbandParameterPlanningModel,
    CoverageFirstGroundStationAccessPlanningModel,
    GatewayHopCountRoutePlanningModel, GroundStationAccessPlanningModel,
    HighestElevationZoneServicePlanningModel, InterSatelliteConnectionPlanningModel,
    OperationalConnectionKind, ParameterKindConfiguration, ParameterPlanningModel,
    PlannerConfiguration, RetainFirstInterSatelliteConfiguration,
    RetainFirstInterSatelliteConnectionPlanningModel, RouteDemand,
    RoutePlanningModel, RoutePlanningResult, ZoneServicePlanningModel,
)
from .simulator import Simulator


class _EmptyRoutes:
    """The default Linux assembly leaves business routes to installed software."""

    def plan_route_outcomes(self, physical_world, operational_connections,
                            zone_service_assignments, demands, previous_outcomes, *, timestamp):
        return RoutePlanningResult()

    def can_reuse_unchanged_inputs(self):
        return True


def _parameters():
    return {
        kind: ParameterKindConfiguration(
            bandwidth_bps=bandwidth, packet_loss_ppm=loss,
            delay_relative_threshold=0.10, max_staleness=timedelta(seconds=120),
        )
        for kind, bandwidth, loss in (
            (OperationalConnectionKind.INTER_SATELLITE, 1_000_000_000, 100),
            (OperationalConnectionKind.GROUND_STATION_ACCESS, 500_000_000, 200),
        )
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class DefaultSimulatorConfiguration:
    """Parameters for the reference Starlink built-in Model composition.

    Layer: Simulator assembly configuration. It is public so ordinary built-in
    parameter changes do not require copying the composition.
    Callers supply this value to ``create_default_simulator``. It is not a
    serialized format or an owner. Omitted Zones use the deterministic 732-Zone
    grid; ``zone_layout_definition=None`` explicitly disables Zones. Loaded and
    handwritten definitions use the same field. Model constructors validate their
    own parameters, so replacing a role does not construct or validate its default.
    """

    zone_layout_definition: ZoneLayoutDefinition | None = field(default_factory=build_global_zone_layout)
    maximum_isl_distance_m: float = 6_000_000.0
    ground_station_minimum_elevation_deg: float = 5.0
    ground_station_maximum_distance_m: float | None = 3_000_000.0
    zone_coverage_minimum_elevation_deg: float = 25.0
    propagation_speed_m_per_s: float = 299_792_458.0
    inter_satellite_configuration: RetainFirstInterSatelliteConfiguration = field(
        default_factory=lambda: RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=2, desired_cross_plane_links=2,
            maximum_cross_plane_separation=1, same_plane_neighbor_span=1,
            cross_plane_neighbor_count=4,
            minimum_service_duration=timedelta(seconds=180),
        )
    )
    default_ground_station_limit: int = 4
    ground_station_policy_limit: int | None = None
    initial_candidate_count: int | None = 32
    ground_station_minimum_service_duration: timedelta = timedelta(seconds=60)
    satellite_scopes: Mapping[NodeId, str] | None = None
    ground_station_scope_limits: Mapping[str, int] | None = None
    parameter_configuration: Mapping[OperationalConnectionKind, ParameterKindConfiguration] = field(default_factory=_parameters)
    gateway_candidate_count: int = 3
    gateway_candidate_max_distance_m: float | None = None

    def __post_init__(self):
        # Detach caller-owned mappings, while keeping the existing immutable values.
        for name in ("satellite_scopes", "ground_station_scope_limits", "parameter_configuration"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))


def create_default_simulator(
    definition: SimulationDefinition,
    *,
    forwarding_backend: ForwardingBackend | str = "bpf",
    configuration: DefaultSimulatorConfiguration | None = None,
    position_model: PositionModel | None = None,
    zone_membership_model: ZoneMembershipModel | None = None,
    physical_link_model: PhysicalLinkModel | None = None,
    inter_satellite_model: InterSatelliteConnectionPlanningModel | None = None,
    ground_station_model: GroundStationAccessPlanningModel | None = None,
    zone_service_model: ZoneServicePlanningModel | None = None,
    parameter_model: ParameterPlanningModel | None = None,
    route_model: RoutePlanningModel | None = None,
    planner_configuration: PlannerConfiguration | None = None,
    route_demands: Iterable[RouteDemand] | None = None,
) -> Simulator:
    """Return an actual Simulator, with its PhysicalWorld then Planner created.

    Each omitted role gets a fresh configured built-in instance. Each supplied
    object is handed directly to the existing construction API without mutation.
    With no route_model, BPF uses gateway routes and Linux uses empty routes.
    The selection affects assembly only; it is not retained by the Simulator.
    Emulator adaptation and finite Timeline selection remain caller responsibilities.
    """
    simulator = None
    # Until each owner is constructed, this assembly holds the supplied instances.
    pending = dict(position=position_model, membership=zone_membership_model,
        physical=physical_link_model, isl=inter_satellite_model, ground=ground_station_model,
        zone=zone_service_model, parameters=parameter_model, route=route_model)
    try:
        backend = _backend(forwarding_backend)
        simulator = Simulator(definition)
        c = DefaultSimulatorConfiguration() if configuration is None else configuration
        if not isinstance(c, DefaultSimulatorConfiguration):
            raise TypeError("configuration must be a DefaultSimulatorConfiguration")
        if pending["position"] is None:
            pending["position"] = CircularOrbitPositionModel()
        simulator.create_physical_world(position_model=pending["position"],
            zone_layout_definition=c.zone_layout_definition,
            zone_membership_model=pending["membership"])
        del pending["position"], pending["membership"]
        if pending["physical"] is None:
            pending["physical"] = GeometricPhysicalLinkModel(
                maximum_isl_distance_m=c.maximum_isl_distance_m,
                ground_station_minimum_elevation_deg=c.ground_station_minimum_elevation_deg,
                ground_station_maximum_distance_m=c.ground_station_maximum_distance_m,
                zone_coverage_minimum_elevation_deg=c.zone_coverage_minimum_elevation_deg,
                propagation_speed_m_per_s=c.propagation_speed_m_per_s)
        if pending["isl"] is None:
            pending["isl"] = RetainFirstInterSatelliteConnectionPlanningModel(c.inter_satellite_configuration)
        if pending["ground"] is None:
            pending["ground"] = CoverageFirstGroundStationAccessPlanningModel(
                default_ground_station_limit=c.default_ground_station_limit,
                ground_station_policy_limit=c.ground_station_policy_limit,
                initial_candidate_count=c.initial_candidate_count,
                minimum_service_duration=c.ground_station_minimum_service_duration,
                satellite_scopes=c.satellite_scopes,
                ground_station_scope_limits=c.ground_station_scope_limits)
        if pending["zone"] is None:
            pending["zone"] = HighestElevationZoneServicePlanningModel(satellite_scopes=c.satellite_scopes)
        if pending["parameters"] is None:
            pending["parameters"] = AccumulatedDeadbandParameterPlanningModel(c.parameter_configuration)
        if pending["route"] is None:
            pending["route"] = _EmptyRoutes() if backend == ForwardingBackend.LINUX else GatewayHopCountRoutePlanningModel(
                satellite_scopes=c.satellite_scopes,
                gateway_candidate_count=c.gateway_candidate_count,
                gateway_candidate_max_distance_m=c.gateway_candidate_max_distance_m)
        simulator.create_planner(physical_link_model=pending["physical"],
            inter_satellite_model=pending["isl"], ground_station_model=pending["ground"],
            zone_service_model=pending["zone"], parameter_model=pending["parameters"],
            route_model=pending["route"],
            planner_configuration=planner_configuration, route_demands=route_demands)
    except BaseException as error:
        try:
            _shutdown_owned((*pending.values(), simulator))
        except BaseException as cleanup_error:
            error.add_note(f"assembly shutdown also failed: {type(cleanup_error).__name__}: {cleanup_error}")
            for note in getattr(cleanup_error, "__notes__", ()):
                error.add_note(note)
        raise
    return simulator


__all__ = ["DefaultSimulatorConfiguration", "create_default_simulator"]
