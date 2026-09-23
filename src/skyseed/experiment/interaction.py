"""Etcd experiment interaction. Values are publications, never process snapshots.

Independent programs use InteractionClient; owners use the private publisher. No
Planner or backend imports this module. A receipt acknowledges storage only.
"""
from __future__ import annotations
from base64 import b64encode, b64decode
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import time, monotonic, sleep
from urllib.parse import urlparse, quote, unquote
from uuid import uuid4

from etcd3gw.client import Etcd3Client
from etcd3gw.exceptions import Etcd3Exception
from requests.exceptions import RequestException

TERMINAL = frozenset({'SUCCEEDED', 'FAILED', 'REJECTED', 'CANCELLED', 'EXPIRED'})


class InteractionUnavailable(RuntimeError):
    """Store unavailable; a submitted mutation may have indeterminate completion."""


class InteractionHistoryCompacted(RuntimeError):
    """Requested MVCC revision was compacted; explicitly reread current records."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def _b64(value):
    return b64encode(value.encode() if isinstance(value, str) else value).decode()


def _utc():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RequestReceipt:
    request_id: str
    owner: str
    session_id: str
    experiment_id: str
    accepted_revision: int

    def artifact(self):
        return asdict(self)


class InteractionClient:
    """Focused v3 queries, durable requests, and unrestricted JSON user values.

    Construction performs no I/O. ``from_output`` reads endpoint discovery only.
    Read versions are etcd modification revisions; optional revision pins an etcd
    consistent read, not simultaneous observations of independent processes.
    """
    def __init__(self, endpoint, experiment_id, *, timeout=2.0):
        parsed = urlparse(endpoint)
        if parsed.scheme not in {'http', 'https', 'unix'} or (parsed.scheme != 'unix' and not parsed.hostname):
            raise ValueError('endpoint must be an http(s) URL or absolute unix:///path to etcd')
        self.endpoint, self.experiment_id = endpoint, experiment_id
        self._root = '/skyseed/' + quote(experiment_id, safe='') + '/'
        self._store = Etcd3Client(host=parsed.hostname, port=parsed.port or 2379,
            protocol=parsed.scheme, timeout=timeout, api_path='/v3/')
        if parsed.scheme == 'unix':
            if not parsed.path.startswith('/'):
                raise ValueError('etcd socket path must be absolute')
            from requests_unixsocket import Session
            self._store.session.close()
            self._store.session = Session()
            # Same etcd v3 HTTP API, carried directly by etcd's own listener.
            self._store.get_url = lambda path: 'http+unix://' + quote(parsed.path, safe='') + '/v3/' + path.lstrip('/')
        # Explicit endpoint access must not inherit an unrelated HTTP proxy.
        self._store.session.trust_env = False

    @classmethod
    def from_output(cls, output, *, container=False, **kwargs):
        try:
            config = json.loads((Path(output) / '.control/interaction.json').read_text())
        except (OSError, ValueError) as error:
            raise InteractionUnavailable('experiment interaction discovery is unavailable') from error
        endpoint = os.environ.get('SKYSEED_INTERACTION_ENDPOINT') or config.get(
            'container_endpoint' if container else 'endpoint')
        if not endpoint:
            raise InteractionUnavailable('no reachable container endpoint was provisioned')
        return cls(endpoint, config['experiment_id'], **kwargs)

    @classmethod
    def from_environment(cls, **kwargs):
        """Container discovery provided by the generated Compose environment."""
        return cls(os.environ['SKYSEED_INTERACTION_ENDPOINT'], os.environ['SKYSEED_EXPERIMENT_ID'], **kwargs)

    def query(self, topic, *, timeout=None, **arguments):
        """On-demand runtime facts: communication, bindings, materials, or inspect.

        Returns a request-result envelope; check status before reading result.
        The observation is one owner's record at the stated effective position.
        Large inspection is explicit, never part of the periodic heartbeat.
        """
        if topic not in {'communication', 'bindings', 'materials', 'inspect'}:
            raise ValueError('unknown runtime query topic')
        return self.wait(self.submit('runtime', topic, **arguments), timeout=timeout)

    def shutdown(self, *, request_id=None):
        """Coordinate both owners, retaining etcd for results and explicit stop."""
        receipts = {}
        try:
            for owner in ('computation', 'runtime'):
                if self.state(owner)['available']:
                    try:
                        receipts[owner] = self.submit(owner, 'shutdown' if owner == 'computation' else 'cancel-runtime', request_id=request_id)
                    except InteractionUnavailable:
                        if self.state(owner).get('live'):
                            raise
        finally:
            self._put('coordination/shutdown', {'requested': True, 'at': _utc()})
        return receipts

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self._store, method)(*args, **kwargs)
        except (Etcd3Exception, RequestException, OSError) as error:
            if 'compacted' in str(getattr(error, 'detail_text', '')).lower():
                raise InteractionHistoryCompacted('requested etcd revision was compacted') from error
            raise InteractionUnavailable(f'etcd unavailable at {self.endpoint}; mutation completion may be indeterminate') from error

    def _key(self, key):
        return self._root + key

    def _read(self, key, *, revision=None, prefix=False, after_revision=None, limit=None):
        full = self._key(key)
        request = {'key': _b64(full)}
        if prefix:
            request['range_end'] = _b64(full[:-1] + chr(ord(full[-1]) + 1))
        if revision is not None:
            request['revision'] = str(revision)
        if after_revision is not None:
            request.update(min_mod_revision=str(after_revision + 1), sort_target='MOD', sort_order='ASCEND')
        if limit is not None:
            request['limit'] = str(limit)
        data = self._call('post', self._store.get_url('/kv/range'), json=request)
        return [{ 'key': b64decode(kv['key']).decode()[len(self._root):],
                  'value': json.loads(b64decode(kv['value'])),
                  'version': int(kv['mod_revision']), 'created_revision': int(kv['create_revision']),
                  'read_revision': int(revision or data['header']['revision']) }
                for kv in data.get('kvs', ())]

    def _get(self, key, **kwargs):
        rows = self._read(key, **kwargs)
        return rows[0] if rows else None

    def _put(self, key, value):
        return self._call('put', self._key(key), _json(value))

    def _txn(self, comparisons, puts=(), deletes=()):
        compare = [{'key': _b64(self._key(k)), 'target': 'MOD', 'result': 'EQUAL',
                    'mod_revision': str(revision)} for k, revision in comparisons]
        success = [{'request_put': {'key': _b64(self._key(k)), 'value': _b64(_json(v))}}
                   for k, v in puts]
        success += [{'request_delete_range': {'key': _b64(self._key(k))}} for k in deletes]
        return self._call('transaction', {'compare': compare, 'success': success, 'failure': []})

    def state(self, owner, *, revision=None):
        records = self._read_values(('state/' + owner, 'liveness/' + owner), revision=revision)
        record = records['state/' + owner]
        if record is None:
            return {'available': False, 'stale': True, 'reason': 'owner has not published'}
        value = record['value']
        heartbeat = record
        if value.get('liveness') == 'separate':
            heartbeat = records['liveness/' + owner]
        matching = heartbeat is not None and heartbeat['value'].get('session_id') == value['session_id']
        observed = heartbeat['value'] if matching else {}
        age = max(0., time() - observed.get('published_unix', 0.))
        stale = not matching or age > observed.get('fresh_for_seconds', 0.)
        return {**value, 'version': record['version'], 'read_revision': record['read_revision'],
                'liveness_version': heartbeat['version'] if matching else None,
                'fresh_for_seconds': observed.get('fresh_for_seconds'),
                'liveness_at': observed.get('published_at'), 'age_seconds': age, 'stale': stale,
                'available': value.get('live', False) and not stale}

    def state_history(self, owner, *, after_revision=0, limit=100):
        """Page meaningful snapshots in commit order, across owner sessions.

        These immutable records survive MVCC compaction. Resume after the last
        returned version. Observation time is owner-local, not a global clock.
        """
        if owner not in {'computation', 'runtime'} or after_revision < 0 or limit <= 0:
            raise ValueError('valid owner, nonnegative revision and positive limit required')
        return [{**row['value'], 'version': row['version'], 'read_revision': row['read_revision']}
                for row in self._read('state-history/' + owner + '/', prefix=True,
                                      after_revision=after_revision, limit=limit)]

    def watch_state(self, owner, *, start_revision=None, timeout=None):
        """Wait for one meaningful snapshot; start_revision is inclusive.

        Heartbeats are excluded. Compaction raises InteractionHistoryCompacted;
        recover explicitly using state_history and resume after its last version.
        """
        if owner not in {'computation', 'runtime'}:
            raise ValueError('owner must be computation or runtime')
        event = self._watch_once('state-history/' + owner + '/', prefix=True,
                                 start_revision=start_revision, timeout=timeout)
        return {**event['value'], 'version': event['version']}

    def user_get(self, key, *, revision=None):
        """Return value and versions, or None. Values have no framework semantics."""
        return self._get('user/' + quote(key, safe=''), revision=revision)

    def user_put(self, key, value, *, expected_version=None):
        path = 'user/' + quote(key, safe='')
        if expected_version is None:
            self._put(path, value)
        else:
            if not self._txn([(path, expected_version)], [(path, value)]).get('succeeded'):
                raise ValueError('user value version changed')
        return self.user_get(key)

    def user_read(self, keys, *, revision=None):
        """Optional consistent read; Models may instead read whenever they choose."""
        names = {key: 'user/' + quote(key, safe='') for key in keys}
        records = self._read_values(names.values(), revision=revision)
        return {name: records[path] for name, path in names.items()}

    def _read_values(self, keys, *, revision=None):
        names = tuple(keys)
        requests = []
        for key in names:
            query = {'key': _b64(self._key(key))}
            if revision is not None:
                query['revision'] = str(revision)
            requests.append({'request_range': query})
        data = self._call('transaction', {'compare': [], 'success': requests, 'failure': []})
        result = {}
        for key, response in zip(names, data.get('responses', ())):
            values = response['response_range'].get('kvs', ())
            kv = values[0] if values else None
            result[key] = None if kv is None else {'value': json.loads(b64decode(kv['value'])),
                'version': int(kv['mod_revision']), 'created_revision': int(kv['create_revision']),
                'read_revision': int(revision or data['header']['revision'])}
        return result

    def watch_user(self, key='', *, start_revision=None, timeout=None):
        """Wait for one change (including historical changes); timeout is caller-owned.

        Compacted history and disconnection raise; callers must reread explicitly.
        """
        event = self._watch_once('user/' + quote(key, safe=''), prefix=not key,
                                 start_revision=start_revision, timeout=timeout)
        return {**event, 'key': unquote(event['key'][len('user/'):])}

    def _watch_once(self, key, *, prefix, start_revision, timeout):
        # The gateway's convenience watcher drops cancellation/compaction frames.
        # Read one native watch stream directly so lost history is never silence.
        full = self._key(key)
        request = {'key': _b64(full)}
        if prefix:
            request['range_end'] = _b64(full[:-1] + chr(ord(full[-1]) + 1))
        if start_revision is not None:
            request['start_revision'] = str(start_revision)
        started = monotonic()
        try:
            with self._store.session.post(self._store.get_url('/watch'),
                    json={'create_request': request}, stream=True,
                    timeout=(self._store.timeout, timeout)) as response:
                response.raise_for_status()
                for line in response.iter_lines(chunk_size=1):
                    if not line:
                        continue
                    frame = json.loads(line)
                    result = frame.get('result', {})
                    if int(result.get('compact_revision', 0)):
                        raise InteractionHistoryCompacted('requested watch revision was compacted')
                    if result.get('canceled') or frame.get('error'):
                        raise InteractionUnavailable('etcd watch canceled: ' + str(frame))
                    for event in result.get('events', ()):
                        kv = event['kv']
                        return {'key': b64decode(kv['key']).decode()[len(self._root):],
                                'value': json.loads(b64decode(kv['value'])) if kv.get('value') else None,
                                'version': int(kv['mod_revision']), 'type': event.get('type', 'PUT')}
        except (RequestException, OSError) as error:
            if timeout is not None and monotonic() - started >= timeout:
                raise TimeoutError('no interaction change observed before timeout') from error
            raise InteractionUnavailable('etcd watch disconnected') from error
        raise InteractionUnavailable('etcd watch ended without a change')

    def submit(self, owner, command, *, request_id=None, at=None, session_id=None, **arguments):
        if isinstance(at, datetime):
            at = at.isoformat()
        if not isinstance(command, str) or not command:
            raise ValueError('command must be a nonempty string')
        if owner not in {'computation', 'runtime'}:
            raise ValueError('owner must be computation or runtime')
        state = self.state(owner)
        session_id = session_id or state.get('session_id')
        if session_id != state.get('session_id'):
            raise ValueError('target session has ended or was reset')
        if not session_id or not state['available']:
            raise InteractionUnavailable(f'{owner} owner unavailable or stale; no new decision admitted')
        identity = request_id or uuid4().hex
        if not identity or '/' in identity:
            raise ValueError('request_id must be a nonempty path segment')
        key = f'requests/{owner}/{session_id}/{identity}'
        intent = {'command': command, 'arguments': arguments, 'requested_position': at,
                  'owner': owner, 'session_id': session_id, 'request_id': identity}
        old = self._get(key)
        if old is not None:
            if old['value']['intent'] != intent:
                raise ValueError('request identity reused with different intent')
            return RequestReceipt(identity, owner, session_id, self.experiment_id, old['created_revision'])
        row = {'intent': intent, 'status': 'SUBMITTED', 'submitted_at': _utc(),
               'scheduled_position': None, 'actual_position': None,
               'history': [{'status': 'SUBMITTED', 'at': _utc()}]}
        # Fence admission against reset/closure, including submissions racing
        # the owner's final drain of its pending index.
        txn = self._txn([(key, 0), ('state/' + owner, state['version'])],
                        [(key, row), (f'pending/{owner}/{session_id}/{identity}', key)])
        if not txn.get('succeeded'):
            return self.submit(owner, command, request_id=identity, at=at, session_id=session_id, **arguments)
        return RequestReceipt(identity, owner, session_id, self.experiment_id, int(txn['header']['revision']))

    def result(self, receipt):
        if isinstance(receipt, dict):
            receipt = RequestReceipt(**receipt)
        if receipt.experiment_id != self.experiment_id:
            raise ValueError('receipt belongs to another experiment')
        record = self._get(f'requests/{receipt.owner}/{receipt.session_id}/{receipt.request_id}')
        return None if record is None else {**record['value'], 'version': record['version']}

    def wait(self, receipt, *, timeout=None):
        """Wait for terminal execution result; a timeout does not cancel a request."""
        start = monotonic()
        while True:
            result = self.result(receipt)
            if result and result['status'] in TERMINAL:
                return result
            if timeout is not None and monotonic() - start >= timeout:
                raise TimeoutError('request completion is indeterminate; retain the receipt and query again')
            sleep(.05)

    def close(self):
        self._store.session.close()


class _OwnerPublisher:
    def __init__(self, client, owner, session_id=None):
        self.client, self.owner = client, owner
        self.session_id = session_id or uuid4().hex
        self._published = self._pending = None
        self._sequence = 0
        self._heartbeat_due = 0.
        self._pending_results = {}

    def publish(self, facts, *, live=True):
        self._flush_results()
        serialized = _json({**facts, 'live': live})
        # Resolve an uncertain prior commit with the same immutable identity.
        # Do not create duplicate history on a lost acknowledgement.
        if self._pending is not None:
            self._commit_state()
        if serialized != self._published:
            row = {**json.loads(serialized), 'experiment_id': self.client.experiment_id,
                'session_id': self.session_id, 'owner': self.owner, 'sequence': self._sequence + 1,
                'published_at': _utc(), 'published_unix': time(), 'liveness': 'separate'}
            self._pending = serialized, row
            self._commit_state()
        elif monotonic() >= self._heartbeat_due:
            self.client._put('liveness/' + self.owner, self._heartbeat())
            self._heartbeat_due = monotonic() + 1.

    def _heartbeat(self):
        return {'session_id': self.session_id, 'published_at': _utc(),
                'published_unix': time(), 'fresh_for_seconds': 5.}

    def _commit_state(self):
        serialized, row = self._pending
        key = f'state-history/{self.owner}/{self.session_id}/{row["sequence"]:020d}'
        result = self.client._txn([(key, 0)], [(key, row), ('state/' + self.owner, row),
                                                ('liveness/' + self.owner, self._heartbeat())])
        if not result.get('succeeded'):
            existing = self.client._get(key)
            if existing is None or existing['value'] != row:
                raise RuntimeError('owner state history identity conflict')
        self._published, self._sequence = serialized, row['sequence']
        self._pending = None
        self._heartbeat_due = monotonic() + 1.

    def pending(self):
        self._flush_results()
        # Pending keys are created in the request's acceptance transaction. Updates
        # and duplicate submissions preserve their creation revision.
        return sorted(self.client._read(f'pending/{self.owner}/{self.session_id}/', prefix=True),
                      key=lambda row: (row['created_revision'], row['key']))

    def update(self, key, row, status, **facts):
        if status in TERMINAL:
            # Save the decision before any store I/O. This includes failed
            # dispatches: losing their publication must never replay the effect.
            if key not in self._pending_results:
                value = {**row, **facts, 'status': status,
                    'history': [*row['history'], {'status': status, 'at': _utc(), **facts}]}
                deletes = (f'pending/{self.owner}/{self.session_id}/{row["intent"]["request_id"]}',)
                self._pending_results[key] = value, deletes
            return self._flush_results()[key]
        self._flush_results()
        if row['status'] == status and all(row.get(k) == v for k, v in facts.items()):
            return row
        stored = self.client._get(key)
        if stored is not None:
            if stored['value']['status'] in TERMINAL:
                return stored['value']
            row = {**stored['value'], **row, 'history': stored['value']['history']}
        value = {**row, **facts, 'status': status,
                 'history': [*row['history'], {'status': status, 'at': _utc(), **facts}]}
        self.client._txn([], [(key, value)])
        return json.loads(_json(value))

    def _flush_results(self):
        completed = {}
        for key, (value, deletes) in tuple(self._pending_results.items()):
            stored = self.client._get(key)
            if stored is not None and stored['value']['status'] in TERMINAL:
                value = stored['value']  # Terminal facts survive lost acknowledgements.
            else:
                if stored is not None:
                    value = {**stored['value'], **value,
                        'history': [*stored['value']['history'], value['history'][-1]]}
                self.client._txn([], [(key, value)], deletes)
            completed[key] = json.loads(_json(value))
            del self._pending_results[key]
        return completed


def _resource_parameters(output):
    if not (Path(output) / '.control/interaction.json').exists():
        return {}  # Explicit standalone, unmanaged deployment.
    client = InteractionClient.from_output(output)
    try:
        row = client._get('parameters/next-resources')
        return {} if row is None else row['value']
    finally:
        client.close()
