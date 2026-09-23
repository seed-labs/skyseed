"""Directional parameter contract and the symmetric deadband algorithm."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable
from collections.abc import Mapping
from datetime import datetime
from fractions import Fraction
from math import isfinite
from types import MappingProxyType
from skyseed.shared.time import normalize_utc_datetime
from ..physical import InterSatelliteLinkCandidate, GroundStationAccessCandidate
from .values import (OperationalConnection, OperationalConnectionId,
    OperationalConnectionKind, EffectiveConnectionParameters, _connection_key)


@dataclass(frozen=True, slots=True)
class ConnectionParameterObservation:
    """Describe optional physical inputs for one operational Connection.

    Layer: public Simulator parameter-planning input value. It is public
    because replaceable Parameter Models receive a stable observation contract
    from ``Planner``. It is not an effective-parameter decision, raw physical
    candidate, or backend qdisc command.
    """

    connection_id: OperationalConnectionId
    physical_delay_us: int | None = None
    physical_candidate: InterSatelliteLinkCandidate | GroundStationAccessCandidate | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        if self.physical_delay_us is not None and (
            isinstance(self.physical_delay_us, bool)
            or not isinstance(self.physical_delay_us, int)
        ):
            raise ValueError("physical_delay_us must be an integer or None")


@dataclass(frozen=True, slots=True)
class DeadbandConnectionMemory:
    """Carry one symmetric deadband baseline and refresh time.

    Layer: public Simulator parameter-planning Model value. It is public
    because the supplied deadband algorithm exposes its immutable baseline
    for inspection. Other Parameter Models define their own memory. It is not authoritative Planner state, a Plan
    payload, or realized backend state.
    """

    connection_id: OperationalConnectionId
    effective: EffectiveConnectionParameters
    last_update: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, OperationalConnectionId):
            raise TypeError("connection_id must be an OperationalConnectionId")
        if not isinstance(self.effective, EffectiveConnectionParameters):
            raise TypeError("effective must be EffectiveConnectionParameters")
        object.__setattr__(
            self,
            "last_update",
            normalize_utc_datetime(self.last_update, "last_update"),
        )


@dataclass(frozen=True, slots=True)
class ParameterPlanningResult:
    """Return complete directional Connections and opaque Model memory.

    Layer: public Simulator planning-Model boundary value. It is public because
    replaceable Parameter Models need one replay-complete result contract. It
    is not a complete PlannedOperationalState, a physical observation, or a
    compiled realization.
    """

    connections: tuple[OperationalConnection, ...]
    memory: object = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "connections", tuple(self.connections))


@runtime_checkable
class ParameterPlanningModel(Protocol):
    """Define replaceable effective-Connection-parameter planning.

    Layer: public Simulator planning Model contract. It is public because
    callers inject parameter policy independently and ``Planner`` validates its
    stable result values. It is not a physical-link calculator, semantic state
    owner, or backend traffic-control compiler.
    """

    def plan_effective_parameters(
        self,
        timestamp: datetime,
        observations: tuple[ConnectionParameterObservation, ...],
        previous_memory: object,
    ) -> ParameterPlanningResult:
        """Return complete connections and immutable, bounded Model-defined memory.

        Planner initially supplies None, then passes the last successful result's
        memory unchanged. Models must not mutate previously supplied memory.
        Stateless Models may always return None. Memory is not Plan content.
        """


@dataclass(frozen=True, slots=True)
class ParameterKindConfiguration:
    """Configure effective parameters and delay suppression for one kind.

    Layer: public Simulator parameter-planning configuration value. It is
    public because callers configure the supplied Parameter Model by
    Connection kind. It is not an observed physical delay, effective
    Connection instance, or backend qdisc instruction.
    """

    bandwidth_bps: int
    packet_loss_ppm: int
    delay_relative_threshold: float = 0.0
    max_staleness: timedelta | None = None

    def __post_init__(self) -> None:
        EffectiveConnectionParameters(
            self.bandwidth_bps, 0, self.packet_loss_ppm
        )
        if (
            isinstance(self.delay_relative_threshold, bool)
            or not isinstance(self.delay_relative_threshold, (int, float))
            or not isfinite(self.delay_relative_threshold)
            or self.delay_relative_threshold < 0
        ):
            raise ValueError("delay_relative_threshold must be finite and non-negative")
        if self.max_staleness is not None and (
            not isinstance(self.max_staleness, timedelta)
            or self.max_staleness <= timedelta(0)
        ):
            raise ValueError("max_staleness must be positive or None")


class AccumulatedDeadbandParameterPlanningModel:
    """Carry physical delay into effective state with bounded suppression.

    Layer: public Simulator planning Model implementation. It is public because
    composition roots need a configurable implementation of the Parameter
    Model contract. It is not the owner of operational state, a physical-link
    Model, or a backend qdisc compiler.
    """

    __slots__ = ("_configuration", "_thresholds")

    def __init__(
        self,
        configuration: Mapping[
            OperationalConnectionKind, ParameterKindConfiguration
        ],
    ) -> None:
        if set(configuration) != set(OperationalConnectionKind):
            raise ValueError(
                "configuration must contain every OperationalConnectionKind"
            )
        copied = {}
        for kind, value in configuration.items():
            if not isinstance(kind, OperationalConnectionKind):
                raise TypeError("configuration keys must be Connection kinds")
            if not isinstance(value, ParameterKindConfiguration):
                raise TypeError(
                    "configuration values must be ParameterKindConfiguration"
                )
            copied[kind] = value
        self._configuration = MappingProxyType(copied)
        self._thresholds = {kind: Fraction(str(value.delay_relative_threshold)) for kind, value in copied.items()}

    def describe_configuration(self):
        """Return only the configured per-kind algorithm parameters."""
        return self._configuration

    def plan_effective_parameters(
        self,
        timestamp: datetime,
        observations: tuple[ConnectionParameterObservation, ...],
        previous_memory: tuple[DeadbandConnectionMemory, ...] | None,
    ) -> ParameterPlanningResult:
        utc = normalize_utc_datetime(timestamp, "timestamp")
        previous: dict[OperationalConnectionId, DeadbandConnectionMemory] = {}
        for value in (() if previous_memory is None else previous_memory):
            if not isinstance(value, DeadbandConnectionMemory):
                raise TypeError(
                    "previous_memory must contain DeadbandConnectionMemory"
                )
            if value.connection_id in previous:
                raise ValueError("previous_memory contains a duplicate")
            previous[value.connection_id] = value
        next_memory: list[DeadbandConnectionMemory] = []
        connections: list[OperationalConnection] = []
        for observation in sorted(
            observations, key=lambda value: _connection_key(value.connection_id)
        ):
            if not isinstance(observation, ConnectionParameterObservation):
                raise TypeError("observations contains an invalid value")
            if observation.physical_delay_us is None:
                raise ValueError("AccumulatedDeadbandParameterPlanningModel requires physical_delay_us")
            identifier = observation.connection_id
            configuration = self._configuration[identifier.kind]
            prior = previous.pop(identifier, None)
            if prior is None:
                effective = EffectiveConnectionParameters(
                    configuration.bandwidth_bps,
                    observation.physical_delay_us,
                    configuration.packet_loss_ppm,
                )
                memory = DeadbandConnectionMemory(
                    identifier, effective, utc
                )
            else:
                drift = abs(
                    observation.physical_delay_us - prior.effective.delay_us
                )
                stale = (
                    configuration.max_staleness is not None
                    and utc - prior.last_update >= configuration.max_staleness
                )
                # Comparing against last effective (not the last observation)
                # accumulates drift. A zero baseline changes on any nonzero value.
                threshold = self._thresholds[identifier.kind]
                threshold_reached = drift > 0 and (
                    prior.effective.delay_us == 0
                    or drift * threshold.denominator >= threshold.numerator
                    * abs(prior.effective.delay_us)
                )
                if threshold_reached or stale:
                    effective = EffectiveConnectionParameters(
                        configuration.bandwidth_bps,
                        observation.physical_delay_us,
                        configuration.packet_loss_ppm,
                    )
                    memory = DeadbandConnectionMemory(identifier, effective, utc)
                else:
                    memory = prior
            next_memory.append(memory)
            connection = OperationalConnection(
                identifier, memory.effective, memory.effective
            )
            connections.append(connection)

        return ParameterPlanningResult(
            tuple(connections),
            tuple(next_memory),
        )
