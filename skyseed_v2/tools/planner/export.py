"""Export complete planned network snapshots, independent of Compiler deltas.

Run offline with ``python -m tools.planner.export --generated PATH --output PATH``.
The output is a temporary visualization copy, not retained experiment evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from skyseed_v2.shared._domain_history import _read_domain_history_manifest, _iter_verified_domain_history_stream
from skyseed_v2.simulator import apply_plan_transition, ReachableRouteOutcome, GroundStationRouteDemandTarget
from .reader import iter_planner_history
from tools.traffic.export import write_traffic_export
from tools._inspection_evidence import iter_physical_link_inspection


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')


def _node(value):
    return f"{value['asn']}/{value['entity_kind']}/{value['entity_id']}"


def _reserved_ground_accesses(evidence):
    """Lifecycle selections include pending reservations; accesses alone do not."""
    if evidence.ground_station_preparations:
        return tuple(value.candidate for value in evidence.ground_station_preparations)
    return evidence.selected_ground_station_accesses


def _zone_feature(zone):
    g = zone['geometry']; south, north = g['south_latitude_deg'], g['north_latitude_deg']
    west, east = g['west_longitude_deg'], g['east_longitude_deg']
    def polygon(a,b):
        return [[[a,south],[b,south],[b,north],[a,north],[a,south]]]
    geometry = {'type':'Polygon','coordinates':polygon(west,east)} if west < east else {
        'type':'MultiPolygon','coordinates':[polygon(west,180),polygon(-180,east)]}
    return {'type':'Feature','id':zone['zone_id'],'properties':{'zone_id':zone['zone_id']},'geometry':geometry}


def write_network_export(generated: Path, output: Path) -> dict:
    generated, output = Path(generated), Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('export destination must be empty')
    physical = json.loads((generated/'inspection/physical-world/physical-world.json').read_text())
    satellites = {}; orbits = []
    for ring in physical['orbit_rings']:
        orbit = {k:ring[k] for k in ('asn','shell_id','orbit_id','altitude_km','inclination_deg','right_ascension_deg','epoch_utc')}
        orbit['satellite_ids'] = [sat['node_id'] for sat in ring['satellites']]
        orbits.append(orbit)
        for sat in ring['satellites']:
            satellites[sat['node_id']] = {k:sat[k] for k in ('node_id','satellite_id','orbit_index','mean_anomaly_deg')}
            satellites[sat['node_id']].update({k:orbit[k] for k in ('asn','shell_id','orbit_id')})
    stations = {station['node_id']: {k:station[k] for k in ('node_id','asn','ground_station_id','latitude_deg','longitude_deg','elevation_m')} for station in physical['ground_stations']}
    descriptors = physical.get('model_provenance',{}).get('descriptors') or {}
    gs_policy = descriptors.get('ground_station',{}).get('configuration',{})
    capacities = {}
    for station in physical['ground_stations']:
        capacity = station.get('max_simultaneous_satellite_accesses')
        if capacity is None: capacity = gs_policy.get('default_ground_station_limit')
        ceiling = gs_policy.get('ground_station_policy_limit')
        capacities[station['node_id']] = min(capacity,ceiling) if capacity is not None and ceiling is not None else capacity
    nodes = set(satellites)|set(stations); zones = {z['zone_id'] for z in physical['zones']}
    _write(output/'network/world.json', {'schema_version':1,'satellites':list(satellites.values()),'orbits':orbits,'ground_stations':list(stations.values())})
    _write(output/'network/zones.geojson', {'type':'FeatureCollection','features':[_zone_feature(z) for z in physical['zones']]})
    manifest = _read_domain_history_manifest(generated/'domain-history/planning.jsonl.gz', minimum_through='planner')
    positions = _iter_verified_domain_history_stream(manifest,'physical_frames'); next(positions)
    candidate_source = generated/'inspection-evidence/physical-link-observations.jsonl.gz'
    candidate_frames = iter_physical_link_inspection(candidate_source) if candidate_source.exists() else None
    candidate_nodes = next(candidate_frames)['nodes'] if candidate_frames is not None else ()
    path_ids = {}; paths = []; demand_ids = {}; demands = []; frame_index = []; state = None; origin = None
    metrics = []; previous_edges = set(); gaps = []; gap_starts = {}
    for publication in iter_planner_history(generated/'domain-history/planning.jsonl.gz'):
        for transition, evidence in zip(publication.plan.transitions, publication.frames, strict=True):
            state = apply_plan_transition(state,transition)
            frame = next(positions)
            timestamp = state.timestamp.isoformat()
            if frame['timestamp'] != timestamp: raise ValueError('position/Plan timeline mismatch')
            origin = origin or state.timestamp
            elapsed = (state.timestamp-origin).total_seconds()
            xyz = {_node(p['node']):[p['x_m'],p['y_m'],p['z_m']] for p in frame['positions']}
            if set(xyz) != nodes: raise ValueError('incomplete position membership')
            connections=[]; adjacency={n:set() for n in nodes}; isl_adjacency={n:set() for n in satellites}
            for connection in state.operational_connections:
                c=connection.connection_id; a,b=c.endpoint_a.value,c.endpoint_b.value
                if a not in nodes or b not in nodes: raise ValueError('foreign Connection endpoint')
                connections.append([a,b]);adjacency[a].add(b);adjacency[b].add(a)
                if a in satellites and b in satellites:
                    if satellites[a]['shell_id'] != satellites[b]['shell_id']: raise ValueError('cross-Shell ISL')
                    isl_adjacency[a].add(b);isl_adjacency[b].add(a)
            serving={a.service_key:None if a.satellite_id is None else a.satellite_id.value for a in state.zone_service_assignments}
            if {key[1] for key in serving}!=zones or any(s is not None and s not in satellites for s in serving.values()):raise ValueError('Zone assignment membership mismatch')
            outcomes=[]
            for outcome in state.route_outcomes:
                demand=outcome.demand; source=demand.source.value
                target=({'ground_station_id':demand.target.ground_station_id.value} if isinstance(demand.target,GroundStationRouteDemandTarget) else {'zone_id':demand.target.zone_id})
                target['network_scope']=demand.target.network_scope
                key=(source,tuple(target.items()))
                if key not in demand_ids:
                    demand_ids[key]=len(demands);demands.append({'demand_id':len(demands),'source_node_id':source,'target':target})
                if source not in nodes:raise ValueError('foreign route source')
                if isinstance(outcome,ReachableRouteOutcome):
                    path=tuple(n.value for n in outcome.path)
                    terminal=target.get('ground_station_id',serving.get((demand.target.network_scope,target.get('zone_id'))))
                    if path[0]!=source or path[-1]!=terminal or any(b not in adjacency[a] for a,b in zip(path,path[1:])):raise ValueError('route inconsistent with operational state')
                    if path not in path_ids:path_ids[path]=len(paths);paths.append(path)
                    outcomes.append([demand_ids[key],path_ids[path],None])
                else:outcomes.append([demand_ids[key],None,outcome.reason.value])
            index=len(frame_index);filename=f'frames/frame-{index:03d}.json'
            _write(output/'network'/filename,{'schema_version':1,'frame_index':index,'timestamp_utc':timestamp,'elapsed_seconds':elapsed,
                'satellite_positions_ecef_m':{n:xyz[n] for n in satellites},'operational_connections':connections,'zone_assignments':[{'network_scope':key[0],'zone_id':key[1],'satellite_id':value} for key,value in sorted(serving.items())],'route_outcomes':outcomes})
            frame_index.append({'frame_index':index,'timestamp_utc':timestamp,'elapsed_seconds':elapsed,'file':filename})
            reserved_ground = _reserved_ground_accesses(evidence)
            selected=Counter(c.ground_station_id.value for c in reserved_ground)
            selected_satellites=Counter(c.satellite_id.value for c in reserved_ground)
            candidates_by_ground=defaultdict(set)
            if candidate_frames is not None:
                candidate_frame=next(candidate_frames)
                if candidate_frame['timestamp'] != timestamp:raise ValueError('candidate/Plan timeline mismatch')
                for ground,satellite,*_ in candidate_frame['ground_station']:
                    candidates_by_ground[candidate_nodes[ground]['node_id']].add(candidate_nodes[satellite]['node_id'])
            operational=Counter({g:len(adjacency[g]) for g in stations})
            direct={s for g in stations for s in adjacency[g]};reachable=set(stations);queue=list(stations)
            while queue:
                n=queue.pop()
                new=adjacency[n]-reachable;reachable.update(new);queue.extend(new)
            unseen=set(satellites);components=defaultdict(list)
            while unseen:
                seed=unseen.pop();queue=[seed];size=1
                while queue:
                    n=queue.pop();new=isl_adjacency[n]&unseen;unseen-=new;size+=len(new);queue.extend(new)
                components[satellites[seed]['shell_id']].append(size)
            edge_set={tuple(c) for c in connections}
            for ground in stations:
                if operational[ground] == 0:
                    gap_starts.setdefault(ground,elapsed)
                elif ground in gap_starts:
                    gaps.append({'ground_station_id':ground,'start_seconds':gap_starts.pop(ground),'end_seconds':elapsed,'recovered':True})
            metrics.append({'timestamp_utc':timestamp,'elapsed_seconds':elapsed,'gs_degrees':{g:{'capacity':capacities[g],
                'occupied':selected[g],'operational':operational[g],'pending':selected[g]-operational[g],
                'unused_slots':None if capacities[g] is None else capacities[g]-selected[g],
                'queried_candidate_count':len(candidates_by_ground[g]) if candidate_frames is not None else None,
                'unused_reason': 'capacity-unavailable' if capacities[g] is None else 'none' if selected[g]==capacities[g] else
                    'returned-candidate-shortage; physical/contact/search split not inferred' if candidate_frames is not None and len(candidates_by_ground[g])<capacities[g] else
                    'admission-or-endpoint-competition-in-admitted-search; not physical-invisibility',
                'reserved_without_service_reason':'preparation' if selected[g]>operational[g] else None} for g in stations},
                'selected_gsl':sum(selected.values()),'operational_gsl':sum(operational.values()),'pending_gsl':sum(selected.values())-sum(operational.values()),
                'distinct_selected_satellites':len(selected_satellites),'selected_direct_shells':dict(Counter(satellites[n]['shell_id'] for n in selected_satellites)),
                'gs_coverage':sum(v>0 for v in operational.values()),'distinct_direct_satellites':len(direct),'direct_shells':dict(Counter(satellites[n]['shell_id'] for n in direct)),
                'isl_degree_histogram':dict(Counter(map(len,isl_adjacency.values()))),'isl_components':dict(components),'ground_reachable_shells':dict(Counter(satellites[n]['shell_id'] for n in reachable if n in satellites)),
                'established_gsl':sum(a in stations or b in stations for a,b in edge_set-previous_edges),
                'withdrawn_gsl':sum(a in stations or b in stations for a,b in previous_edges-edge_set),
                'established_connections':len(edge_set-previous_edges),'withdrawn_connections':len(previous_edges-edge_set)})
            previous_edges=edge_set
    if next(positions,None) is not None:raise ValueError('unmatched formal position frames')
    if candidate_frames is not None and next(candidate_frames,None) is not None:raise ValueError('unmatched candidate frames')
    gaps.extend({'ground_station_id':g,'start_seconds':start,'end_seconds':frame_index[-1]['elapsed_seconds'],'recovered':False} for g,start in gap_starts.items())
    expected=manifest.data['timeline']['formal_frame_count'] if 'formal_frame_count' in manifest.data['timeline'] else len(frame_index)
    if len(frame_index)!=expected:raise ValueError('formal frame coverage differs')
    if [f['timestamp_utc'] for f in frame_index] != [f['timestamp'] for f in physical['frame_index']]:raise ValueError('physical Tool frame coverage differs')
    _write(output/'network/demands.json',{'schema_version':1,'demands':demands})
    path_chunks=[]
    for start in range(0,len(paths),2000):
        filename=f'paths/paths-{start//2000:04d}.json';_write(output/'network'/filename,{'schema_version':1,'first_path_id':start,'paths':paths[start:start+2000]});path_chunks.append({'file':filename,'first_path_id':start,'count':min(2000,len(paths)-start)})
    _write(output/'network/index.json',{'schema_version':1,'authority':'planned-network','coordinate_system':'WGS84 ECEF metres; GeoJSON longitude/latitude degrees',
        'start_utc':origin.isoformat(),'frames':frame_index,'paths':path_chunks,'world':'world.json','zones':'zones.geojson','demands':'demands.json'})
    _write(generated/'inspection/planner/network-behavior.json',{'schema_version':1,'authority':'formal planned states; reservations do not provide service',
        'gap_resolution':'formal observation interval; zero-operational-GSL service gaps, not per-satellite all-path outages',
        'service_gaps':gaps,'frames':metrics})
    traffic=write_traffic_export(generated,output/'traffic',origin,path_ids)
    (output/'README.md').write_text('# 卫星网络可视化临时交接\n\nnetwork/ 是正式仿真时轴上的最终计划网络；traffic/ 是实际观测流量。两部分以稳定节点ID、UTC和仿真秒关联。计划可达不等于实测交付。此目录是仓库外临时副本，可删除而不影响保留实验档案，不提交或推送。\n')
    (output/'network/README.md').write_text('# 计划网络数据 v1\n\n先读 index.json；frames 每项指向一个完整正式状态，不需重放内部增量。时间戳为UTC，elapsed_seconds相对起点。动画插值不增加仿真精度。\n\nworld.json：卫星稳定node_id、AS/Shell/orbit、轨内索引与历元平近点角；轨道高度km、倾角/升交点赤经deg、epoch_utc；GS经纬度deg及WGS84海拔m。zones.geojson为经度/纬度GeoJSON，跨日期线矩形拆成MultiPolygon。\n\n逐帧 satellite_positions_ecef_m 为WGS84地固坐标[x,y,z]米；operational_connections 是全部无向卫星间/GS接入边[a,b]，不含准备预约；zone_assignments 的null表示未服务。\n\nroute_outcomes 每行[demand_id,path_id,unreachable_reason]，不可达时path_id=null，demands.json给出源和GS/Zone目的。paths分块中路径ID=first_path_id+数组下标，节点顺序为有向路径。每帧包含全部最终路由结果和未服务Zone，可直接读取；不包含候选、内部编译数据或逐链路参数。\n')
    result={'status':'PASS','formal_frames':len(frame_index),'paths':len(paths),'demands':len(demands),'traffic':traffic}
    _write(output/'validation.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--generated',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(write_network_export(args.generated,args.output)))

if __name__=='__main__':main()
