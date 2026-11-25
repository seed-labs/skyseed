"""Prepare native routing daemons through ordinary SEED Node facilities."""
from seedemu.core import BaseSystem


def configure_native_software(node, *, daemon, configuration):
    """Prepare one node from concrete BIRD text or FRR daemon configurations.

    The caller owns protocol policy and must not also give this node to a Routing
    Layer. BIRD authors include /etc/skyseed/bird-kernel-options.conf in their
    kernel protocol; FRR authors can override the prefixed Zebra default later.
    """
    if getattr(node, '_skyseed_native_software', None) is not None:
        raise ValueError('routing software is already prepared on this node')
    if daemon == 'bird':
        valid = isinstance(configuration, str) and bool(configuration.strip())
    elif daemon == 'frr':
        supported = {'zebra', 'ospfd', 'ospf6d', 'bgpd', 'ripd', 'ripngd',
                     'isisd', 'pimd', 'ldpd', 'staticd', 'babeld', 'bfdd'}
        valid = (isinstance(configuration, dict) and 'zebra' in configuration
                 and set(configuration) <= supported
                 and all(isinstance(v, str) and v.strip() for v in configuration.values()))
    else:
        valid = False
    if not valid:
        raise ValueError('expected bird text or frr daemon configurations including zebra')

    # Validate the complete input before any Node mutation. Installation uses
    # public Node methods; no Routing layer or SEED convenience API is required.
    if daemon == 'bird':
        node.addBuildCommand('mkdir -p /usr/share/doc/bird2/examples/')
        node.addBuildCommand('touch /usr/share/doc/bird2/examples/bird.conf')
        node.addSoftware('bird2')
    else:
        node.addSoftware('frr')
    base = node.getBaseSystem()
    if base != BaseSystem.SEEDEMU_ROUTER and not base.contains(BaseSystem.SEEDEMU_ROUTER):
        node.setBaseSystem(BaseSystem.SEEDEMU_ROUTER)

    if daemon == 'bird':
        node.setFile('/etc/bird/bird.conf', configuration)
        node.appendStartCommand('mkdir -p /run/bird')
        node.appendStartCommand('bird -f -c /etc/bird/bird.conf', fork=True)
        node.setFile('/etc/skyseed/bird-kernel-options.conf', 'netlink rx buffer 4194304;\n')
    else:
        configuration = dict(configuration)
        configuration['zebra'] = 'no zebra nexthop kernel enable\n' + configuration['zebra']
        for name, text in configuration.items():
            node.setFile(f'/etc/frr/{name}.conf', text)
        node.appendStartCommand('mkdir -p /run/frr && chown frr:frr /run/frr')
        node.appendStartCommand('/usr/lib/frr/zebra -f /etc/frr/zebra.conf', fork=True)
        for name in configuration:
            if name != 'zebra':
                node.appendStartCommand(
                    'while [ ! -S /run/frr/zserv.api ]; do sleep 0.1; done; '
                    f'exec /usr/lib/frr/{name} -f /etc/frr/{name}.conf', fork=True)
    node._skyseed_native_software = daemon
