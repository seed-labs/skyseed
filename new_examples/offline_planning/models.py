"""A deliberately simple experiment: apply loss only to satellite-to-GS traffic."""
from dataclasses import replace

from skyseed.pc.link import LinkPlan


class DownlinkLossModel:
    """Constant execution loss, not an interference or radio propagation model."""

    def __init__(self, *, packet_loss_ppm=50_000):
        self.packet_loss_ppm = packet_loss_ppm

    def _configuration(self, configuration):
        first, second = configuration.id
        directions = []
        for source, target in ((first, second), (second, first)):
            properties = configuration.properties_from(source)
            if (properties is not None
                    and self.layer.scene.get_node(source).kind == "satellite"
                    and self.layer.scene.get_node(target).kind == "ground_station"):
                properties = replace(properties, packet_loss_ppm=self.packet_loss_ppm)
            directions.append(properties)
        return replace(configuration, properties=directions[0], reverse_properties=directions[1])

    def perturb(self, plan):
        initial = {identity: self._configuration(value) for identity, value in plan.initial_links.items()}
        operations = []
        for operation in plan.operations:
            if operation.configuration is not None:
                operation = replace(operation, configuration=self._configuration(operation.configuration))
            operations.append(operation)
        return LinkPlan(plan.start_time, plan.end_time, initial, tuple(operations))
