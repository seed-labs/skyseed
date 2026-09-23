"""Fixed GS/Zone communication selection from stable identities only."""
from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
import json
import math


@dataclass(frozen=True)
class Communication:
    communication_id: str
    ground_station_id: str
    zone_id: str
    direction: str
    network_scope: str = ''


@dataclass(frozen=True)
class TrafficWorkload:
    communications: tuple[Communication, ...]
    interval_seconds: float = 1.
    duration_seconds: float = 300.

    def artifact(self):
        return {'format_version': 1, 'artifact_type': 'fixed-traffic-workload',
                'communications': [asdict(c) for c in self.communications],
                'interval_seconds': self.interval_seconds, 'duration_seconds': self.duration_seconds}


def select_workload(physical_world, *, pairs=None, direction='both', interval_seconds=1., duration_seconds=300.):
    for value in (interval_seconds, duration_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Traffic interval and intended duration must be positive finite seconds')
    if direction not in {'both', 'ground-to-zone', 'zone-to-ground'}:
        raise ValueError('direction must be both, ground-to-zone, or zone-to-ground')
    grounds = sorted(g.node_id.value for system in physical_world.autonomous_systems for g in system.ground_stations)
    zones = sorted(z.zone_id for z in physical_world.get_zone_layout().zones)
    if pairs is None:
        pairs = [(ground, zones[i % len(zones)]) for i, ground in enumerate(grounds[:2])] if zones else []
    rows, seen = [], set()
    for pair in pairs:
        if len(pair) not in (2, 3):
            raise ValueError('pair is (ground_station_id, zone_id) or (ground_station_id, scope, zone_id)')
        ground, zone = pair[0], pair[-1]
        scope = pair[1] if len(pair) == 3 else ''
        if ground not in grounds or zone not in zones:
            raise ValueError('Traffic pair must name existing stable GS and Zone identities')
        for way in ('ground-to-zone', 'zone-to-ground') if direction == 'both' else (direction,):
            identity = (ground, scope, zone, way)
            if identity in seen:
                raise ValueError('duplicate Traffic communication')
            seen.add(identity)
            rows.append(Communication('communication-' + sha256(json.dumps(identity).encode()).hexdigest()[:16], ground, zone, way, scope))
    return TrafficWorkload(tuple(rows), float(interval_seconds), float(duration_seconds))


def write_workload(workload, path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workload.artifact(), indent=2) + '\n')
    return path


def read_workload(path):
    value = json.loads(Path(path).read_text())
    if value.get('artifact_type') != 'fixed-traffic-workload' or value.get('format_version') != 1:
        raise ValueError('unsupported Traffic workload; historical observations remain readable separately')
    rows = tuple(Communication(**row) for row in value['communications'])
    return TrafficWorkload(rows, value['interval_seconds'], value['duration_seconds'])
