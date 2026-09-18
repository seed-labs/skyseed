#!/usr/bin/env python3
"""Regenerate these historical demos offline from explicitly supplied ME2 records.

This is a one-off presentation migration, not production old-format support.
It changes temporary record locations/manifest envelopes, never original records
or calculated/runtime values. No Models are advanced and no network is started.
"""
from pathlib import Path
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(',', ':')) + '\n')


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def relocate(source, destination):
    """Change only the historical envelope needed by current detached readers."""
    from skyseed_v2.experiment.output.history import _DOMAIN_HISTORY_STREAM_NAMES
    manifest = json.loads((source/'domain-history/domain-history-manifest.json').read_text())
    assert manifest['format_version'] == 3 and manifest['terminal']
    for kind, record in manifest['streams'].items():
        copy(source/'domain-history'/record['path'], destination/'results'/_DOMAIN_HISTORY_STREAM_NAMES[kind])
        record['path'] = _DOMAIN_HISTORY_STREAM_NAMES[kind]
    manifest['format_version'] = 4
    write(destination/'results/experiment/domain-history-manifest.json', manifest)
    status = json.loads((source/'generation-status.json').read_text())
    write(destination/'results/experiment/generation-status.json', status)
    copy(source/'inspection-evidence/generation-performance.jsonl', destination/'results/experiment/generation-performance.jsonl')
    candidate = json.loads((source/'inspection-evidence/physical-link-observations-manifest.json').read_text())
    timestamps = candidate.pop('timestamps')
    data = ''.join(json.dumps(t)+'\n' for t in timestamps)
    index = destination/'results/physical-world/candidates/timestamps.jsonl'
    index.parent.mkdir(parents=True, exist_ok=True); index.write_text(data)
    candidate.update(timestamp_index_bytes=len(data.encode()), first_timestamp=timestamps[0], last_timestamp=timestamps[-1])
    write(index.with_name('physical-link-observations-manifest.json'), candidate)
    copy(source/'inspection-evidence/physical-link-observations.jsonl.gz', index.with_name('physical-link-observations.jsonl.gz'))
    for name in ('execution.json', 'materials.jsonl'):
        path = source/'runtime'/name
        if path.exists():
            target = destination/'results/compiler'/name
            target.write_text(path.read_text().replace('runtime/runner-artifact', 'results/compiler/runner-artifact').replace('inspection/traffic/probe-plan', 'results/traffic/probe-plan'))
    for path in (source/'runtime').glob('runner-artifact*'):
        if path.is_dir():shutil.copytree(path, destination/'results/compiler'/path.name)
    for path in (source/'inspection/traffic').glob('probe-plan*.json'):
        copy(path, destination/'results/traffic'/path.name)
    for name in ('deployment-startup.json',):
        if (source/'runtime'/name).exists():copy(source/'runtime'/name, destination/'deployment'/name)
    if (source/'inspection/orchestration-timing.json').exists():
        copy(source/'inspection/orchestration-timing.json', destination/'results/experiment/orchestration-timing.json')
    if (source/'inspection/runtime-runs').exists():
        shutil.copytree(source/'inspection/runtime-runs', destination/'runs')
        for path in (destination/'runs').glob('*/performance/execution-observations.jsonl'):
            lines = path.read_text().splitlines(keepends=True)
            header = json.loads(lines[0])
            header['metadata']['generation_reference'] = '../../../results/experiment/generation-performance.jsonl'
            path.write_text(json.dumps(header)+'\n'+''.join(lines[1:]))
    shutil.copytree(source/'inputs', destination/'inputs')
    publication = dict(status, history=manifest, physical_links=candidate,
                       published_batches=1, session_terminated=True, completed_end=manifest['timeline']['end_timestamp'],
                       performance_bytes=(destination/'results/experiment/generation-performance.jsonl').stat().st_size)
    write(destination/'results/experiment/completed.json', publication)
    return publication


def s5_world():
    from examples.Sky01_s5_custom_models import s5
    from skyseed_v2 import adapters, emulator, simulator, configuration
    network = s5.build_emulator()
    systems = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    definition = simulator.SimulationDefinition(adapters.create_simulator_static_input(systems),
        simulator.SimulationTimeline(start, start+timedelta(seconds=600), timedelta(seconds=5)))
    simulation = simulator.Simulator(definition)
    # Only materialize static world facts. Stored frames provide every timed position.
    simulation.create_physical_world(position_model=simulator.CircularOrbitPositionModel(),
        zone_layout_definition=configuration.simulator.load_zone_layout_definition(s5.ZONE_FILE),
        zone_membership_model=simulator.DefaultZoneMembershipModel())
    return simulation


