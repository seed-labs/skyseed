"""Inspect real LinkPlans and optional evaluations without re-running planning."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

from skyseed import Scene
from skyseed.pc.physical import PhysicalLayer
from skyseed.pc.link import LinkLayer, LinkPlan, PeriodicLinkPlanModel
from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.plan_details import write_plan_details
from tools.observation._presentation.scene_display import scene_inputs


def _candidate(candidate):
    return {'id': candidate.id, 'reports': [
        {'source': report.node_id, 'target': report.peer_id,
         'distance_m': report.distance_m, 'propagation_delay_s': report.propagation_delay_s,
         'elevation_deg': report.elevation_deg, 'data': dict(report.data)}
        for report in candidate.reports.values()]}


def _configuration(link):
    directions = []
    for source, report in link.candidate.reports.items():
        properties = link.properties_from(source)
        directions.append({'source': source, 'target': report.peer_id,
                           'bandwidth_bps': properties.bandwidth_bps,
                           'delay_s': properties.delay_s, 'packet_loss_ppm': properties.packet_loss_ppm,
                           'data': dict(properties.data)})
    return {**_candidate(link.candidate), 'directions': directions}


def _joined(plans):
    plans = tuple(plans)
    if not plans:
        raise ValueError('inspection needs a nonempty plan sequence')
    for previous, current in zip(plans, plans[1:]):
        if previous.end_time != current.start_time or previous.end_links != current.initial_links:
            raise ValueError('inspection plans must continue without a gap or state reset')
    return LinkPlan(plans[0].start_time, plans[-1].end_time, plans[0].initial_links,
                    tuple(operation for plan in plans for operation in plan.operations))


def inspection_data(physical, base_plans, execution_plans, *, evaluations=(), timestamps=None):
    base = _joined(base_plans)
    execution = _joined(execution_plans)
    if (base.start_time, base.end_time) != (execution.start_time, execution.end_time):
        raise ValueError('inspection branches must cover the same interval')
    evaluations = sorted(evaluations, key=lambda evaluation: evaluation.timestamp)
    # Display samples supplement decision boundaries. Replacing the boundaries
    # would hide short-lived connections and misrepresent preparation intervals.
    times = {base.start_time}
    times.update(operation.timestamp for plan in (base, execution) for operation in plan.operations)
    times.update(evaluation.timestamp for evaluation in evaluations
                 if base.start_time <= evaluation.timestamp < base.end_time)
    if timestamps is not None:
        times.update(timestamps)
    timestamps = sorted(times)
    frames = []
    cursor = 0
    latest = None
    for timestamp, base_state, execution_state in zip(
        timestamps, base.states_at(timestamps), execution.states_at(timestamps), strict=True
    ):
        while cursor < len(evaluations) and evaluations[cursor].timestamp <= timestamp:
            latest = evaluations[cursor]
            cursor += 1
        snapshot = physical.snapshot_at(timestamp)
        frame = {'timestamp': timestamp.isoformat(),
                 'positions': {node: state.position_m for node, state in snapshot.states.items()},
                 'base': [_configuration(link) for link in base_state.values()],
                 'execution': [_configuration(link) for link in execution_state.values()],
                 'evaluation_timestamp': None, 'candidates': None, 'selected': None, 'pending': None}
        if latest is not None:
            frame.update(evaluation_timestamp=latest.timestamp.isoformat(),
                         candidates=[_candidate(candidate) for candidate in latest.candidates],
                         selected=[list(identity) for identity in latest.selected],
                         pending=[{'id': identity, 'ready_at': ready.isoformat()}
                                  for identity, ready in latest.pending.items() if ready > timestamp])
        frames.append(frame)
    events = {}
    for name, plan in (('base', base), ('execution', execution)):
        events[name] = [{'timestamp': operation.timestamp.isoformat(), 'kind': operation.kind,
                         'id': operation.link_id,
                         'configuration': None if operation.configuration is None
                         else _configuration(operation.configuration)} for operation in plan.operations]
    return {**scene_inputs(physical.scene), 'format': 'skyseed.link-inspection/v1',
            'start': base.start_time.isoformat(), 'end': base.end_time.isoformat(),
            'frames': frames, 'events': events}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name('viewer.html'), data_name='link')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', default='2026-01-01T00:00:00+00:00')
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--position-model', choices=('circular', 'sgp4'), default='circular')
    parser.add_argument('--setup-seconds', type=float, default=7.5)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start)
    if start.tzinfo is None or args.seconds <= 0:
        parser.error('use a timezone-aware --start and positive --seconds')
    if args.scene:
        scene = Scene.load(args.scene)
    else:
        from tools.observation.scene.inspection import demo_scene
        scene = demo_scene(start)
    physical = PhysicalLayer(scene)
    if args.position_model == 'sgp4':
        from skyseed.pc.physical import SGP4PositionModel
        physical.set_position_model(SGP4PositionModel)
    layer = LinkLayer(physical)
    evaluations = []
    layer.set_plan_model(PeriodicLinkPlanModel, model_options={
        'isl_setup_duration': timedelta(seconds=args.setup_seconds),
        'ground_setup_duration': timedelta(seconds=args.setup_seconds),
        'observer': evaluations.append})
    begun = perf_counter()
    layer.generate_plan(start, start + timedelta(seconds=args.seconds))
    planned = perf_counter()
    data = inspection_data(physical, layer.base_plans, layer.execution_plans, evaluations=evaluations)
    sampled = perf_counter()
    write_plan_details(args.output, kind='link', streams={
        'base': layer.base_plans, 'execution': layer.execution_plans})
    archived = perf_counter()
    data['plan_details'] = 'plans/index.html'
    path = write_inspection(data, args.output)
    print(f'{path.resolve()}\n{len(scene.nodes)} nodes; {len(data["frames"])} frames; '
          f'planning {planned-begun:.3f}s; display sampling {sampled-planned:.3f}s; '
          f'plan files and details {archived-sampled:.3f}s; '
          f'page write {perf_counter()-archived:.3f}s')


if __name__ == '__main__':
    main()
