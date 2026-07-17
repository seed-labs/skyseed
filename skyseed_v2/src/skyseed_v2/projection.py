"""Application projection services for read-only Emulator contracts."""

from __future__ import annotations

from skyseed_v2.contracts import SatelliteDescriptor
from skyseed_v2.domain import Satellite


class SatelliteDescriptorProjector:
    """Project the unique Satellite into its restricted read-only contract."""

    def project(
        self,
        satellite: Satellite,
    ) -> SatelliteDescriptor:
        """Project approved scalar values from the unique Satellite object."""

        parameters = satellite.orbit_parameters
        return SatelliteDescriptor(
            satellite_id=satellite.satellite_id,
            shell_id=satellite.shell_id,
            orbit_id=satellite.orbit_id,
            orbit_index=satellite.orbit_index,
            altitude_km=parameters.altitude_km,
            inclination_deg=parameters.inclination_deg,
            right_ascension_deg=parameters.right_ascension_deg,
            mean_anomaly_deg=satellite.mean_anomaly_deg,
            runtime_node=satellite.runtime_reference,
        )
