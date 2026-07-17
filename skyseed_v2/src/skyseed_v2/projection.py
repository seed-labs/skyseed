"""Application projection services for read-only Emulator contracts."""

from __future__ import annotations

from skyseed_v2.contracts import GroundStationDescriptor, SatelliteDescriptor
from skyseed_v2.domain import GroundStation, Satellite


class GroundStationDescriptorProjector:
    """Project the unique GroundStation into its restricted contract."""

    def project(self, ground_station: GroundStation) -> GroundStationDescriptor:
        if not isinstance(ground_station, GroundStation):
            raise TypeError("ground_station must be a GroundStation")
        location = ground_station.location
        return GroundStationDescriptor(
            ground_station_id=ground_station.ground_station_id,
            latitude_deg=location.latitude_deg,
            longitude_deg=location.longitude_deg,
            elevation_m=location.elevation_m,
            station_type=ground_station.station_type,
            operator=ground_station.operator,
            runtime_node=ground_station.runtime_reference,
        )


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
