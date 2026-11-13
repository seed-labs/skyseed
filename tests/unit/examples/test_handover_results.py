"""A connection with no replies after handover must not look continuous."""
import json

from examples.s4_handover.experiment.observation.results import write_results


def records(path, values):
    path.write_text(''.join(json.dumps(value) + '\n' for value in values))


def test_trailing_tcp_outage_is_counted_and_handover_not_claimed(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (tmp_path / "build.json").write_text('{"ground_stations": 99}')
    (run / 'inputs').mkdir()
    (run / 'inputs/build.json').write_text('{"ground_stations": 25}')
    records(run / "execution.jsonl", [
        {"kind": "ready", "started": 98, "completed": 99},
        {"kind": "clock", "origin": 100, "start_time": "2026-01-01T00:00:00+00:00"},
        {"kind": "drained", "completed": 110}])
    records(run / "attachment.jsonl", [
        {"kind": "traffic", "wall_offset": 1000},
        {"kind": "attachment", "monotonic": 103, "route_activated": 102.9, "previous": "a", "node": "b"}])
    records(run / "tcp-client.jsonl", [
        {"kind": "connected", "monotonic": 101},
        {"kind": "echo", "monotonic": 102, "sent": 101.9, "sequence": 0, "rtt_s": .1},
        {"kind": "error", "monotonic": 110, "message": "timed out"}])
    records(run / "tcp-server.jsonl", [{"kind": "accepted", "monotonic": 101}])
    results = write_results(tmp_path, run)
    assert results['build']['ground_stations'] == 25
    summary = json.loads((run / "summary.json").read_text())
    assert summary['tcp_max_silence_s'] == 8
    assert summary['service_changes'][0]['echo_before_and_after'] is False
    assert summary['tcp_errors']
    assert summary['tcp_pending_at_end'] is None

    # A -> no service -> B must remain visible, but final cleanup must not.
    records(run / "attachment.jsonl", [
        {"kind": "traffic", "wall_offset": 1000},
        {"kind": "attachment", "monotonic": 101, "route_activated": 101, "previous": None, "node": "a"},
        {"kind": "attachment", "monotonic": 103, "route_activated": 103, "previous": "a", "node": None},
        {"kind": "attachment", "monotonic": 105, "route_activated": 105, "previous": None, "node": "b"},
        {"kind": "attachment", "monotonic": 111, "route_activated": 111, "previous": "b", "node": None}])
    write_results(tmp_path, run)
    summary = json.loads((run / "summary.json").read_text())
    assert [event['kind'] for event in summary['service_changes']] == ['disconnect', 'reconnect']
    assert not any(event['echo_before_and_after'] for event in summary['service_changes'])


def test_replay_interpolates_actual_sample_times():
    """Extra operation timestamps must not shift the position interpolation grid."""
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import pytest
    import examples.s4_handover.experiment.observation as observation

    node = os.environ.get('SKYSEED_TEST_NODE') or shutil.which('node')
    if node is None:
        pytest.skip('Node is needed for the replay JavaScript check')
    template = Path(observation.__file__).with_name('replay.html').read_text()
    function = template[template.index('function interpolatedPositions('):template.index('function render(){')]
    program = function + '''
const samples=[{time:0,positions:{a:[0,0,0]}},{time:2,positions:{a:[20,0,0]}},
               {time:5,positions:{a:[50,0,0]}},{time:10,positions:{a:[100,0,0]}}];
for(const [time,expected]of [[-1,0],[0,0],[1,10],[2,20],[5,50],[9,90],[10,100],[11,100]]){
 if(interpolatedPositions(samples,time).a[0]!==expected)throw Error('wrong position at '+time);
}
'''
    subprocess.run([node, '-e', program], check=True, capture_output=True, text=True)
