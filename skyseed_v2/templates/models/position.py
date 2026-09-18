"""Static positions supplied by the author; inject position_model=StaticPositionModel(positions)."""
from types import MappingProxyType


class StaticPositionModel:
    def __init__(self, positions):
        # NodeId -> EarthFixedPosition, including every Satellite and GroundStation.
        self.positions = MappingProxyType(dict(positions))

    def compute_positions(self, physical_world, timestamp):
        return self.positions
