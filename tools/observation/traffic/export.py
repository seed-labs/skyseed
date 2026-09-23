"""Export observed communications without planned route conformance."""
import json
from pathlib import Path


def write_traffic_export(generated, output, origin=None, path_ids=None):
    generated, output = Path(generated), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    workload = generated / 'results/traffic/workload.json'
    config = json.loads(workload.read_text()) if workload.exists() else {}
    replay = generated / 'visualizations/traffic/traffic-replay.json'
    data = json.loads(replay.read_text()) if replay.exists() else {}
    events = data.get('events', [])
    # Historical files stay intact; only explicit actual evidence is exported.
    value = {'schema_version': 2, 'authority': 'observed-traffic',
        'status': 'available' if replay.exists() else 'unavailable',
        'communications': config.get('communications', data.get('communications', [])),
        'events': events, 'evidence_limit': data.get('evidence_limit', 'No recorded observations available')}
    (output / 'index.json').write_text(json.dumps(value, separators=(',', ':'))+'\n')
    (output / 'README.md').write_text('# Observed Traffic\n\nCommunication identities remain fixed. Events retain recorded times, replies, migrations and supported hops. Missing hops and unknown outcomes are not filled from planned routes. Opening this export is read-only.\n')
    return {'status': value['status'], 'flows': len(value['communications']), 'transactions': len(events)}
