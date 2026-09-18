"""Built-in planned circular-Orbit Position algorithm."""

from __future__ import annotations

from datetime import datetime
from math import cos, pi, radians, sin, sqrt
from types import MappingProxyType
from typing import Mapping, TYPE_CHECKING

from skyseed_v2.shared.time import normalize_utc_datetime
from skyseed_v2.shared import NodeId

from skyseed_v2.simulator.geometry import EarthFixedPosition, geodetic_to_ecef
from skyseed_v2.simulator.geometry.coordinates import _gcrs_cartesian_batch_to_ecef
if TYPE_CHECKING:
    from ..world import PhysicalWorld


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

    Layer: built-in Simulator physical Model with private static-element reuse.
    Default Simulator assembly selects it unless a Position Model is supplied;
    users may also construct it explicitly. One ``PhysicalWorld`` owns it, and
    ``PhysicalWorld.frame_at()`` invokes it. It is not an ephemeris owner,
    velocity source, time cursor or fallback, and is public from
    ``skyseed_v2.simulator``.
    """

    __slots__ = ("_prepared",)

    def describe_configuration(self):
        return {}

    def __init__(self) -> None:
        self._prepared = None

    def semantic_fingerprint_material(self) -> dict[str, object]:
        """Describe the fixed algorithm independently of derived static caches."""
        return {}

    def _prepare(self, world):
        cached = self._prepared
        if cached is not None and cached[0] is world:
            return cached[1:]
        elements, ground = [], {}
        for system in world.autonomous_systems:
            for shell in system.shells:
                for orbit in shell.orbits:
                    radius = _ORBIT_REFERENCE_RADIUS_M + orbit.altitude_km * 1000.0
                    motion = sqrt(_EARTH_GRAVITATIONAL_PARAMETER_M3_PER_S2 / radius**3)
                    inclination, raan = radians(orbit.inclination_deg), radians(orbit.right_ascension_deg)
                    elements.append((radius, motion, orbit.epoch_utc,
                        cos(raan), sin(raan), cos(inclination), sin(inclination),
                        tuple((satellite.node_id, radians(satellite.mean_anomaly_deg)) for satellite in orbit.satellites)))
            for station in system.ground_stations:
                ground[station.node_id] = geodetic_to_ecef(station.latitude_deg, station.longitude_deg, station.elevation_m)
        ordered_ids = tuple(sorted((*ground, *(identifier for element in elements for identifier, _ in element[-1])), key=_node_key))
        prepared = (world, tuple(elements), MappingProxyType(ground), ordered_ids)
        self._prepared = prepared
        return prepared[1:]

    def compute_positions(
        self,
        physical_world: PhysicalWorld,
        timestamp: datetime,
    ) -> Mapping[NodeId, EarthFixedPosition]:
        from ..world import PhysicalWorld

        if not isinstance(physical_world, PhysicalWorld):
            raise TypeError("physical_world must be a PhysicalWorld")
        utc = normalize_utc_datetime(timestamp, "timestamp")
        elements, ground_positions, ordered_ids = self._prepare(physical_world)
        positions = dict(ground_positions)
        satellite_ids, inertial_points = [], []
        for radius, motion, epoch, cos_raan, sin_raan, cos_inc, sin_inc, satellites in elements:
            elapsed = (utc-epoch).total_seconds()
            for identifier, initial_phase in satellites:
                phase = (initial_phase + motion * elapsed) % (2.0 * pi)
                orbital_x, orbital_y = radius * cos(phase), radius * sin(phase)
                inertial_points.append((
                    cos_raan * orbital_x - sin_raan * cos_inc * orbital_y,
                    sin_raan * orbital_x + cos_raan * cos_inc * orbital_y,
                    sin_inc * orbital_y,
                ))
                satellite_ids.append(identifier)
        satellite_positions = _gcrs_cartesian_batch_to_ecef(
            tuple(inertial_points), utc
        )
        positions.update(
            zip(satellite_ids, satellite_positions, strict=True)
        )
        return MappingProxyType(
            {identifier: positions[identifier] for identifier in ordered_ids}
        )


def _node_key(node_id: NodeId) -> tuple[int, str, str]:
    return node_id.asn, node_id.entity_kind, node_id.entity_id


__all__ = ["CircularOrbitPositionModel"]
