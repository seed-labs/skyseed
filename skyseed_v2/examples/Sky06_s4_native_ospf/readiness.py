"""Editable, one-shot B0 OSPF readiness policy; no simulation or traffic owner."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
import tempfile
from threading import Event
from time import monotonic

from skyseed_v2.backend.linux.environment import NativeRoutingEnvironment
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    OperationalConnectionKind,
    LocalRouteOutcome, GroundStationRouteDemandTarget, ZoneRouteDemandTarget, RouteDisposition,
)


def _node(key):
    asn, kind, name = key.split('/', 2)
    return NodeId(int(asn), kind, name)


def initial_criterion(context, *, sample_limit=4):
    """Use the initial prepared facts supplied once by the execution owner."""
    if not 1 <= sample_limit <= 4:
        raise ValueError('readiness samples must be between one and four')
    runner = context.runner_snapshot['runner']
    state, manifest = context.initial_state, context.runtime_manifest
    timestamp = state.timestamp.isoformat()
    if (manifest['backend'] != 'linux' or runner['activation_count'] != 1
            or runner['frontiers']['last_active'] != timestamp or manifest['timeline']['start'] != timestamp):
        raise ValueError('readiness requires the actual initial Linux activation, before formal time')
    addresses = {_node(row['key']): row['node_address'] for row in manifest['dataplane']['nodes']}
    zones = {tuple(key): prefix for key, prefix in manifest['zone_prefixes']}
    environment = NativeRoutingEnvironment(addresses)
    if dict(environment.node_addresses) != addresses:
        raise ValueError('native manifest identity allocation differs from the current interface environment')
    bindings = {row['node_id']: row['container_id'] for row in runner['runtime_foundation']['bindings']}
    if set(bindings) != {node.value for node in addresses}:
        raise ValueError('prepared runtime bindings do not cover the native manifest identities')
    neighbors = {node.value: [] for node in addresses if node.entity_kind == 'satellite'}
    connections = {row.connection_id for row in state.operational_connections}
    for connection in connections:
        if connection.kind is OperationalConnectionKind.INTER_SATELLITE:
            for node, peer in ((connection.endpoint_a, connection.endpoint_b), (connection.endpoint_b, connection.endpoint_a)):
                neighbors[node.value].append({'router_id': addresses[peer],
                    'interface': environment.interface_name(connection, node), 'peer': peer.value})
    if not any(neighbors.values()):
        raise ValueError('B0 has no expected ISL adjacency; readiness would be vacuous')
    for values in neighbors.values():
        values.sort(key=lambda row: (row['router_id'], row['interface']))
    services = {row.service_key: row.satellite_id for row in state.zone_service_assignments if row.is_served}
    local = [row for row in state.route_outcomes if isinstance(row, LocalRouteOutcome)
             and row.disposition is RouteDisposition.FORWARD]
    returns = {}
    for row in local:
        if isinstance(row.demand.target, GroundStationRouteDemandTarget):
            returns.setdefault((row.demand.source, row.demand.target.ground_station_id), []).extend(
                candidate.exit for candidate in row.candidates if candidate.exit.connection in connections)
    candidates = []
    unavailable = []
    for row in local:
        gs, target = row.demand.source, row.demand.target
        if gs.entity_kind != 'ground-station' or not isinstance(target, ZoneRouteDemandTarget):
            continue
        if target.service_key not in services:
            unavailable.append({'gs': gs.value, 'zone': target.service_key, 'reason': 'unserved'})
            continue
        exits = [candidate.exit for candidate in row.candidates
                 if candidate.exit.connection in connections
                 and candidate.exit.connection.kind is OperationalConnectionKind.GROUND_STATION_ACCESS]
        admitted = False
        for edge in exits:
            if any(ret.connection == edge.connection for ret in returns.get((edge.peer, gs), ())):
                candidates.append((gs, target.service_key, services[target.service_key], edge))
                admitted = True
        if not admitted:
            unavailable.append({'gs': gs.value, 'zone': target.service_key, 'reason': 'missing access or return edge'})
    candidates.sort(key=lambda item: (item[0].value, item[1], item[3].peer.value))
    if not candidates:
        raise ValueError(f'B0 has no GS–Zone candidate with service, actual access and return edge: {unavailable[:8]}')
    # Evenly spaced indices cover the stable sorted candidate population; the
    # selected facts are recorded, and do not depend on Traffic or geography.
    count = min(sample_limit, len(candidates))
    indices = sorted({round(i * (len(candidates) - 1) / max(count - 1, 1)) for i in range(count)})
    samples, routes = [], []
    for index in indices:
        gs, zone, service, access = candidates[index]
        uplink = access.peer
        gs_device = environment.interface_name(access.connection, gs)
        return_device = environment.interface_name(access.connection, uplink)
        sample = {'gs': gs.value, 'zone': zone, 'zone_prefix': zones[zone], 'service': service.value,
                  'uplink': uplink.value, 'access_interface': gs_device, 'return_interface': return_device}
        samples.append(sample)
        def route(node, prefix, devices, *, local=False):
            value = {'node': node.value, 'destination': prefix, 'interfaces': sorted(set(devices)), 'local': local}
            if value not in routes:
                routes.append(value)
        route(gs, zones[zone], [gs_device])
        route(uplink, zones[zone], [r['interface'] for r in neighbors[uplink.value]], local=uplink == service)
        route(uplink, addresses[gs] + '/32', [return_device])
        service_exits = [r['interface'] for r in neighbors[service.value]]
        service_exits.extend(environment.interface_name(edge.connection, service)
                             for edge in returns.get((service, gs), ()))
        route(service, addresses[gs] + '/32', service_exits)
    return {'timestamp': timestamp, 'bindings': bindings, 'neighbors': neighbors,
            'candidate_count': len(candidates), 'unavailable_candidates': unavailable,
            'samples': samples, 'routes': routes, 'identity_interface': manifest['dataplane']['identity_interface']}


def neighbor_rows(text):
    """Read the BIRD neighbor command's ordinary table."""
    lines = text.splitlines()
    if not any('Router ID' in line and 'Interface' in line for line in lines):
        raise ValueError('BIRD neighbor output is missing its table header')
    rows = []
    table = False
    for line in lines:
        if 'Router ID' in line and 'Interface' in line:
            table = True
            continue
        if not table or not line.strip():
            continue
        parts = line.split()
        if len(parts) != 6:
            raise ValueError(f'BIRD neighbor row malformed: {line}')
        rows.append({'router_id': parts[0], 'state': parts[2], 'interface': parts[4]})
    return rows


