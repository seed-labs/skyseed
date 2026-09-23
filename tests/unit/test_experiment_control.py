"""Real etcd transport, independent processes, request identity and freshness."""
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from threading import Thread
from time import sleep, monotonic
import json
import pytest
from skyseed.experiment.interaction import InteractionClient, InteractionUnavailable, _OwnerPublisher
from skyseed.experiment import interaction_service
stop_interaction = interaction_service.stop_interaction
from skyseed.experiment._control import _serve_control


@pytest.mark.parametrize('owner', ['computation', 'runtime'])
@pytest.mark.parametrize('outcome', [None, {'status': 'completed'}, {
    'status': 'failed', 'primary_error': {'type': 'RuntimeError', 'message': 'owned drain failed'},
    'cleanup': {'status': 'failed'},
}])
def test_status_cli_distinguishes_closed_outcomes_from_unavailable(tmp_path, capsys, owner, outcome):
    from skyseed.experiment._control import control_main
    client = interaction_service.start_interaction(tmp_path)
    try:
        _OwnerPublisher(client, owner).publish({
            'outcome': outcome,
            'error': {'type': 'RuntimeError', 'message': 'final handoff failed'} if owner == 'runtime' else None,
        }, live=False)
        assert control_main(tmp_path, argv=['status']) == 0
        captured = capsys.readouterr()
        state = json.loads(captured.out)[owner]
        assert state['live'] is False and state['outcome'] == outcome
        label = 'Calculation' if owner == 'computation' else 'Runtime'
        assert f'{label} owner explicitly closed' in captured.err
        assert f'{label} observation unavailable' not in captured.err
        if outcome is None:
            assert 'completion remains indeterminate' in captured.err
        elif outcome['status'] == 'failed':
            assert 'owned drain failed' in captured.err and '"cleanup": {"status": "failed"}' in captured.err
        else:
            assert '"status": "completed"' in captured.err
        if owner == 'runtime':
            assert 'final handoff failed' in captured.err
            assert 'cleanup.sh' in captured.err
    finally:
        client.close()


def test_stale_live_status_never_claims_owner_closed(tmp_path, capsys):
    from skyseed.experiment._control import control_main
    client = interaction_service.start_interaction(tmp_path)
    try:
        publisher = _OwnerPublisher(client, 'runtime')
        publisher.publish({'outcome': None})
        client._put('liveness/runtime', {**publisher._heartbeat(), 'published_unix': 0})
        assert control_main(tmp_path, argv=['status']) == 0
        captured = capsys.readouterr()
        state = json.loads(captured.out)['runtime']
        assert state['stale'] and state['live'] and not state['available']
        assert 'Runtime observation unavailable or stale' in captured.err
        assert 'explicitly closed' not in captured.err
    finally:
        publisher.publish({'outcome': {'status': 'completed'}}, live=False)
        client.close()


def eventually(predicate):
    end = monotonic()+15
    while not predicate():
        assert monotonic()<end
        sleep(.02)


def _owner_process(output, ready):
    stopped = False
    counter = 0
    def dispatch(request):
        nonlocal stopped, counter
        if request['command'] == 'cancel-runtime':
            stopped = True
        elif request['command'] == 'count':
            counter += 1
            return {'count': counter}
        elif request['command'] != 'status':
            raise ValueError('unsupported command')
        return {'runner': {'active_frontier': '2026-01-01T00:00:00+00:00'}, 'count': counter,
                'outcome': {'status': 'completed'} if stopped else None}
    with _serve_control(output, 'runtime', dispatch):
        ready.set()
        while not stopped:
            sleep(.01)


def _write_user(endpoint, identity):
    client = InteractionClient(endpoint, identity)
    client.user_put('opaque', {'researcher': [1, 'value']})


