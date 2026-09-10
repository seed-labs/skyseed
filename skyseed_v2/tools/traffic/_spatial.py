"""Compact private browser shards, derived only from retained Traffic evidence."""
from bisect import bisect_right
import json
from pathlib import Path
from tools._html import json_for_script

_TRANSACTION_CHUNK_SIZE = 25
_VALID_IDENTITIES = {'exact-generation', 'in-flight-boundary'}


def _node_id(value):
    return value if isinstance(value, str) else f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"


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
               'missing or unresolved ingress evidence' if not arrival else
               'missing observations between prior node and ingress peer'
               if previous is not None and previous != peer else None)
        steps.append({'packet': index, 'node': current, 'ingress_peer': peer,
                      'arrival_supported': arrival, 'gap_before': gap,
                      'egress_peer': node_index.get(egress.get('peer_node_id')),
                      'decision': packet.get('decision'), 'reason': packet.get('reason'),
                      'observed_time': timestamp, 'frame': frame})
        previous = current
    if steps and steps[-1]['decision'] == 'redirect':
        steps[-1]['gap_after'] = 'redirect recorded; arrival at the egress peer is unobserved'
    return steps


def _browser_index(data):
    nodes = data.get('shell_nodes') or [
        {'node_id': p['node_id'], 'asn': p['node']['asn'], 'kind': p['node']['entity_kind']}
        for p in data['physical_evidence']['frames'][0]['positions']]
    by_id = {n['node_id']: i for i, n in enumerate(nodes)}
    rows = []
    for i, tx in enumerate(data['transactions']):
        generator = tx.get('generator') or {}
        path = (tx.get('route_outcome') or {}).get('path', ())
        observed = [data['telemetry']['packets'][j].get('node_id') for j in
                    (*tx.get('request_packet_indices', ()), *tx.get('reply_packet_indices', ()))]
        peers = [data['telemetry']['packets'][j].get('ingress_identity') or {} for j in
                 (*tx.get('request_packet_indices', ()), *tx.get('reply_packet_indices', ()))]
        identities = {*observed, *(_node_id(n) for n in path),
                      *(p.get('peer_node_id') for p in peers if p.get('validity') in _VALID_IDENTITIES)}
        rows.append({'index': i, 'shard': i // _TRANSACTION_CHUNK_SIZE,
                     'label': f"{generator.get('episode_id', 'unassociated')} · segment {generator.get('segment_index', '?')} · ICMP {tx['identifier']}/{tx['sequence']}",
                     'boundary': tx['formal_boundary_timestamp'],
                     'nodes': sorted(by_id[n] for n in identities if n in by_id),
                     'classification': tx['classification'],
                     'request_time': (tx.get('request') or {}).get('simulation_timestamp'),
                     'episode_id': generator.get('episode_id'),
                     'probe_id': generator.get('probe_id'),
                     'segment_index': generator.get('segment_index'),
                     # Replay only after the displayed observations occurred.
                     # Missing timestamps remain outside timed playback.
                     'evidence_time': _evidence_time(tx, data['telemetry']['packets']),
                     'reply': tx['observed_reply']})
    return {'schema_version': 2, 'nodes': nodes, 'transactions': rows,
            'flows': _flow_index(data, rows),
            'times': [f['timestamp'] for f in data['physical_evidence']['frames']],
            'chunk_size': _TRANSACTION_CHUNK_SIZE,
            'frame_sampling': 'latest retained formal PhysicalFrame at or before the observation; no continuous position measurement or interpolation'}


def _evidence_time(transaction, packets):
    from datetime import datetime
    times = [packets[i].get('simulation_timestamp') for i in
             (*transaction.get('request_packet_indices', ()),
              *transaction.get('reply_packet_indices', ()))]
    if not times or any(t is None for t in times):
        return None
    return max(times, key=lambda t: datetime.fromisoformat(t))


