"""Fixed executable directional parameters, independent of optional physical delay.

Inject parameter_model=FixedParameterModel(); edit the two directions as needed.

Read each observation.connection_id, optional physical_delay_us/physical_candidate,
and previous_memory. This hook receives no World. Directional effective parameters
use bps, microseconds and loss ppm. Reference: docs/api/planning.md (parameters).
"""
from skyseed_v2.simulator import planner


class FixedParameterModel:
    def __init__(self, bandwidth_bps=1_000_000, delay_us=100, packet_loss_ppm=0):
        self.parameters = planner.EffectiveConnectionParameters(bandwidth_bps, delay_us, packet_loss_ppm)

    def plan_effective_parameters(self, timestamp, observations, previous_memory):
        return planner.ParameterPlanningResult(tuple(planner.OperationalConnection(
            value.connection_id, self.parameters, self.parameters) for value in observations))
