"""Example-owned BIRD rendering and node descriptions; SEED owns files/startup."""
from pathlib import Path
from string import Template
from skyseed.simulator import OperationalConnectionId, OperationalConnectionKind

CONFIG_DIRECTORY = Path(__file__).with_name('config')


def describe_node_identities(system, static_input, environment):
    """Expose authoritative stable /32s through SEED's existing Map description."""
    nodes = [(satellite.node_id, system.get_satellite(satellite.satellite_id))
             for satellite in static_input.list_satellites()]
    nodes.extend((station.node_id, system.get_ground_station(station.ground_station_id))
                 for station in static_input.list_ground_stations())
    for identity, node in nodes:
        description = node.getDescription()
        node.setDescription((description + '\n' if description else '')
            + f"Stable node identity IPv4: {environment.node_addresses[identity]}/32. "
              "Zone service addresses are separate and may migrate between satellites.")


class BirdConfigRenderer:
    """Load editable templates once and share GS prefix facts across satellites."""
    def __init__(self, directory=CONFIG_DIRECTORY):
        self.template = Template((Path(directory) / 'bird.conf.template').read_text())
        self.environment = None

    def __call__(self, node, environment):
        if self.environment is None:
            self.environment = environment
            self.stations = tuple(n for n in environment.node_addresses if n.entity_kind == 'ground-station')
            prefixes = tuple(environment.node_addresses[gs] + '/32' for gs in self.stations)
            self.shared = dict(
                route_protocol=environment.route_protocol,
                identity_interface=environment.identity_interface,
                gs_prefix_set=', '.join(prefixes))
        elif self.environment is not environment:
            raise ValueError('use one configuration renderer per native environment')
        # Interface names encode each endpoint pair, not its connection kind.
        # Each satellite therefore needs its own possible GS-access names to
        # exclude/passivate them before dynamic creation. Prefix data is shared.
        interfaces = tuple(environment.interface_name(OperationalConnectionId(
            OperationalConnectionKind.GROUND_STATION_ACCESS, node, gs), node) for gs in self.stations)
        values = dict(self.shared, identity=environment.node_addresses[node])
        values['gs_interface_exclusions'] = ''.join('-"' + name + '", ' for name in interfaces)
        return self.template.substitute(values)
