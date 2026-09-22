"""Read-only ping execution evidence when packet/path telemetry was not collected."""
import json
from html import escape
from time import perf_counter
import resource
from pathlib import Path
from tools.observation._presentation.html import standalone_page, json_for_script


def write_sender_observation(execution_path, directory, *, physical_frames_path=None,
                             planning_path=None, probe_plan_path=None, runner_events_path=None,
                             provenance_paths=()):
    started = perf_counter()
    path, output = Path(execution_path), Path(directory)
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    if records and records[0].get('record_type') != 'execution_header':
        raise ValueError('Traffic execution header is missing')
    stopped = [r for r in records if r.get('record_type') == 'generator_stopped']
    ending = next((r for r in reversed(records) if r.get('record_type') == 'executor_end'), None)
    replies = [r for r in records if r.get('observation') == 'reply']
    stats = {'sent': sum(r['sent'] for r in stopped) if stopped else None,
             'received': sum(r['received'] for r in stopped) if stopped else None,
             'reply_records': len(replies) if records else None, 'finished_segments': len(stopped),
             'executor_complete': bool(ending and ending.get('complete')),
             'cleanup_complete': bool(ending and ending.get('cleanup_complete'))}
    stats['loss'] = None if stats['sent'] is None else max(0, stats['sent'] - stats['received'])
    stats['loss_percent'] = None if not stats['sent'] else stats['loss'] * 100 / stats['sent']
    stats['zone_source_migrations'] = sum(r.get('record_type') == 'zone_source_migration' for r in records)
    launches, segments_by_episode, sources_by_episode = {}, {}, {}
    for row in records:
        episode = row.get('episode_id')
        if row.get('record_type') == 'generator_launched':
            launches[episode] = row
            sources_by_episode.setdefault(episode, set()).add(row['physical_source_node_id'])
        elif row.get('record_type') == 'generator_stopped':
            segments_by_episode.setdefault(episode, []).append(row)
    episodes = []
    for episode, launch in sorted(launches.items()):
        segments = segments_by_episode.get(episode, ())
        sent = sum(r['sent'] for r in segments) if segments else None
        received = sum(r['received'] for r in segments) if segments else None
        episodes.append({'episode_id': episode, 'direction': launch['logical_direction'],
                         'source_ipv4': launch['source_ipv4'], 'destination_ipv4': launch['destination_ipv4'],
                         'sent': sent, 'received': received, 'loss': None if sent is None else max(0, sent-received),
                         'segments': len(segments),
                         'source_nodes': sorted(sources_by_episode[episode])})
    # Tool success says only that available records were read. No absent packet
    # observations are converted into zero traffic, failed delivery or path claims.
    data = {'telemetry_available': False, 'packet_paths': None, 'statistics': stats,
            'records': records, 'episodes': episodes, 'statistics_scope': 'completed sender segments; raw replies remain available for an incomplete run'}
    output.mkdir(parents=True, exist_ok=True)
    spatial = None
    if physical_frames_path is not None and planning_path is not None:
        from .sender_spatial import write_sender_spatial
        spatial = write_sender_spatial(records, output, physical_frames_path=physical_frames_path,
            planning_path=planning_path, probe_plan_path=probe_plan_path, runner_events_path=runner_events_path)
        data['spatial'] = spatial
    from hashlib import file_digest
    inputs = [Path(p) for p in (execution_path, physical_frames_path, planning_path,
              probe_plan_path, runner_events_path, *provenance_paths) if p is not None and Path(p).is_file()]
    data['provenance'] = {'inputs': [], 'projection': 'native-sender-spatial-v1'}
    for source in inputs:
        with source.open('rb') as stream:
            data['provenance']['inputs'].append({'path': str(source.resolve()), 'sha256': file_digest(stream, 'sha256').hexdigest()})
    (output / 'traffic-replay.json').write_text(json.dumps(data, indent=2)+'\n')
    rows = ''.join('<tr>' + ''.join('<td>' + escape(str(e[k])) + '</td>' for k in
                   ('direction', 'source_ipv4', 'destination_ipv4', 'sent', 'received', 'loss', 'segments')) + '</tr>' for e in episodes)
    table = '<table><thead><tr>' + ''.join('<th>'+s+'</th>' for s in
            ('Direction', 'Stable source', 'Destination', 'Sent', 'Replies', 'Loss', 'Segments')) + '</tr></thead><tbody>'+rows+'</tbody></table>'
    body = '''<p><a href="../index.html">All tools</a></p>
<section class="card" aria-label="Native Traffic evidence"><h2>Sender observations — no recorded intermediate packet hops</h2>
<p>Explore sender observations, replies and source migrations alongside topology and Zone service context.
The endpoint line shows a <strong>communication relationship</strong>; it is not a recorded packet route.
Packet/path telemetry is unavailable. Internet Map live observations are not automatically Traffic replay evidence.</p>
<p><a href="#sender-summary">Sender totals and flows</a> · <a href="#sender-records">Raw execution records</a></p></section>
<p>Counts cover finished sender segments. Executor completion and cleanup do not imply that the entire planned schedule ran.</p>
<p id="stats"></p>''' + '<section id="sender-summary"><h2>Sender totals and flows</h2>' + table + '''</section>
<h2 id="sender-records">Raw execution records</h2><label>Replay execution record <input id="cursor" type="range" min="0" value="0"></label>
<button id="play">Play / pause</button><pre id="record"></pre>'''
    script = 'const DATA=' + json_for_script(data) + ''';
const cursor=document.getElementById('cursor'), record=document.getElementById('record');
const s=DATA.statistics;
document.getElementById('stats').textContent=`Sent: ${s.sent??'unavailable'} · Replies: ${s.received??'unavailable'} · Loss: ${s.loss??'unavailable'} (${s.loss_percent===null?'unavailable':s.loss_percent.toFixed(1)+'%'}) · Zone source migrations: ${s.zone_source_migrations} · Executor complete: ${s.executor_complete} · Cleanup complete: ${s.cleanup_complete}`;
cursor.max=Math.max(0,DATA.records.length-1);
function show(){record.textContent=DATA.records.length?JSON.stringify(DATA.records[Number(cursor.value)],null,2):'Execution records unavailable.';}
cursor.oninput=show;let recordTimer=null;document.getElementById('play').onclick=()=>{if(recordTimer){clearInterval(recordTimer);recordTimer=null;return;}recordTimer=setInterval(()=>{if(Number(cursor.value)>=Number(cursor.max)){clearInterval(recordTimer);recordTimer=null;return;}cursor.value=String(Number(cursor.value)+1);show();},300);};show();'''
    if spatial is not None:
        from .sender_spatial import BODY, script as spatial_script
        body = body.replace('<p id="stats"></p>', '<p id="stats"></p>' + BODY)
        body = body.replace('<a href="#sender-summary">', '<a href="#communication-globe">Communication globe and timed evidence</a> · <a href="#sender-summary">')
        script += '\n' + spatial_script(spatial)
    (output / 'index.html').write_text(standalone_page(title='Traffic observation / replay', eyebrow='Actual sender evidence', body=body, script=script))
    (output / 'summary.txt').write_text('status=PASS\npacket_telemetry=unavailable\n'+''.join(f'{k}={v}\n' for k,v in stats.items()))
    return {'status': 'PASS', 'directory': output,
            'message': 'Sender observations; packet/path telemetry unavailable', **stats,
            'artifact_metrics': {'wall_seconds': perf_counter()-started, 'input_bytes': sum(p.stat().st_size for p in inputs),
                'output_bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
                'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}}
