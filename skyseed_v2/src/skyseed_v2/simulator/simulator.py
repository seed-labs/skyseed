"""Top-level composition root for time, physical, and planning owners."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from skyseed_v2.configuration.simulator import ZoneLayoutDefinition
from skyseed_v2.shared._shutdown import _shutdown_owned
from skyseed_v2.shared.recording import PerformanceRecorder, record_performance

from .definition import SimulationDefinition
from .planner import (
    GroundStationAccessPlanningModel,
    InterSatelliteConnectionPlanningModel,
    ParameterPlanningModel,
    Planner,
    PlannerConfiguration,
    RouteDemand,
    RoutePlanningModel,
    ZoneServicePlanningModel,
)
from .timeline import SimulationTimeline
from .physical import (
    PhysicalLinkModel,
    PositionModel,
    PhysicalWorld,
    ZoneMembershipModel,
)


class Simulator:
    """Own one definition and coordinate explicit subsystem construction.

    Layer: top-level Simulator composition root and facade. Users create it from
    one ``SimulationDefinition`` and explicitly request PhysicalWorld construction.
    It is not an Emulator wrapper, planner, compiler, runner or runtime-resource
    locator. It is public from ``skyseed_v2.simulator`` as the canonical Simu
    entry point.
    """

    __slots__ = (
        "_shutdown",
        "_definition",
        "_physical_world",
        "_physical_link_model",
        "_planner",
        "_execution_observer",
    )

    def __init__(self, definition: SimulationDefinition) -> None:
        if not isinstance(definition, SimulationDefinition):
            raise TypeError("definition must be a SimulationDefinition")
        self._shutdown = False
        self._definition = definition
        self._physical_world: PhysicalWorld | None = None
        self._physical_link_model: PhysicalLinkModel | None = None
        self._planner: Planner | None = None
        self._execution_observer: PerformanceRecorder | None = None

    def _set_execution_observer(self, observer: PerformanceRecorder | None) -> None:
        """Bind an optional fact receiver without owning its output or lifetime."""
        self._execution_observer = observer
        if self._physical_world is not None:
            self._physical_world._set_execution_observer(observer)
        if self._planner is not None:
            self._planner._set_execution_observer(observer)

    def get_definition(self) -> SimulationDefinition:
        return self._definition

    def get_timeline(self) -> SimulationTimeline:
        return self._definition.timeline

    def create_physical_world(
        self,
        *,
        position_model: PositionModel,
        zone_layout_definition: ZoneLayoutDefinition | None = None,
        zone_membership_model: ZoneMembershipModel | None = None,
    ) -> PhysicalWorld:
        if self._shutdown:
            raise RuntimeError("Simulator is shut down")
        if self._physical_world is not None:
            raise RuntimeError("PhysicalWorld has already been created")
        if zone_layout_definition is None and zone_membership_model is not None:
            raise ValueError(
                "zone_membership_model requires zone_layout_definition"
            )
        if position_model is zone_membership_model:
            raise ValueError("each Model role requires a distinct instance")
        physical_world = PhysicalWorld._materialize(
            self._definition.static_input,
            position_model=position_model,
            zone_layout_definition=zone_layout_definition,
            zone_membership_model=zone_membership_model,
            execution_observer=self._execution_observer,
        )
        self._physical_world = physical_world
        return physical_world

    def get_physical_world(self) -> PhysicalWorld:
        if self._physical_world is None:
            raise RuntimeError("PhysicalWorld has not been created")
        return self._physical_world

    def create_planner(
        self,
        *,
        physical_link_model: PhysicalLinkModel,
        inter_satellite_model: InterSatelliteConnectionPlanningModel,
        ground_station_model: GroundStationAccessPlanningModel,
        zone_service_model: ZoneServicePlanningModel,
        parameter_model: ParameterPlanningModel,
        route_model: RoutePlanningModel,
        planner_configuration: PlannerConfiguration | None = None,
        route_demands: Iterable[RouteDemand] | None = None,
    ) -> Planner:
        """Create the sole Planner and retain its physical observation Model."""

        if self._shutdown:
            raise RuntimeError("Simulator is shut down")
        if self._planner is not None:
            raise RuntimeError("Planner has already been created")
        physical_world = self.get_physical_world()
        if not isinstance(physical_link_model, PhysicalLinkModel):
            raise TypeError(
                "physical_link_model must implement PhysicalLinkModel"
            )
        model_roles = (
            physical_world.get_position_model(),
            physical_link_model,
            inter_satellite_model,
            ground_station_model,
            zone_service_model,
            parameter_model,
            route_model,
        )
        try:
            membership = physical_world.get_zone_layout().get_membership_model()
        except RuntimeError:
            membership = None
        if membership is not None:
            model_roles += (membership,)
        if len({id(value) for value in model_roles}) != len(model_roles):
            raise ValueError("each Model role requires a distinct instance")
        planner_arguments: dict[str, object] = {
            "physical_world": physical_world,
            "observation_interval": (
                self._definition.timeline.observation_interval
            ),
            "inter_satellite_model": inter_satellite_model,
            "ground_station_model": ground_station_model,
            "zone_service_model": zone_service_model,
            "parameter_model": parameter_model,
            "route_model": route_model,
            "route_demands": route_demands,
            "execution_observer": self._execution_observer,
        }
        if planner_configuration is not None:
            planner_arguments["configuration"] = planner_configuration
        planner = Planner(**planner_arguments)
        self._physical_link_model = physical_link_model
        self._planner = planner
        return planner

    def shutdown(self, *, wait: bool = True) -> None:
        """Close optional resources of the components assembled for this run."""
        if not isinstance(wait, bool):
            raise TypeError("wait must be a bool")
        if self._shutdown:
            return
        self._shutdown = True
        _shutdown_owned((self._planner, self._physical_link_model, self._physical_world), wait=wait)

    def get_physical_link_model(self) -> PhysicalLinkModel:
        """Return the exact Model injected with the sole Planner."""

        if self._physical_link_model is None:
            raise RuntimeError("Planner has not been created")
        return self._physical_link_model

    def get_planner(self) -> Planner:
        if self._planner is None:
            raise RuntimeError("Planner has not been created")
        return self._planner


__all__ = ["Simulator"]
