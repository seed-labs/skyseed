"""Scene entities and their local invariants; no SEED or position calculation.

Identity, membership and initial conditions are fixed after construction. Each
entity's data dictionary remains editable. Scene owns the indexes and adopts
references to these same entities, never a second copy of shared orbit geometry.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import ClassVar


def _identifier(value, field_name="identifier"):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a nonempty, trimmed string")


def _number(value, field_name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number, got {value!r}")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{field_name} outside supported range")


def _angle(value, field_name):
    _number(value, field_name, 0, 360)
    if value == 360:
        raise ValueError(f"{field_name} must be less than 360 degrees")


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("epoch must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _validate_data(value, label="data"):
    """Check lossless JSON data and identify the field that cannot be saved."""
    def visit(item, path):
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            _number(item, path)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
        elif isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError(f"{path}: data keys must be strings")
            for key, child in item.items():
                visit(child, f"{path}[{key!r}]")
        else:
            raise ValueError(f"{path}: expected a JSON value, got {type(item).__name__}")
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a dictionary")
    try:
        visit(value, label)
    except RecursionError as error:
        raise ValueError(f"{label}: data has a cycle or exceeds Python's nesting limit") from error


@dataclass(frozen=True, slots=True)
class ScenePositionSource:
    """Position input values, interpreted by the consuming position model."""
    kind: str
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.kind, "position source kind")
        self.validate()
        object.__setattr__(self, "data", deepcopy(self.data))
        if self.kind == "circular-orbit/v1":
            self.data["epoch"] = _utc(datetime.fromisoformat(self.data["epoch"])).isoformat()

    def validate(self):
        """Recheck editable source data; kind is fixed at construction."""
        _validate_data(self.data, f"position source {self.kind!r}.data")
        if self.kind == "circular-orbit/v1":
            required = {"altitude_m", "inclination_deg", "raan_deg", "mean_anomaly_deg", "epoch"}
            if not required <= self.data.keys():
                raise ValueError("incomplete circular-orbit/v1 position data")
            _number(self.data["altitude_m"], "altitude_m", 0)
            if self.data["altitude_m"] == 0:
                raise ValueError("altitude_m must be positive")
            _number(self.data["inclination_deg"], "inclination_deg", 0, 180)
            _angle(self.data["raan_deg"], "raan_deg")
            _angle(self.data["mean_anomaly_deg"], "mean_anomaly_deg")
            if not isinstance(self.data["epoch"], str):
                raise ValueError("circular epoch must be an ISO 8601 string")
            _utc(datetime.fromisoformat(self.data["epoch"]))


@dataclass(frozen=True, slots=True, kw_only=True)
class _SceneGroup:
    group_id: str
    data: dict = field(default_factory=dict)
    kind: ClassVar[str]

    def __post_init__(self):
        _identifier(self.group_id, "group_id")
        _validate_data(self.data, f"group {self.group_id!r}.data")
        object.__setattr__(self, "data", deepcopy(self.data))

    @property
    def parent_id(self):
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class Constellation(_SceneGroup):
    kind: ClassVar[str] = "constellation"


@dataclass(frozen=True, slots=True, kw_only=True)
class SatelliteShell(_SceneGroup):
    """Shared geometry and reference time for a circular-orbit shell."""
    constellation: Constellation
    altitude_m: float
    inclination_deg: float
    epoch: datetime
    kind: ClassVar[str] = "shell"

    def __post_init__(self):
        _SceneGroup.__post_init__(self)
        if not isinstance(self.constellation, Constellation):
            raise TypeError("shell requires a Constellation")
        _number(self.altitude_m, "altitude_m", 0)
        if self.altitude_m == 0:
            raise ValueError("altitude_m must be positive")
        _number(self.inclination_deg, "inclination_deg", 0, 180)
        object.__setattr__(self, "epoch", _utc(self.epoch))

    @property
    def parent_id(self):
        return self.constellation.group_id


@dataclass(frozen=True, slots=True, kw_only=True)
class SatelliteOrbit(_SceneGroup):
    shell: SatelliteShell
    raan_deg: float
    kind: ClassVar[str] = "orbit"

    def __post_init__(self):
        _SceneGroup.__post_init__(self)
        if not isinstance(self.shell, SatelliteShell):
            raise TypeError("orbit requires a SatelliteShell")
        _angle(self.raan_deg, "raan_deg")

    @property
    def parent_id(self):
        return self.shell.group_id


@dataclass(frozen=True, slots=True, kw_only=True)
class GroundStationGroup(_SceneGroup):
    kind: ClassVar[str] = "ground_station_group"


@dataclass(frozen=True, slots=True, kw_only=True)
class _SceneNode:
    node_id: str
    name: str
    data: dict = field(default_factory=dict)
    kind: ClassVar[str]

    def __post_init__(self):
        _identifier(self.node_id, "node_id")
        _identifier(self.name, "name")
        _validate_data(self.data, f"node {self.node_id!r}.data")
        object.__setattr__(self, "data", deepcopy(self.data))


@dataclass(frozen=True, slots=True, kw_only=True)
class Satellite(_SceneNode):
    """A shared orbit plus phase, or an independent position source."""
    orbit: SatelliteOrbit | None = None
    mean_anomaly_deg: float | None = None
    position_source: ScenePositionSource | None = None
    kind: ClassVar[str] = "satellite"

    def __post_init__(self):
        _SceneNode.__post_init__(self)
        if self.orbit is not None:
            if not isinstance(self.orbit, SatelliteOrbit):
                raise TypeError("orbit must be a SatelliteOrbit")
            if self.position_source is not None:
                raise ValueError("satellite cannot have both an orbit and an independent position source")
            _angle(self.mean_anomaly_deg, "mean_anomaly_deg")
        else:
            if self.mean_anomaly_deg is not None or self.position_source is None:
                raise ValueError("satellite requires an orbit with phase, or an independent position source")
            if not isinstance(self.position_source, ScenePositionSource):
                raise TypeError("position_source must be a ScenePositionSource")
            object.__setattr__(self, "position_source", ScenePositionSource(
                self.position_source.kind, self.position_source.data))

    @property
    def group_id(self):
        return self.orbit.group_id if self.orbit is not None else None

    @property
    def position(self):
        """A detached input value; changing it cannot modify shared geometry."""
        if self.orbit is None:
            return ScenePositionSource(self.position_source.kind, self.position_source.data)
        shell = self.orbit.shell
        return ScenePositionSource("circular-orbit/v1", {
            "altitude_m": shell.altitude_m, "inclination_deg": shell.inclination_deg,
            "raan_deg": self.orbit.raan_deg, "mean_anomaly_deg": self.mean_anomaly_deg,
            "epoch": shell.epoch.isoformat(),
        })


@dataclass(frozen=True, slots=True, kw_only=True)
class GroundStation(_SceneNode):
    latitude_deg: float
    longitude_deg: float
    elevation_m: float = 0
    group: GroundStationGroup | None = None
    kind: ClassVar[str] = "ground_station"

    def __post_init__(self):
        _SceneNode.__post_init__(self)
        _number(self.latitude_deg, "latitude_deg", -90, 90)
        _number(self.longitude_deg, "longitude_deg", -180, 180)
        _number(self.elevation_m, "elevation_m")
        if self.group is not None and not isinstance(self.group, GroundStationGroup):
            raise TypeError("ground station group must be a GroundStationGroup")

    @property
    def group_id(self):
        return self.group.group_id if self.group is not None else None

    @property
    def position(self):
        return ScenePositionSource("geodetic/v1", {
            "latitude_deg": self.latitude_deg, "longitude_deg": self.longitude_deg,
            "elevation_m": self.elevation_m,
        })
