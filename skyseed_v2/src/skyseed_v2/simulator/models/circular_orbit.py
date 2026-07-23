"""Built-in planned circular-Orbit Position algorithm."""

from __future__ import annotations

from datetime import datetime
from math import cos, pi, radians, sin, sqrt
from types import MappingProxyType
from typing import Mapping

from skyseed_v2._time import normalize_utc_datetime
from skyseed_v2.node_id import NodeId

from ..coordinates import _gcrs_cartesian_batch_to_ecef, geodetic_to_ecef
from ..position import EarthFixedPosition
from ..topology import Topology


_EARTH_GRAVITATIONAL_PARAMETER_M3_PER_S2 = 3.986_004_418e14
_ORBIT_REFERENCE_RADIUS_M = 6_378_137.0


class CircularOrbitPositionModel:
    """Compute planned circular-Orbit and fixed GroundStation Positions.

    This built-in privately interprets the planned circular elements in GCRS:
    the origin is Earth's centre, the GCRS ``x-y`` plane is the reference
    equatorial plane and ``+z`` is its north normal. RAAN is measured in that
    plane from ``+x`` toward ``+y``. Inclination is the angle from the
    equatorial plane. Zero mean anomaly is the ascending-node radius
    ``(cos(RAAN), sin(RAAN), 0)``, and positive phase advances toward ``+z``
    for prograde inclined Orbits. GCRS is therefore declared by construction:
    this concrete algorithm defines the input angles against those named GCRS
    axes instead of inferring a frame from generic orbital elements. Its
    rotated Cartesian points are not TEME, ICRS, J2000 or a generic "ECI"
    state.

    Satellite mean anomaly is defined at the parent Orbit's UTC epoch. The
    Model advances it by circular mean motion over the exact UTC elapsed
    seconds to the evaluation timestamp, then transforms that timestamp's
    GCRS Cartesian point to the formal ITRS/ECEF Position. No velocity or
    inertial-frame value crosses the public Position boundary.

    Layer: built-in stateless Simulator Model. Users explicitly create it and
    one Slicer invokes it. It is not a default, ephemeris owner, velocity
    source or fallback and is public from ``skyseed_v2.simulator``.
    """

    __slots__ = ()

    def compute_positions(
        self,
        topology: Topology,
        timestamp: datetime,
    ) -> Mapping[NodeId, EarthFixedPosition]:
        if not isinstance(topology, Topology):
            raise TypeError("topology must be a Topology")
        utc = normalize_utc_datetime(timestamp, "timestamp")
        positions: dict[NodeId, EarthFixedPosition] = {}
        satellite_ids: list[NodeId] = []
        inertial_points: list[tuple[float, float, float]] = []
        for autonomous_system in topology.autonomous_systems:
            for shell in autonomous_system.shells:
                for orbit in shell.orbits:
                    radius_m = (
                        _ORBIT_REFERENCE_RADIUS_M + orbit.altitude_km * 1_000.0
                    )
                    mean_motion_rad_s = sqrt(
                        _EARTH_GRAVITATIONAL_PARAMETER_M3_PER_S2
                        / radius_m**3
                    )
                    elapsed_seconds = (utc - orbit.epoch_utc).total_seconds()
                    inclination = radians(orbit.inclination_deg)
                    right_ascension = radians(orbit.right_ascension_deg)
                    for satellite in orbit.satellites:
                        phase = (
                            radians(satellite.mean_anomaly_deg)
                            + mean_motion_rad_s * elapsed_seconds
                        ) % (2.0 * pi)
                        orbital_x = radius_m * cos(phase)
                        orbital_y = radius_m * sin(phase)
                        inertial_x = (
                            cos(right_ascension) * orbital_x
                            - sin(right_ascension)
                            * cos(inclination)
                            * orbital_y
                        )
                        inertial_y = (
                            sin(right_ascension) * orbital_x
                            + cos(right_ascension)
                            * cos(inclination)
                            * orbital_y
                        )
                        inertial_z = sin(inclination) * orbital_y
                        satellite_ids.append(satellite.node_id)
                        inertial_points.append(
                            (inertial_x, inertial_y, inertial_z)
                        )
            for ground_station in autonomous_system.ground_stations:
                positions[ground_station.node_id] = geodetic_to_ecef(
                    ground_station.latitude_deg,
                    ground_station.longitude_deg,
                    ground_station.elevation_m,
                )
        satellite_positions = _gcrs_cartesian_batch_to_ecef(
            tuple(inertial_points), utc
        )
        positions.update(
            zip(satellite_ids, satellite_positions, strict=True)
        )
        return MappingProxyType(
            dict(sorted(positions.items(), key=lambda item: _node_key(item[0])))
        )


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


__all__ = ["CircularOrbitPositionModel"]
