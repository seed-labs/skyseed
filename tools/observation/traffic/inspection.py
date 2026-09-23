"""Write detached Traffic observations; never invokes Models or runtime controls."""
import json
from pathlib import Path
from time import perf_counter
from .reader import read_traffic_replay
from .projection import project_traffic_replay
from .renderer import render_traffic_replay_page


def collect_traffic_replay_data(telemetry_path, physical_frames_path, *,
                                workload_path=None, probe_execution_path=None):
    return project_traffic_replay(read_traffic_replay(telemetry_path, physical_frames_path,
        workload_path=workload_path, probe_execution_path=probe_execution_path))


def write_traffic_replay_tool(telemetry_path, physical_frames_path, directory, *,
                              workload_path=None, probe_execution_path=None):
    started = perf_counter()
    data = collect_traffic_replay_data(telemetry_path, physical_frames_path,
        workload_path=workload_path, probe_execution_path=probe_execution_path)
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    (root/'traffic-replay.json').write_text(json.dumps(data, separators=(',', ':'))+'\n')
    (root/'index.html').write_text(render_traffic_replay_page(data))
    (root/'summary.txt').write_text('status=PASS\nauthority=recorded-observations\n'+data['evidence_limit']+'\n')
    return {'status': 'PASS', 'directory': str(root), 'artifact_metrics': {'wall_seconds': perf_counter()-started,
        'events': len(data['events']), 'output_bytes': sum((root/name).stat().st_size for name in ('traffic-replay.json','index.html','summary.txt'))}}
