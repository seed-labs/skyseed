"""Probe identity and forwarding observations are different cardinalities."""
from datetime import datetime, timezone, timedelta

from tools.observation.traffic.reader import _ping_transactions

ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def packet(at, kind=8, node='sat', *, endpoint=False, sequence=1):
    source, destination = ('100.64.0.133', '198.18.0.17')
    if kind == 0:
        source, destination = destination, source
    return {'protocol': 1, 'kernel_monotonic_ns': int(at * 1e9),
            'simulation_timestamp': (ORIGIN + timedelta(seconds=at)).isoformat(),
            'source_ipv4': source, 'destination_ipv4': destination, 'node_id': node,
            'decision': 'local/pass' if endpoint else 'redirect',
            'reason': 'local-route' if endpoint else 'route-redirect',
            'icmp': {'valid': True, 'type': kind, 'identifier': 40226, 'sequence': sequence}}


def lifetime(segment, start, end, *, sent=1):
    return ({'record_type': 'generator_launched', 'episode_id': 'episode',
             'segment_index': segment, 'icmp_identifier': 40226,
             'source_ipv4': '100.64.0.133', 'destination_ipv4': '198.18.0.17',
             'launch_monotonic_ns': int(start*1e9), 'host_monotonic_ns': int(start*1e9)},
            {'record_type': 'generator_stopped', 'episode_id': 'episode',
             'segment_index': segment, 'stop_monotonic_ns': int(end*1e9), 'sent': sent})


def test_zone_success_retains_all_three_or_eight_hops():
    for hops in (3, 8):
        packets = tuple(packet(1+i*.001, node=f'sat{i}', endpoint=i==hops-1) for i in range(hops))
        packets += tuple(packet(1.1+i*.001, 0, node=f'return{i}', endpoint=i==hops-1) for i in range(hops))
        rows = _ping_transactions(packets, nodes_by_address={})
        assert len(rows) == 1
        row = rows[0]
        assert row['pairing_status'] == 'paired'
        assert row['reply_delivery'] == 'endpoint-observed'
        assert row['packet_observations'] == hops*2
        assert row['request_packet_indices'] + row['reply_packet_indices'] == list(range(hops*2))
        assert row['request']['kernel_monotonic_ns'] == int(1e9)


def test_reused_identity_is_partitioned_by_generator_and_late_reply_is_uncertain():
    records = (*lifetime(1, 0, 2), *lifetime(2, 3, 5))
    rows = _ping_transactions((packet(1, endpoint=True), packet(2.5, 0, endpoint=True),
                               packet(4, endpoint=True), packet(4.1, 0, endpoint=True)),
                              probe_execution=records)
    first = next(row for row in rows if row['generator'] and row['generator']['segment_index'] == 1)
    assert first['reply_packet_indices'] == [1]  # retained beyond generator stop
    assert all(row['pairing_status'] == 'ambiguous' for row in rows)
    assert sum(row['packet_observations'] for row in rows) == 4
    orphan = next(row for row in rows if row['generator'] is None)
    assert orphan['reply_packet_indices'] == [3]  # could be a delayed duplicate of first probe


def test_migration_and_repeated_hop_observation_do_not_create_sends():
    p = packet(1, endpoint=True)
    rows = _ping_transactions((p, dict(p), packet(1.1, 0, endpoint=True)),
                              probe_execution=lifetime(1, 0, 2))
    assert len(rows) == 1 and rows[0]['packet_observations'] == 3
    assert rows[0]['pairing_status'] == 'paired'


def test_sequence_wrap_and_missing_endpoint_identity_remain_explicit():
    rows = _ping_transactions((packet(1), packet(1.1, 0)), probe_execution=lifetime(1, 0, 2, sent=65537))
    assert rows[0]['pairing_status'] == 'ambiguous'
    assert rows[0]['reply_delivery'] == 'unconfirmed'
    assert 'wrap' in rows[0]['association_uncertainty'][0]


def test_forwarded_reply_without_delivery_evidence_is_not_a_confirmed_loss():
    from tools.observation.traffic.projection import _project_transaction
    row = _ping_transactions((packet(1, endpoint=True), packet(1.1, 0)),
                             probe_execution=lifetime(1, 0, 2))[0]
    ground = {'asn': 65040, 'entity_kind': 'ground-station', 'entity_id': 'gs'}
    route = {'kind': 'reachable', 'path': [ground],
             'demand': {'source': ground, 'target': {'kind': 'zone', 'zone_id': 'z'}}}
    projected = _project_transaction(row, boundary={'timestamp': ORIGIN.isoformat()},
        zone_assignments={}, route_outcomes=[route],
        nodes_by_address={'100.64.0.133': {'node_id': '65040/ground-station/gs'}},
        zone_prefixes={('', 'z'): '198.18.0.17/32'}, activation_times=())
    assert projected['observed_reply'] is True
    assert projected['reply_delivery'] == 'unconfirmed'
    assert projected['classification'] == 'unclassified'


