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


def test_forwarded_reply_without_delivery_evidence_stays_unconfirmed():
    row = _ping_transactions((packet(1, endpoint=True), packet(1.1, 0)),
                             probe_execution=lifetime(1, 0, 2))[0]
    assert row['observed_reply'] is True
    assert row['reply_delivery'] == 'unconfirmed'


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


def sender_reply(at, *, sequence=1, duplicate=False, segment=1):
    return {'record_type': 'ping_observation', 'communication_id': 'episode', 'segment_index': segment,
            'host_monotonic_ns': int(at * 1e9), 'observation': 'reply', 'icmp_identifier': 40226,
            'icmp_sequence': sequence, 'duplicate': duplicate}


def test_actual_reply_stream_confirms_delivery_without_terminal_sequence_lists():
    launch = lifetime(1, 0, 2)[0]  # Even if terminal recording was interrupted.
    row = _ping_transactions((packet(1),), probe_execution=(launch, sender_reply(1.1), sender_reply(1.2, duplicate=True)))[0]
    assert row['reply_delivery'] == 'executor-confirmed'
    assert row['request_packet_indices'] == [0]
    assert row['pairing_status'] == 'unpaired'  # No fabricated BPF reply hop.


def test_sender_reuse_wrap_and_incomplete_observations_remain_unknown():
    launch, stop = lifetime(1, 0, 5)
    for observations in ((sender_reply(.5),), (sender_reply(1.1), sender_reply(3.1)),
                         ({**sender_reply(1.1), 'observation': 'timeout'},)):
        row = _ping_transactions((packet(1),), probe_execution=(launch, *observations))[0]
        assert row['reply_delivery'] == 'unconfirmed'
    repeated = [dict(packet(at), local_origin=True) for at in (1, 3)]
    row = _ping_transactions(repeated, probe_execution=(launch, sender_reply(3.1)))[0]
    assert row['pairing_status'] == 'ambiguous' and row['reply_delivery'] == 'unconfirmed'
    # Keep the real historical terminal-only contract, but do not prefer it
    # over an incomplete current observation stream.
    historical = dict(stop, reply_sequences=[1])
    assert _ping_transactions((packet(1),), probe_execution=(launch, historical))[0]['reply_delivery'] == 'executor-confirmed'
    rows = _ping_transactions((packet(1),), probe_execution=(launch, historical,
        {**sender_reply(1.1), 'observation': 'timeout'}))
    assert rows[0]['reply_delivery'] == 'unconfirmed'
