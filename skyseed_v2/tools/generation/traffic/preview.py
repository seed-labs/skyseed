"""Offline preview of exactly the sealed plans admitted by experiment execution."""
from html import escape
import json
from pathlib import Path
from tools.observation._presentation.html import standalone_page
from .probe_plan import read_generated_plan


def write_traffic_preview(output, *, publication=None):
    output = Path(output)
    if publication is None:
        publication = json.loads((output / 'results/experiment/completed.json').read_text())
    plan = read_generated_plan(output / 'results/traffic/probe-plan.json',
                               published_batches=publication['published_batches'])
    identities = {p['probe_id']: p for p in plan['probe_identities']}
    # A pair can be near in one delivery and far in another's available coverage.
    delivery_identities = {}
    for delivery in plan.get('delivery_plans', (plan,)):
        by_probe = {p['probe_id']: p for p in delivery['probe_identities']}
        delivery_identities.update((e['episode_id'], by_probe[e['probe_id']]) for e in delivery['episodes'])
    provenance = output / 'inputs/provenance.json'
    labels = (json.loads(provenance.read_text()).get('scenario_parameters', {}).get('node_labels', {})
              if provenance.exists() else {})
    def endpoint(node):
        return '<span title="' + escape(node, quote=True) + '">' + escape(labels.get(node, node.rsplit('/', 1)[-1])) + '</span>'
    native = plan.get('forwarding_backend') == 'linux'
    def separation(identity):
        distance = identity.get('geographic_separation_m')
        if distance is None:
            return 'unavailable'
        label = identity.get('geographic_separation_class', 'unavailable')
        return f'{distance / 1000:,.1f} km ({escape(label)})'
    rows = []
    for episode in plan['episodes']:
        identity = delivery_identities.get(episode['episode_id'], identities[episode['probe_id']])
        services = []
        for state in episode['state_intervals']:
            if not services or services[-1]['service_satellite_id'] != state['service_satellite_id']:
                services.append(state)
        changes = '<details><summary>' + str(len(services)) + ' service states</summary><ol>' + ''.join(
            '<li>' + escape(s['start_time']) + ' → ' + endpoint(s['service_satellite_id'] or 'unserved') + '</li>'
            for s in services) + '</ol></details>'
        times = tuple('<time title="' + escape(episode[k], quote=True) + '">' + escape(episode[k].split('T')[-1]) + '</time>'
                      for k in ('start_time', 'end_time'))
        values = (endpoint(identity['logical_source']), endpoint(identity['logical_destination']),
                  'GS → Zone' if identity['direction'] == 'ground-station-to-zone' else 'Zone → GS',
                  *times, str(episode['interval_seconds']), str(episode['estimated_echo_requests']),
                  *((separation(identity),) if native else ()),
                  escape(identity['path_length_class']), changes)
        rows.append('<tr>' + ''.join('<td>' + v + '</td>' for v in values) + '</tr>')
    horizon = plan['planning_horizon']
    body = f'''<p><a href="../index.html">All tools</a></p>
<p>Planned GS ↔ Zone traffic. No packets are sent by this page. Runtime follows Runner's completed ACTIVE frontier and clock.</p>
<p>Computed coverage: {escape(horizon['coverage_start_time'])} to {escape(horizon['coverage_end_time'])}.
{publication['published_batches']} published deliveries. Refresh this preview after continued computation completes.</p>
<p>Transit paths: {escape(plan.get("transit_path_evidence", "planned"))}. Packet-path telemetry: {escape(plan.get("packet_path_telemetry", "BPF when enabled"))}.</p>
<p>Counts are estimates; service/source changes are planned facts, not delivery, loss or latency measurements.</p>
{('<p>Geographic separation is GS-to-Zone great-circle distance. Near/far are relative to eligible arranged coverage in each delivery, not transit hops or a promise of intercontinental traffic.</p>' if native else '')}
<p>{escape(str(plan.get('planning', {}).get('empty_reason') or ''))}</p>
<table><thead><tr>{''.join('<th>'+v+'</th>' for v in ('Source','Destination','Direction','Start','End','Interval (s)','Estimated packets', *(('Geographic separation',) if native else ()), 'Transit path','Planned service/source changes'))}</tr></thead>
<tbody>{''.join(rows)}</tbody></table>'''
    destination = output / 'visualizations/traffic-generation'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'index.html').write_text(standalone_page(title='Traffic generation preview', eyebrow='Planned workload', body=body))
    (destination / 'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    (destination / 'summary.txt').write_text(f"status=PASS\nepisodes={len(rows)}\ncoverage_end={horizon['coverage_end_time']}\n")
    return {'status': 'PASS', 'message': f'{len(rows)} planned episodes; coverage through {horizon["coverage_end_time"]}'}
