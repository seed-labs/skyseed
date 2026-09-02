"""Top-level composition root for time, physical, and planning owners."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from skyseed_v2.configuration.simulator import ZoneLayoutDefinition
from skyseed_v2.shared._execution_observation import _ExecutionObserver

from .definition import SimulationDefinition
from .planning import (
    CoordinationPolicy,
    GroundStationAccessPlanningModel,
    InterSatelliteConnectionPlanningModel,
    ParameterPlanningModel,
    Planner,
    PlanningWindowPolicy,
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
        "_definition",
        "_physical_world",
        "_physical_link_model",
        "_planner",
        "_execution_observer",
        "_execution_observation_path",
    )

    def __init__(self, definition: SimulationDefinition) -> None:
        if not isinstance(definition, SimulationDefinition):
            raise TypeError("definition must be a SimulationDefinition")
        self._definition = definition
        self._physical_world: PhysicalWorld | None = None
        self._physical_link_model: PhysicalLinkModel | None = None
        self._planner: Planner | None = None
        self._execution_observer: _ExecutionObserver | None = None
        self._execution_observation_path: Path | None = None

    def _enable_execution_observation(
        self,
        path: Path,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> _ExecutionObserver:
        """Enable private detached observations for this exact execution."""

        if self._execution_observer is not None:
            raise RuntimeError("execution observation is already enabled")
        observer = _ExecutionObserver(metadata)
        self._execution_observer = observer
        self._execution_observation_path = Path(path)
        if self._physical_world is not None:
            self._physical_world._set_execution_observer(observer)
        if self._planner is not None:
            self._planner._set_execution_observer(observer)
        return observer

    def _write_execution_observations(self) -> Path:
        """Persist observations already produced by successful work."""

        if (
            self._execution_observer is None
            or self._execution_observation_path is None
        ):
            raise RuntimeError("execution observation is not enabled")
        return self._execution_observer.write_jsonl(
            self._execution_observation_path
        )

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
        if self._physical_world is not None:
            raise RuntimeError("PhysicalWorld has already been created")
        if zone_layout_definition is None and zone_membership_model is not None:
            raise ValueError(
                "zone_membership_model requires zone_layout_definition"
            )
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
        coordination_policy: CoordinationPolicy,
        planning_window_policy: PlanningWindowPolicy | None = None,
        route_demands: Iterable[RouteDemand] = (),
    ) -> Planner:
        """Create the sole Planner and retain its physical observation Model."""

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
            coordination_policy,
        )
        if len({id(value) for value in model_roles}) != len(model_roles):
            raise ValueError("each Model/policy role requires a distinct instance")
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
            "coordination_policy": coordination_policy,
            "route_demands": route_demands,
            "execution_observer": self._execution_observer,
        }
        if planning_window_policy is not None:
            planner_arguments["planning_window_policy"] = planning_window_policy
        planner = Planner(**planner_arguments)
        self._physical_link_model = physical_link_model
        self._planner = planner
        return planner

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
