"""Static positions supplied by the author; inject position_model=StaticPositionModel(positions).

Read world.autonomous_systems, AS.shells, orbit parameters and satellites,
and GS coordinates; geometry.geodetic_to_ecef creates an ECEF position.
Do not call world.frame_at() inside Position: it would invoke this Model again.
Reference: docs/api/physical.md (entities, snapshots and geometry).
"""
from types import MappingProxyType


class StaticPositionModel:
    def __init__(self, positions):
        # NodeId -> EarthFixedPosition, including every Satellite and GroundStation.
        self.positions = MappingProxyType(dict(positions))

    def compute_positions(self, physical_world, timestamp):
        return self.positions
