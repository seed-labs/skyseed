"""Small timeline and globe projection of actual communication observations."""
from datetime import datetime
from ._spatial import _observed_steps


def project_traffic_replay(observation):
    source = observation.snapshot
    records, telemetry = source['records'], source['telemetry']
    frames = source['frames']
    nodes = [{'node_id': p['node_id'], 'kind': p['kind']} for p in frames[0]['positions']] if frames else []
    indices = {n['node_id']: i for i, n in enumerate(nodes)}
    geometry = [{'timestamp': f['timestamp'], 'positions': [[indices[p['node_id']], *(p['ecef_m'][axis] for axis in ('x','y','z'))] for p in f['positions']]} for f in frames]
    communications = {c['communication_id']: c for c in source['workload'].get('communications', ())}
    # Historic identities are displayed as recorded; selection is never rerun.
    for c in source['workload'].get('probe_identities', ()):
        communications.setdefault(c['probe_id'], {**c, 'communication_id': c['probe_id']})
    events = []
    for row in records:
        identity = row.get('communication_id', row.get('probe_id', row.get('episode_id')))
        if identity is None:
            continue
        communications.setdefault(identity, {'communication_id': identity})
        events.append({**row, 'communication_id': identity,
                       'time_ns': row.get('host_monotonic_ns'), 'hops': []})
    if telemetry:
        packets = telemetry['packets']
        timestamps = [datetime.fromisoformat(f['timestamp']).timestamp() for f in frames]
        for ordinal, transaction in enumerate(telemetry['ping_transactions']):
            generator = transaction.get('generator') or {}
            identity = generator.get('episode_id') or 'unassociated-packets'
            communications.setdefault(identity, {'communication_id': identity})
            for direction in ('request', 'reply'):
                steps = _observed_steps(transaction, direction, packets, indices, timestamps)
                for step in steps:
                    packet = packets[step['packet']]
                    events.append({'record_type': 'packet_observation', 'communication_id': identity,
                        'packet_id': f'{ordinal}:{direction}', 'icmp_sequence': transaction['sequence'],
                        'direction': direction, 'time_ns': packet['kernel_monotonic_ns'],
                        'published_active_frontier': packet.get('formal_boundary_timestamp'),
                        'simulation_timestamp': packet.get('simulation_timestamp'), 'hops': [step],
                        'association_uncertainty': transaction['association_uncertainty']})
    events.sort(key=lambda e: (e['time_ns'] is None, e['time_ns'] or 0))
    origin = min((e['time_ns'] for e in events if e['time_ns'] is not None), default=0)
    for event in events:
        event['seconds'] = None if event['time_ns'] is None else (event['time_ns']-origin)/1e9
    return {'format_version': 1, 'nodes': nodes, 'frames': geometry,
            'communications': list(communications.values()), 'events': events,
            'evidence_limit': ('Only recorded BPF ingress supports hop arrivals; gaps and ambiguous associations remain unknown.' if telemetry else
                              'Linux sender observations contain no intermediate per-packet transit evidence.'),
            'telemetry_complete': None if telemetry is None else telemetry['telemetry_complete'],
            'telemetry_losses': None if telemetry is None else telemetry['telemetry_losses']}
