"""Provide the built-in geometric physical-link fact implementation."""

from __future__ import annotations

from threading import RLock
from time import perf_counter

from ..physical.physical_frame import PhysicalFrame
from ..physical.physical_link import (
    PhysicalLinkObservation,
    PhysicalLinkRequest,
)
from ..physical.world import PhysicalWorld
from ._geometric_physical import (
    _GeometricPhysicalCore,
    validate_geometric_physical_configuration,
)


class GeometricPhysicalLinkModel:
    """Compute deterministic sparse physical-link facts.

    Layer: built-in Simulator physical algorithm. A Network Planner or focused
    caller supplies one exact ``PhysicalFrame`` plus a bounded request and
    consumes the returned observation. It is not a Connection selector,
    lifecycle owner, planning adapter, Position source, or operational-state
    producer and is public from ``skyseed_v2.simulator.models``.
    """

    __slots__ = (
        "_maximum_isl_distance_m",
        "_ground_station_minimum_elevation_deg",
        "_ground_station_maximum_distance_m",
        "_zone_coverage_minimum_elevation_deg",
        "_propagation_speed_m_per_s",
        "_core",
        "_state_lock",
    )

    def __init__(
        self,
        *,
        maximum_isl_distance_m: float,
        ground_station_minimum_elevation_deg: float,
        ground_station_maximum_distance_m: float | None,
        zone_coverage_minimum_elevation_deg: float,
        propagation_speed_m_per_s: float,
    ) -> None:
        configuration = validate_geometric_physical_configuration(
            maximum_isl_distance_m=maximum_isl_distance_m,
            ground_station_minimum_elevation_deg=(
                ground_station_minimum_elevation_deg
            ),
            ground_station_maximum_distance_m=(
                ground_station_maximum_distance_m
            ),
            zone_coverage_minimum_elevation_deg=(
                zone_coverage_minimum_elevation_deg
            ),
            propagation_speed_m_per_s=propagation_speed_m_per_s,
        )
        self._maximum_isl_distance_m = (
            configuration.maximum_isl_distance_m
        )
        self._ground_station_minimum_elevation_deg = (
            configuration.ground_station_minimum_elevation_deg
        )
        self._ground_station_maximum_distance_m = (
            configuration.ground_station_maximum_distance_m
        )
        self._zone_coverage_minimum_elevation_deg = (
            configuration.zone_coverage_minimum_elevation_deg
        )
        self._propagation_speed_m_per_s = (
            configuration.propagation_speed_m_per_s
        )
        self._core: _GeometricPhysicalCore | None = None
        self._state_lock = RLock()

    def compute_observation(
        self,
        physical_world: PhysicalWorld,
        physical_frame: PhysicalFrame,
        request: PhysicalLinkRequest,
    ) -> PhysicalLinkObservation:
        """Evaluate only requested relationships at the frame's timestamp."""

        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(physical_frame, PhysicalFrame):
            raise TypeError("physical_frame must be a PhysicalFrame")
        if not isinstance(request, PhysicalLinkRequest):
            raise TypeError("request must be a PhysicalLinkRequest")
        observer = physical_world._get_execution_observer()
        started = perf_counter() if observer is not None else 0.0
        with self._state_lock:
            core = self._core
            if core is None:
                core = _GeometricPhysicalCore(
                    physical_world,
                    maximum_isl_distance_m=self._maximum_isl_distance_m,
                    ground_station_minimum_elevation_deg=(
                        self._ground_station_minimum_elevation_deg
                    ),
                    ground_station_maximum_distance_m=(
                        self._ground_station_maximum_distance_m
                    ),
                    zone_coverage_minimum_elevation_deg=(
                        self._zone_coverage_minimum_elevation_deg
                    ),
                    propagation_speed_m_per_s=(
                        self._propagation_speed_m_per_s
                    ),
                )
                self._core = core
            elif core.physical_world is not physical_world:
                raise ValueError(
                    "GeometricPhysicalLinkModel cannot be reused with "
                    "another PhysicalWorld"
                )
            observation = core.compute(
                physical_frame.timestamp,
                physical_frame.positions,
                request,
            )
        if observation.timestamp != physical_frame.timestamp:
            raise RuntimeError(
                "physical-link observation timestamp does not match frame"
            )
        if observer is not None:
            counts = observation.work_counts
            observer.record(
                component="physical-link",
                stage="physical-link-observation",
                phase="one-shot",
                duration_seconds=perf_counter() - started,
                scope={"timestamp": observation.timestamp.isoformat()},
                work={
                    "inter_satellite_pairs_requested": (
                        counts.inter_satellite_pairs_requested
                    ),
                    "inter_satellite_pairs_evaluated": (
                        counts.inter_satellite_pairs_evaluated
                    ),
                    "inter_satellite_pairs_feasible": (
                        counts.inter_satellite_pairs_feasible
                    ),
                    "ground_station_pairs_requested": (
                        counts.ground_station_pairs_requested
                    ),
                    "ground_station_pairs_evaluated": (
                        counts.ground_station_pairs_evaluated
                    ),
                    "ground_station_pairs_feasible": (
                        counts.ground_station_pairs_feasible
                    ),
                    "zone_service_pairs_requested": (
                        counts.zone_service_pairs_requested
                    ),
                    "zone_service_pairs_evaluated": (
                        counts.zone_service_pairs_evaluated
                    ),
                    "zone_service_pairs_feasible": (
                        counts.zone_service_pairs_feasible
                    ),
                },
                started_monotonic=started,
            )
        return observation


__all__ = ["GeometricPhysicalLinkModel"]
