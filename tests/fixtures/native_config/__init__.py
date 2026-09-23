"""Dual-daemon validation policy retained from the September native campaigns."""
from pathlib import Path
from string import Template
from ipaddress import collapse_addresses, ip_network
from skyseed.simulator import OperationalConnectionId, OperationalConnectionKind

CONFIG_DIRECTORY = Path(__file__).with_name('config')


class FixtureConfigRenderer:
    """Load editable templates once and share GS prefix facts across satellites."""
    def __init__(self, daemon, directory=CONFIG_DIRECTORY):
        if daemon not in ('frr', 'bird'):
            raise ValueError('choose frr or bird')
        self.daemon = daemon
        names = ('bird',) if daemon == 'bird' else ('frr-zebra', 'frr-ospfd')
        self.templates = {name: Template((Path(directory) / (name + '.conf.template')).read_text())
                          for name in names}
        self.environment = None

    def __call__(self, node, environment):
        if self.environment is None:
            self.environment = environment
            self.stations = tuple(n for n in environment.node_addresses if n.entity_kind == 'ground-station')
            prefixes = tuple(environment.node_addresses[gs] + '/32' for gs in self.stations)
            # Preserve exactly the GS /32 set, including holes in the allocation.
            # FRR's ge 32 keeps a collapsed prefix from admitting shorter routes.
            frr_prefixes = tuple(collapse_addresses(map(ip_network, prefixes)))
            self.shared = dict(
                route_protocol=environment.route_protocol,
                identity_interface=environment.identity_interface,
                gs_prefix_set=', '.join(prefixes),
                gs_prefix_list='\n'.join(
                    f'ip prefix-list GS-IDENTITIES seq {i * 5} permit {p}'
                    + (' ge 32' if p.prefixlen < 32 else '')
                    for i, p in enumerate(frr_prefixes, 1)),
                link_networks='\n'.join(f' network {p} area 0' for p in environment.link_address_pools))
        elif self.environment is not environment:
            raise ValueError('use one configuration renderer per native environment')
        # Interface names encode each endpoint pair, not its connection kind.
        # Each satellite therefore needs its own possible GS-access names to
        # exclude/passivate them before dynamic creation. Prefix data is shared.
        interfaces = tuple(environment.interface_name(OperationalConnectionId(
            OperationalConnectionKind.GROUND_STATION_ACCESS, node, gs), node) for gs in self.stations)
        values = dict(self.shared, identity=environment.node_addresses[node])
        if self.daemon == 'bird':
            values['gs_interface_exclusions'] = ''.join('-"' + name + '", ' for name in interfaces)
            return self.templates['bird'].substitute(values)
        values['passive_interfaces'] = '\n'.join(f'interface {name}\n ip ospf passive\n!'
            for name in (environment.identity_interface, *interfaces))
        # FRR calls unknown Linux route protocols "kernel". Its OSPF
        # route-map supports prefix/interface matching, not metric matching.
        values['gs_edge_policy'] = '\n'.join(
            f'route-map GS-EDGES permit {i * 5}\n match ip address prefix-list GS-IDENTITIES\n match interface {name}\n!'
            for i, name in enumerate(interfaces, 1))
        return {name: self.templates['frr-' + name].substitute(values) for name in ('zebra', 'ospfd')}
