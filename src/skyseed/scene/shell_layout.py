"""Circular shell geometry, independent of node identity and persistence."""
from .scene_entities import _number


def circular_shell_layout(planes, satellites_per_plane, *, raan_start_deg=0,
                          phase_start_deg=0, phase_factor=0, orbit_raan_deg=None):
    """Yield each plane's RAAN and evenly spaced satellite phases.

    A plane advances phase by phase_factor * 360 / total_satellites.
    Explicit plane directions support layouts such as the four-plane S5 group.
    """
    for name, count in (("planes", planes), ("satellites_per_plane", satellites_per_plane)):
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"{name} must be a positive integer")
    _number(raan_start_deg, "raan_start_deg")
    _number(phase_start_deg, "phase_start_deg")
    _number(phase_factor, "phase_factor")
    if orbit_raan_deg is not None:
        if len(orbit_raan_deg) != planes:
            raise ValueError("orbit_raan_deg must contain one direction per plane")
        for angle in orbit_raan_deg:
            _number(angle, "orbit_raan_deg")
        if raan_start_deg != 0:
            raise ValueError("choose raan_start_deg or explicit orbit_raan_deg")

    plane_phase_shift = phase_factor * 360 / (planes * satellites_per_plane)
    for plane in range(planes):
        if orbit_raan_deg is None:
            raan = raan_start_deg + plane * 360 / planes
        else:
            raan = orbit_raan_deg[plane]
        phase_start = phase_start_deg + plane * plane_phase_shift
        phases = []
        for satellite in range(satellites_per_plane):
            phase = phase_start + satellite * 360 / satellites_per_plane
            phases.append(phase % 360)
        yield raan % 360, phases
