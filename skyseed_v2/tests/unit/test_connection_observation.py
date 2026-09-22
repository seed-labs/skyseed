"""Applied topology publication is separate from lightweight status/liveness."""
import json
from queue import Full
from time import monotonic, sleep
from skyseed_v2.backend.runner.connection_observer import _ConnectionObserver


def published(directory, predicate):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        try:
            state = json.loads((directory / 'live.json').read_text())
            topology = json.loads((directory / 'connections.json').read_text())
            if (state['invocation'], state['topology_revision']) == (topology['invocation'], topology['topology_revision']) and predicate(state, topology):
                return state, topology
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        sleep(.01)
    raise AssertionError('observer did not publish the expected state')


LINK = {'op': 'create', 'connection': 'isl', 'node_a': 'a', 'node_b': 'b',
        'interface_a': 'sv2a', 'interface_b': 'sv2b'}


def test_idle_pause_and_frontier_updates_do_not_process_topology(tmp_path):
    class CountedLinks(dict):
        traversals = 0
        def values(self):
            self.traversals += 1
            return super().values()
    observer = _ConnectionObserver(tmp_path, invocation='first', backend='linux')
    try:
        observer.links = CountedLinks()
        observer.enqueue('bindings', {'a': 'container-a', 'b': 'container-b'})
        observer.enqueue('activation', 't0', (LINK,))
        state, topology = published(tmp_path, lambda s, t: s['available'])
        content = (tmp_path / 'connections.json').read_bytes()
        inode = (tmp_path / 'connections.json').stat().st_ino
        count = observer.links.traversals
        observer.enqueue('activation', 't1', ())
        observer.enqueue('pause_start')
        paused, _ = published(tmp_path, lambda s, t: s['status'] == 'paused')
        # Two actual idle heartbeat publications, without a viewer or new events.
        idle, _ = published(tmp_path, lambda s, t: s['updated_at'] > paused['updated_at'])
        published(tmp_path, lambda s, t: s['updated_at'] > idle['updated_at'])
        assert paused['active_frontier'] == 't1'
        assert paused['revision'] > state['revision']
        assert paused['topology_revision'] == state['topology_revision']
        assert observer.links.traversals == count
        assert (tmp_path / 'connections.json').read_bytes() == content
        assert (tmp_path / 'connections.json').stat().st_ino == inode
    finally:
        observer.close()


def test_replacement_gap_cleanup_and_reset(tmp_path, monkeypatch):
    observer = _ConnectionObserver(tmp_path, invocation='first', backend='bpf')
    try:
        published(tmp_path, lambda s, t: not s['available'] and not t['connections'])
        observer.enqueue('bindings', {'a': 'container-a', 'b': 'container-b'})
        observer.enqueue('activation', 't0', (LINK,))
        first, _ = published(tmp_path, lambda s, t: bool(t['connections']))
        observer.enqueue('activation', 't1', ({**LINK, 'op': 'remove'},))
        published(tmp_path, lambda s, t: s['active_frontier'] == 't1' and not t['connections'])
        observer.enqueue('activation', 't2', (LINK,))
        recreated, topology = published(tmp_path, lambda s, t: s['active_frontier'] == 't2')
        assert topology['connections'][0]['applied_at'] == 't2'
        assert recreated['topology_revision'] > first['topology_revision']
        # An overflow with no subsequent queued event must still invalidate.
        with monkeypatch.context() as patch:
            def full(*args): raise Full
            patch.setattr(observer.queue, 'put_nowait', full)
            observer.enqueue('activation', 'lost', ())
            published(tmp_path, lambda s, t: not s['available'] and not t['connections'])
        observer.enqueue('runtime_cleanup_end')
        published(tmp_path, lambda s, t: s['available'] and s['status'] == 'cleaned' and not t['connections'])
    finally:
        observer.close()
    reset = _ConnectionObserver(tmp_path, invocation='second', backend='bpf')
    try:
        published(tmp_path, lambda s, t: s['invocation'] == 'second' and not s['available'] and not t['connections'])
        reset.enqueue('failure')
        published(tmp_path, lambda s, t: s['status'].startswith('unavailable:'))
    finally:
        reset.close()


def test_observer_io_failure_does_not_escape_or_resurrect_topology(tmp_path):
    destination = tmp_path / 'not-a-directory'
    destination.write_text('occupied')
    observer = _ConnectionObserver(destination, invocation='failure', backend='bpf')
    observer.enqueue('activation', 't0', ())
    observer.close()
    assert observer.failed
    destination.unlink()
    observer._publish()
    state, topology = published(destination, lambda s, t: not s['available'])
    assert topology['connections'] == []
