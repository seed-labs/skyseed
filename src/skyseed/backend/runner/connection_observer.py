"""Optional, bounded, nonblocking observation of acknowledged connection changes.

The worker maintains only current applied connections. It has no Docker client,
Model, viewer callback or authority to change forwarding. An overflow or partial
activation invalidates the view until cleanup/reset; it never fills gaps from Plans.
"""
import json
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Event, Thread
from time import time


class _ConnectionObserver:
    def __init__(self, directory, *, invocation, backend, capacity=512):
        self.directory = Path(directory)
        self.queue = Queue(maxsize=capacity)
        self.stop = Event()
        self.failed = False
        self.state = {'version': 2, 'invocation': invocation, 'backend': backend,
                      'available': False, 'status': 'preparing', 'active_frontier': None,
                      'revision': 0, 'topology_revision': 0}
        self.links, self.bindings = {}, {}
        self.published_topology_revision = None
        self.thread = Thread(target=self._run, name='skyseed-connection-observer')
        self.thread.start()

    def enqueue(self, event, *values):
        try:
            self.queue.put_nowait((event, values))
        except Full:
            self.failed = True

    def close(self):
        self.stop.set()
        self.thread.join()

    def _apply(self, event, values):
        topology_changed = False
        if event == 'bindings':
            self.bindings = values[0]
        elif event == 'activation':
            timestamp, changes = values
            if not self.failed:
                for change in changes:
                    key = change['connection']
                    if change['op'] == 'remove':
                        topology_changed |= self.links.pop(key, None) is not None
                    else:
                        link = {**change, 'from': self.bindings[change['node_a']],
                            'to': self.bindings[change['node_b']], 'applied_at': timestamp}
                        topology_changed |= self.links.get(key) != link
                        self.links[key] = link
                self.state.update(available=True, active_frontier=timestamp, status='active')
        elif event == 'failure':
            self.failed = True
        elif event == 'runtime_cleanup_end':
            topology_changed |= bool(self.links)
            self.links.clear()
            self.failed = False
            self.state.update(available=True, status='cleaned')
        elif event in ('pause_start', 'material_wait_start'):
            self.state['status'] = 'paused' if event == 'pause_start' else 'waiting-for-material'
        elif event in ('pause_end', 'material_wait_end', 'automatic_run_start'):
            self.state['status'] = 'active'
        if self.failed:
            topology_changed |= bool(self.links)
            self.links.clear()
            self.state.update(available=False, status='unavailable: partial execution or observation gap')
        if topology_changed:
            self.state['topology_revision'] += 1
        self.state['revision'] += 1

    def _write(self, name, value):
        temporary = self.directory / (name + '.tmp')
        temporary.write_text(json.dumps(value, separators=(',', ':'))+'\n')
        temporary.replace(self.directory / name)

    def _publish(self):
        # Overflow can occur without a subsequent event to apply the invalidation.
        if self.failed and not self.state['status'].startswith('unavailable:'):
            self._apply('failure', ())
        self.directory.mkdir(parents=True, exist_ok=True)
        revision = self.state['topology_revision']
        if revision != self.published_topology_revision:
            self._write('connections.json', {'invocation': self.state['invocation'],
                'topology_revision': revision, 'connections': list(self.links.values())})
            self.published_topology_revision = revision
        # Publish content first, then the small status/heartbeat referencing it.
        # Idle, paused and status-only updates never traverse the connection table.
        self.state['updated_at'] = time()
        self._write('live.json', self.state)

    def _run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                event, values = self.queue.get(timeout=0.5)
                self._apply(event, values)
                while True:
                    try:
                        self._apply(*self.queue.get_nowait())
                    except Empty:
                        break
            except Empty:
                pass
            except Exception:
                self.failed = True
                self._apply('failure', ())
            try:
                self._publish()
            except Exception:
                self._apply('failure', ())
        try:
            self._publish()
        except Exception:
            pass
