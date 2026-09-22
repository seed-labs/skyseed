"""Runner-owned fixed local-output injection; independent of forwarding Plans."""
import errno
import os
from pathlib import Path

from skyseed_v2.backend.linux import network
from skyseed_v2.backend.execution_materials.mechanics import _ZONE_POOL

_LOCAL_OUTPUT = 'sv2localout'
_LOCAL_INPUT = 'sv2localin'
_LOCAL_PORT = 0xffffffff  # Origin marker, never an egress binding or LogicalExit.
_LOCAL_TABLE = 0x535632
_LOCAL_PROTOCOL = 253
_LOCAL_GATEWAY = '169.254.0.1'  # On-link neighbour only, not a node/assigned address.
_COMMON_MAC = '02:53:4b:59:00:01'


def _business_ranges(node_range, zone_prefixes):
    return (node_range, *((str(_ZONE_POOL),) if zone_prefixes else ()))


def _audit_local_ingress(node, ranges, routes):
    node['netlink'].audit_local_ingress(_LOCAL_TABLE, tuple(range(1, len(ranges) + 1)), routes)
    original = os.open('/proc/self/ns/net', os.O_RDONLY | os.O_CLOEXEC)
    try:
        network._setns(node['namespace_fd'])
        if Path('/proc/sys/net/ipv4/conf/all/rp_filter').read_text().strip() != '0':
            raise RuntimeError('BPF local ingress requires SEED net.ipv4.conf.all.rp_filter=0')
    finally:
        network._setns(original)
        os.close(original)


def _create_local_ingress(node, ranges, program_fd, bind):
    channel = node['netlink']
    node['local_ingress'] = owned = {'pair': True, 'routes': [], 'rules': []}
    try:
        channel.create_veth(_LOCAL_OUTPUT, _LOCAL_INPUT, node['namespace_fd'], mac=_COMMON_MAC)
    except OSError as error:
        if error.errno == errno.EEXIST:
            owned['pair'] = False
        raise
    output, ingress = (channel.link_index(name) for name in (_LOCAL_OUTPUT, _LOCAL_INPUT))
    owned.update(output=output, ingress=ingress)
    channel.local_ingress_ipv4(ingress)
    channel.attach_bpf(ingress, program_fd)
    bind(node['maps']['ingress_bindings'], ingress, _LOCAL_PORT)
    channel.set_link(ingress, up=True)
    channel.set_link(output, up=True)
    channel.neighbour(output, _LOCAL_GATEWAY, _COMMON_MAC)
    for priority, destination in enumerate(ranges, 1):
        route = dict(table=_LOCAL_TABLE, gateway=_LOCAL_GATEWAY,
                     prefsrc=node['node_address'], protocol=_LOCAL_PROTOCOL)
        owned['routes'].append((destination, route))
        try:
            channel.local_output_route(output, destination, **route)
        except OSError as error:
            if error.errno == errno.EEXIST:
                owned['routes'].pop()
            raise
        rule = dict(table=_LOCAL_TABLE, priority=priority, protocol=_LOCAL_PROTOCOL)
        owned['rules'].append((destination, rule))
        try:
            channel.local_output_rule(destination, **rule)
        except OSError as error:
            if error.errno == errno.EEXIST:
                owned['rules'].pop()
            raise


def _delete_local_ingress(node):
    owned = node.get('local_ingress')
    if owned is None:
        return
    channel = node['netlink']
    # Stop steering first; exact recorded requests survive uncertain/failed ACKs.
    for field, operation in (('rules', channel.local_output_rule), ('routes', channel.local_output_route)):
        while owned[field]:
            destination, arguments = owned[field][-1]
            try:
                if field == 'rules':
                    operation(destination, **arguments, delete=True)
                else:
                    operation(owned['output'], destination, **arguments, delete=True)
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ESRCH, errno.ENODEV):
                    raise
            owned[field].pop()
    if owned['pair']:
        try:
            output = owned.get('output')
            if output is None:
                output = channel.link_index(_LOCAL_OUTPUT)
            channel.delete_link(output)  # Removes both ends, the fixed neighbour and TC attachment.
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENODEV):
                raise
    del node['local_ingress']
