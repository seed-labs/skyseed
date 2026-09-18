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
