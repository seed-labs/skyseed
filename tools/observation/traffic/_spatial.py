"""Project only ingress-supported observed hops; preserve gaps."""
from bisect import bisect_right
_VALID_IDENTITIES = {"exact-generation", "in-flight-boundary"}

def _observed_steps(transaction, direction, packets, node_index, timestamps):
    """One step per packet, including repeated visits; ingress proves arrival.

    A redirect is intent at this node, never arrival at the next. Association
    ambiguity prevents connecting the candidate observations into a traversal.
    """
    steps = []
    uncertain = bool(transaction.get('association_uncertainty'))
    previous = None
    indices = transaction.get(direction + '_packet_indices', ())
    for index in sorted(indices, key=lambda i: (packets[i]['kernel_monotonic_ns'], i)):
        packet = packets[index]
        ingress = packet.get('ingress_identity') or {}
        egress = packet.get('egress_identity') or {}
        peer = node_index.get(ingress.get('peer_node_id'))
        current = node_index.get(packet.get('node_id'))
        local_origin = packet.get('local_origin') is True and packet.get('observation') == 'local-output'
        arrival = (not uncertain and packet.get('observation') == 'ingress'
                   and ingress.get('validity') in _VALID_IDENTITIES
                   and ingress.get('connection_id') is not None
                   and peer is not None and current is not None)
        timestamp = packet.get('simulation_timestamp')
        # ISO timestamps in the projection have one normalized UTC basis.
        from datetime import datetime
        seconds = None if timestamp is None else datetime.fromisoformat(timestamp).timestamp()
        frame = None if seconds is None else bisect_right(timestamps, seconds) - 1
        if frame is not None and frame < 0:
            frame = None
        gap = ('ambiguous transaction association' if uncertain else
               'missing or unresolved ingress evidence' if not arrival and not local_origin else
               'missing observations between prior node and ingress peer'
               if not local_origin and previous is not None and previous != peer else None)
        steps.append({'packet': index, 'node': current, 'ingress_peer': peer,
                      'arrival_supported': arrival, 'local_origin': local_origin, 'gap_before': gap,
                      'egress_peer': node_index.get(egress.get('peer_node_id')),
                      'decision': packet.get('decision'), 'reason': packet.get('reason'),
                      'observed_time': timestamp, 'frame': frame})
        previous = current
    if steps and steps[-1]['decision'] == 'redirect':
        steps[-1]['gap_after'] = 'redirect recorded; arrival at the egress peer is unobserved'
    return steps
