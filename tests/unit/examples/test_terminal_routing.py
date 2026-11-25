"""The temporary UT classifier follows GZ path changes without new transit rules."""
from datetime import timedelta

from examples.s4_handover.experiment.ut.routing import TerminalRoutingModel
from tests.unit.network.conftest import epoch, network_factory


def test_ut_ingress_changes_with_ground_path_and_disappears_without_service(epoch, network_factory):
    def edges(seconds):
        if seconds < 5:
            return [("g", "a"), ("a", "c")]
        return [("g", "b"), ("b", "c")]

    network = network_factory(edges)
    network.scene.data["experiment"] = {"zone": "z", "ground_station": "g",
                                         "ut_address": "192.0.2.2"}

    class Services:
        def assign_service(self, scene, snapshot):
            return {"z": "c" if snapshot.timestamp < epoch + timedelta(seconds=10) else None}

    network.set_service_assignment_model(Services)
    network.set_routing_hook(TerminalRoutingModel)
    network.generate_plans(epoch, epoch + timedelta(seconds=15))
    plan = network.base_plans[0]
    for seconds, first_hop in ((0, "a"), (5, "b")):
        tables = plan.state_at(epoch + timedelta(seconds=seconds))
        ingress = next(rule for rule in tables["g"] if rule.destination.kind == "prefix")
        assert ingress.destination.identity == "192.0.2.2/32"
        assert ingress.action == "push" and ingress.exit.peer == first_hop
        receiver = next(rule for rule in tables[first_hop] if rule.incoming_label == ingress.outgoing_label)
        assert receiver.destination.identity == "z"
        assert receiver.exit.peer == "c"
        assert all(rule.destination.kind != "prefix" for rule in tables["c"])
    assert plan.state_at(epoch + timedelta(seconds=10)) == {}


def test_all_associated_ground_stations_receive_ut_classifier(epoch, network_factory):
    network = network_factory(lambda seconds: [('g', 'a'), ('h', 'a'), ('i', 'a'), ('a', 'b')])
    network.scene.create_ground_station('h', latitude_deg=0, longitude_deg=.1)
    network.scene.create_ground_station('i', latitude_deg=0, longitude_deg=-.2)
    network.scene.data['experiment'] = {'zone': 'z', 'ut_address': '192.0.2.2'}
    class Services:
        def assign_service(self, scene, snapshot):
            return {'z': 'b'}
    network.set_service_assignment_model(Services)
    network.set_routing_hook(TerminalRoutingModel, model_options={'ground_stations_per_zone': 3})
    assert network.routing_model.ground_station_associations(network.scene)['z'] == ('g', 'h', 'i')
    network.generate_plans(epoch, epoch + timedelta(seconds=5))
    tables = network.base_plans[0].state_at(epoch)
    for ground in ('g', 'h', 'i'):
        ingress = next(rule for rule in tables[ground] if rule.destination.kind == 'prefix')
        zone = next(rule for rule in tables[ground] if rule.destination.identity == 'z')
        assert ingress.exit == zone.exit
        assert ingress.outgoing_label == zone.outgoing_label
