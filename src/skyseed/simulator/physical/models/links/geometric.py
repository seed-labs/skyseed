"""Provide the built-in geometric physical-link fact implementation."""

from __future__ import annotations

from skyseed.shared.recording import record_performance

from datetime import datetime, timedelta
from threading import RLock
from time import perf_counter

from skyseed.shared.time import normalize_utc_datetime

from skyseed.simulator.physical.queries.position import PhysicalPositionSnapshot
from skyseed.simulator.physical.queries.physical_link import (
    PhysicalLinkQueryResult,
    PhysicalLinkRequest,
)
from skyseed.simulator.physical.world.world import PhysicalWorld
from skyseed.simulator.physical.models.links._geometry import (
    _GeometricPhysicalCore,
    validate_geometric_physical_configuration,
)


class GeometricPhysicalLinkModel:
    """Compute deterministic sparse physical-link facts.

    Layer: built-in Simulator physical algorithm. A Network Planner or focused
    caller supplies one exact ``PhysicalPositionSnapshot`` plus a bounded request and
    consumes the returned observation. It is not a Connection selector,
    lifecycle owner, planning adapter, Position source, or operational-state
    producer and is public from ``skyseed.simulator.physical.models``.
    """

    __slots__ = (
        "_maximum_isl_distance_m",
        "_ground_station_minimum_elevation_deg",
        "_ground_station_maximum_distance_m",
        "_zone_coverage_minimum_elevation_deg",
        "_propagation_speed_m_per_s",
        "_core",
        "_closed",
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
        self._closed = False

    def supports_ground_station_candidate_limits(self) -> bool:
        return True

    def shutdown(self, *, wait: bool = True) -> None:
        with self._state_lock:
            self._closed = True
            self._core = None

    def describe_configuration(self):
        """Describe fixed input parameters without accessing derived geometry."""
        return {
            "maximum_isl_distance_m": self._maximum_isl_distance_m,
            "ground_station_minimum_elevation_deg": self._ground_station_minimum_elevation_deg,
            "ground_station_maximum_distance_m": self._ground_station_maximum_distance_m,
            "zone_coverage_minimum_elevation_deg": self._zone_coverage_minimum_elevation_deg,
            "propagation_speed_m_per_s": self._propagation_speed_m_per_s,
        }

    def predict_contacts(
        self, physical_world: PhysicalWorld, request: PhysicalLinkRequest, *,
        start_timestamp: datetime, end_timestamp: datetime,
        observation_interval: timedelta, inter_satellite_tail: timedelta,
        ground_station_tail: timedelta,
    ) -> dict[str, int]:
        """Prepare compact physical proofs over the finite admission interval.

        Producer supplies dependency-derived bounds. The physical Model owns
        the contact index; subsequent observations carry immutable windows.
        """
        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(request, PhysicalLinkRequest):
            raise TypeError("request must be a PhysicalLinkRequest")
        start_timestamp = normalize_utc_datetime(start_timestamp, "start_timestamp")
        end_timestamp = normalize_utc_datetime(end_timestamp, "end_timestamp")
        if end_timestamp < start_timestamp:
            raise ValueError("prediction end must not precede its start")
        if not isinstance(observation_interval, timedelta) or observation_interval <= timedelta(0):
            raise ValueError("prediction observation_interval must be positive")
        for duration in (inter_satellite_tail, ground_station_tail):
            if not isinstance(duration, timedelta) or duration < timedelta(0):
                raise ValueError("prediction tails must be non-negative durations")
        from skyseed.simulator.physical.models.links._contact_prediction import _ContactPrediction
        # Initialize the same core without requesting future semantic work.
        self._ensure_core(physical_world)
        with self._state_lock:
            previous = self._core._prediction
            if previous is not None and (
                previous.request != request or previous.interval != observation_interval
                or start_timestamp < previous.start or end_timestamp <= previous.end
                or end_timestamp + inter_satellite_tail < previous.coverage["isl"]
                or end_timestamp + ground_station_tail < previous.coverage["ground"]
            ):
                # Earlier/repeated inputs begin a new proof range. The immutable
                # old proof remains valid for existing observations; geometry
                # resources stay owned by this same core until shutdown.
                previous = None
            prediction = _ContactPrediction(
                self._core, request, start_timestamp=start_timestamp,
                end_timestamp=end_timestamp, observation_interval=observation_interval,
                inter_satellite_tail=inter_satellite_tail,
                ground_station_tail=ground_station_tail,
                continuation=previous,
            )
            self._core._prediction = prediction
        return dict(prediction.counts)

    def _ensure_core(self, physical_world):
        with self._state_lock:
            if self._closed:
                raise RuntimeError("PhysicalLink Model is shut down")
            if self._core is None:
                self._core = _GeometricPhysicalCore(
                    physical_world,
                    maximum_isl_distance_m=self._maximum_isl_distance_m,
                    ground_station_minimum_elevation_deg=self._ground_station_minimum_elevation_deg,
                    ground_station_maximum_distance_m=self._ground_station_maximum_distance_m,
                    zone_coverage_minimum_elevation_deg=self._zone_coverage_minimum_elevation_deg,
                    propagation_speed_m_per_s=self._propagation_speed_m_per_s,
                )
            elif self._core.physical_world is not physical_world:
                raise ValueError("GeometricPhysicalLinkModel cannot be reused with another PhysicalWorld")
        return self._core

    def compute_observation(
        self,
        physical_world: PhysicalWorld,
        physical_frame: PhysicalPositionSnapshot,
        request: PhysicalLinkRequest,
    ) -> PhysicalLinkQueryResult:
        """Evaluate only requested relationships at the frame's timestamp."""

        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        if not isinstance(physical_frame, PhysicalPositionSnapshot):
            raise TypeError("physical_frame must be a PhysicalPositionSnapshot")
        if not isinstance(request, PhysicalLinkRequest):
            raise TypeError("request must be a PhysicalLinkRequest")
        observer = physical_world._get_execution_observer()
        started = perf_counter() if observer is not None else 0.0
        core = self._ensure_core(physical_world)
        with self._state_lock:
            observation = core.compute(
                physical_frame.timestamp, physical_frame.positions, request,
            )
        if observation.timestamp != physical_frame.timestamp:
            raise RuntimeError(
                "physical-link observation timestamp does not match frame"
            )
        if observer is not None:
            counts = observation.work_counts
            record_performance(observer,
                component="physical-link",
                stage="physical-link-observation",
                phase="one-shot",
                duration_seconds=perf_counter() - started,
                scope={"timestamp": observation.timestamp.isoformat()},
                work={
                    **core._query_work,
                    "inter_satellite_pairs_requested": (
                        counts.inter_satellite_pairs_requested
                    ),
                    "inter_satellite_pairs_evaluated": (
                        counts.inter_satellite_pairs_evaluated
                    ),
                    "inter_satellite_pairs_feasible": (
                        counts.inter_satellite_pairs_feasible
                    ),
                    "ground_station_pairs_not_returned": counts.ground_station_pairs_not_returned,
                    "inter_satellite_pairs_not_returned": counts.inter_satellite_pairs_not_returned,
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