def test_health_requires_actual_relevant_transition_duration_and_recovery():
    from tools.observation.traffic._probe_cases import _request_cases
    episode = {'episode_id': 'e', 'affected_activation_times': ['2026-01-01T00:00:05+00:00'],
               'state_intervals': [{'start_time': ORIGIN.isoformat(), 'end_time': (ORIGIN+timedelta(seconds=20)).isoformat(), 'expected_reachable': True}]}
    episode['state_intervals'] = [
        {'start_time': ORIGIN.isoformat(), 'end_time': '2026-01-01T00:00:05+00:00', 'expected_reachable': True},
        {'start_time': '2026-01-01T00:00:05+00:00', 'end_time': '2026-01-01T00:00:20+00:00', 'expected_reachable': True, 'boundary_reasons': ['zone-service-handover']}]
    records = [{'record_type': 'generator_launched', 'segment_index': 1, 'host_monotonic_ns': 0, 'published_active_frontier': ORIGIN.isoformat()}]
    records += [{'record_type': 'ping_observation', 'segment_index': 1, 'icmp_sequence': seq,
                 'observation': result, 'host_monotonic_ns': int(at*1e9)}
                for seq, result, at in [(1, 'timeout', 6), (2, 'reply', 6.1)]]
    tx = [{'generator': {'episode_id': 'e', 'segment_index': 1}, 'sequence': seq,
           'planned_reachability': 'reachable', 'request': {'kernel_monotonic_ns': int(at*1e9),
            'formal_boundary_timestamp': ORIGIN.isoformat(), 'transition_boundary_timestamp': transition}}
          for seq, at, transition in [(1, 5.005, '2026-01-01T00:00:05+00:00'), (2, 6, None)]]
    cases = _request_cases(episode, records, tx, (), ())
    assert cases[0]['classification'] == 'expected-handover-disturbance'
    assert cases[0]['recovery']['duration_seconds'] == .995
    assert _request_cases(episode, records, tx, (), (), maximum_disruption_seconds=.5)[0]['classification'] == 'unexplained-reachable-loss'
    tx[0]['request']['transition_boundary_timestamp'] = None
    assert _request_cases(episode, records, tx, (), ())[0]['classification'] == 'unexplained-reachable-loss'
    tx[0]['request']['transition_boundary_timestamp'] = '2026-01-01T00:00:10+00:00'
    assert _request_cases(episode, records, tx, (), ())[0]['classification'] == 'unexplained-reachable-loss'


def test_timeout_then_late_reply_is_one_success_not_one_loss_plus_success():
    from tools.observation.traffic._probe_cases import _request_cases
    episode = {'episode_id': 'e', 'state_intervals': [{'start_time': ORIGIN.isoformat(), 'end_time': (ORIGIN+timedelta(seconds=20)).isoformat(), 'expected_reachable': True}]}
    records = [{'record_type': 'generator_launched', 'segment_index': 1, 'host_monotonic_ns': 0, 'published_active_frontier': ORIGIN.isoformat()}]
    records += [{'record_type': 'ping_observation', 'segment_index': 1, 'icmp_sequence': 1,
                 'observation': result, 'host_monotonic_ns': at} for result, at in [('timeout', 200), ('reply', 300)]]
    cases = _request_cases(episode, records, (), (), ())
    assert len(cases) == 1 and cases[0]['classification'] == 'observed-reachable'
    records[0].pop('published_active_frontier')
    assert _request_cases(episode, records, (), (), ())[0]['classification'] == 'unclassified'