def test_independent_programs_requests_versions_and_watch(tmp_path):
    client = interaction_service.start_interaction(tmp_path)
    context = get_context('spawn')
    writer = context.Process(target=_write_user, args=(client.endpoint,client.experiment_id))
    writer.start();writer.join();assert writer.exitcode == 0
    row = client.user_get('opaque')
    assert row['value'] == {'researcher':[1,'value']}
    event = client.watch_user('opaque', start_revision=row['version'], timeout=2)
    assert event['version'] == row['version']
    with pytest.raises(ValueError, match='version changed'):
        client.user_put('opaque', 9, expected_version=0)
    client.user_put('second', False)
    consistent = client.user_read(['absent','opaque','second'])
    assert consistent['absent'] is None
    assert consistent['opaque']['read_revision'] == consistent['second']['read_revision']
    ready = context.Event(); owner=context.Process(target=_owner_process,args=(tmp_path,ready));owner.start()
    assert ready.wait(15)
    try:
        future = client.submit('runtime','count',at='2026-01-01T01:00:00+00:00')
        receipt = client.submit('runtime','count',request_id='same')
        assert client.wait(receipt,timeout=10)['result'] == {'count':1}
        assert client.submit('runtime','count',request_id='same') == receipt
        with pytest.raises(ValueError,match='different intent'):
            client.submit('runtime','count',request_id='same',delta=1)
        assert client.result(future)['status'] == 'DEFERRED'
        failed=client.submit('runtime','unknown')
        assert client.wait(failed,timeout=10)['status'] == 'REJECTED'
    finally:
        stop=client.submit('runtime','cancel-runtime'); owner.join(15)
    assert not owner.is_alive() and owner.exitcode == 0
    assert client.wait(stop,timeout=2)['status'] == 'SUCCEEDED'
    assert client.result(future)['status'] == 'EXPIRED'
    assert not client.state('runtime')['available']
    with pytest.raises(InteractionUnavailable):client.submit('runtime','count')


def test_reset_changes_session_and_expires_queued_commands(tmp_path, monkeypatch):
    client=interaction_service.start_interaction(tmp_path)
    invocation={'id':'first'}
    original = _OwnerPublisher.update
    outages = []
    def update(self, key, row, status, **facts):
        if status == 'EXPIRED' and not outages:
            outages.append(True)
            raise InteractionUnavailable('fixture loses connection while retiring old session')
        return original(self, key, row, status, **facts)
    monkeypatch.setattr(_OwnerPublisher, 'update', update)
    def dispatch(request):
        if request['command']=='reset':invocation['id']='second'
        return {'invocation_id':invocation['id'],'runner':{'active_frontier':'2026-01-01T00:00:00+00:00'}}
    with _serve_control(tmp_path,'runtime',dispatch):
        old=client.state('runtime')['session_id']
        late=client.submit('runtime','pause',at='2027-01-01T00:00:00+00:00')
        reset=client.submit('runtime','reset')
        eventually(lambda:client.state('runtime')['session_id']!=old)
        assert client.wait(reset,timeout=2)['status']=='SUCCEEDED'
        assert client.wait(late, timeout=5)['status']=='EXPIRED' and outages
        with pytest.raises(ValueError,match='session'):
            client.submit('runtime','count',session_id=old)


def test_unavailable_service_is_not_a_second_control_route(tmp_path):
    client=interaction_service.start_interaction(tmp_path)
    owner=_OwnerPublisher(client,'computation');owner.publish({'state':'IDLE'})
    row=client.state('computation');assert row['owner']=='computation' and row['version']>0
    with pytest.raises(RuntimeError,match='not confirmed closure'):stop_interaction(tmp_path)
    owner.publish({'state':'CLOSED'},live=False)
    stop_interaction(tmp_path)
    with pytest.raises(InteractionUnavailable,match='indeterminate'):
        client.user_put('feedback', 1)
    with pytest.raises(InteractionUnavailable):client.state('computation')


def test_native_etcd_container_endpoint_and_explicit_restart(tmp_path, monkeypatch):
    client = interaction_service.start_interaction(tmp_path)
    config = json.loads((tmp_path / '.control/interaction.json').read_text())
    client.user_put('persistent', {'data': 3})
    monkeypatch.setenv('SKYSEED_INTERACTION_ENDPOINT', 'unix://' + config['client_socket'])
    monkeypatch.setenv('SKYSEED_EXPERIMENT_ID', client.experiment_id)
    container = InteractionClient.from_environment()
    assert container.user_get('persistent')['value'] == {'data': 3}
    container.user_put('returned', True)
    assert client.user_get('returned')['value'] is True
    container.close()
    monkeypatch.delenv('SKYSEED_INTERACTION_ENDPOINT')
    interaction_service.stop_interaction(tmp_path)
    restarted = interaction_service.start_interaction(tmp_path)
    assert restarted.endpoint == client.endpoint
    assert restarted.experiment_id == client.experiment_id
    assert restarted.user_get('persistent')['value'] == {'data': 3}
    restarted.close(); client.close()


