"""Top-level Simulator composition root and explicit Topology lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from skyseed_v2.configuration.simulator import ZoneLayoutDefinition
from skyseed_v2.shared._execution_observation import _ExecutionObserver

from .definition import SimulationDefinition
from .planning import (
    ConnectionPlanningModel,
    CoordinationPolicy,
    ForwardingPlanningModel,
    ParameterPlanningModel,
    Planner,
    PlanningWindowPolicy,
)
from .slicing import ConnectionModel, RouteModel, Slicer
from .timeline import SimulationTimeline
from .topology import PositionModel, Topology, ZoneMembershipModel


class Simulator:
    """Own one definition and coordinate explicit subsystem construction.

    Layer: top-level Simulator composition root and facade. Users create it from
    one ``SimulationDefinition`` and explicitly request Topology construction.
    It is not an Emulator wrapper, planner, compiler, runner or runtime-resource
    locator. It is public from ``skyseed_v2.simulator`` as the canonical Simu
    entry point.
    """

    __slots__ = (
        "_definition",
        "_topology",
        "_slicer",
        "_planner",
        "_execution_observer",
        "_execution_observation_path",
    )

    def __init__(self, definition: SimulationDefinition) -> None:
        if not isinstance(definition, SimulationDefinition):
            raise TypeError("definition must be a SimulationDefinition")
        self._definition = definition
        self._topology: Topology | None = None
        self._slicer: Slicer | None = None
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
        if self._topology is not None:
            self._topology._set_execution_observer(observer)
        if self._slicer is not None:
            self._slicer._set_execution_observer(observer)
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

    def create_topology(
        self,
        *,
        position_model: PositionModel,
        zone_layout_definition: ZoneLayoutDefinition | None = None,
        zone_membership_model: ZoneMembershipModel | None = None,
    ) -> Topology:
        if self._topology is not None:
            raise RuntimeError("Topology has already been created")
        if zone_layout_definition is None and zone_membership_model is not None:
            raise ValueError(
                "zone_membership_model requires zone_layout_definition"
            )
        topology = Topology._materialize(
            self._definition.static_input,
            position_model=position_model,
            zone_layout_definition=zone_layout_definition,
            zone_membership_model=zone_membership_model,
            execution_observer=self._execution_observer,
        )
        self._topology = topology
        return topology

    def get_topology(self) -> Topology:
        if self._topology is None:
            raise RuntimeError("Topology has not been created")
        return self._topology

    def create_slicer(
        self,
        *,
        connection_model: ConnectionModel,
        route_model: RouteModel,
    ) -> Slicer:
        if self._slicer is not None:
            raise RuntimeError("Slicer has already been created")
        topology = self.get_topology()
        if len(
            {
                id(topology.get_position_model()),
                id(connection_model),
                id(route_model),
            }
        ) != 3:
            raise ValueError("each Model role requires a distinct instance")
        slicer = Slicer._create(
            topology=topology,
            timeline=self.get_timeline(),
            connection_model=connection_model,
            route_model=route_model,
            execution_observer=self._execution_observer,
        )
        self._slicer = slicer
        return slicer

    def get_slicer(self) -> Slicer:
        if self._slicer is None:
            raise RuntimeError("Slicer has not been created")
        return self._slicer

    def create_planner(
        self,
        *,
        connection_model: ConnectionPlanningModel,
        parameter_model: ParameterPlanningModel,
        forwarding_model: ForwardingPlanningModel,
        coordination_policy: CoordinationPolicy,
        planning_window_policy: PlanningWindowPolicy | None = None,
    ) -> Planner:
        if self._planner is not None:
            raise RuntimeError("Planner has already been created")
        slicer = self.get_slicer()
        planner = Planner._create(
            slicer=slicer,
            planning_window_policy=planning_window_policy,
            connection_model=connection_model,
            parameter_model=parameter_model,
            forwarding_model=forwarding_model,
            coordination_policy=coordination_policy,
            execution_observer=self._execution_observer,
        )
        self._planner = planner
        return planner

    def get_planner(self) -> Planner:
        if self._planner is None:
            raise RuntimeError("Planner has not been created")
        return self._planner


__all__ = ["Simulator"]