def browser_files(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob('*'):
        if path.is_file() and path.suffix in {'.html', '.js', '.css', '.svg', '.png', '.woff2'}:
            target=destination/path.relative_to(source)
            copy(path,target)
            if target.suffix=='.html':
                depth=len(target.relative_to(destination.parent).parts)-1
                prefix='../'*depth
                nav=f'<nav data-demo-nav style="padding:10px 18px;background:#142a3b;color:#d5e9f1;font:14px system-ui"><a href="{prefix}index.html">All historical demonstrations</a> · ME2 records, 2026-09-17 · <a href="{prefix}README.md">Provenance</a></nav>'
                page=target.read_text()
                marker=page.find('>',page.find('<body')) if '<body' in page else -1
                page=page[:marker+1]+nav+page[marker+1:] if marker>=0 else nav+page
                target.write_text(page)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',type=Path,required=True,help='ME2 current-results directory')
    parser.add_argument('--output',type=Path,default=Path(__file__).parent)
    parser.add_argument('--scenario',choices=('s5','s3','both'),default='both')
    args=parser.parse_args()
    provenance=json.loads((args.output/'provenance.json').read_text()) if (args.output/'provenance.json').exists() else {}
    with tempfile.TemporaryDirectory(prefix='skyseed-demo-') as temporary:
        for scenario in (('s5_custom_models','s3_ospf') if args.scenario=='both' else (('s5_custom_models',) if args.scenario=='s5' else ('s3_ospf',))):
            source=args.records/scenario/'output';target=Path(temporary)/scenario
            publication=relocate(source,target)
            if scenario=='s5_custom_models':
                import tools
                simulation=s5_world()
                try:results=tools.write_experiment_tools(target, physical_world=simulation.get_physical_world())
                finally:simulation.shutdown()
                assert all(v['status'] in {'PASS','WARN','COMPLETE','COMPLETED'} for v in results.values()),{k:v for k,v in results.items() if v['status']=='FAILED'}
                browser_files(target/'visualizations',args.output/'s5')
                interval=publication['history']['timeline']
            else:
                from tools.planner import read_planner_history, write_planner_tool
                evidence=read_planner_history(target/'results/planner/planning.jsonl.gz')[:1]
                # Retain the first complete published Plan and matching real frame prefix.
                plan=evidence[0].plan
                manifest=publication['history']
                for kind,record in manifest['streams'].items():
                    path=target/'results'/record['path']
                    opener=gzip.open if path.suffix=='.gz' else open
                    with opener(path,'rt') as stream:lines=stream.readlines()
                    count=len(plan.transitions) if kind=='physical_frames' else 1
                    with opener(path,'wt') as stream:stream.writelines(lines[:count+1])
                    record.update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),record_count=count)
                manifest['counts'].update(expected_formal_frames=len(plan.transitions),formal_frames=len(plan.transitions),
                    transitions=len(plan.transitions),plans=1,realizations=1)
                manifest['timeline']['end_timestamp']=plan.end_timestamp.isoformat()
                manifest['frontiers']={k:plan.end_timestamp.isoformat() for k in manifest['frontiers']}
                manifest['lineage'].update(last_plan_id=plan.plan_id,last_plan_previous_plan_id=plan.previous_plan_id,
                    last_plan_output_state_fingerprint=plan.output_state_fingerprint,
                    last_realization_plan_id=plan.plan_id,last_realization_output_state_fingerprint=plan.output_state_fingerprint)
                write(target/'results/experiment/domain-history-manifest.json',manifest)
                candidates=target/'results/physical-world/candidates'
                stream=candidates/'physical-link-observations.jsonl.gz'
                with gzip.open(stream,'rt') as source_stream:
                    header=next(source_stream)
                    rows=[next(source_stream) for _ in plan.transitions]
                with gzip.open(stream,'wt') as target_stream:target_stream.write(header+''.join(rows))
                cm=publication['physical_links']
                times=[json.loads(row)['timestamp'] for row in rows]
                timestamp_data=''.join(json.dumps(value)+'\n' for value in times)
                (candidates/'timestamps.jsonl').write_text(timestamp_data)
                cm.update(formal_observation_count=len(times), first_timestamp=times[0],last_timestamp=times[-1],
                    timestamp_index_bytes=len(timestamp_data.encode()),stream_size_bytes=stream.stat().st_size,
                    stream_sha256=hashlib.sha256(stream.read_bytes()).hexdigest(),
                    candidate_totals={kind:sum(len(json.loads(row)[kind]) for row in rows)
                                      for kind in ('inter_satellite','ground_station','zone_service')})
                write(candidates/'physical-link-observations-manifest.json',cm)
                result=write_planner_tool(evidence,target/'visualizations/planner',
                    physical_link_inspection_path=stream,
                    physical_frames_path=target/'results/physical-world/physical-frames.jsonl',
                    model_descriptors=publication['configuration'])
                assert result['status']=='PASS',result
                browser_files(target/'visualizations',args.output/'s3')
                interval={'start_timestamp':evidence[0].plan.start_timestamp.isoformat(),
                          'end_timestamp':evidence[0].plan.end_timestamp.isoformat()}
            hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [source/'domain-history/domain-history-manifest.json',source/'domain-history/planning.jsonl.gz',
                          source/'domain-history/physical-frames.jsonl',source/'generation-status.json']}
            if scenario=='s5_custom_models':
                for p in (source/'inspection/runtime-runs').rglob('*.jsonl'):
                    hashes[str(p.relative_to(source))]=hashlib.sha256(p.read_bytes()).hexdigest()
            provenance[scenario]={'source':str(source),'interval':interval,'source_sha256':hashes,
                'transformation':'Temporary layout/envelope relocation; calculated and observed values unchanged. Browser HTML/JS only retained.'}
    write(args.output/'provenance.json',provenance)
    print(json.dumps(provenance,indent=2))


if __name__=='__main__':main()