def test_lost_result_ack_preserves_admitted_effect_without_reexecution(tmp_path, monkeypatch):
    client = interaction_service.start_interaction(tmp_path)
    effects = []
    real_update = _OwnerPublisher.update
    lost = []
    def update(self, key, row, status, **facts):
        value = real_update(self, key, row, status, **facts)
        if status == 'RUNNING' and 'result' in facts and not lost:
            lost.append(True)
            raise InteractionUnavailable('fixture lost acknowledgement after etcd committed result')
        return value
    monkeypatch.setattr(_OwnerPublisher, 'update', update)
    def dispatch(request):
        if request['command'] == 'count':
            effects.append('executed')
            return {'count': len(effects)}
        return {'invocation_id': 'one', 'runner': {}}
    with _serve_control(tmp_path, 'runtime', dispatch):
        receipt = client.submit('runtime', 'count')
        assert client.wait(receipt, timeout=10)['status'] == 'SUCCEEDED'
        assert effects == ['executed'] and lost


def test_submission_racing_owner_closure_is_not_orphaned(tmp_path, monkeypatch):
    client = interaction_service.start_interaction(tmp_path)
    owner = _OwnerPublisher(client, 'runtime')
    owner.publish({'invocation_id': 'first'})
    original = client._txn
    def close_before_admission(comparisons, puts=(), deletes=()):
        if comparisons and comparisons[0][0].startswith('state-history/'):
            return original(comparisons, puts, deletes)
        owner.publish({'invocation_id': 'first'}, live=False)
        return original(comparisons, puts, deletes)
    monkeypatch.setattr(client, '_txn', close_before_admission)
    with pytest.raises(InteractionUnavailable):
        client.submit('runtime', 'count', request_id='closure-race')
    assert client._read('requests/', prefix=True) == []


def test_service_stop_excludes_unpublished_owner_startup(tmp_path):
    from skyseed.experiment.output.occupancy import claim_output, claim_runtime
    client = interaction_service.start_interaction(tmp_path)
    with claim_output(tmp_path, computation=True):
        with pytest.raises(RuntimeError, match='actively owned'):
            stop_interaction(tmp_path)
    with claim_runtime(tmp_path):
        with pytest.raises(RuntimeError, match='runtime is active'):
            stop_interaction(tmp_path)
    client.user_put('still-running', True)


def test_initializing_computation_defers_without_blocking_immediate_cancel(tmp_path):
    client = interaction_service.start_interaction(tmp_path)
    ready, cancelled = False, []
    def dispatch(request):
        if request['command'] == 'cancel-computation':
            cancelled.append(True)
        if request['command'] == 'revise':
            assert ready
            raise ValueError('fixture has no replaceable range')
        return {'state': 'INITIALIZING', 'producer_ready': ready}
    with _serve_control(tmp_path, 'computation', dispatch):
        eventually(lambda: client.state('computation')['available'])
        receipt = client.submit('computation', 'revise')
        eventually(lambda: client.result(receipt)['status'] == 'DEFERRED')
        client.submit('computation', 'cancel-computation')
        eventually(lambda: bool(cancelled))
        ready = True
        assert client.wait(receipt, timeout=5)['status'] == 'REJECTED'


def test_resume_expires_an_admitted_future_pause(tmp_path):
    client = interaction_service.start_interaction(tmp_path)
    runner = {'active_frontier': '2026-01-01T00:00:00+00:00',
              'paused': False, 'pause_requested': False}
    def dispatch(request):
        if request['command'] == 'pause':
            runner['pause_scheduled_at'] = request['at']
        elif request['command'] == 'resume':
            runner['pause_scheduled_at'] = None
        return {'invocation_id': 'first', 'runner': dict(runner)}
    with _serve_control(tmp_path, 'runtime', dispatch):
        pause = client.submit('runtime', 'pause', at='2027-01-01T00:00:00+00:00')
        eventually(lambda: client.result(pause)['status'] == 'RUNNING')
        resume = client.submit('runtime', 'resume')
        assert client.wait(resume, timeout=5)['status'] == 'SUCCEEDED'
        result = client.wait(pause, timeout=5)
        assert result['status'] == 'EXPIRED' and result['superseded_by'] == resume.request_id


