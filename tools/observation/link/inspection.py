"""Inspect real LinkPlans and optional evaluations without re-running planning."""
from datetime import datetime
from pathlib import Path

from skyseed.pc.link import LinkPlan
from tools.observation._presentation.inspection_page import write_page
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


def inspection_data(scene, base_plans, execution_plans, *, positions, evaluations=(), timestamps=None):
    base = _joined(base_plans)
    execution = _joined(execution_plans)
    if (base.start_time, base.end_time) != (execution.start_time, execution.end_time):
        raise ValueError('inspection branches must cover the same interval')
    evaluations = sorted(evaluations, key=lambda evaluation: evaluation["timestamp"])
    # Display samples supplement decision boundaries. Replacing the boundaries
    # would hide short-lived connections and misrepresent preparation intervals.
    times = {base.start_time}
    times.update(operation.timestamp for plan in (base, execution) for operation in plan.operations)
    times.update(datetime.fromisoformat(value["timestamp"]) for value in evaluations
                 if base.start_time <= datetime.fromisoformat(value["timestamp"]) < base.end_time)
    if timestamps is not None:
        times.update(timestamps)
    timestamps = sorted(times)
    frames = []
    cursor = 0
    latest = None
    for timestamp, base_state, execution_state in zip(
        timestamps, base.states_at(timestamps), execution.states_at(timestamps), strict=True
    ):
        while cursor < len(evaluations) and datetime.fromisoformat(evaluations[cursor]["timestamp"]) <= timestamp:
            latest = evaluations[cursor]
            cursor += 1
        frame = {'timestamp': timestamp.isoformat(),
                 'positions': positions[timestamp.isoformat()],
                 'base': [_configuration(link) for link in base_state.values()],
                 'execution': [_configuration(link) for link in execution_state.values()],
                 'evaluation_timestamp': None, 'candidates': None, 'selected': None, 'pending': None}
        if latest is not None:
            frame.update(evaluation_timestamp=latest["timestamp"],
                         candidates=latest["candidates"], selected=latest["selected"],
                         pending=[item for item in latest["pending"]
                                  if datetime.fromisoformat(item["ready_at"]) > timestamp])
        frames.append(frame)
    events = {}
    for name, plan in (('base', base), ('execution', execution)):
        events[name] = [{'timestamp': operation.timestamp.isoformat(), 'kind': operation.kind,
                         'id': operation.link_id,
                         'configuration': None if operation.configuration is None
                         else _configuration(operation.configuration)} for operation in plan.operations]
    return {**scene_inputs(scene), 'format': 'skyseed.link-inspection/v1',
            'start': base.start_time.isoformat(), 'end': base.end_time.isoformat(),
            'frames': frames, 'events': events}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name('viewer.html'), data_name='link')