def route_present(expected, rows, identity_interface):
    destination = expected['destination'].removesuffix('/32')
    for row in rows:
        if row.get('dst', '').removesuffix('/32') != destination:
            continue
        if row.get('type') == 'local':
            if expected['local'] and row.get('dev') in (identity_interface, 'lo'):
                return True
            continue
        if expected['local'] or row.get('type', 'unicast') != 'unicast':
            continue
        if {'dead', 'linkdown'} & set(row.get('flags', ())):
            continue
        if any(hop.get('dev') in expected['interfaces'] and not ({'dead', 'linkdown'} & set(hop.get('flags', ())))
               for hop in row.get('nexthops', [row])):
            return True
    return False


# stdin remains open while the host owns this query. EOF asks the in-container
# wrapper to stop and reap its current child before Docker exec returns. Killing
# only a Docker client would leave a blocked vtysh/ip process in the container.
QUERY_WRAPPER = r'''
exec 3<&0
"$@" </dev/null &
command_pid=$!
# The shell builtin read needs no descendant process of its own. stdin EOF is
# the cancellation channel; ordinary query commands (ip/birdc) are leaves.
(
    while IFS= read -r line <&3; do :; done
    kill -TERM "$command_pid" 2>/dev/null || :
) &
watcher_pid=$!
wait "$command_pid"
result=$?
kill -TERM "$watcher_pid" 2>/dev/null || :
wait "$watcher_pid" 2>/dev/null || :
exit "$result"
'''