def test_pending_acceptance_order_dedup_and_future_bypass(tmp_path, monkeypatch):
    from threading import Event
    client = interaction_service.start_interaction(tmp_path)
    entered, release = Event(), Event()
    pending = _OwnerPublisher.pending
    def gated_pending(self):
        if not entered.is_set():
            entered.set()
            assert release.wait(15)
        return pending(self)
    monkeypatch.setattr(_OwnerPublisher, 'pending', gated_pending)
    effects = []
    paused = False
    def dispatch(request):
        nonlocal paused
        command = request['command']
        if command != 'status':
            effects.append(command)
            if command in {'pause', 'resume'}:
                paused = command == 'pause'
        return {'invocation_id': 'ordered', 'runner': {
            'active_frontier': '2026-01-01T00:00:00+00:00',
            'pause_requested': paused, 'paused': paused}}
    with _serve_control(tmp_path, 'runtime', dispatch):
        try:
            assert entered.wait(15)
            future = client.submit('runtime', 'count', request_id='future', at='2027-01-01T00:00:00+00:00')
            pause = client.submit('runtime', 'pause', request_id='z-first')
            resume = client.submit('runtime', 'resume', request_id='a-second')
            assert client.submit('runtime', 'pause', request_id='z-first') == pause
        finally:
            release.set()
        assert client.wait(resume, timeout=10)['status'] == 'SUCCEEDED'
        result = client.wait(pause, timeout=10)
        assert result['status'] == 'EXPIRED' and result['superseded_by']
        assert effects == ['pause', 'resume'] and not paused
        assert client.submit('runtime', 'pause', request_id='z-first') == pause
        assert effects == ['pause', 'resume']
        assert client.result(future)['status'] == 'DEFERRED'
    assert client.result(future)['status'] == 'EXPIRED'


def test_idle_liveness_and_deferred_requests_do_not_rewrite_meaningful_state(tmp_path):
    client = interaction_service.start_interaction(tmp_path)
    facts = {'state': 'IDLE', 'completed_end': '2026-01-01T00:00:00+00:00'}
    with _serve_control(tmp_path, 'computation', lambda request: dict(facts)):
        eventually(lambda: client.state('computation')['available'])
        receipt = client.submit('computation', 'continue', at='2027-01-01T00:00:00+00:00')
        eventually(lambda: client.result(receipt)['status'] == 'DEFERRED')
        initial, request = client.state('computation'), client.result(receipt)
        eventually(lambda: client.state('computation')['liveness_version'] > initial['liveness_version'])
        current = client.state('computation')
        assert current['available'] and current['version'] == initial['version']
        assert current['fresh_for_seconds'] == 5.
        assert client.result(receipt) == request
        assert len(client.state_history('computation')) == 1
        # No control request causes this change: state observation itself owns it.
        facts['completed_end'] = '2026-01-01T00:00:10+00:00'
        event = client.watch_state('computation', start_revision=current['version'] + 1, timeout=3)
        assert event['completed_end'] == facts['completed_end']
        assert event['session_id'] == initial['session_id'] and event['sequence'] == 2
        assert event['published_at'] >= initial['published_at']
        history = client.state_history('computation', after_revision=initial['version'], limit=1)
        assert len(history) == 1 and history[0]['version'] == event['version']
        pinned = client.state('computation', revision=initial['read_revision'])
        assert pinned['completed_end'] == initial['completed_end']
        assert pinned['read_revision'] == initial['read_revision']
        assert client.result(receipt)['version'] == request['version']
    closed = client.state_history('computation')[-1]
    assert not closed['live'] and closed['sequence'] == 3
    assert client.result(receipt)['status'] == 'EXPIRED'