def _flow_index(data, rows):
    """Retain authoritative episode/probe continuity, including segment changes.

    Unassociated transactions stay separate. Qualification-only episodes remain
    selectable with no animated success and their executor losses intact.
    """
    flows = {}
    for episode in (data.get('qualification') or {}).get('episodes', ()):
        key = json.dumps([episode['episode_id'], episode['probe_id']])
        if key in flows:
            raise ValueError('duplicate authoritative Traffic episode/probe')
        flows[key] = {name: episode.get(name) for name in
                      ('episode_id', 'probe_id', 'direction', 'zone_id',
                       'ground_station_id', 'sent', 'received', 'loss')}
        flows[key].update(key=key, transactions=[], segments=[])
    for row in rows:
        associated = (row['episode_id'] is not None and row['probe_id'] is not None
                      and row['segment_index'] is not None)
        key = json.dumps([row['episode_id'], row['probe_id']] if associated
                         else ['unassociated-transaction', row['index']])
        if key not in flows:
            flows[key] = {'key': key, 'episode_id': row['episode_id'] if associated else None,
                          'probe_id': row['probe_id'] if associated else None,
                          'transactions': [], 'segments': []}
        flow = flows[key]
        flow['transactions'].append(row['index'])
        if row['segment_index'] is not None and row['segment_index'] not in flow['segments']:
            flow['segments'].append(row['segment_index'])
        row['flow'] = key
    for flow in flows.values():
        flow['segments'].sort()
        flow['transactions'].sort(key=lambda i: (rows[i]['evidence_time'] is None,
                                                rows[i]['evidence_time'] or '', i))
        flow['label'] = ('Zone ' + str(flow['zone_id']) + ' ↔ ' + str(flow.get('ground_station_id'))
                         + ' · ' + str(flow.get('direction')) + ' · ' if flow.get('zone_id') else '') + (
                         flow.get('episode_id') or 'Unassociated transaction ' + str(flow['transactions'][0]))
    return list(flows.values())


def _write_spatial_assets(data, directory):
    from datetime import datetime
    from .renderer import _transaction_row
    root = Path(directory) / 'data'; root.mkdir(parents=True, exist_ok=True)
    index = _browser_index(data)
    nodes = {n['node_id']: i for i, n in enumerate(index['nodes'])}
    times = [datetime.fromisoformat(t).timestamp() for t in index['times']]
    paths = []
    def write(name, variable, value):
        path = root / name
        path.write_text(variable + '=' + json_for_script(value) + ';\n')
        paths.append(path)
    for i, frame in enumerate(data['physical_evidence']['frames']):
        positions = [[nodes[p['node_id']], *(p['ecef_m'][axis] for axis in ('x', 'y', 'z'))]
                     for p in frame['positions'] if p['node_id'] in nodes]
        write(f'frame-{i:03d}.js', 'window.SKYSEED_TRAFFIC_FRAME',
              {'index': i, 'timestamp': frame['timestamp'], 'positions': positions})
    for start in range(0, len(data['transactions']), _TRANSACTION_CHUNK_SIZE):
        shard = {}
        for i in range(start, min(start + _TRANSACTION_CHUNK_SIZE, len(data['transactions']))):
            tx = data['transactions'][i]
            route = tx.get('route_outcome') or {}
            shard[i] = {'index': i, 'html': _transaction_row(tx),
                        'association_uncertainty': tx.get('association_uncertainty', ()),
                        'request': _observed_steps(tx, 'request', data['telemetry']['packets'], nodes, times),
                        'reply': _observed_steps(tx, 'reply', data['telemetry']['packets'], nodes, times),
                        'planned': [nodes.get(_node_id(n)) for n in route.get('path', ())],
                        'planned_status': route.get('kind'), 'planned_reason': route.get('reason')}
        write(f'transactions-{start // _TRANSACTION_CHUNK_SIZE:03d}.js',
              'window.SKYSEED_TRAFFIC_TRANSACTIONS', shard)
    return tuple(paths)
