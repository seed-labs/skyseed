"""WGS84 geometry and batched state transforms; no model or Scene ownership."""
from threading import RLock
import numpy as np
from astropy import units as u
from astropy.coordinates import CartesianRepresentation, CartesianDifferential, GCRS, TEME, ITRS
from astropy.time import Time
from astropy.utils import iers

WGS84_A_M = 6378137.0
WGS84_B_M = WGS84_A_M * (1 - 1 / 298.257223563)
EARTH_MU_M3_S2 = 3.986004418e14
LIGHT_SPEED_M_S = 299792458.0
_AXES = np.array([WGS84_A_M, WGS84_A_M, WGS84_B_M])
_IERS_LOCK = RLock()


def elapsed_seconds(timestamp, epochs):
    """UTC datetime subtraction alone would omit leap seconds in orbital motion."""
    with _IERS_LOCK, iers.conf.set_temp("auto_download", False):
        return (Time(timestamp) - Time(list(epochs))).to_value(u.s)


def julian_date(timestamp):
    with _IERS_LOCK, iers.conf.set_temp("auto_download", False):
        time = Time(timestamp)
        return time.jd1, time.jd2


def ground_ecef(latitude_deg, longitude_deg, elevation_m):
    lat, lon = np.radians([latitude_deg, longitude_deg])
    eccentricity2 = 1 - (WGS84_B_M / WGS84_A_M) ** 2
    radius = WGS84_A_M / np.sqrt(1 - eccentricity2 * np.sin(lat) ** 2)
    return ((radius + elevation_m) * np.cos(lat) * np.cos(lon),
            (radius + elevation_m) * np.cos(lat) * np.sin(lon),
            (radius * (1 - eccentricity2) + elevation_m) * np.sin(lat))


def ground_up(latitude_deg, longitude_deg):
    lat, lon = np.radians([latitude_deg, longitude_deg])
    return np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def earth_visible(first, second):
    """Segment visibility against WGS84, including ground endpoints on its surface."""
    origin = np.asarray(first) / _AXES
    end = np.asarray(second) / _AXES
    direction = end - origin
    squared = np.sum(direction * direction, axis=-1)
    fraction = np.divide(-np.sum(origin * direction, axis=-1), squared,
                         out=np.zeros_like(squared), where=squared != 0)
    closest = origin + np.clip(fraction, 0, 1)[..., None] * direction
    # Only a round-off tolerance at the ellipsoid surface (~micrometres).
    return np.sum(closest * closest, axis=-1) >= 1 - 1e-12


def states_to_ecef(positions_m, velocities_m_s, timestamp, *, frame):
    """Transform position AND its derivative, including Earth's frame motion.

    Use installed IERS data without network downloads. Astropy's accuracy
    warnings remain visible; callers may supply an IERS table via its context.
    """
    position = np.asarray(positions_m, dtype=float).reshape((-1, 3))
    velocity = np.asarray(velocities_m_s, dtype=float).reshape((-1, 3))
    if not len(position):
        return position, velocity
    representation = CartesianRepresentation(position.T * u.m,
        differentials=CartesianDifferential(velocity.T * u.m / u.s))
    # Astropy configuration is process-global; keep this scoped policy from
    # overlapping when independent Physical instances are queried in threads.
    with _IERS_LOCK, iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        time = Time(timestamp)
        source = {"gcrs": GCRS, "teme": TEME}[frame](representation, obstime=time)
        fixed = source.transform_to(ITRS(obstime=time)).cartesian
    return (fixed.xyz.to_value(u.m).T,
            fixed.differentials["s"].d_xyz.to_value(u.m / u.s).T)