def test_outstanding_reports_bound_missing_send_time_without_using_receipt_as_send():
    from tools.observation.traffic._probe_cases import _ping_report_ranges, _request_cases
    def record(at, **values):
        return {'wall_time': (ORIGIN+timedelta(seconds=at)).isoformat(),
                'host_monotonic_ns': int(at*1e9), **values}
    records = [record(0, record_type='generator_launched', segment_index=1,
                      published_active_frontier=ORIGIN.isoformat())]
    records += [record(at, record_type='clock') for at in (3, 4, 5, 6)]
    records += [record(7+seq*.1, record_type='ping_observation', segment_index=1,
                       icmp_sequence=seq, observation='timeout',
                       raw_line=f'[{int(ORIGIN.timestamp())+at}.500000] no answer yet for icmp_seq={seq}')
                for seq, at in ((1, 4), (2, 5))]
    ranges = _ping_report_ranges(records)
    # Buffered receipt at 7.1/7.2 does not move the request into the later state.
    assert ranges[id(records[-2])] == (int(4e9), int(6e9))
    assert ranges[id(records[-1])] == (int(5e9), int(7.1e9))
    episode = {'episode_id': 'e', 'state_intervals': [
        {'start_time': ORIGIN.isoformat(), 'end_time': (ORIGIN+timedelta(seconds=3)).isoformat(), 'expected_reachable': True},
        {'start_time': (ORIGIN+timedelta(seconds=3)).isoformat(), 'end_time': (ORIGIN+timedelta(seconds=20)).isoformat(), 'expected_reachable': False}]}
    activations = [{'preparation': True, 'host_monotonic_ns': 0, 'active_frontier': ORIGIN.isoformat()},
                   {'generation_publication_occurred': True, 'generation_switch_end_monotonic_ns': int(3e9),
                    'active_frontier': (ORIGIN+timedelta(seconds=3)).isoformat()}]
    cases = _request_cases(episode, records, (), activations, (), report_ranges=ranges)
    assert cases[0]['classification'] == 'unclassified'
    assert cases[1]['classification'] == 'expected-unreachable'
    assert cases[1]['request_interval_monotonic_ns'] == [int(4e9), int(7.1e9)]
    assert _request_cases(episode, records, (), activations, ())[1]['classification'] == 'unclassified'
    # Clock reversal cannot support an emission bracket.
    records[-1]['wall_time'] = ORIGIN.isoformat()
    assert _ping_report_ranges(records) == {}


def test_in_flight_hops_cross_generator_stop_without_identity_reuse():
    rows = _ping_transactions((packet(1.99), packet(2.01, endpoint=True),
                               packet(2.10, 0, endpoint=True)),
                              probe_execution=lifetime(1, 0, 2))
    assert len(rows) == 1
    assert rows[0]['pairing_status'] == 'paired'
    assert rows[0]['request_packet_indices'] == [0, 1]
    assert rows[0]['reply_packet_indices'] == [2]


def test_late_request_hop_with_intervening_reuse_remains_uncertain():
    rows = _ping_transactions((packet(1.99), packet(2.04), packet(2.10, 0)),
                              probe_execution=(*lifetime(1, 0, 2), *lifetime(2, 2.01, 2.03)))
    assert sum(r['packet_observations'] for r in rows) == 3
    assert any(r['pairing_status'] == 'ambiguous' for r in rows)


def test_shell_transition_coverage_requires_relevant_later_observation():
    from tools.observation.traffic.projection import _runtime_shell_coverage
    stamp = lambda at: (ORIGIN + timedelta(seconds=at)).isoformat()
    episode = {'episode_id': 'e', 'probe_id': 'p', 'start_time': stamp(0), 'end_time': stamp(10),
        'affected_activation_times': [stamp(5)], 'coverage_cases': [{'shell_id': 'S1', 'case': 'transition'}],
        'state_intervals': [{'start_time': stamp(a), 'end_time': stamp(b), 'expected_reachable': True,
            'path_node_ids': ['s'], 'boundary_reasons': reasons}
            for a,b,reasons in [(0,5,[]),(5,10,['zone-service-handover'])]]}
    qualification = {'plan': {'probe_identities': [{'probe_id':'p','direction':'ground-to-zone'}],
        'episodes': [episode], 'shell_coverage': [{'shell_id':'S1','eligible':True,'goals':[
            {'direction':'ground-to-zone','case':'transition','selected':True,'eligible_candidate_observations':1}]}]}}
    tx = {'generator':{'episode_id':'e','probe_id':'p'}, 'planned_shell_ids':['S1'],
          'request':{'simulation_timestamp':stamp(3)}, 'reply_delivery':'endpoint-observed'}
    assert _runtime_shell_coverage(qualification,[tx],{'s':'S1'})[0]['status'] == 'coverage-gap'
    tx['request']['simulation_timestamp'] = stamp(5.5)
    assert _runtime_shell_coverage(qualification,[tx],{'s':'S1'})[0]['status'] == 'covered'
    tx['request']['simulation_timestamp'] = stamp(9)
    assert _runtime_shell_coverage(qualification,[tx],{'s':'S1'})[0]['status'] == 'coverage-gap'
    tx['request']['simulation_timestamp'] = stamp(5.5)
    episode['affected_activation_times'] = [stamp(7)]
    assert _runtime_shell_coverage(qualification,[tx],{'s':'S1'})[0]['status'] == 'coverage-gap'