def test_state_history_lost_ack_and_reset_do_not_duplicate_changes(tmp_path, monkeypatch):
    client = interaction_service.start_interaction(tmp_path)
    publisher = _OwnerPublisher(client, 'runtime')
    real = client._txn
    lost = []
    def commit_then_disconnect(*args, **kwargs):
        result = real(*args, **kwargs)
        if not lost:
            lost.append(True)
            raise InteractionUnavailable('lost publication acknowledgement')
        return result
    monkeypatch.setattr(client, '_txn', commit_then_disconnect)
    facts = {'runner': {'frozen_through': 0}}
    with pytest.raises(InteractionUnavailable): publisher.publish(facts)
    publisher.publish(facts)
    assert len(client.state_history('runtime')) == 1
    # Cache does not alias caller-owned mutable facts.
    facts['runner']['frozen_through'] = 1
    publisher.publish(facts)
    successor = _OwnerPublisher(client, 'runtime')
    successor.publish(facts)
    rows = client.state_history('runtime')
    assert [r['sequence'] for r in rows] == [1, 2, 1]
    assert rows[0]['session_id'] != rows[2]['session_id']
    assert [r['version'] for r in rows] == sorted(r['version'] for r in rows)
    with pytest.raises(ValueError, match='session'):
        client.submit('runtime', 'count', session_id=publisher.session_id)
    heartbeat = client._get('liveness/runtime')['value']
    client._put('liveness/runtime', {**heartbeat, 'published_unix': 0.})
    assert client.state('runtime')['stale']
    with pytest.raises(InteractionUnavailable): client.submit('runtime', 'count')
    successor.publish(facts, live=False)


def test_owned_compaction_preserves_records_and_rejects_lost_mvcc_history(tmp_path):
    from skyseed.experiment import InteractionHistoryCompacted
    client = interaction_service.start_interaction(tmp_path)
    config = json.loads((tmp_path / '.control/interaction.json').read_text())
    assert config['mvcc_retention'] == {'mode': 'periodic', 'retention': '1h'}
    arguments = Path(f'/proc/{config["pid"]}/cmdline').read_bytes().split(b'\0')
    assert b'--auto-compaction-retention' in arguments and b'1h' in arguments
    publisher = _OwnerPublisher(client, 'computation')
    publisher.publish({'state': 'IDLE'})
    old = client.state('computation')
    receipt = client.submit('computation', 'count')
    old_user = client.user_put('retained', 1)
    client.user_put('retained', 2)
    publisher.publish({'state': 'CLOSED'}, live=False)
    newest = client.state('computation')['read_revision']
    client._call('post', client._store.get_url('/kv/compaction'), json={'revision': str(newest), 'physical': True})
    with pytest.raises(InteractionHistoryCompacted): client.state('computation', revision=old['version'])
    with pytest.raises(InteractionHistoryCompacted): client.user_get('retained', revision=old_user['version'])
    with pytest.raises(InteractionHistoryCompacted): client.watch_state('computation', start_revision=old['version'], timeout=2)
    with pytest.raises(InteractionHistoryCompacted): client.watch_user('retained', start_revision=old_user['version'], timeout=2)
    assert client.user_get('retained')['value'] == 2
    assert client.result(receipt)['status'] == 'SUBMITTED'
    assert len(client.state_history('computation')) == 2
    with pytest.raises(TimeoutError): client.watch_state('computation', start_revision=newest + 1, timeout=.1)


def test_external_attachment_has_no_framework_compaction_policy(tmp_path, monkeypatch):
    client = interaction_service.start_interaction(tmp_path / 'shared-service')
    def unexpected_start(*args, **kwargs):
        raise AssertionError('external attachment must not configure/start the server')
    monkeypatch.setattr(interaction_service.subprocess, 'Popen', unexpected_start)
    external = interaction_service.start_interaction(tmp_path / 'attached', endpoint=client.endpoint)
    config = json.loads((tmp_path / 'attached/.control/interaction.json').read_text())
    assert config['owned'] is False and config['mvcc_retention'] is None
    external.user_put('preserved', True)
    interaction_service.stop_interaction(tmp_path / 'attached')
    assert external.user_get('preserved')['value'] is True
    external.close()


