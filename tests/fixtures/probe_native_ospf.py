"""Opt-in live probes for a generated five-node scene; always drain and clean up."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from skyseed.experiment._control import _request, _ControlUnavailable


def probe(output, daemon, evidence):
    output, evidence = Path(output).resolve(), Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    addresses = json.loads((output / 'inputs/provenance.json').read_text())['scenario_parameters']['stable_addresses']
    foundation = json.loads((output / 'results/compiler/runner-artifact/manifest.json').read_text())['dataplane']
    service, = foundation['zone_addresses']
    project = (output / 'deployment/compose-project-name').read_text().strip()
    nodes = {n.rsplit('/', 1)[-1]: n for n in addresses}
    def command(node, *args):
        container = 'as65040' + ('ext-' if node in 'abc' else 'r-') + node
        result = subprocess.run(['docker', 'exec', container, *args], capture_output=True, text=True)
        return {'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    def ping(source, destination):
        return command(source, 'ping', '-n', '-I', addresses[nodes[source]], '-c', '2', '-i', '0.2', '-W', '1', destination)
    def snapshot(node):
        result = {name: command(node, 'ip', '-j', name) for name in ('link', 'address', 'route')}
        result['processes'] = command(node, 'ps', '-eo', 'pid,args')
        if node in 'abc':
            result['protocol'] = command(node, *(('vtysh', '-c', 'show ip ospf neighbor', '-c',
                'show ip ospf interface', '-c', 'show ip route ospf', '-c', 'show ip ospf database external')
                if daemon == 'frr' else ('birdc', 'show protocols all')))
            if daemon == 'frr':
                result['route_maps'] = command(node, 'vtysh', '-d', 'ospfd', '-c', 'show route-map')
            if daemon == 'bird':
                result['ospf'] = command(node, 'birdc', 'show ospf neighbors')
                result['rib'] = command(node, 'birdc', 'show route all')
        return result
    seen, checks = set(), []
    with (evidence / f'{daemon}-live.jsonl').open('w') as records, (evidence / f'{daemon}-run.log').open('w') as log:
        def record(kind, **facts):
            records.write(json.dumps({'kind': kind, 'wall': time.time(), **facts}) + '\n'); records.flush()
        record('packages', result=command('a', 'sh', '-c', 'dpkg-query -W frr bird2 2>/dev/null'))
        process = subprocess.Popen([str(output / 'run.sh')], stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'SKYSEED_NO_TOOLS': '1'})
        try:
            last = None
            while process.poll() is None:
                try:
                    status = _request(output, 'runtime', 'status')
                except _ControlUnavailable:
                    time.sleep(.5); continue
                frontier = (status.get('runner') or {}).get('active_frontier')
                if frontier and frontier != last:
                    record('frontier', status=status); last = frontier
                if frontier:
                    offset = (datetime.fromisoformat(frontier) - datetime.fromisoformat('2026-01-01T00:00:00+00:00')).total_seconds()
                    for stage in (75, 105, 165, 195, 275, 335, 380):
                        if offset < stage or stage in seen:
                            continue
                        seen.add(stage)
                        with ThreadPoolExecutor(max_workers=5) as pool:
                            facts = dict(zip(nodes, pool.map(snapshot, nodes)))
                            requests = [('a-c', 'a', addresses[nodes['c']], stage != 195),
                                        ('g-c', 'g', addresses[nodes['c']], stage != 105),
                                        ('c-g', 'c', addresses[nodes['g']], stage != 105),
                                        ('a-service', 'a', service, stage in (75, 105, 335, 380))]
                            results = list(pool.map(lambda r: ping(r[1], r[2]), requests))
                        for request, result in zip(requests, results):
                            checks.append({'stage': stage, 'name': request[0], 'expected': request[3],
                                           'passed': (result['exit'] == 0) == request[3]})
                        record('checkpoint', stage=stage, frontier=frontier, nodes=facts,
                               pings=dict(zip((r[0] for r in requests), results)))
                        print(daemon, stage, checks[-4:], flush=True)
                time.sleep(.5)
            record('runtime-exit', code=process.wait())
        finally:
            if process.poll() is None:
                record('cancel-runtime', result=_request(output, 'runtime', 'cancel-runtime'))
                process.wait()
            record('post-runtime', nodes={n: snapshot(n) for n in nodes})
            for n in 'abc':
                result = subprocess.run(['docker', 'logs', 'as65040ext-' + n], capture_output=True, text=True)
                (evidence / f'{daemon}-daemon-{n}.log').write_text(result.stdout + result.stderr)
            with (evidence / f'{daemon}-cleanup.log').open('w') as cleanup_log:
                cleanup = subprocess.run([str(output / 'cleanup.sh')], stdout=cleanup_log, stderr=subprocess.STDOUT)
            objects = {kind: subprocess.check_output(['docker', *args, '--filter',
                       'label=com.docker.compose.project=' + project], text=True).strip()
                       for kind, args in (('containers', ['ps', '-aq']), ('networks', ['network', 'ls', '-q']))}
            record('cleanup', code=cleanup.returncode, objects=objects)
        summary = {'daemon': daemon, 'stages': sorted(seen), 'checks': checks,
                   'runtime_exit': process.returncode, 'cleanup_exit': cleanup.returncode, 'remaining_objects': objects}
        (evidence / f'{daemon}-live-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        assert seen == {75, 105, 165, 195, 275, 335, 380}
        assert all(c['passed'] for c in checks), summary
        assert process.returncode == cleanup.returncode == 0 and not any(objects.values()), summary


if __name__ == '__main__':
    probe(*sys.argv[1:])
