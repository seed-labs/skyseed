"""Compact observed traffic for the offline external visualization delivery."""
from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path
from collections import defaultdict


def _request_timestamps(transactions):
    """Same executor association key as qualification; never guess a send time."""
    matches = defaultdict(list)
    for transaction in transactions:
        generator = transaction.get('generator') or {}
        if generator.get('episode_id') is not None:
            matches[(generator['episode_id'], generator.get('segment_index'),
                     transaction['sequence'])].append(transaction)
    return {key: (values[0].get('request') or {}).get('simulation_timestamp')
            if len(values) == 1 and not values[0].get('association_uncertainty')
            else None for key, values in matches.items()}


def write_traffic_export(generated, output, origin, path_ids):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    def write(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
    def seconds(timestamp):
        return None if timestamp is None else (datetime.fromisoformat(timestamp)-origin).total_seconds()
    plan_path=Path(generated)/'results/traffic/probe-plan.json'
    plans=list(Path(generated).glob('results/traffic/probe-plan*.json')) if not plan_path.exists() else [plan_path]
    plan=None if not plans else json.loads(plans[0].read_text())
    flows=[]
    if plan:
        identities={p['probe_id']:p for p in plan['probe_identities']}
        for episode in plan['episodes']:
            identity=identities[episode['probe_id']]
            flows.append({k:episode.get(k) for k in ('episode_id','probe_id','mode','start_time','end_time','interval_seconds')} | {
                'definition':{k:identity[k] for k in ('direction','ground_station_id','zone_id','zone_ipv4','logical_source','logical_destination','selection_reasons','path_stability')},
                'coverage_cases':episode.get('coverage_cases',[]),
                'start_seconds':seconds(episode['start_time']),'end_seconds':seconds(episode['end_time'])})
    replays=sorted(Path(generated).glob('runs/*/traffic/traffic-replay.json'))
    if not replays:
        replays=sorted(Path(generated).glob('visualizations/traffic/traffic-replay.json'))
    rows=[];summary={};status='unavailable';flow_results=[];request_cases=[]
    if replays:
        data=json.loads(replays[-1].read_text());status='available'
        for transaction in data['transactions']:
            request=transaction.get('request');reply=transaction.get('reply');route=transaction.get('route_outcome') or {}
            path=tuple(f"{n['asn']}/{n['entity_kind']}/{n['entity_id']}" if isinstance(n,dict) else n for n in route.get('path',()))
            rows.append({k:transaction.get(k) for k in ('identifier','sequence','pairing_status','association_uncertainty','reply_delivery','classification','classification_reason','planned_reachability')} | {
                'episode_id':(transaction.get('generator') or {}).get('episode_id'),
                'probe_id':(transaction.get('generator') or {}).get('probe_id'),
                'request_timestamp_utc':None if request is None else request.get('simulation_timestamp'),
                'reply_timestamp_utc':None if reply is None else reply.get('simulation_timestamp'),
                'request_seconds':None if request is None else seconds(request.get('simulation_timestamp')),
                'reply_seconds':None if reply is None else seconds(reply.get('simulation_timestamp')),
                'source_ipv4':None if request is None else request.get('source_ipv4'),
                'destination_ipv4':None if request is None else request.get('destination_ipv4'),
                'planned_path_id':path_ids.get(path),'path_reference_authority':'planned route at observed request; not per-hop execution proof'})
        timestamps = _request_timestamps(data['transactions'])
        q=data.get('qualification') or {};raw_summary=q.get('summary') or {}
        for episode in q.get('episodes',()):
            flow_results.append({k:episode.get(k) for k in ('episode_id','probe_id','sent','received','loss','loss_percent',
                'rtt_ms','expected_unreachable_requests','unresolved_sent_request_count','conformance_counts','classifications')})
            for case in episode.get('request_conformance',()):
                request_cases.append({k:case.get(k) for k in ('segment_index','sequence','reply','classification')} | {
                    'episode_id':episode['episode_id'],'probe_id':episode['probe_id'],
                    'request_seconds':seconds(timestamps.get((episode['episode_id'], case.get('segment_index'), case['sequence']))),
                    'transition_timestamp_utc':(case.get('actual_transition_association') or {}).get('frontier')})
        summary={k:v for k,v in raw_summary.items() if any(word in k for word in ('sent','received','loss','status','reply','request','unreachable','failure'))}
        summary['packet_observations']=data['inventory'].get('packet_observations');summary['reconstructed_transactions']=len(rows)
        summary['shell_coverage']=q.get('shell_coverage')
    write('flows.json',{'schema_version':1,'flows':flows,'sampling':None if plan is None else plan.get('sampling')})
    write('flow-results.json',{'schema_version':1,'flows':flow_results})
    case_chunks=[]
    for start in range(0,len(request_cases),500):
        name=f'request-cases-{start//500:04d}.json';write(name,{'schema_version':1,'cases':request_cases[start:start+500]});case_chunks.append(name)
    chunks=[]
    for start in range(0,len(rows),500):
        name=f'observations-{start//500:04d}.json';write(name,{'schema_version':1,'observations':rows[start:start+500]});chunks.append(name)
    write('index.json',{'schema_version':1,'authority':'observed-traffic','status':status,'flows':'flows.json','flow_results':'flow-results.json',
        'request_cases':case_chunks,'observations':chunks,'summary':summary})
    (output/'README.md').write_text('# 实际观测流量 v1\n\nindex.json记录可用性与摘要；flows.json给出长短流定义和随机种子。flow-results.json保留每流实际发送/回复和未解决分类；request-cases分块保留执行器ICMP序号分类，包括没有包遥测的请求，request_seconds=null表示没有可证明的观测请求时间。segment_index仅区分同一流中复用的ICMP序号。observations分块每行是重建的ICMP事务，不是逐跳遥测。原始包观察数量、事务数量、执行器发送/回复数量分别保留，不能混算。\n\n节点/GS/Zone ID与network相同；UTC和相对仿真秒用于时间关联。planned_path_id只指向观测请求对应的计划路径，不表示逐跳实际转发已证明。null、ambiguous、unclassified表示缺少证据或真实歧义，不补造成功。这里不提供Runner执行时序、滞后、宿主单调时钟或资源轨迹。\n')
    return {'status':status,'flows':len(flows),'transactions':len(rows)}
