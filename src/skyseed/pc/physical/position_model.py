"""Satellite position contract and built-in circular-orbit and SGP4 models."""
from datetime import datetime
from typing import Collection, Mapping, Protocol

import numpy as np
from sgp4.api import Satrec, SatrecArray, SGP4_ERRORS
from sgp4.io import verify_checksum

from skyseed.scene import Scene, ScenePositionSource, Satellite
from .physical_results import NodeState, utc_time
from .earth_geometry import (
    EARTH_MU_M3_S2,
    WGS84_A_M,
    elapsed_seconds,
    julian_date,
    states_to_ecef,
)


class SatellitePositionModel(Protocol):
    """Return ECEF states for the requested satellites at one query time."""

    def compute_positions(
        self,
        scene: Scene,
        satellite_ids: Collection[str],
        timestamp: datetime,
    ) -> Mapping[str, NodeState]: ...


class CircularOrbitPositionModel:
    """Propagate circular inputs in GCRS, then transform position and velocity."""

    def __init__(self):
        self._elements = {}

    def _prepare(self, satellite):
        if not isinstance(satellite, Satellite):
            raise ValueError(f"{satellite.node_id!r} is not a satellite")

        if satellite.orbit is not None:
            orbit = satellite.orbit
            shell = orbit.shell
            signature = (
                shell.altitude_m,
                shell.inclination_deg,
                orbit.raan_deg,
                satellite.mean_anomaly_deg,
                shell.epoch,
            )
        else:
            source = satellite.position_source
            if source.kind != "circular-orbit/v1":
                raise ValueError(
                    f"satellite {satellite.node_id!r}: "
                    f"expected circular-orbit/v1, got {source.kind}"
                )
            data = source.data
            signature = (
                data["altitude_m"],
                data["inclination_deg"],
                data["raan_deg"],
                data["mean_anomaly_deg"],
                data["epoch"],
            )

        cached = self._elements.get(satellite.node_id)
        if cached is not None:
            previous_satellite, previous_signature, prepared = cached
            if previous_satellite is satellite and previous_signature == signature:
                return prepared

        altitude, inclination, raan, phase, epoch = signature
        if satellite.orbit is None:
            source.validate()
            epoch = utc_time(datetime.fromisoformat(epoch))

        radius = WGS84_A_M + altitude
        inclination, raan, phase = np.radians([inclination, raan, phase])
        first = np.array([np.cos(raan), np.sin(raan), 0])
        second = np.array([
            -np.sin(raan) * np.cos(inclination),
            np.cos(raan) * np.cos(inclination),
            np.sin(inclination),
        ])
        motion = np.sqrt(EARTH_MU_M3_S2 / radius**3)
        prepared = (radius, motion, epoch, phase, first, second)
        self._elements[satellite.node_id] = (satellite, signature, prepared)
        return prepared

    def compute_positions(self, scene, satellite_ids, timestamp):
        timestamp = utc_time(timestamp)
        identities = tuple(satellite_ids)
        if not identities:
            return {}

        elements = [self._prepare(scene.get_node(node_id)) for node_id in identities]
        epochs = tuple(dict.fromkeys(element[2] for element in elements))
        elapsed = dict(zip(epochs, elapsed_seconds(timestamp, epochs), strict=True))
        points = []
        velocities = []
        for radius, motion, epoch, phase, first, second in elements:
            angle = (phase + motion * elapsed[epoch]) % (2 * np.pi)
            position = radius * (np.cos(angle) * first + np.sin(angle) * second)
            velocity = radius * motion * (-np.sin(angle) * first + np.cos(angle) * second)
            points.append(position)
            velocities.append(velocity)

        positions_ecef, velocities_ecef = states_to_ecef(
            points, velocities, timestamp, frame="gcrs"
        )
        states = {}
        for node_id, position, velocity in zip(
            identities, positions_ecef, velocities_ecef, strict=True
        ):
            states[node_id] = NodeState(tuple(position), tuple(velocity))
        return states


def _parse_tle(line1, line2):
    if (
        len(line1) != 69
        or len(line2) != 69
        or not line1.startswith("1 ")
        or not line2.startswith("2 ")
    ):
        raise ValueError("TLE requires its two 69-character element lines")
    if line1[2:7] != line2[2:7]:
        raise ValueError("TLE lines refer to different satellites")
    verify_checksum(line1, line2)
    record = Satrec.twoline2rv(line1, line2)
    if (
        not np.isfinite(record.no_kozai)
        or record.no_kozai <= 0
        or not 0 <= record.ecco < 1
    ):
        raise ValueError("TLE requires positive mean motion and elliptic eccentricity")
    return record


def tle_position_source(line1, line2, *, source=None):
    """Adapt two GP/TLE lines to persistable Scene input; never downloads data.

    The TLE preserves its own epoch. ``source`` is optional provenance text.
    The satellite's stable Scene ID is chosen separately by the caller.
    """
    line1 = line1.rstrip("\r\n")
    line2 = line2.rstrip("\r\n")
    _parse_tle(line1, line2)
    data = {"line1": line1, "line2": line2}
    if source is not None:
        data["source"] = source
    return ScenePositionSource("sgp4-tle/v1", data)


class SGP4PositionModel:
    """Propagate GP/TLE inputs in TEME, then transform position and velocity."""

    def __init__(self):
        self._records = {}

    def _record(self, satellite):
        if not isinstance(satellite, Satellite) or satellite.position_source is None:
            raise ValueError(f"node {satellite.node_id!r}: SGP4 requires sgp4-tle/v1 input")
        source = satellite.position_source
        if source.kind != "sgp4-tle/v1":
            raise ValueError(
                f"satellite {satellite.node_id!r}: "
                f"expected sgp4-tle/v1, got {source.kind}"
            )

        try:
            signature = (source.data["line1"], source.data["line2"])
            cached = self._records.get(satellite.node_id)
            if cached is not None:
                previous_satellite, previous_signature, record = cached
                if previous_satellite is satellite and previous_signature == signature:
                    return record
            record = _parse_tle(*signature)
        except (ValueError, KeyError) as error:
            raise ValueError(f"satellite {satellite.node_id!r}: {error}") from error
        self._records[satellite.node_id] = (satellite, signature, record)
        return record

    def compute_positions(self, scene, satellite_ids, timestamp):
        timestamp = utc_time(timestamp)
        identities = tuple(satellite_ids)
        if not identities:
            return {}

        records = [self._record(scene.get_node(node_id)) for node_id in identities]
        jd1, jd2 = julian_date(timestamp)
        errors, positions, velocities = SatrecArray(records).sgp4(
            np.array([jd1]), np.array([jd2])
        )
        for node_id, error in zip(identities, errors[:, 0], strict=True):
            if error:
                raise ValueError(
                    f"satellite {node_id!r} at {timestamp.isoformat()}: "
                    f"SGP4 {SGP4_ERRORS[int(error)]}"
                )

        positions_ecef, velocities_ecef = states_to_ecef(
            positions[:, 0] * 1000,
            velocities[:, 0] * 1000,
            timestamp,
            frame="teme",
        )
        states = {}
        for node_id, position, velocity in zip(
            identities, positions_ecef, velocities_ecef, strict=True
        ):
            states[node_id] = NodeState(tuple(position), tuple(velocity))
        return states
