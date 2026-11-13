"""A failed setup must not hide or retain later independent resources."""
import errno
import io
from unittest.mock import Mock

from examples.s4_handover.experiment.ut.attachment import Attachment


def test_partial_attachment_cleanup_continues_after_missing_route_and_ingress(monkeypatch):
    attachment = Attachment.__new__(Attachment)
    attachment.children = []
    attachment.captures = []
    attachment.files = []
    attachment.config = {"ground": "gs", "ground_ip": "198.18.0.1", "ground_ips": {"gs": "198.18.0.1"}, "ut_address": "192.0.2.2"}
    attachment.current = "star"
    attachment.original = 99
    attachment.log = io.StringIO()
    star, ut = Mock(), Mock()
    star.ingress.side_effect = OSError(errno.ENOENT, "missing rule")
    star.link_index.return_value = 11
    ut.route.side_effect = OSError(errno.ESRCH, "missing route")
    attachment.nodes = {"star": {"socket": star, "namespace": 10},
                        "ut": {"socket": ut, "namespace": 20, "identity": 22}}
    close = Mock()
    monkeypatch.setattr("examples.s4_handover.experiment.ut.attachment.os.close", close)
    attachment.close()
    assert [call.args[0] for call in star.delete_link.call_args_list] == [11]
    assert [call.args[0] for call in ut.delete_link.call_args_list] == [22]
    assert [call.args[0] for call in close.call_args_list] == [10, 20, 99]
    star.close.assert_called_once()
    ut.close.assert_called_once()


def test_late_same_time_adoption_does_not_attach_to_future_service_state():
    attachment = Attachment.__new__(Attachment)
    attachment.current = None
    attachment.config = {}
    attachment.change_positions = {}
    attachment.changes = {'2026-01-01T00:00:00+00:00|a': [True, None, False]}
    active = set()
    def event(count):
        return {'adoptions': [{'scheduled': '2026-01-01T00:00:00+00:00', 'nodes': ['a'], 'instruction_counts': {'a': count}}]}
    assert attachment.adopted_service(event(1), active) == 'a'
    attachment.current = 'a'
    assert attachment.adopted_service(event(1), active) == 'a'
    assert attachment.adopted_service(event(1), active) is None
    assert active == set()


def test_manual_stop_cancels_client_before_closing_its_server(monkeypatch):
    attachment = Attachment.__new__(Attachment)
    attachment.stopped = True
    attachment.config = {'ground_ips': {}}
    attachment.current = None
    attachment.nodes = {}
    attachment.original = 99
    attachment.log = io.StringIO()
    attachment.files = attachment.captures = []
    client_cancelled = False
    client, server = Mock(), Mock()
    client.poll.return_value = server.poll.return_value = None
    client.returncode = server.returncode = 0
    def cancel_client():
        nonlocal client_cancelled
        client_cancelled = True
    def close_server():
        assert client_cancelled, 'closing server first exposes unexpected EOF to the live client'
    client.terminate.side_effect = cancel_client
    server.terminate.side_effect = close_server
    attachment.children = [server, client]
    monkeypatch.setattr('examples.s4_handover.experiment.ut.attachment.os.close', lambda fd: None)
    attachment.close()
