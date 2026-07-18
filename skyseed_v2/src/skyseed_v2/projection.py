"""Application projection services for read-only Emulator contracts."""

from __future__ import annotations

from skyseed_v2.contracts import (
    GroundStationDescriptor,
    SatelliteDescriptor,
    SimulationNodeId,
)
from skyseed_v2.contracts.identity import (
    GROUND_STATION_ENTITY_KIND,
    SATELLITE_ENTITY_KIND,
)
from skyseed_v2.domain import GroundStation, Satellite


__all__ = [
    "GroundStationDescriptorProjector",
    "SatelliteDescriptorProjector",
]


class GroundStationDescriptorProjector:
    """Copy approved GroundStation scalars into its restricted Descriptor.

    Layer: stateless application service. Catalog builders or explicit
    projection callers create and use it. It is not an entity factory,
    serializer or Compiler; it is public from this submodule because projection
    is independent of both the entity and cross-layer contract.
    """

    def project(self, ground_station: GroundStation) -> GroundStationDescriptor:
        if not isinstance(ground_station, GroundStation):
            raise TypeError("ground_station must be a GroundStation")
        location = ground_station.location
        return GroundStationDescriptor(
            node_id=SimulationNodeId(
                asn=ground_station.getAsn(),
                entity_kind=GROUND_STATION_ENTITY_KIND,
                entity_id=ground_station.ground_station_id,
            ),
            ground_station_id=ground_station.ground_station_id,
            latitude_deg=location.latitude_deg,
            longitude_deg=location.longitude_deg,
            elevation_m=location.elevation_m,
            station_type=ground_station.station_type,
            operator=ground_station.operator,
        )


class SatelliteDescriptorProjector:
    """Copy approved Satellite scalars into its restricted Descriptor.

    Layer: stateless application service. Catalog builders or explicit
    projection callers create and use it. It is not an entity factory,
    serializer or Compiler; it is public from this submodule because projection
    is independent of both the entity and cross-layer contract.
    """

    def project(
        self,
        satellite: Satellite,
    ) -> SatelliteDescriptor:
        """Project approved scalar values from the unique Satellite object."""

        parameters = satellite.orbit_parameters
        return SatelliteDescriptor(
            node_id=SimulationNodeId(
                asn=satellite.getAsn(),
                entity_kind=SATELLITE_ENTITY_KIND,
                entity_id=satellite.satellite_id,
            ),
            satellite_id=satellite.satellite_id,
            shell_id=satellite.shell_id,
            orbit_id=satellite.orbit_id,
            orbit_index=satellite.orbit_index,
            altitude_km=parameters.altitude_km,
            inclination_deg=parameters.inclination_deg,
            right_ascension_deg=parameters.right_ascension_deg,
            mean_anomaly_deg=satellite.mean_anomaly_deg,
        )
