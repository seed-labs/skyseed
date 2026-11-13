"""An unstarted TCP client is an experiment outcome, not an archival failure."""
import json
from examples.s4_handover.experiment.record_store import archive_measurements


class MemoryStore:
    def __init__(self):
        self.values = {}

    def put_many(self, values):
        self.values.update(values)

    def put_user(self, key, value):
        self.values['user/' + key] = value

    def list(self):
        return self.values


def test_no_tcp_client_still_archives_attachment_and_server(tmp_path):
    for name in ['attachment', 'tcp-server']:
        (tmp_path / f'{name}.jsonl').write_text(json.dumps({'kind': 'waiting', 'monotonic': 3.5}) + '\n')
    result = archive_measurements(MemoryStore(), tmp_path)
    assert result['user/measurement-status'] == {'tcp_client_started': False}
    assert result['user/attachment/000000000']['monotonic'] == 3.5
    assert json.loads((tmp_path / 'store.json').read_text()) == result