def query(container, commands, stop):
    results = []
    for command in commands:
        if stop.is_set():
            return None
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            client = subprocess.Popen(['docker', 'exec', '-i', container, 'sh', '-c', QUERY_WRAPPER,
                'native-readiness-query', *command], stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                start_new_session=True)
            try:
                while client.poll() is None:
                    if stop.wait(.05) and not client.stdin.closed:
                        client.stdin.close()
                if stop.is_set():
                    return None
                stdout.seek(0); stderr.seek(0)
                row = {'argv': command, 'exit_code': client.returncode,
                       'stdout': stdout.read().decode(), 'stderr': stderr.read().decode()}
                if row['exit_code'] or row['stderr'].strip():
                    raise RuntimeError(f'native query failed on {container}: {row}')
                results.append(row)
            finally:
                client.stdin.close()
                client.wait()  # EOF drains the remote command even on a local exception.
    return results


def wait_ready(criterion, directory, stop, *, query_fn=query, interval=2.0):
    routes = {}
    for row in criterion['routes']:
        routes.setdefault(row['node'], []).append(row)
    nodes = sorted(set(criterion['neighbors']) | set(routes))
    neighbor_command = ['birdc', 'show', 'ospf', 'neighbors']
    commands = {node: ([neighbor_command] if node in criterion['neighbors'] else [])
                      + ([['ip', '-j', '-4', 'route', 'show', 'table', 'all']] if node in routes else [])
                for node in nodes}
    started = monotonic()
    poll = 0
    with (directory / 'native.jsonl').open('a') as evidence, ThreadPoolExecutor(max_workers=4) as workers:
        while not stop.is_set():
            poll += 1
            pending, errors, observations = [], [], {}
            futures = {node: workers.submit(query_fn, criterion['bindings'][node], commands[node], stop) for node in nodes}
            for node, future in futures.items():
                result = None
                try:
                    result = future.result()
                    if result is None:
                        continue
                    if node in criterion['neighbors']:
                        actual = neighbor_rows(result[0]['stdout'])
                        observations.setdefault(node, {})['neighbors'] = actual
                        for expected in criterion['neighbors'][node]:
                            if not any(row['router_id'] == expected['router_id'] and row['interface'] == expected['interface']
                                       and row['state'].split('/', 1)[0] == 'Full' for row in actual):
                                pending.append({'node': node, 'neighbor': expected, 'observed': actual})
                    if node in routes:
                        actual = json.loads(result[-1]['stdout'])
                        if not isinstance(actual, list):
                            raise ValueError('kernel route response must be a list')
                        destinations = {row['destination'].removesuffix('/32') for row in routes[node]}
                        observations.setdefault(node, {})['routes'] = [row for row in actual
                            if row.get('dst', '').removesuffix('/32') in destinations]
                        for expected in routes[node]:
                            if not route_present(expected, actual, criterion['identity_interface']):
                                pending.append({'node': node, 'route': expected})
                except Exception as error:
                    errors.append({'node': node, 'error': f'{type(error).__name__}: {error}', 'response': result})
                    stop.set()  # Drain already accepted queries and prevent queued launches.
            row = {'poll': poll, 'timestamp': datetime.now(timezone.utc).isoformat(),
                   'elapsed_seconds': monotonic() - started, 'pending': pending, 'errors': errors, 'observations': observations,
                   'status': 'failed' if errors else 'cancelled' if stop.is_set() else 'waiting' if pending else 'ready'}
            evidence.write(json.dumps(row) + '\n'); evidence.flush()
            print(f"native readiness {row['status']}: {len(pending)} missing, {len(errors)} command/configuration errors", flush=True)
            if errors:
                raise RuntimeError(f'native readiness command/configuration errors: {errors}')
            if stop.is_set():
                return False
            if not pending:
                return True
            stop.wait(interval)
    return False


def prepare(context):
    """Example-owned OSPF criterion, called once before Traffic and formal time."""
    stop = Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        criterion = initial_criterion(context)
        directory = context.invocation_directory / 'readiness'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'criterion.json').write_text(json.dumps(criterion, indent=2) + '\n')
        if not wait_ready(criterion, directory, stop):
            raise SystemExit(143)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
