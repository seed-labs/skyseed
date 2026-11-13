"""A measurement deadline retains an unanswered probe; peer failure still fails."""
from io import StringIO
import errno
import json
import socket
from threading import Thread
from time import sleep
from types import SimpleNamespace

import pytest

from examples.s4_handover.experiment.ut import traffic


def exercise_client(monkeypatch, *, close_peer, kernel_timeout=False):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    address = listener.getsockname()

    def serve():
        with listener:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                if not close_peer:
                    sleep(.1)

    class Connection:
        """Redirect only the example's fixed destination to the test listener."""
        def __init__(self):
            self.socket = socket.socket()

        def connect(self, _address):
            self.socket.connect(address)

        def sendall(self, payload):
            if kernel_timeout:
                raise OSError(errno.ETIMEDOUT, 'kernel connection timed out')
            self.socket.sendall(payload)

        def __getattr__(self, name):
            return getattr(self.socket, name)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.socket.close()

    monkeypatch.setattr(traffic, 'socket', SimpleNamespace(
        socket=Connection, IPPROTO_TCP=socket.IPPROTO_TCP, TCP_NODELAY=socket.TCP_NODELAY))
    worker = Thread(target=serve)
    worker.start()
    output = StringIO()
    try:
        traffic.client('127.0.0.1', .04, output)
    finally:
        worker.join()
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_window_end_records_unanswered_probe_without_reconnect(monkeypatch):
    records = exercise_client(monkeypatch, close_peer=False)
    assert [record['kind'] for record in records] == ['connecting', 'connected', 'finished']
    assert records[-1]['echoes'] == 0
    assert records[-1]['pending_sequence'] == 0
    assert records[-1]['pending_sent'] < records[-1]['monotonic']


def test_peer_close_remains_an_error(monkeypatch):
    with pytest.raises(ConnectionError, match='server closed'):
        exercise_client(monkeypatch, close_peer=True)


def test_kernel_timeout_before_deadline_remains_an_error(monkeypatch):
    with pytest.raises(TimeoutError, match='kernel connection timed out'):
        exercise_client(monkeypatch, close_peer=True, kernel_timeout=True)