@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('status', ['SUCCEEDED', 'FAILED', 'REJECTED', 'EXPIRED'])
def test_terminal_publication_loss_is_recovered_without_overwrite(tmp_path, monkeypatch, committed, status):
    client = interaction_service.start_interaction(tmp_path)
    owner = _OwnerPublisher(client, 'runtime');owner.publish({'invocation_id': 'one'})
    receipt = client.submit('runtime', 'adopt')
    key = f'requests/runtime/{receipt.session_id}/{receipt.request_id}'
    row = client._get(key)['value']
    running = owner.update(key, row, 'RUNNING')
    real = client._txn
    lost = []
    def txn(comparisons, puts=(), deletes=()):
        if not lost and any(k == key and value['status'] == status for k, value in puts):
            lost.append(True)
            if committed:real(comparisons, puts, deletes)
            raise InteractionUnavailable('terminal result transport loss')
        return real(comparisons, puts, deletes)
    monkeypatch.setattr(client, '_txn', txn)
    with pytest.raises(InteractionUnavailable):
        owner.update(key, running, status, result={'adopted': status == 'SUCCEEDED', 'evidence': 'preserve'})
    owner.publish({'invocation_id': 'one', 'outcome': {'status': 'completed'}}, live=False)
    after = client.result(receipt)
    assert after['status'] == status and after['result']['evidence'] == 'preserve'
    assert [r['status'] for r in after['history']].count(status) == 1
    assert not owner.pending()
    owner.update(key, running, 'EXPIRED', result={'reason': 'owner closed'})
    assert client.result(receipt) == after


@pytest.mark.parametrize('transition', ['reset', 'closure'])
def test_unknown_dispatched_effect_is_not_expired_or_reexecuted(tmp_path, transition):
    client = interaction_service.start_interaction(tmp_path)
    state = {'invocation_id': 'one', 'runner': {}, 'outcome': {'status': 'completed'}}
    calls = []
    def dispatch(request):
        if request['command'] == 'adopt':
            calls.append('adopt')
            raise InteractionUnavailable('effect outcome unknown')
        if request['command'] == 'reset':state['invocation_id'] = 'two'
        return dict(state)
    with _serve_control(tmp_path, 'runtime', dispatch):
        receipt = client.submit('runtime', 'adopt')
        eventually(lambda: calls)
        if transition == 'reset':
            reset = client.submit('runtime', 'reset')
            assert client.wait(reset, timeout=5)['status'] == 'SUCCEEDED'
    assert calls == ['adopt']
    assert client.result(receipt)['status'] == 'RUNNING'
    assert 'result' not in client.result(receipt)


@pytest.mark.parametrize('committed', [False, True])
def test_failed_dispatch_terminal_write_loss_does_not_repeat_effect(tmp_path, monkeypatch, committed):
    client = interaction_service.start_interaction(tmp_path)
    effects, lost = [], []
    real = InteractionClient._txn
    def txn(self, comparisons, puts=(), deletes=()):
        if not lost and any(k.startswith('requests/') and v.get('status') == 'FAILED' for k, v in puts):
            lost.append(True)
            if committed:real(self, comparisons, puts, deletes)
            raise InteractionUnavailable('failed dispatch result write lost')
        return real(self, comparisons, puts, deletes)
    monkeypatch.setattr(InteractionClient, '_txn', txn)
    def dispatch(request):
        if request['command'] == 'act':
            effects.append('acted');raise OSError('failure after effect')
        return {'invocation_id': 'one', 'runner': {}, 'outcome': {'status': 'completed'}}
    with _serve_control(tmp_path, 'runtime', dispatch):
        receipt = client.submit('runtime', 'act')
        result = client.wait(receipt, timeout=10)
        assert result['status'] == 'FAILED' and 'failure after effect' in result['error']
    assert client.result(receipt) == result and effects == ['acted'] and lost


def test_owner_exit_without_drainage_outcome_does_not_complete_cancel(tmp_path):
    client = interaction_service.start_interaction(tmp_path)
    calls = []
    def dispatch(request):
        if request['command'] == 'cancel-runtime':calls.append('cancel')
        return {'invocation_id': 'one', 'runner': {}, 'outcome': None}
    with _serve_control(tmp_path, 'runtime', dispatch):
        receipt = client.submit('runtime', 'cancel-runtime')
        eventually(lambda: calls)
    assert not client.state('runtime')['live']
    assert client.result(receipt)['status'] == 'RUNNING'
