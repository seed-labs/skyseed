"""Read-only lifecycle input shared by the two connection-selection roles."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from skyseed_v2.shared.time import normalize_utc_datetime
from .values import OperationalConnectionId


@dataclass(frozen=True, slots=True)
class ConnectionSelectionState:
    """Planner-created projection of preparing and operational selections.

    Layer: public Planner Model input. Authors read it during selection and optional
    search expansion. It is public because independent Models receive this
    projection. It is not a lifecycle owner, result or Tool evidence.
    """

    timestamp: datetime
    observation_interval: timedelta
    acquisition_duration: timedelta
    selected_since: Mapping[OperationalConnectionId, datetime]
    operational_connections: frozenset[OperationalConnectionId]

    def __post_init__(self):
        object.__setattr__(self, "timestamp", normalize_utc_datetime(self.timestamp, "timestamp"))
        for name in ("observation_interval", "acquisition_duration"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value < timedelta(0):
                raise ValueError(f"{name} must be a non-negative timedelta")
        if not self.observation_interval:
            raise ValueError("observation_interval must be positive")
        times = {}
        for identifier, timestamp in self.selected_since.items():
            if not isinstance(identifier, OperationalConnectionId):
                raise TypeError("selected_since keys must be OperationalConnectionId")
            times[identifier] = normalize_utc_datetime(timestamp, "selected_since")
            if times[identifier] > self.timestamp:
                raise ValueError("selected_since must not be after timestamp")
        operational = frozenset(self.operational_connections)
        if not operational <= times.keys():
            raise ValueError("operational connections must have original selection times")
        object.__setattr__(self, "selected_since", MappingProxyType(times))
        object.__setattr__(self, "operational_connections", operational)
