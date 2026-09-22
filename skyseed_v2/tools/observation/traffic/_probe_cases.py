"""Private observation association; no scheduling or network lifecycle authority."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import re


def _request_cases(episode, records, transactions, activations, migrations, *, maximum_disruption_seconds=2.0, report_ranges=None):
    launches = {r.get('segment_index'): r for r in records if r.get('record_type') == 'generator_launched'}
    stops = {r.get('segment_index'): r for r in records if r.get('record_type') == 'generator_stopped'}
    observations = defaultdict(list)
    for r in records:
        if r.get('record_type') == 'ping_observation' and isinstance(r.get('icmp_sequence'), int):
            observations[(r.get('segment_index'), r['icmp_sequence'])].append(r)
    by_probe = defaultdict(list)
    for t in transactions:
        g = t.get('generator')
        if g and g.get('episode_id') == episode['episode_id']:
            by_probe[(g['segment_index'], t['sequence'])].append(t)
    publications = sorted((int(a.get('generation_switch_end_monotonic_ns') or a.get('main_end_monotonic_ns') or a.get('host_monotonic_ns', 0)), str(a['active_frontier']))
                          for a in activations if a.get('generation_publication_occurred') is True or a.get('preparation') is True)
    times = [p[0] for p in publications]
    affected = set(episode.get('affected_activation_times', ())) & {s['start_time'] for s in episode['state_intervals'] if s.get('boundary_reasons')}
    report_ranges = {} if report_ranges is None else report_ranges
    outstanding = defaultdict(list)
    for (segment, sequence), reports in observations.items():
        for r in reports:
            if r.get('observation') == 'timeout' and id(r) in report_ranges:
                outstanding[segment].append((sequence, *report_ranges[id(r)]))
    cases = []
    for (segment, sequence), reports in sorted(observations.items()):
        reports.sort(key=lambda r: r['host_monotonic_ns'])
        launch, stop = launches.get(segment, {}), stops.get(segment, {})
        matches = by_probe.get((segment, sequence), ())
        transaction = matches[0] if len(matches) == 1 and not matches[0].get('association_uncertainty') else None
        request = None if transaction is None else transaction.get('request')
        reply = any(r.get('observation') == 'reply' for r in reports) or sequence in stop.get('reply_sequences', ())
        loss = not reply and (any(r.get('observation') in {'timeout', 'network-error'} for r in reports) or sequence in stop.get('timeout_sequences', ()))
        if not reply and not loss:
            continue
        report_ns = min(r['host_monotonic_ns'] for r in reports)
        if request is not None:
            lower = upper = int(request['kernel_monotonic_ns'])
            frontier = request.get('transition_boundary_timestamp') if request.get('transition_boundary_timestamp') is not None and request.get('generation_boundary_timestamp') == request.get('transition_boundary_timestamp') else request.get('formal_boundary_timestamp')
            states = [_state(episode, frontier)]
            basis = 'first-kernel-request-observation'
        else:
            # A timeout is reported after emission. Never apply the report's
            # published frontier as though it were a recorded send timestamp.
            lower = int(launch.get('launch_monotonic_ns', launch.get('host_monotonic_ns', report_ns)))
            upper = report_ns
            prior_reports = [r for r in outstanding[segment] if r[0] < sequence and r[2] <= upper]
            own_reports = [r for r in reports if r.get('observation') == 'timeout' and id(r) in report_ranges]
            bracketed = bool(prior_reports and own_reports)
            if bracketed:
                previous = max(prior_reports, key=lambda r: r[0])
                lower = max(lower, previous[1])
                upper = min(upper, min(report_ranges[id(r)][1] for r in own_reports))
                if lower > upper:
                    lower = int(launch.get('launch_monotonic_ns', launch.get('host_monotonic_ns', report_ns)))
                    upper = report_ns
                    bracketed = False
            first = bisect_right(times, lower) - 1
            last = bisect_right(times, upper)
            frontiers = [p[1] for p in publications[max(0, first):last]]
            if first < 0:
                frontiers.insert(0, launch.get('published_active_frontier'))
            states = [_state(episode, f) for f in frontiers]
            # Historical minimal fixtures may have no clock-aligned activation
            # evidence. Only a constant complete episode can establish truth.
            if not frontiers:
                states = list(episode['state_intervals'])
            basis = 'bracketed-ping-outstanding-report-window' if bracketed else 'generator-launch-to-report-window'
        reachabilities = {s.get('expected_reachable') if s is not None else None for s in states}
        reachability = next(iter(reachabilities)) if len(reachabilities) == 1 else None
        if transaction is not None and transaction.get('planned_reachability') in {'reachable', 'unreachable'}:
            reachability = transaction['planned_reachability'] == 'reachable'
        if matches and transaction is None:
            reachability = None
        classification = ('observed-reachable' if reply else 'unexplained-reachable-loss') if reachability is True else (
            ('unexpected-reachable' if reply else 'expected-unreachable') if reachability is False else 'unclassified')
        association = None
        if request is not None:
            transition = request.get('transition_boundary_timestamp')
            # Both an actual in-flight Main and a relevant planned path/Zone
            # transition are required. An unrelated publication is insufficient.
            if transition is not None and transition in affected:
                association = {'kind': 'actual-path-transition', 'frontier': transition}
            for m in migrations:
                if (m.get('episode_id') == episode['episode_id'] and
                    int(m['migration_start_monotonic_ns']) <= lower <= int(m['migration_end_monotonic_ns'])):
                    association = {'kind': 'actual-generator-migration', 'episode_id': episode['episode_id']}
        cases.append({'segment_index': segment, 'sequence': sequence, 'reply': reply,
                      'classification': classification, 'request_time_basis': basis,
                      'request_interval_monotonic_ns': [lower, upper], 'report_monotonic_ns': report_ns,
                      'actual_transition_association': association, 'recovery': None,
                      'transaction': transaction})
    cases.sort(key=lambda c: (c['request_interval_monotonic_ns'][1], c['segment_index'] or 0, c['sequence']))
    # Normal disruption needs recovery of this logical probe, including across
    # a recorded source migration, within a fixed, disclosed duration bound.
    pending = []
    for case in cases:
        if case['classification'] == 'unexplained-reachable-loss':
            pending.append(case)
        elif case['classification'] == 'observed-reachable':
            if pending:
                first = pending[0]
                duration = (case['request_interval_monotonic_ns'][1] - first['request_interval_monotonic_ns'][0]) / 1e9
                if first['actual_transition_association'] and 0 <= duration <= maximum_disruption_seconds:
                    for lost in pending:
                        lost['classification'] = 'expected-handover-disturbance'
                        lost['recovery'] = {'segment_index': case['segment_index'], 'sequence': case['sequence'], 'duration_seconds': duration}
            pending = []
        else:
            pending = []
    return cases


def _state(episode, frontier):
    if not isinstance(frontier, str):
        return None
    t = datetime.fromisoformat(frontier)
    return next((s for s in episode['state_intervals']
                 if datetime.fromisoformat(s['start_time']) <= t < datetime.fromisoformat(s['end_time'])), None)


def _ping_report_ranges(records):
    """Bound ping -D report time using host wall/monotonic clock samples.

    iputils -O reports an outstanding sequence BEFORE sending the next packet.
    Thus a prior outstanding report and this sequence's report bound emission;
    timeout receipt alone is never treated as send time. No cadence extrapolation
    or fixed RTT deadline is used. Missing/nonmonotonic clocks remain unknown.
    """
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    clocks = []
    for r in records:
        if not isinstance(r.get('wall_time'), str) or not isinstance(r.get('host_monotonic_ns'), int):
            continue
        t = datetime.fromisoformat(r['wall_time']) - epoch
        clocks.append(((t.days*86400+t.seconds)*10**9+t.microseconds*1000, r['host_monotonic_ns']))
    if any(a[0] > b[0] or a[1] > b[1] for a, b in zip(clocks, clocks[1:])):
        return {}
    wall = [c[0] for c in clocks]
    result = {}
    for r in records:
        match = re.match(r'^\[(\d+)\.(\d{1,6})\]', str(r.get('raw_line', '')))
        if match is None or not clocks:
            continue
        at = int(match[1])*10**9+int(match[2].ljust(6,'0'))*1000
        # Equal microsecond timestamps do not establish ordering within that
        # microsecond; require a strictly earlier sample for the lower bound.
        index = bisect_left(wall, at)-1
        if index < 0:
            continue
        # mono[i] precedes wall[i]. wall[i+1] precedes mono[i+2].
        # Receipt of this line is an independent upper bound even when buffered.
        lower = clocks[index][1]
        upper = int(r['host_monotonic_ns'])
        if index+2 < len(clocks):
            upper = min(upper, clocks[index+2][1])
        if lower <= upper:
            result[id(r)] = (lower, upper)
    return result
