"""Map this one UT address onto its existing GZ label path at the GS ingress."""
from skyseed.pc.network import ForwardingRule, GroundStationZoneRoutingModel, RouteDestination


class TerminalRoutingModel(GroundStationZoneRoutingModel):
    def compute_routes(self, scene, timestamp, link_plans):
        tables = super().compute_routes(scene, timestamp, link_plans)
        experiment = scene.data["experiment"]
        zone = RouteDestination("endpoint", experiment["zone"])
        terminal = RouteDestination("prefix", experiment["ut_address"] + "/32")
        for ground in self.ground_station_associations(scene)[experiment["zone"]]:
            rules = tables.get(ground, ())
            for rule in rules:
                if rule.destination != zone or rule.action != "push":
                    continue
                # Only classify the original packet at this edge. The same
                # downstream labels carry it unchanged through the network.
                ingress = ForwardingRule(terminal, "push", rule.exit,
                                         outgoing_label=rule.outgoing_label)
                tables[ground] = (*rules, ingress)
                break
        return tables
