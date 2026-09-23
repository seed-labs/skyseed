"""Read-only preview of fixed communications, without route suitability work."""
from html import escape
import json
from pathlib import Path
from tools.observation._presentation.html import standalone_page
from .workload import read_workload


def write_traffic_preview(output, *, publication=None):
    output = Path(output)
    workload = read_workload(output / 'results/traffic/workload.json')
    rows = ''.join('<tr>' + ''.join('<td>' + escape(str(value)) + '</td>' for value in
        (c.communication_id, c.ground_station_id, c.network_scope or 'unique runtime scope', c.zone_id, c.direction)) + '</tr>'
        for c in workload.communications)
    body = f'''<p>Fixed logical communications. This page sends no packets and evaluates no routes.</p>
<p>Sending interval: {workload.interval_seconds:g} seconds. Intended duration: {workload.duration_seconds:g} wall seconds from runtime start.
Pairs persist across material revisions; temporary unreachability is recorded as observation.</p>
<table><thead><tr><th>Communication</th><th>Ground station</th><th>Scope</th><th>Zone</th><th>Direction</th></tr></thead><tbody>{rows}</tbody></table>'''
    root = output / 'visualizations/traffic-generation'; root.mkdir(parents=True, exist_ok=True)
    (root / 'index.html').write_text(standalone_page(title='Traffic workload', eyebrow='Configured communications', body=body))
    (root / 'workload.json').write_text(json.dumps(workload.artifact(), indent=2) + '\n')
    return {'status': 'PASS', 'message': f'{len(workload.communications)} fixed communications'}
