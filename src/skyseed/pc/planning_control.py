"""Assemble the three planning layers and expose independent plan requests."""
from datetime import timedelta

from .physical import PhysicalLayer
from .link import LinkLayer
from .network import NetworkLayer


class PlanningControl:
    def __init__(self, scene):
        self.scene = scene
        physical = PhysicalLayer(scene)
        link = LinkLayer(physical)
        network = NetworkLayer(link)
        self._layers = {"physical-layer": physical, "link-layer": link,
                        "network-layer": network}

    def get_layer(self, name):
        """Return an existing layer; configuration remains on that layer."""
        return self._layers[name]

    def generate_link_plans(self, *, start, end):
        """Yield execution links; this request never computes routes."""
        yield self.get_layer("link-layer").generate_plan(start, end)

    def generate_route_plans(self, *, start, end, link_lookahead=timedelta(0)):
        """Yield delivered routes, preparing only the required base links."""
        yield from self.get_layer("network-layer").generate_plans(
            start, end, link_lookahead=link_lookahead)
