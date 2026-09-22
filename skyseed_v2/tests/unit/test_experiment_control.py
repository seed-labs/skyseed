"""Closing owner endpoints cannot hide the other owner or imply mutation success."""
from contextlib import contextmanager
import json
import socket
from threading import Thread

import pytest
from skyseed_v2.experiment import _control as control


@contextmanager
def closing_endpoint(output, owner, accepted):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(control._address(output, owner))
        server.listen()
        def close_after_acceptance():
            with server.accept()[0] as connection, connection.makefile('rb') as stream:
                accepted.append(json.loads(stream.readline()))
                # No response: the client cannot know a mutation's outcome.
        thread = Thread(target=close_after_acceptance)
        thread.start()
        try:
            yield
        finally:
            thread.join()


def test_status_keeps_computation_addressable_when_runtime_closes(tmp_path, capsys):
    accepted = []
    with control._serve_control(tmp_path, 'computation', lambda request: {'state': 'IDLE', 'cancellation_requested': False}):
        with closing_endpoint(tmp_path, 'runtime', accepted):
            assert control.control_main(tmp_path, argv=['status']) == 0
        status = json.loads(capsys.readouterr().out)
        assert status['computation'] == {'state': 'IDLE', 'cancellation_requested': False}
        assert status['runtime']['available'] is False
        assert 'closed before replying' in status['runtime']['reason']
        assert control._request(tmp_path, 'computation', 'status')['state'] == 'IDLE'
    assert accepted == [{'command': 'status'}]


@pytest.mark.parametrize('command', ['continue', 'reset', 'cancel-runtime', 'shutdown'])
def test_lost_mutation_response_stays_unknown_and_is_not_retried(tmp_path, command):
    accepted = []
    with closing_endpoint(tmp_path, 'runtime', accepted):
        with pytest.raises(RuntimeError, match='operation completion is unknown') as failure:
            control._request(tmp_path, 'runtime', command)
        assert not isinstance(failure.value, control._ControlUnavailable)
        assert isinstance(failure.value.__cause__, EOFError)
    assert accepted == [{'command': command}]


@pytest.mark.parametrize('failure', [ConnectionResetError('closed peer'), BrokenPipeError('closed peer')])
@pytest.mark.parametrize('command', ['status', 'reset'])
def test_connection_reset_has_read_only_status_semantics(tmp_path, monkeypatch, failure, command):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect(self, address): pass
        def makefile(self, mode): return self
        def write(self, value): raise failure
    monkeypatch.setattr(control.socket, 'socket', lambda *a: Connection())
    with pytest.raises(RuntimeError) as caught:
        control._request(tmp_path, 'runtime', command)
    assert isinstance(caught.value, control._ControlUnavailable) == (command == 'status')
    assert caught.value.__cause__ is failure


def test_explicit_dispatch_failure_remains_a_known_failure(tmp_path):
    def failed(request): raise ValueError('known operation failure')
    with control._serve_control(tmp_path, 'computation', failed):
        with pytest.raises(RuntimeError, match='ValueError: known operation failure') as failure:
            control._request(tmp_path, 'computation', 'continue')
        assert not isinstance(failure.value, control._ControlUnavailable)


@pytest.mark.parametrize('command,owner,terminal,completion', [
    ('cancel-runtime', 'runtime', 'run.sh execution terminal', 'Runtime resource cleanup succeeded'),
    ('cancel-computation', 'computation', 'calculation terminal', 'Calculation session closed'),
])
def test_cancel_acknowledges_request_without_changing_json(tmp_path, capsys, command, owner, terminal, completion):
    response = {'cancel_requested': True, 'completed': False}
    with control._serve_control(tmp_path, owner, lambda request: response):
        assert control.control_main(tmp_path, argv=[command]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == response
    assert 'request accepted' in captured.err and 'not yet confirmed' in captured.err
    assert terminal in captured.err and completion in captured.err


@pytest.mark.parametrize('command,owner', [('cancel-runtime', 'runtime'), ('cancel-computation', 'computation')])
@pytest.mark.parametrize('lost_reply', [False, True])
def test_unavailable_or_lost_cancel_does_not_claim_acceptance(tmp_path, capsys, command, owner, lost_reply):
    from contextlib import nullcontext
    accepted = []
    with closing_endpoint(tmp_path, owner, accepted) if lost_reply else nullcontext():
        with pytest.raises(SystemExit) as caught:
            control.control_main(tmp_path, argv=[command])
    captured = capsys.readouterr()
    assert caught.value.code == 1 and not captured.out
    assert 'request accepted' not in captured.err and 'succeeded' not in captured.err
    assert ('completion is unknown' if lost_reply else 'session is unavailable') in captured.err


def test_console_feedback_flushes_and_cannot_replace_operation_errors(monkeypatch):
    from io import StringIO
    from skyseed_v2.shared import _console as console
    class Stream(StringIO):
        flushed = False
        def flush(self): self.flushed = True
    stream = Stream()
    with monkeypatch.context() as patch:
        patch.setattr(console.sys, 'stderr', stream)
        console._console('lifecycle milestone')
        assert stream.flushed and stream.getvalue() == 'lifecycle milestone\n'
        stream.close()
        console._console('closed destination')


@pytest.mark.parametrize('runtime,expected', [
    ({'runner': {'lifecycle_state': 'preparing'}, 'initial_readiness': {'status': 'waiting'}}, 'User preparation: waiting'),
    ({'runner': {'lifecycle_state': 'prepared', 'paused': True}}, 'use resume'),
    ({'runner': {'lifecycle_state': 'prepared', 'waiting_for_material': True}}, 'Use continue'),
    ({'runner': {'cancel_requested': True}}, 'owned work is draining'),
    ({'runner': {'maintenance_error': 'broken worker'}, 'delivery_error': 'broken delivery'}, 'broken worker'),
    ({'available': False}, 'cleanup is not confirmed'),
])
def test_status_summary_precedes_unchanged_json(tmp_path, monkeypatch, runtime, expected):
    from io import StringIO
    from skyseed_v2.shared import _console as console
    writes = []
    class Stream(StringIO):
        def __init__(self, name): super().__init__(); self.name = name
        def write(self, value): writes.append((self.name, value)); return super().write(value)
        def flush(self): writes.append((self.name, 'FLUSH'))
    out, err = Stream('out'), Stream('err')
    computation = {'state': 'IDLE', 'requested_end': 'requested', 'completed_end': 'ready'}
    result = {'computation': computation, 'runtime': runtime}
    monkeypatch.setattr(control, '_request', lambda output, owner, command: result[owner])
    monkeypatch.setattr(console.sys, 'stdout', out)
    monkeypatch.setattr(console.sys, 'stderr', err)
    assert control.control_main(tmp_path, argv=['status']) == 0
    assert json.loads(out.getvalue()) == result
    assert expected in err.getvalue() and 'not runtime completion' in err.getvalue()
    assert writes.index(('err', 'FLUSH')) < next(i for i, (name, _) in enumerate(writes) if name == 'out')
